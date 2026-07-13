#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style_similarity_scanner.py — L1b 风格相似度漂移分（cluster 视野 · advisory · 2026-05-31）

【是什么】把「作者原文池」当成 one-class / OOD 参照系：
  1. baseline —— 对作者原文每章算 embedding，得到作者**风格中心**（centroid）
     + **作者自身章节相对中心的余弦相似度分布**（mean / σ）。
  2. drift —— 对当前 cluster 草稿算 embedding，量它与作者风格中心的 cosine。
     若 sim < (mean − 2σ)（作者自己 95% 章节都在 mean−2σ 之上），则该 cluster 在
     作者风格空间里是 **离群点（out-of-distribution）** → 出一条 advisory「风格相似度漂移」。

【阈值自适应标定】band 不写死，从**作者原文自身的章节相似度分布涌现**（mean−2σ）——
  即「这个作者的章节彼此之间能差到什么程度」由作者样本决定（北极星① band/baseline 从原文涌现）。
  不同作者的风格内聚程度不同（蛊真人冷峻设定厚重 / 惊悚乐园黑色幽默群像），σ 不同，阈值各异。

【复用】严格只读复用 embedding_store.py（compute_embedding / cosine_similarity / embedding_method），
  范式照搬 embedding_store.compute_character_drift（centroid + cosine + 维度混用防护），
  不改 embedding_store 任何一行。

【embedding 后端】默认 hash（embedding_store._detect_backend 默认 hash · opt-in 才本地模型链）。
  ⚠️ hash 后端是 md5 ngram 袋，**语义弱**——同义改写/换词会被当成漂移，相似度只反映字面 ngram 重叠。
  本 scanner 在 hash 下是**占位**（结构成立但语义信号弱，prone to 误判）；
  只有用户 opt-in 真语义后端（EMBED_BACKEND=mstyle/ruoyu_style/local 等本地链）后，
  相似度才有强语义意义。报告里 backend 字段透出当前后端 + 占位提示，供裁决者判定可信度。

【顾问非法官 · 影子并行】(北极星⑤ + 共同纪律 4)
  · 永远 advisory，code STYLE_SIMILARITY_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。
  · env L1B_SIMILARITY_MODE 控制（默认 shadow）：
      shadow（默认）：算 drift 分但**只记录到报告 + stderr · 顶层 warning=null · severity=info**
                      → audit_hub 收不到 issue → 零回归不改判决。
      active：超 (mean−2σ) 时顶层 warning 非空 + severity=warning → 作为 advisory 待裁决项上报。
      off：完全跳过（连 embedding 都不算）。

用法：
  baseline:  python style_similarity_scanner.py <project> rebuild [--author-pool <dir>]
  drift:     python style_similarity_scanner.py <project> <cluster_draft_path> [--author-pool <dir>]
  （drift 模式 baseline 缺失时自动尝试 rebuild 一次）
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

# 只读复用 embedding_store —— 不改它任何一行（北极星⑥ 别新起并行系统）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import embedding_store as es  # noqa: E402

ISSUE_CODE = "STYLE_SIMILARITY_DRIFT"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES
SIGMA_K = 2.0                            # 阈值 = mean − k·σ（k=2 → 覆盖作者自身 ~95% 章节）
_MIN_BASELINE_CHAPTERS = 3               # 少于 3 章无法估 σ → 跳过（样本不足不强判）


# ════════════════════════════════════════════════════════════════
# 影子并行模式（共同纪律 4）
# ════════════════════════════════════════════════════════════════

def _mode() -> str:
    """L1B_SIMILARITY_MODE：shadow（默认·只记不判） / active / off。非法值回退 shadow。"""
    m = os.environ.get("L1B_SIMILARITY_MODE", "shadow").strip().lower()
    return m if m in ("shadow", "active", "off") else "shadow"


