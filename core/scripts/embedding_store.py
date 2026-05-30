"""embedding_store.py — 轻量级 embedding 索引（v19.6 G1 新增）"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path


def _stable_hash_embedding(text: str, dim: int = 384) -> list[float]:
    ngrams = []
    for n in (2, 3):
        for i in range(len(text) - n + 1):
            ngrams.append(text[i:i+n])
    if not ngrams:
        return [0.0] * dim
    vec = [0.0] * dim
    for ng in ngrams:
        h = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if (h >> 1) & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if len(v1) != len(v2):
        return 0.0
    return sum(a * b for a, b in zip(v1, v2))


# ── 真语义 embedding 后端（2026-05-30 · 取代 md5 哈希袋假语义 · 用户选通义 API）──────
# 降级链：通义/OpenAI兼容 embedding API（.env GEN_EMBED__*）→ 本地 sentence-transformers → hash。
# compute_embedding 签名不变（消费方 build_manifest RAG / voice drift 零改动）；
# 默认无 key + 无本地包 → hash（行为完全不变，零回归）；配 .env 的 GEN_EMBED key 自动启用真语义。
# ⚠️ 切换后端会改变维度（hash 384 / bge 512 / 通义 1024）→ 必须重建缓存（rebuild 写 .embed_manifest.json
#    记 method+dim；cosine 维度不等返回 0，drift 会显示 sim 异常提示重建）。
_BACKEND = None          # (method:str, dim:int, fn) 探测缓存
_LOCAL_MODEL = None      # 本地 sentence-transformers 模型 lazy 缓存


def _embed_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_embed_profile() -> "dict | None":
    """读 .env 的 GEN_EMBED__<name>__{BASE_URL,API_KEY,MODEL,DIM}（同 gen_model_loader 模式）。
    GEN_EMBED_ACTIVE 指定用哪个 profile；缺则取第一个。字段不全 → None（降级本地/hash）。"""
    env_path = None
    for cand in (Path(".env"), _embed_repo_root() / ".env"):
        if cand.exists():
            env_path = cand
            break
    if env_path is None:
        return None
    try:
        text = env_path.read_text(encoding="utf-8")
    except Exception:
        return None
    profiles: dict = {}
    active = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        m_act = re.match(r"GEN_EMBED_ACTIVE\s*=\s*(.+)", line)
        if m_act:
            active = m_act.group(1).strip()
            continue
        m = re.match(r"^GEN_EMBED__(.+?)__([A-Z_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            name, field, val = m.group(1), m.group(2).lower(), m.group(3).strip()
            profiles.setdefault(name, {})[field] = val
    if not profiles:
        return None
    name = active if (active and active in profiles) else next(iter(profiles))
    p = profiles[name]
    if not (p.get("api_key") and p.get("base_url") and p.get("model")):
        return None
    p["name"] = name
    p["dim"] = int(p["dim"]) if str(p.get("dim", "")).isdigit() else 1024
    return p


def _api_embed(profile: dict, text: str) -> list[float]:
    from openai import OpenAI
    client = OpenAI(api_key=profile["api_key"], base_url=profile["base_url"])
    kwargs = {"model": profile["model"], "input": text[:8000]}
    if profile.get("dim"):
        kwargs["dimensions"] = profile["dim"]   # 通义 text-embedding-v4 支持自定义维度
    resp = client.embeddings.create(**kwargs)
    return list(resp.data[0].embedding)


def _local_embed(text: str) -> list[float]:
    global _LOCAL_MODEL
    if _LOCAL_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _LOCAL_MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _LOCAL_MODEL.encode(text[:8000], normalize_embeddings=True).tolist()


def _detect_backend():
    """探测 embedding 后端（一次，缓存）。返回 (method, dim, fn)。

    🔴 真语义是 **opt-in**：默认 hash（零回归 · 与现有缓存维度一致 · 即使环境恰好装了
    sentence-transformers 也不自动切，避免「维度混用导致 cosine=0 → voice drift/RAG 静默失效」+
    「首次 encode 触发模型下载拖慢流水线」）。只有用户显式配置才启用真语义：
      ① .env 配 GEN_EMBED__* key → 通义/OpenAI 兼容 API
      ② 环境变量 EMBED_BACKEND=local（且装了 sentence-transformers）→ 本地 bge
    切换后端务必先 `embedding_store.py <proj> rebuild` 重建缓存（维度变了）。"""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    # ① 用户显式配 GEN_EMBED API
    prof = _load_embed_profile()
    if prof:
        _BACKEND = (f"api:{prof['model']}", prof["dim"], lambda t: _api_embed(prof, t))
        print(f"[embedding_store] 后端=API {prof['model']} (dim={prof['dim']}) · 切后端记得 rebuild", file=sys.stderr)
        return _BACKEND
    # ② 用户显式 EMBED_BACKEND=local + 装了包
    if os.environ.get("EMBED_BACKEND", "").strip().lower() == "local":
        try:
            import sentence_transformers  # noqa: F401
            _BACKEND = ("local:bge-small-zh-v1.5", 512, _local_embed)
            print("[embedding_store] 后端=本地 bge-small-zh-v1.5 (dim=512) · 切后端记得 rebuild", file=sys.stderr)
            return _BACKEND
        except ImportError:
            print("[embedding_store] EMBED_BACKEND=local 但未装 sentence-transformers，降级 hash", file=sys.stderr)
    # ③ 默认 hash（零回归 · 不因环境装了包就静默切换）
    _BACKEND = ("hash", 384, lambda t: _stable_hash_embedding(t, 384))
    return _BACKEND


def embedding_method() -> str:
    """当前 embedding 后端标识（供 .embed_manifest 记录 + 一致性校验）。"""
    return _detect_backend()[0]


def compute_embedding(text: str) -> list[float]:
    """真语义 embedding（降级链）。任何后端失败 → hash 兜底（永不崩，呼应 MAPE-K「失败记录降级」）。"""
    method, _dim, fn = _detect_backend()
    try:
        v = fn(text)
        if v and len(v) > 0:
            return v
    except Exception as e:
        print(f"[embedding_store] {method} 失败降级 hash: {str(e)[:120]}", file=sys.stderr)
    return _stable_hash_embedding(text, dim=384)


def _emb_dir(project_root: Path) -> Path:
    p = project_root / "_数据库" / ".embeddings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def store_chapter_embedding(project_root: Path, ch: int):
    text_path = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
    if not text_path.exists():
        return None
    text = text_path.read_text(encoding="utf-8")
    chunks = [text[i:i+500] for i in range(0, len(text), 500)]
    chunk_embs = [compute_embedding(c) for c in chunks]
    record = {
        "scope": "chapter", "ch": ch, "wc": len(text), "n_chunks": len(chunks),
        "method": embedding_method(),   # 维度混用防护：记录生成时的后端
        "chunks": [{"idx": i, "text_preview": c[:80], "embedding": e} for i, (c, e) in enumerate(zip(chunks, chunk_embs))],
    }
    out_path = _emb_dir(project_root) / f"chapter_{ch:03d}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return out_path


def _extract_character_dialogues(project_root: Path, character_name: str, max_d: int = 20, chapters: list[int] = None) -> list[str]:
    chars = json.loads((project_root / "_数据库" / "人物卡.json").read_text(encoding="utf-8"))
    char = next((c for c in chars.get("characters", []) if c.get("name") == character_name or c.get("id") == character_name), None)
    if not char:
        return []
    aliases = list(set([a for a in [char.get("name"), char.get("id")] + char.get("name_aliases", []) if a]))

    dialogues = []
    files = []
    if chapters:
        for ch in chapters:
            p = project_root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
            if p.exists():
                files.append(p)
    else:
        files = list((project_root / "章节").glob("第*章/第*章.txt"))

    for f in files:
        text = f.read_text(encoding="utf-8")
        for alias in aliases:
            for m in re.finditer(re.escape(alias), text):
                start = m.start()
                window = text[max(0, start-200):min(len(text), start+200)]
                # 2026-05-30 北极星复审：原两个 alternation 全是 ASCII " 重复 + 漏中文弯引号 → 中文对话提取失效
                for q in re.findall(r'"([^"\n]{3,80})"|“([^”\n]{1,80})”|「([^」\n]{1,80})」', window):
                    d = next((g for g in q if g), "").strip()
                    if d and d not in dialogues:
                        dialogues.append(d)
                        if len(dialogues) >= max_d:
                            return dialogues
    return dialogues


def store_character_baseline(project_root: Path, character_name: str):
    dialogues = _extract_character_dialogues(project_root, character_name, max_d=20)
    if not dialogues:
        return None
    embs = [compute_embedding(d) for d in dialogues]
    n = len(embs)
    baseline = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
    norm = math.sqrt(sum(v * v for v in baseline))
    if norm > 0:
        baseline = [v / norm for v in baseline]
    record = {
        "scope": "character_dialogue", "character": character_name, "n_samples": n,
        "method": embedding_method(),   # 维度混用防护：记录生成时的后端
        "baseline_embedding": baseline, "sample_dialogues": dialogues[:5],
    }
    out_path = _emb_dir(project_root) / f"character_{character_name}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return out_path


def compute_character_drift(project_root: Path, character_name: str, recent_ch: int) -> dict:
    baseline_path = _emb_dir(project_root) / f"character_{character_name}.json"
    if not baseline_path.exists():
        return {"error": "baseline 不存在", "character": character_name}
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_emb = baseline_data.get("baseline_embedding", [])
    # 维度混用防护：baseline 后端 ≠ 当前后端 → 提示重建（而非 cosine=0 误报 drift）
    bl_method = baseline_data.get("method", "hash")
    if bl_method != embedding_method():
        return {"drift": None, "character": character_name, "recent_ch": recent_ch,
                "reason": f"baseline method={bl_method} ≠ 当前={embedding_method()}，请先跑 embedding_store rebuild 重建"}

    dialogues = _extract_character_dialogues(project_root, character_name, max_d=10, chapters=[recent_ch])
    if not dialogues:
        return {"drift": None, "character": character_name, "recent_ch": recent_ch, "reason": "本章无对话"}

    embs = [compute_embedding(d) for d in dialogues]
    n = len(embs)
    avg = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
    norm = math.sqrt(sum(v * v for v in avg))
    if norm > 0:
        avg = [v / norm for v in avg]
    sim = cosine_similarity(baseline_emb, avg)
    return {
        "character": character_name, "recent_ch": recent_ch,
        "baseline_samples": baseline_data.get("n_samples"),
        "recent_samples": n,
        "cosine_similarity": round(sim, 3),
        "drift": round(1 - sim, 3),
        "alert": "drift > 0.3" if (1 - sim) > 0.3 else "ok",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["rebuild", "drift", "chapter"])
    ap.add_argument("--character", default=None)
    ap.add_argument("--ch", type=int, default=None)
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "rebuild":
        chapters = sorted(int(re.match(r"第(\d+)章", d.name).group(1))
                          for d in (project_root / "章节").glob("第*章")
                          if re.match(r"第(\d+)章", d.name))
        for ch in chapters:
            p = store_chapter_embedding(project_root, ch)
            if p:
                print(f"  [OK] ch{ch} chapter embedding")
        chars = json.loads((project_root / "_数据库" / "人物卡.json").read_text(encoding="utf-8"))
        for c in chars.get("characters", []):
            name = c.get("name")
            if name:
                p = store_character_baseline(project_root, name)
                if p:
                    print(f"  [OK] character_{name} baseline")
                else:
                    print(f"  [SKIP] character_{name}（无对话样本）")
        print(f"[embedding_store] rebuild 完成")
        sys.exit(0)

    elif args.action == "drift":
        if not args.character or not args.ch:
            print("[ERROR] drift 需 --character 和 --ch", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(compute_character_drift(project_root, args.character, args.ch), ensure_ascii=False, indent=2))
        sys.exit(0)

    elif args.action == "chapter":
        if not args.ch:
            print("[ERROR] chapter 需 --ch", file=sys.stderr)
            sys.exit(2)
        p = store_chapter_embedding(project_root, args.ch)
        print(f"[OK] ch{args.ch} → {p}")
        sys.exit(0)


if __name__ == "__main__":
    main()
