#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""genre_pack_clash_scanner.py — pack 间已知冲突两端峰值 advisory
(shadow · cluster · 2026-06-20 R11 W6 MODEST 占位)

【缺口】两 pack clash 在 cluster 内呈现"两端峰值"分布(scene A 全主 pack / scene B 全副 pack)
缺少融合·与 dominance_scanner 正交(那个查整体平均·此处查 clash 维度峰值二态分布)

【做法】
  1. 取 author_genre_packs 2-combination 命中 registry 的 axis(emotional_intensity_baseline /
     stakes_persistence / metaphysics_register 等)
  2. 每 scene 给 axis 打分：
     · 默认 = marker_density 差(词袋计数 pack A - pack B)
     · 🔴 2026-07-01 EMBED_BACKEND 配置真后端时 = scene embedding 与两 pack「原型描述文本」
       (marker_lexicon 词袋拼接) 余弦相似度之差·原型/场景 embedding 缺失或维度不一致时
       该 pair 单独退回词袋差(不影响其他 pair)
  3. 若分布双峰(top 1/3 全 pack A 主导 + bottom 1/3 全 pack B 主导 + 中段无过渡)
     → advisory CLASH_UNRESOLVED（双峰阈值：词袋差值 1.0 / 语义相似度差值 0.15·量纲不同分开标定）
  4. 作者档 author_fusion_resolution 覆盖 registry 时 skip

【依赖】embedding_store.compute_embedding() + cosine_similarity()（同 topic_drift_scanner 模式）。
  EMBED_BACKEND 未设（默认 hash·无真语义）→ 完全走词袋差·输出 match_method="lexicon"。

【北极星】② 作者档显式 fusion_resolution > registry seed·shadow 默认占位·绝不 hard_gate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "CLASH_UNRESOLVED"
_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
_LEXICON_PATH = Path(__file__).resolve().parent / "lexicons" / "genre_markers"