# ════════════════════════════════════════════════════════════════
# 工具：正文剥离 / CJK 计数 / 章节文本读取
# ════════════════════════════════════════════════════════════════

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _strip_changes(text: str) -> str:
    """剥离系统写作时拼在正文尾部的 CHANGES 段（作者原文一般没有，幂等安全）。"""
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _embedding_dim() -> int:
    """当前后端维度（embedding_store._detect_backend 返回 (method, dim, fn)）。"""
    return es._detect_backend()[1]


def _text_centroid(text: str, chunk: int = 500) -> "list[float] | None":
    """把一段长文本切 chunk 求 embedding 均值并归一 → 这段文本的风格 centroid。
    范式同 embedding_store.store_character_baseline（chunk + 均值 + L2 归一）。"""
    text = text.strip()
    if not text:
        return None
    chunks = [text[i:i + chunk] for i in range(0, len(text), chunk)]
    embs = [es.compute_embedding(c) for c in chunks if c.strip()]
    embs = [e for e in embs if e]
    if not embs:
        return None
    dim = len(embs[0])
    vec = [sum(e[i] for e in embs) / len(embs) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _mean_centroid(centroids: "list[list[float]]") -> "list[float] | None":
    """多个章节 centroid 再求均值并归一 → 作者整体风格中心。"""
    centroids = [c for c in centroids if c]
    if not centroids:
        return None
    dim = len(centroids[0])
    vec = [sum(c[i] for c in centroids) / len(centroids) for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ════════════════════════════════════════════════════════════════
# 作者原文池定位（顾问制 · 多路降级 · 作者档第一权威）
# ════════════════════════════════════════════════════════════════

def _workspace_root(project_root: Path) -> "Path | None":
    """从 project_root 向上找含 workspace/styles 的仓库根。"""
    for parent in [project_root, *project_root.parents]:
        if (parent / "workspace" / "styles").is_dir():
            return parent
    # 退到 scanner 自身仓库根（core/scripts 的 parents[2]）
    repo = _SCRIPT_DIR.parents[1]
    if (repo / "workspace" / "styles").is_dir():
        return repo
    return None


def resolve_author_pool(project_root: Path, explicit: "str | None" = None) -> "Path | None":
    """定位作者原文池目录（含第NNN章.txt）。降级链：
      ① 显式 --author-pool（直跑/测试）
      ② 项目 用户偏好.json.style_baseline_data_path → 其父目录 / 原文
      ③ 项目 作者风格.json._meta.work → workspace/styles/<work>/原文
    都缺 → None（baseline 不可建 · 顾问制：不报错只跳过）。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else None

    db = project_root / "_数据库"

    # ② 用户偏好里登记的风格基线 JSON 路径 → 同目录 原文/
    pref = db / "用户偏好.json"
    if pref.exists():
        try:
            up = json.loads(pref.read_text(encoding="utf-8"))
            sp = up.get("style_baseline_data_path") or up.get("style_skill_path")
            if sp:
                ws = _workspace_root(project_root)
                cand = (ws / sp) if ws else Path(sp)
                pool = cand.parent / "原文"
                if pool.is_dir():
                    return pool
        except Exception:
            pass

    # ③ 作者风格.json._meta.work → workspace/styles/<work>/原文
    style = db / "作者风格.json"
    if style.exists():
        try:
            sd = json.loads(style.read_text(encoding="utf-8"))
            work = ((sd.get("_meta") or {}).get("work")
                    or (sd.get("meta") or {}).get("work") or sd.get("work"))
            if work:
                ws = _workspace_root(project_root)
                if ws:
                    pool = ws / "workspace" / "styles" / work / "原文"
                    if pool.is_dir():
                        return pool
        except Exception:
            pass
    return None


def _list_author_chapters(pool: Path, limit: int = 40) -> "list[Path]":
    """作者原文池里的章节文件（第NNN章.txt），按章号排序，取前 limit 章。"""
    files = []
    for f in pool.glob("第*章.txt"):
        m = re.match(r"第(\d+)章", f.name)
        if m:
            files.append((int(m.group(1)), f))
    files.sort(key=lambda x: x[0])
    return [f for _, f in files[:limit]]


# ════════════════════════════════════════════════════════════════
# baseline 构建 / 持久化
# ════════════════════════════════════════════════════════════════

def _baseline_path(project_root: Path) -> Path:
    d = project_root / "_数据库" / ".embeddings"
    d.mkdir(parents=True, exist_ok=True)
    return d / "style_similarity_baseline.json"


def build_baseline(project_root: Path, author_pool: "Path | None" = None) -> dict:
    """对作者原文池建风格 centroid + 章节自相似分布（mean/σ + mean−2σ 阈值）。"""
    pool = author_pool or resolve_author_pool(project_root)
    if pool is None:
        return {"_skip": "未定位到作者原文池（无 --author-pool / 用户偏好 / 作者风格._meta.work）"}
    chapters = _list_author_chapters(pool)
    if len(chapters) < _MIN_BASELINE_CHAPTERS:
        return {"_skip": f"作者原文池章节数 {len(chapters)} < {_MIN_BASELINE_CHAPTERS}，样本不足不建 baseline"}

    method = es.embedding_method()
    chapter_centroids = []
    for f in chapters:
        try:
            text = _strip_changes(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _cjk_count(text) < 200:   # 太短的目录页/空章跳过
            continue
        c = _text_centroid(text)
        if c:
            chapter_centroids.append(c)
    if len(chapter_centroids) < _MIN_BASELINE_CHAPTERS:
        return {"_skip": f"有效章节 centroid {len(chapter_centroids)} < {_MIN_BASELINE_CHAPTERS}，样本不足"}

    center = _mean_centroid(chapter_centroids)
    # 作者每章 vs 风格中心的 cosine 分布 → 自适应阈值（one-class / OOD 范式）
    sims = [es.cosine_similarity(center, c) for c in chapter_centroids]
    n = len(sims)
    mean = sum(sims) / n
    var = sum((s - mean) ** 2 for s in sims) / n        # population variance（含中心点自身）
    std = math.sqrt(var)
    threshold = mean - SIGMA_K * std                     # 低于此 = OOD（漂移）

    record = {
        "scope": "style_similarity_baseline",
        "schema_version": "1.0",
        "method": method,                # 维度混用防护：记录建库后端
        "embedding_dim": len(center),
        "author_pool": str(pool),
        "n_chapters_used": n,
        "center_embedding": center,
        "self_similarity": {
            "mean": round(mean, 6),
            "std": round(std, 6),
            "min": round(min(sims), 6),
            "max": round(max(sims), 6),
        },
        "sigma_k": SIGMA_K,
        "drift_threshold": round(threshold, 6),   # sim < 此 → advisory（active 模式）
    }
    out = _baseline_path(project_root)
    out.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    record["_path"] = str(out)
    return record


def _load_baseline(project_root: Path) -> "dict | None":
    p = _baseline_path(project_root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# drift 评分（核心 · 范式同 embedding_store.compute_character_drift）
# ════════════════════════════════════════════════════════════════

def scan(project_root: Path, draft_path: Path, author_pool: "Path | None" = None) -> dict:
    mode = _mode()
    base = {
        "schema_version": "1.0",
        "scanner": "style_similarity_scanner",
        "cluster_mode": True,
        "mode": mode,
        "backend": es.embedding_method(),
        "code": ISSUE_CODE,
        "gate_level": "advisory",   # 永久 advisory · 绝不进 HARD_GATE_CODES
    }
    # hash 后端语义弱 → 透出占位提示（裁决者据此判可信度）
    if base["backend"] == "hash":
        base["backend_note"] = ("hash 后端为占位（md5 ngram 袋·语义弱·相似度只反映字面重叠）·"
                                "bge/通义 API opt-in 后才强语义")

    if mode == "off":
        base.update({"warning": None, "severity": "info", "_skip": "mode=off"})
        return base

    if not draft_path.exists():
        base["_fatal"] = f"draft 不存在: {draft_path}"
        return base
    draft_text = _strip_changes(draft_path.read_text(encoding="utf-8"))
    if _cjk_count(draft_text) < 200:
        base.update({"warning": None, "severity": "info", "_skip": "草稿 CJK < 200，样本不足不评"})
        return base

    bl = _load_baseline(project_root)
    if bl is None:
        bl = build_baseline(project_root, author_pool)   # 首跑自动建
        if "_skip" in bl:
            base.update({"warning": None, "severity": "info", "baseline_skip": bl["_skip"]})
            return base

    # 维度混用防护：baseline 后端 ≠ 当前后端 → 不误报 drift，提示重建（范式同 compute_character_drift）
    bl_method = bl.get("method", "hash")
    if bl_method != es.embedding_method():
        base.update({
            "warning": None, "severity": "info",
            "baseline_skip": (f"baseline method={bl_method} ≠ 当前={es.embedding_method()}，"
                              f"请先 style_similarity_scanner rebuild 重建（维度变了）"),
        })
        return base

    center = bl.get("center_embedding") or []
    draft_centroid = _text_centroid(draft_text)
    if draft_centroid is None or not center:
        base.update({"warning": None, "severity": "info", "_skip": "centroid 计算失败"})
        return base

    sim = es.cosine_similarity(center, draft_centroid)
    threshold = bl.get("drift_threshold", 0.0)
    self_mean = (bl.get("self_similarity") or {}).get("mean")
    self_std = (bl.get("self_similarity") or {}).get("std")
    is_drift = sim < threshold

    base.update({
        "n_baseline_chapters": bl.get("n_chapters_used"),
        "author_self_similarity_mean": self_mean,
        "author_self_similarity_std": self_std,
        "sigma_k": bl.get("sigma_k", SIGMA_K),
        "drift_threshold": threshold,
        "cluster_similarity": round(sim, 6),
        "is_ood_drift": is_drift,
    })

    if is_drift:
        msg = (f"cluster 风格相似度 {sim:.3f} < 作者自适应阈值 {threshold:.3f}"
               f"（作者自相似 mean={self_mean} σ={self_std} · mean−{base['sigma_k']}σ）"
               f"→ 在作者风格空间里是离群点（OOD）")
        if mode == "active":
            # active：作为 advisory 待裁决项上报（warning 非空 → audit_hub 收）
            base.update({"warning": "⚠️ " + msg, "severity": "warning"})
        else:
            # shadow（默认）：只记录不改判决（warning=null → audit_hub 收不到 issue）
            base.update({"warning": None, "severity": "info", "shadow_note": msg})
            print(f"[style_similarity:shadow] {msg}", file=sys.stderr)
    else:
        base.update({"warning": None, "severity": "info"})
    return base


# ════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    project = Path(args[0]).resolve()
    pos1 = args[1]
    author_pool = None
    if "--author-pool" in args:
        i = args.index("--author-pool")
        if i + 1 < len(args):
            author_pool = Path(args[i + 1]).resolve()

    if pos1 == "rebuild":
        rec = build_baseline(project, author_pool)
        # 打印不含巨大 embedding 向量的摘要
        summary = {k: v for k, v in rec.items() if k != "center_embedding"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.exit(0 if "_skip" not in rec else 0)   # skip 也 exit 0（顾问制·样本不足不报错）

    draft = Path(pos1).resolve()
    report = scan(project, draft, author_pool)
    summary = {k: v for k, v in report.items() if k != "center_embedding"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if "_fatal" in report:
        sys.exit(2)   # 与兄弟 scanner 一致：草稿缺失 ≠ 干净通过
    # advisory scanner：active 模式命中漂移才 exit 1（待裁决项）；shadow/off/正常 exit 0
    if report.get("warning"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
