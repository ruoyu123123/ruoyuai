"""embedding_store.py — 轻量级 embedding 索引（v19.6 G1 新增）"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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


def compute_embedding(text: str) -> list[float]:
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
