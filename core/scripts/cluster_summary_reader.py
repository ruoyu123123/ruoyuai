"""cluster_summary_reader.py — cluster 账本（故事块摘要.json）消费侧 + 字段契约单一来源

v2 cluster 化「摘要驱动」架构（2026-05-29）：
- cluster-save-state 完成时由 `cluster_summary_builder.py` 把每个 cluster 的富摘要写入
  `_数据库/故事块摘要.json` 的 `clusters[]`（每个 cluster 一条记录，内嵌 chapters[ch]）。
- 22 个 cross_cluster_*_aggregate.py 在 CLUSTER_MODE=1 时改用本 reader 从账本取预计算
  字段（不再逐章 glob 重扫正文）；账本缺失字段时回退各自原有的逐章逻辑（向后兼容）。

═══════════════════════════════════════════════════════════════════════
账本字段契约（cluster_summary_builder.py 必须按此产出 · aggregator 按此消费）
═══════════════════════════════════════════════════════════════════════
故事块摘要.json = {
  "schema_version": "v2.cluster",
  "clusters": [ <ClusterRecord>, ... ]   # 按 chapter_range[0] 升序
}

ClusterRecord = {
  # ---- 锚点（必填，db_schema_validate 要求 cluster_id + title）----
  "cluster_id": "cluster_002",
  "title": "...",
  "chapter_range": [lo, hi],            # splitter 切定后回填；fluid 未切时可缺
  "cluster_end_ch": hi,                 # = chapter_range[1]，供 fate/foreshadow/will_learn 取锚点
  "word_count": int,                    # cluster 总 CJK

  # ---- cluster 级 rollup（builder 预算的跨章汇总）----
  "length_stats": {"median": int, "mean": float, "cv": float},
  "throughline_distribution": {"OS": float, "MC": float, "IC": float, "RS": float},
  "faction_snapshot": {faction: {power,stability,wealth}},   # cluster 末态（world_dynamics 用）
  "judge_grade": "A"|"B"|"C"|"D"|null,  # cluster 级综合评级（judge 化后填）
  "fate_overdue_snapshot": [{"event_id","title","overdue_by"}],

  # ---- 增量贡献（本 cluster 对全局表的贡献，供节奏类 aggregator）----
  "foreshadow_planted": ["fid", ...],
  "foreshadow_paid": ["fid", ...],
  "foreshadow_reinforced": {"fid": [ch, ...]},
  "secrets_revealed": ["sid", ...],
  "relationship_changes": [{"from","to","ch"}],
  "spawn_events": [{"char_id","spawned_at_ch","promoted_at_ch"|null}],

  # ---- per-chapter 预算字段（账本主体，key = 物理章号 str）----
  "chapters": {
    "5": <ChapterRecord>, "6": ..., ...
  }
}

ChapterRecord = {            # 每个字段都 optional —— aggregator 用 .get 取，缺则回退
  "cjk_count": int,                                  # meta_quality 字数分布
  "summary": str,                                    # ≥50 字富摘要 meta_quality
  "summary_keywords": [str],                         # 预抽关键词
  "text_keyword_set": [str],                         # 正文 2-4 字关键词指纹（meta_quality/will_learn hint）
  "pattern_metrics": {...20 维...},                  # pattern aggregator 全部维度（最高价值）
  "idiom_hits": {idiom: int},                        # pattern idiom 冷却
  "char_mention_counts": {char: int},                # pattern rotation / offscreen / 出场
  "char_appearance_chs_flag": [char],                # 本章出场角色（= char_mention_counts.keys 命中>0）
  "persona_drift": {char: float},                    # persona_drift（save 时算好的 embedding drift）
  "char_emotion_counts": {char: {emotion: int}},     # emotion_pattern 逐章分类情绪计数
  "emotion_value": int,                              # continuity 用的粗粒度情绪值（WAL summary）
  "relationships": [{"from","to","affinity","trust","fear","respect"}],  # relationship_trend
  "scene_type": str,                                 # scene_pov
  "pov": str,                                        # scene_pov 主视角
  "characters": [str],                               # build_manifest 已在读
  "beat": str, "beat_signal_hit": bool, "beats_addressed": [str],        # structure_compliance
  "user_choice": str, "choice_leads_to": str, "turning_point": str,      # structure_compliance
  "throughline_progress": {"OS":bool,"MC":bool,"IC":bool,"RS":bool},     # throughline_balance
  "time_anchor": str, "time_transition_present": bool,                   # timeline
  "item_changes": [{"id","holder"}],                 # timeline
  "locations_mentioned": [str],                      # timeline 地点
  "ending_type": str, "ending_line": str, "has_pre_opening": bool,       # continuity / ending_diversity
  "time_advance": {"period": str, "key_events": [str]},                  # continuity
  "plot_nodes": [str],                               # continuity 过渡说明
  "cliffhanger_resonance_next": float,               # 与下一章 head 重叠分（continuity cliffhanger）
  "aspects_addressed": [aspect_id], "aspect_text_hit": [aspect_id],      # data_consumption
  "clocks_addressed": [clock_id],                    # data_consumption
  "fate_dice_consumed": [{"event_id","evidence_hit_ratio"}],             # data_consumption
  "coping_hit": bool,                                # character_dynamics coping 关键词
  "stress_total": int, "stress_trigger": str,        # character_dynamics
  "mental_break_card": str|null,                     # character_dynamics
  "moves_used": [{"character","move_id","instances"}],                   # character_dynamics
  "position_effect_evals": [{"position","effect"}],  # character_dynamics
  "arc_stage": {char: stage_str},                    # arc_progression
  "hook_score": float, "golden_scores": {"kindling","hook","turn"},      # engagement_metrics
  "judge_score": float, "waivers": [{"code","reason"}],                  # judge_quality
  "prev_findings_consumed": bool,                    # judge_quality
  "offscreen": {"expected": [...], "executed": [...], "backlog_count": int},  # offscreen
  "outcome": str,                                    # world_dynamics win/setback
  "beats_addressed": [str],
}
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

__all__ = [
    "is_cluster_mode",
    "current_cluster_id",
    "load_summary",
    "get_clusters",
    "get_chapter_records",
    "ledger_has_field",
    "SUMMARY_FILENAME",
]

SUMMARY_FILENAME = "故事块摘要.json"


def is_cluster_mode() -> bool:
    """调度器 run_cross_cluster_aggregates 在 cluster 模式给子进程透传 CLUSTER_MODE=1。"""
    return os.environ.get("CLUSTER_MODE") == "1"


def current_cluster_id() -> str | None:
    return os.environ.get("CLUSTER_ID")


def _db_dir(project_root) -> Path:
    root = Path(project_root)
    if root.name == "_数据库":
        return root
    return root / "_数据库"


def load_summary(project_root) -> dict:
    """读 故事块摘要.json 全量。不存在/损坏返回空账本骨架。"""
    p = _db_dir(project_root) / SUMMARY_FILENAME
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"schema_version": "v2.cluster", "clusters": []}


def _cluster_sort_key(rec: dict):
    cr = rec.get("chapter_range")
    if isinstance(cr, list) and cr and isinstance(cr[0], int):
        return cr[0]
    end = rec.get("cluster_end_ch")
    if isinstance(end, int):
        return end
    # fallback：cluster_id 抽数字
    m = re.search(r"(\d+)", str(rec.get("cluster_id", "")))
    return int(m.group(1)) * 1000 if m else 1_000_000


def get_clusters(project_root, last_n: int | None = None) -> list[dict]:
    """返回 cluster 记录列表（按 chapter_range[0] 升序）。

    只返回「真实落账」的 cluster（含 chapters 或 chapter_range），过滤 candidate 雏形。
    last_n 截最后 N 个 cluster。
    """
    summary = load_summary(project_root)
    clusters = [
        c for c in summary.get("clusters", [])
        if isinstance(c, dict) and c.get("cluster_id")
        and (c.get("chapters") or c.get("chapter_range"))
        and c.get("status") != "candidate"
    ]
    clusters.sort(key=_cluster_sort_key)
    if last_n is not None and last_n > 0:
        clusters = clusters[-last_n:]
    return clusters


def get_chapter_records(project_root, last_n_clusters: int | None = None) -> list[tuple[int, dict]]:
    """把账本里所有 cluster 的 chapters[ch] 拍平成 [(ch:int, ChapterRecord), ...]，按 ch 升序。

    aggregator 在 cluster 模式可直接用它替代逐章 glob：拿到 (ch, record) 后从 record
    里取自己需要的预算字段（.get），缺字段说明 builder 未填 → 调用方应回退逐章逻辑。
    """
    out: list[tuple[int, dict]] = []
    for c in get_clusters(project_root, last_n=last_n_clusters):
        chapters = c.get("chapters") or {}
        if not isinstance(chapters, dict):
            continue
        for ch_key, rec in chapters.items():
            try:
                ch = int(ch_key)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                out.append((ch, rec))
    out.sort(key=lambda x: x[0])
    return out


def ledger_has_field(project_root, field: str, min_chapters: int = 1) -> bool:
    """检查账本里至少 min_chapters 个章记录含某字段 —— aggregator 用它决定走账本还是回退。"""
    recs = get_chapter_records(project_root)
    hit = sum(1 for _ch, r in recs if field in r and r.get(field) not in (None, [], {}))
    return hit >= min_chapters


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        root = sys.argv[1]
        cs = get_clusters(root)
        print(f"账本 cluster 数: {len(cs)}")
        for c in cs:
            print(f"  {c.get('cluster_id')} range={c.get('chapter_range')} chapters={len(c.get('chapters') or {})}")
        recs = get_chapter_records(root)
        print(f"拍平章记录数: {len(recs)}")
    else:
        # 自测：空账本不崩
        import tempfile
        d = Path(tempfile.mkdtemp()) / "_数据库"
        d.mkdir(parents=True)
        assert get_clusters(d.parent) == []
        assert get_chapter_records(d.parent) == []
        assert is_cluster_mode() in (True, False)
        print("[OK] cluster_summary_reader self-test passed")