def _mode() -> str:
    m = (os.environ.get("GENRE_PACK_CLASH_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_packs_and_override(project_root):
    if not project_root:
        return [], {}
    p = Path(project_root) / "_数据库" / "作者风格.json"
    if not p.exists():
        return [], {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], {}
    if not isinstance(obj, dict):
        return [], {}
    packs = obj.get("author_genre_packs", []) or []
    override = obj.get("author_fusion_resolution", {}) or {}
    return [str(p_).lower() for p_ in packs if isinstance(p_, str)], override


def _load_marker_lexicon(pack):
    f = _LEXICON_PATH / f"{pack}.json"
    if not f.exists():
        return []
    try:
        obj = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(obj, dict):
        return [str(t) for t in obj.get("markers", []) if isinstance(t, str)]
    return []


# ── 🔴 2026-07-01 真语义 embedding 可选路径（完全照抄 topic_drift_scanner 已验证的模式）───────
def _has_real_embedding_backend() -> bool:
    """跟 topic_drift_scanner._has_real_embedding_backend 判断逻辑完全一致（各文件各自留一份）。"""
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def _pack_prototype_text(pack: str) -> str:
    """题材包"原型描述文本"：marker_lexicon 无独立 description 字段·由判别词袋拼接代替。"""
    markers = _load_marker_lexicon(pack)
    return "、".join(markers)


# 语义余弦相似度差值双峰阈值(值域[-1,1])。区别于词袋密度差阈值 1.0（量纲不同分开标定）。
_SEMANTIC_DIFF_THRESHOLD = 0.15


def _pair_scores_lexicon(scenes: list, pa: str, pb: str) -> "list[dict] | None":
    """词袋密度差（原逻辑不变）。lexicon 缺任一 pack → None。"""
    ma, mb = _load_marker_lexicon(pa), _load_marker_lexicon(pb)
    if not ma or not mb:
        return None
    scores = []
    for sc in scenes:
        k = max(1, _cjk_count(sc) / 1000.0)
        da = sum(sc.count(t) for t in ma) / k
        db = sum(sc.count(t) for t in mb) / k
        scores.append({"scene_idx": len(scores), "da": round(da, 3),
                       "db": round(db, 3), "diff": round(da - db, 3)})
    return scores


def _pair_scores_semantic(scenes: list, pa: str, pb: str,
                          compute_embedding, cosine_similarity, cache: dict) -> "list[dict] | None":
    """embedding 余弦相似度替代词袋密度差：scene vs 两 pack 原型描述文本相似度之差。
    原型缺失/维度不一致 → None（调用方对该 pair 整体兜底词袋差·不产生半真半假统计）。"""
    def _proto(pack):
        if pack not in cache:
            proto_text = _pack_prototype_text(pack)
            try:
                cache[pack] = compute_embedding(proto_text) if proto_text else None
            except Exception:
                cache[pack] = None
        return cache[pack]

    emb_a, emb_b = _proto(pa), _proto(pb)
    if not emb_a or not emb_b or len(emb_a) != len(emb_b):
        return None
    scores = []
    try:
        for sc in scenes:
            sc_emb = compute_embedding(sc)
            if not sc_emb or len(sc_emb) != len(emb_a):
                return None
            da = cosine_similarity(sc_emb, emb_a)
            db = cosine_similarity(sc_emb, emb_b)
            scores.append({"scene_idx": len(scores), "da": round(da, 3),
                           "db": round(db, 3), "diff": round(da - db, 3)})
    except Exception:
        return None
    return scores


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "genre_pack_clash", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory",
           "violations": [], "verdict": "PASS", "warning": None}
    if mode == "off":
        return out
    try:
        text = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    text = _strip_changes(text)
    cjk = _cjk_count(text)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out
    packs, override = _read_packs_and_override(project_root)
    if len(packs) < 2:
        out["note"] = "无多 pack 声明·跳过"
        return out
    try:
        import load_clash_registry as _lcr
    except ImportError:
        out["note"] = "registry loader 缺·跳过"
        return out
    hints = _lcr.resolve_clashes(packs, override)
    out["fusion_resolution_hints"] = hints
    if not hints:
        out["note"] = "无命中 registry clash·跳过"
        return out

    scenes = [s for s in re.split(r"\n\s*\n+", text) if _cjk_count(s) >= 80]
    if len(scenes) < 4:
        out["note"] = "场景 <4·二态分布判定不充分·跳过"
        return out

    # 🔴 2026-07-01 真语义后端可用时优先用 embedding 相似度差；否则(含逐 pair 兜底)走词袋密度差
    use_semantic = False
    compute_embedding = cosine_similarity = prefetch_embeddings = None
    if _has_real_embedding_backend():
        try:
            from embedding_store import compute_embedding, cosine_similarity, prefetch_embeddings
            use_semantic = True
        except (ImportError, TypeError):
            use_semantic = False
    out["match_method"] = "semantic" if use_semantic else "lexicon"

    proto_cache: dict = {}
    # 🔴 2026-07-03 Wave-4：本次涉及的全部 pack 原型 + 全部场景一次性批量预热（单次后端批
    # 调用）·随后 _pair_scores_semantic 内逐条 compute_embedding 全部命中缓存(不改变判定逻辑)。
    if use_semantic:
        hint_packs = {p for h in hints for p in h["pair"]}
        ordered_packs = [p for p in packs if p in hint_packs]
        proto_texts = [_pack_prototype_text(p) for p in ordered_packs]
        prefetch_embeddings(scenes + [t for t in proto_texts if t])

    flags = []
    for h in hints:
        pa, pb = h["pair"][0], h["pair"][1]
        scores = None
        pair_method = "lexicon"
        if use_semantic:
            scores = _pair_scores_semantic(scenes, pa, pb, compute_embedding,
                                           cosine_similarity, proto_cache)
            if scores is not None:
                pair_method = "semantic"
        if scores is None:
            scores = _pair_scores_lexicon(scenes, pa, pb)
        if not scores:
            continue
        # 双峰检测：top 1/3 平均 diff 与 bottom 1/3 平均 diff 反向超阈值(词袋 1.0 / 语义 0.15)
        n = len(scores)
        top = sorted(scores, key=lambda s: -s["diff"])[:max(1, n // 3)]
        bot = sorted(scores, key=lambda s: s["diff"])[:max(1, n // 3)]
        top_mu = sum(s["diff"] for s in top) / len(top)
        bot_mu = sum(s["diff"] for s in bot) / len(bot)
        threshold = _SEMANTIC_DIFF_THRESHOLD if pair_method == "semantic" else 1.0
        if top_mu > threshold and bot_mu < -threshold:
            flags.append({
                "pair": h["pair"],
                "top_mu": round(top_mu, 3),
                "bot_mu": round(bot_mu, 3),
                "fusion_hint": h.get("fusion_hint"),
                "match_method": pair_method,
            })
    out["bipolar_flags"] = flags
    if flags:
        msg = f"题材融合两端峰值未化解：{[f['pair'] for f in flags]}"
        if mode == "active":
            for f in flags:
                out["violations"].append({
                    "kind": "genre_pack_clash", "severity": "minor",
                    "code": ISSUE_CODE,
                    "message": f"pair={f['pair']}·top diff={f['top_mu']}·bot diff={f['bot_mu']}",
                    "fusion_hint": f.get("fusion_hint"),
                    "match_method": f.get("match_method", "lexicon"),
                    "_doc": "registry 冲突·advisory·绝不 hard_gate",
                })
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] genre_pack_clash: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="题材包 clash advisory(shadow)")
    ap.add_argument("draft_path")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
