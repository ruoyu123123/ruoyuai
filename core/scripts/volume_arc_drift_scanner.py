#!/usr/bin/env python3
"""volume_arc_drift_scanner.py — 卷级大势收敛漂移哨兵（2026-05-29 北极星 P2 · H1/H3-trend）

北极星原则 3「大势已定」：每卷无论小势（走向卡/涟漪）怎么折腾，最后方向都一致。
代码层的收敛靠三道：
  ① build_manifest.volume_convergence_anchor —— writer 始终看到本卷固定终点（模型自己导航）
  ② cluster_emergence 收敛打分维度 —— 涌现的候选 ME 朝未达成 milestone 倾斜（advisory 排序）
  ③ 本哨兵 —— 卷推进过半但里程碑覆盖明显落后时 advisory 告警「注意朝终点收敛」

**严守 advisory 顾问位**（gate_level=advisory，绝不 hard_gate）：只提醒「可能偏离」，
不阻断、不替模型决定怎么收敛——符合原则 5「不干涉模型判断」。

数据源（全防御性，缺则 SKIP exit 0）：
- 大势卡.json volumes[].key_milestones / ending_state（本卷固定终点）
- 事件簇.json clusters[]（vol 归属 + status + scope_summary）
- 故事块摘要.json clusters[]（已写 cluster 的摘要，判断「实际写了什么」）

【2026-07-01 语义覆盖率补齐 · 2026-07-04 迁移内容嵌入】milestone「是否已触及」默认判据是
关键词 2-gram 字面重叠——同义改写零容错（如milestone写「夺取王座」，已写内容写「登上
帝位」，字面零重叠会误判「未触及」→ 假阳性 VOLUME_ARC_DRIFT）。内容语义后端（bge）
就绪时（content_backend_available()），改用 milestone 文本 vs 已写内容（cluster
scope_summary + 账本章 summary 聚合）embedding 的余弦相似度，≥ 阈值（0.49·
content_embed_separability_20260704 报告 Youden 点·召回优先·env
VOLUME_ARC_SEMANTIC_TOUCH_FLOOR 可覆盖）才算「已触及」；embedding 不可用/维度不
一致/未配后端 → 回退关键词重叠，逐字节零回归。返回值 match_method 字段标注本次
实际用的是 "embedding_cosine" 还是 "bigram_keyword_overlap"。

【2026-07-07 S11 scene 级大势对齐自评汇总】outline-planner 详化/涌现 storyboard 时
每 scene 自答大势对齐三问，自评落 scene 的 alignment 字段（aligned / minor-deviation /
needs-review·全 optional advisory·权威 schema = event_cluster_schema.json
scene_storyboard.items）。本哨兵把当前卷 scene 里 alignment=needs-review 的计数/清单
汇总进报告 alignment_review 段——**只报告不裁决**：不生成 issue、不改退出码
（needs-review 是 planner 的诚实标记不是失败·北极星⑤）。

CLI: python volume_arc_drift_scanner.py <project> [--last-n N]
退出码：0=无漂移/数据不足 · 1=advisory 漂移告警（SC-2）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方回退关键词 2-gram 重叠）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


# milestone embedding 与已写内容聚合 embedding 余弦相似度 ≥ 此值才视为「已触及」。
# 金标准校准 2026-07-04：content_embed_separability_20260704 报告 neg_p95=0.5165/Youden=0.4904
# （advisory 漂移哨兵取 Youden 点召回优先·宁可多提醒不漏报）。env VOLUME_ARC_SEMANTIC_TOUCH_FLOOR 可覆盖。
MILESTONE_SEMANTIC_TOUCH_FLOOR = 0.49


def _semantic_touch_floor() -> float:
    raw = os.environ.get("VOLUME_ARC_SEMANTIC_TOUCH_FLOOR")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return MILESTONE_SEMANTIC_TOUCH_FLOOR


def _embed_or_none(text: str):
    """内容语义 embedding；内容后端不可用/编码异常/空文本 → None（调用方回退字面 bigram）。"""
    if not text or not str(text).strip():
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import compute_content_embedding
        emb = compute_content_embedding(text)
        return emb if emb else None
    except Exception:
        return None


def _cosine_or_none(v1, v2) -> "float | None":
    """维度不一致/任一为 None → None（调用方回退字面 bigram，不误判)。"""
    if v1 is None or v2 is None or len(v1) != len(v2):
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from embedding_store import cosine_similarity
        return cosine_similarity(v1, v2)
    except Exception:
        return None


def _load(p: Path, default=None):
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _kw(text: str) -> set:
    """中文 2-gram + 英数 token 关键词集（与 cluster_emergence._keyword_set 同口径思路）。"""
    if not text:
        return set()
    out = set()
    for tok in re.findall(r"[A-Za-z0-9_]+", text):
        if len(tok) >= 2:
            out.add(tok.lower())
    # 段内 2-gram（按 CJK 连续段成词·不跨标点/英数边界拼假 bigram·如「胜利。反派」不再产「利反」
    # 这种跨句桥接虚词·对齐 cluster_emergence._keyword_set·2026-06-15 审计修）
    for seg in re.findall(r"[一-鿿]+", text):
        for i in range(len(seg) - 1):
            out.add(seg[i:i + 2])
    return out


def _milestone_text(ms) -> str:
    """milestone 条目 → 可读文本（str 直用·dict 取 text/milestone·否则整体 json 化）。"""
    return str(ms if isinstance(ms, str) else (ms.get("text") or ms.get("milestone") or json.dumps(ms, ensure_ascii=False)))


def _cluster_vol(c: dict) -> int | None:
    """cluster 的卷号：vol → volume → parent_me 正则回退（2026-06-15 审计修：真实 event 簇
    cluster 存 "volume"/"parent_me" 无 "vol"·原 vol_clusters 只读 c.get("vol") → 本卷 cluster
    全过滤 → coverage 恒 0 → 每卷过半误报 VOLUME_ARC_DRIFT·verify 隔离实验铁证）。"""
    v = c.get("vol")
    if v is None:
        v = c.get("volume")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    m = re.search(r"V?(\d+)", str(c.get("parent_me") or ""))
    return int(m.group(1)) if m else None


def _collect_alignment_review(vol_clusters: list) -> dict:
    """🔴 2026-07-07 S11：汇总本卷 scene_storyboard 里 alignment=needs-review 的
    scene 计数/清单（planner 大势对齐三问自评·advisory）。**只报告不裁决**：
    返回值只并入报告 alignment_review 段，绝不生成 issue / 不影响退出码
    （needs-review 是诚实标记不是失败·北极星⑤）。字段全 optional——历史产物
    无 alignment 字段时清单为空即合法（fluid）。"""
    needs_review = []
    for c in vol_clusters:
        sb = c.get("scene_storyboard")
        if not isinstance(sb, list):
            continue
        for i, sc in enumerate(sb):
            if isinstance(sc, dict) and sc.get("alignment") == "needs-review":
                needs_review.append({
                    "cluster_id": str(c.get("cluster_id") or ""),
                    "scene_idx": sc.get("scene_idx", sc.get("scene_index", i)),
                    "scene": str(sc.get("scene") or sc.get("summary") or "")[:40],
                    "scene_purpose": str(sc.get("scene_purpose") or "")[:60],
                    "conflict_stage": str(sc.get("conflict_stage") or ""),
                })
    return {
        "needs_review_count": len(needs_review),
        "needs_review_scenes": needs_review[:10],
        "_note": "planner 大势对齐三问自评汇总（S11）·advisory 只报告不裁决·不生成 issue",
    }


def _current_vol(shijianji: dict) -> int | None:
    """取最近在写/已写 cluster 的 vol（已落章优先）。"""
    best = None
    for c in shijianji.get("clusters", []):
        if not isinstance(c, dict):
            continue
        v = _cluster_vol(c)
        cr = c.get("chapter_range")
        landed = isinstance(cr, list) and len(cr) == 2 and isinstance(cr[0], int)
        if v is not None and (landed or c.get("status") in ("done", "in_progress", "进行中", "已完成")):
            if best is None or v > best:
                best = v
    return best


def scan(project_root: Path) -> dict:
    db = project_root / "_数据库"
    dashishi = _load(db / "大势卡.json", {}) or {}
    shijianji = _load(db / "事件簇.json", {"clusters": []}) or {"clusters": []}
    ledger = _load(db / "故事块摘要.json", {"clusters": []}) or {"clusters": []}

    issues = []
    cur_vol = _current_vol(shijianji)
    if cur_vol is None:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": "无在写 cluster，跳过"}

    # 本卷固定终点
    vol_obj = None
    for v in (dashishi.get("volumes") or []):
        if v.get("vol") == cur_vol:
            vol_obj = v
            break
    if not vol_obj:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": f"大势卡无 vol{cur_vol} 终点定义，跳过"}
    milestones = vol_obj.get("key_milestones") or []
    if not isinstance(milestones, list) or not milestones:
        return {"scanner": "volume_arc_drift", "issues": [], "_note": f"vol{cur_vol} 无 key_milestones，跳过"}

    # 本卷已写 cluster（用于「实际写了什么」内容覆盖）
    vol_clusters = [c for c in shijianji.get("clusters", [])
                    if isinstance(c, dict) and (_cluster_vol(c) == cur_vol)]
    written = [c for c in vol_clusters
               if (isinstance(c.get("chapter_range"), list) and len(c.get("chapter_range")) == 2)
               or c.get("status") in ("done", "已完成")]
    # 2026-05-29 复审 W1：progress 用【卷 ME 完成度】而非 cluster 计数——fluid 下 事件簇.json 通常
    # 只含已涌现 cluster（total≈written→progress 恒≈1.0 卷首即假阳性）。ME 池是固定参照系。
    def _me_vol(m):
        # 对齐 cluster_emergence._me_volume：ME 权威卷字段是 "volume"(gen_creative schema)·先
        # volume → vol → id 锚定正则 [Vv](\d+)（避免裸 \d+ 误取 me_id 里非卷号数字·2026-06-15 审计修）
        v = m.get("volume")
        if v is None:
            v = m.get("vol")
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        mm = re.search(r"[Vv](\d+)", str(m.get("id") or ""))
        return int(mm.group(1)) if mm else None
    me_pool = dashishi.get("major_events") or []
    vol_mes = [m for m in me_pool if isinstance(m, dict) and _me_vol(m) == cur_vol]
    done_mes = [m for m in vol_mes if m.get("status") == "completed"]
    total_me = len(vol_mes) or 1
    progress = len(done_mes) / total_me if vol_mes else 0.0
    if not vol_mes:  # 无 ME 池数据 → 退回 cluster 计数（带标记，避免静默假阳性）
        progress = (len(written) / (len(vol_clusters) or 1)) if vol_clusters else 0.0

    # 实际写内容关键词（cluster scope + 账本各 cluster summary 并集）
    written_text = " ".join(str(c.get("scope_summary", "")) for c in written)
    led_by_cid = {}
    for lc in ledger.get("clusters", []):
        if isinstance(lc, dict):
            led_by_cid[str(lc.get("cluster_id"))] = lc
    for c in written:
        lc = led_by_cid.get(str(c.get("cluster_id")))
        if lc:
            chs = lc.get("chapters") or {}
            for rec in (chs.values() if isinstance(chs, dict) else []):
                if isinstance(rec, dict) and rec.get("summary"):
                    written_text += " " + str(rec["summary"])
    written_kw = _kw(written_text)

    # 内容语义后端就绪 → 已写内容整体只编码一次（milestone 逐条复用比余弦相似度）；
    # 否则（默认）保留关键词 2-gram 重叠原样不动
    match_method = "bigram_keyword_overlap"
    written_embedding = None
    semantic_floor = None
    if _content_backend_ready():
        # 🔴 2026-07-03 Wave-4：先收集本次要 embed 的全部文本（已写内容聚合 + 每条
        # milestone）一次性 prefetch 灌缓存——下面 written_embedding + 逐条 milestone
        # 的 _embed_or_none 全部命中缓存（取代已写内容 1 次 + 每个 milestone 各自
        # 触发一次后端 subprocess 调用）。
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from embedding_store import prefetch_content_embeddings
            prefetch_content_embeddings([written_text] + [_milestone_text(ms) for ms in milestones])
        except Exception:
            pass
        written_embedding = _embed_or_none(written_text)
        if written_embedding is not None:
            match_method = "embedding_cosine"
            semantic_floor = _semantic_touch_floor()

    # milestone 覆盖率：内容语义就绪 → 余弦相似度 ≥ floor 视为「已触及」；
    # 否则 → 与已写内容关键词有重叠即视为「已触及」（原逻辑不动）
    touched = 0
    untouched = []
    for ms in milestones:
        ms_text = _milestone_text(ms)
        is_touched = None
        if written_embedding is not None:
            sim = _cosine_or_none(_embed_or_none(ms_text), written_embedding)
            if sim is not None:
                is_touched = sim >= semantic_floor
        if is_touched is None:   # 语义路径不可用（未配后端/该条编码失败/维度不一致）→ 回退字面
            ms_kw = _kw(ms_text)
            is_touched = bool(ms_kw and (ms_kw & written_kw))
        if is_touched:
            touched += 1
        else:
            untouched.append(ms_text[:40])
    coverage = touched / len(milestones) if milestones else 1.0

    # 漂移判据（advisory）：卷推进过半（≥50%）但 milestone 覆盖明显落后于推进（coverage < progress - 0.25）
    if progress >= 0.5 and coverage < (progress - 0.25):
        issues.append({
            "code": "VOLUME_ARC_DRIFT",
            "gate_level": "advisory",
            "severity": "warning",
            "vol": cur_vol,
            "progress": round(progress, 2),
            "milestone_coverage": round(coverage, 2),
            "untouched_milestones": untouched[:5],
            "msg": (f"⚠️ vol{cur_vol} 已推进 {progress:.0%} 但卷里程碑仅覆盖 {coverage:.0%}，"
                    f"可能偏离大势终点。未触及里程碑：{'；'.join(untouched[:3])}。"
                    f"建议后续 cluster 朝本卷 ending_state 收敛（顾问提示，不限定写法）。"),
        })
    return {"scanner": "volume_arc_drift", "vol": cur_vol,
            "progress": round(progress, 2), "milestone_coverage": round(coverage, 2),
            "match_method": match_method,
            # 🔴 2026-07-07 S11：本卷 scene alignment=needs-review 自评汇总（advisory·
            # 只报告不裁决——不进 issues、不影响退出码·北极星⑤）
            "alignment_review": _collect_alignment_review(vol_clusters),
            "issues": issues}


def main():
    ap = argparse.ArgumentParser(description="卷级大势收敛漂移哨兵（advisory）")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=10)  # 兼容编排器统一调用签名，本哨兵按卷算不用
    args = ap.parse_args()
    project_root = Path(args.project).resolve()
    if not (project_root / "_数据库").exists():
        print(f"[SKIP] 无 _数据库: {project_root}", file=sys.stderr)
        sys.exit(0)
    result = scan(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result.get("issues") else 0)


if __name__ == "__main__":
    main()
