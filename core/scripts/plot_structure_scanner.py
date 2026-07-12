"""扫描当前故事块的情节结构并输出可豁免的顾问建议。

输入必须明确给出 ``cluster_id`` 和完整故事块草稿。扫描器不读取物理章节，
也不会在目标故事块缺少结构数据时借用其他故事块的数据。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cluster_lookup  # noqa: E402
from beat_evidence import addressed_beats  # noqa: E402
from narrative_scanner import detect_narrative_mode  # noqa: E402


TRY_PATTERN = re.compile(
    r"(?:尝试|试着|想要|打算|准备|要去|寻找|推开|拉住|抓住|握住)"
)
FAIL_PATTERN = re.compile(
    r"(?:失败|没能|未能|没找到|无果|落空|挡住|拒绝|不行|失手|没成|未成|阻拦)"
)
SUCCESS_PATTERN = re.compile(
    r"(?:成功|做到|拿到|找到|打开|解开|解决|完成|搞定)"
)
REVERSAL_PATTERN = re.compile(
    r"(?:反转|颠覆|没想到|出乎意料|竟然|原来|真相|背叛|揭开|"
    r"发现[^，。\n]{0,12}(?:其实|根本|真正))"
)
TEN_REALIZATION_PATTERN = re.compile(
    r"才(?:意识到|想起来|明白|知道|发现|察觉)|原来|竟然?|没想到|这才|"
    r"直到这时|直到[^，。\n]{1,8}才|忽地|猛地"
)
TEN_TIME_PIVOT_PATTERN = re.compile(
    r"突然|忽然|这时|正当|转眼|一刹那|刹那间|蓦地|蓦然|霎时"
)


ALL_CHECKS = {
    "beat": "故事块 beat 兑现",
    "tryfail": "Try-Fail 循环",
    "midpoint": "中点反转",
    "knowledge": "信息差",
    "arc": "角色弧线",
    "subplot": "副线活跃度",
    "kishotenketsu": "起承转结",
}


def load_json(path: Path, default=None):
    """读取 UTF-8 JSON；缺失或损坏时返回调用方给定的默认值。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _cluster_number(cluster_id: str | None) -> int | None:
    return cluster_lookup.cluster_num(cluster_id) if cluster_id else None


def _cluster_beats(project_root: Path, cluster_id: str) -> list[dict]:
    beat_map = load_json(project_root / "_数据库" / "beat_map.json", {}) or {}
    cluster_beats = beat_map.get("cluster_beats") or {}
    beats = cluster_beats.get(cluster_id) if isinstance(cluster_beats, dict) else None
    return [beat for beat in beats or [] if isinstance(beat, dict)]


def scan_beat(project_root: Path, cluster_id: str, body: str) -> dict:
    """读取声明节拍并在完整 cluster 正文中提取兑现证据。"""
    beats = _cluster_beats(project_root, cluster_id)
    names = [str(beat.get("beat") or "").strip() for beat in beats]
    names = [name for name in names if name]
    addressed = addressed_beats(names, body)
    return {
        "cluster_id": cluster_id,
        "beats_declared_count": len(beats),
        "beats_declared": names,
        "beats_addressed": addressed,
        "beat_signal_hit": bool(addressed),
        "warning": (
            None
            if beats
            else f"⚠️ {cluster_id} 未声明 beat 序列（beat_map.cluster_beats 缺失）"
        ),
    }


def _protagonist_name(project_root: Path) -> str | None:
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}) or {}
    characters = cards.get("characters") or []
    for character in characters:
        if not isinstance(character, dict):
            continue
        if character.get("role") in {"主角", "protagonist"} or character.get(
            "is_protagonist"
        ):
            return character.get("name") or character.get("id")
    return None


def scan_try_fail(project_root: Path, cluster_id: str, body: str) -> dict:
    """统计完整故事块中的尝试、失败与成功节拍。"""
    protagonist = _protagonist_name(project_root)
    attempts = len(TRY_PATTERN.findall(body))
    failures = len(FAIL_PATTERN.findall(body))
    successes = len(SUCCESS_PATTERN.findall(body))
    warning = None
    if attempts and successes and not failures:
        warning = (
            f"⚠️ {cluster_id} 有 {attempts} 个尝试和 {successes} 个成功信号，"
            "但没有失败/受阻节拍；可检查阻力是否被写透"
        )
    return {
        "cluster_id": cluster_id,
        "protagonist": protagonist,
        "tries_estimate": attempts,
        "fails": failures,
        "successes": successes,
        "warning": warning,
    }


def scan_midpoint(project_root: Path, cluster_id: str, body: str) -> dict:
    """仅当目标故事块声明 Midpoint beat 时检查反转信号。"""
    beats = _cluster_beats(project_root, cluster_id)
    midpoint_declared = any(
        "midpoint" in str(beat.get("beat") or "").lower()
        or "中点" in str(beat.get("beat") or "")
        for beat in beats
    )
    if not midpoint_declared:
        return {
            "cluster_id": cluster_id,
            "status": "n/a",
            "reason": "本故事块未声明 Midpoint beat",
            "warning": None,
        }
    reversal_count = len(REVERSAL_PATTERN.findall(body))
    return {
        "cluster_id": cluster_id,
        "status": "active",
        "midpoint_declared": True,
        "reversal_keyword_count": reversal_count,
        "warning": (
            None
            if reversal_count >= 2
            else f"⚠️ {cluster_id} 声明了 Midpoint，但反转信号仅 {reversal_count} 个"
        ),
    }


def scan_knowledge_graph(project_root: Path, cluster_id: str) -> dict:
    """汇总目标故事块时点仍处于隐藏状态的事实与秘密。"""
    knowledge = load_json(project_root / "_数据库" / "knowledge_graph.json", {}) or {}
    facts = [fact for fact in knowledge.get("facts") or [] if isinstance(fact, dict)]
    foreshadow = load_json(project_root / "_数据库" / "伏笔表.json", {}) or {}
    secrets = [secret for secret in foreshadow.get("secrets") or [] if isinstance(secret, dict)]
    current_number = _cluster_number(cluster_id)
    secret_status = []
    for secret in secrets:
        status = str(secret.get("status") or "").strip().lower()
        reveal_cluster = cluster_lookup.normalize_cluster_id(secret.get("reveal_at_cluster"))
        reveal_number = _cluster_number(reveal_cluster)
        if status in {"revealed", "leaked", "已揭示", "已泄露"}:
            hidden = False
        elif status in {"hidden", "隐藏"}:
            hidden = True
        else:
            hidden = not (
                current_number is not None
                and reveal_number is not None
                and reveal_number <= current_number
            )
        secret_status.append(
            {
                "id": secret.get("id"),
                "content": secret.get("secret") or secret.get("title"),
                "reveal_at_cluster": reveal_cluster,
                "epistemic_class": secret.get("epistemic_class"),
                "status_now": "hidden" if hidden else "revealed",
            }
        )
    hidden_count = sum(item["status_now"] == "hidden" for item in secret_status)
    return {
        "cluster_id": cluster_id,
        "registered_facts": len(facts),
        "derived_from_secrets": len(secret_status),
        "asymmetry_index": hidden_count,
        "secrets_status": secret_status[:5],
        "warning": (
            "⚠️ 当前没有隐藏信息差；可检查戏剧反讽或认知差是否需要补强"
            if secret_status and hidden_count == 0
            else None
        ),
    }


def scan_character_arc(project_root: Path, cluster_id: str) -> dict:
    """读取主角在目标故事块的弧线阶段。"""
    protagonist = _protagonist_name(project_root)
    if not protagonist:
        return {"cluster_id": cluster_id, "error": "no protagonist", "warning": None}
    arc_state = load_json(
        project_root / "_数据库" / "character_arc_state.json", {}
    ) or {}
    characters = arc_state.get("characters") or {}
    arc = characters.get(protagonist) if isinstance(characters, dict) else None
    if not isinstance(arc, dict):
        return {
            "cluster_id": cluster_id,
            "protagonist": protagonist,
            "arc_declared": False,
            "warning": f"⚠️ 主角「{protagonist}」尚未声明角色弧线",
        }
    stages = arc.get("stages_by_cluster") or {}
    current_stage = stages.get(cluster_id) if isinstance(stages, dict) else None
    return {
        "cluster_id": cluster_id,
        "protagonist": protagonist,
        "arc_declared": True,
        "lie": arc.get("lie", ""),
        "want": arc.get("want", ""),
        "need": arc.get("need", ""),
        "truth": arc.get("truth", ""),
        "current_stage": current_stage,
        "warning": (
            None
            if current_stage
            else f"⚠️ {cluster_id} 未在 stages_by_cluster 标注主角弧线阶段"
        ),
    }


def scan_subplot_threads(project_root: Path, cluster_id: str) -> dict:
    """找出超过五个故事块没有推进的活跃副线。"""
    payload = load_json(project_root / "_数据库" / "subplot_threads.json", {}) or {}
    threads = [thread for thread in payload.get("threads") or [] if isinstance(thread, dict)]
    current_number = _cluster_number(cluster_id)
    sleeping = []
    if current_number is not None:
        for thread in threads:
            status = str(thread.get("current_status") or "").strip().lower()
            if status in {"resolved", "closed", "已完成", "已关闭"}:
                continue
            last_cluster = cluster_lookup.normalize_cluster_id(
                thread.get("last_advanced_cluster")
            )
            last_number = _cluster_number(last_cluster)
            if last_number is None or current_number - last_number <= 5:
                continue
            sleeping.append(
                {
                    "thread": thread.get("name") or thread.get("id"),
                    "last_advanced_cluster": last_cluster,
                    "clusters_silent": current_number - last_number,
                    "current_status": thread.get("current_status"),
                }
            )
    return {
        "cluster_id": cluster_id,
        "total_threads": len(threads),
        "sleeping_threads_count": len(sleeping),
        "sleeping_threads": sleeping,
        "warning": (
            f"⚠️ {len(sleeping)} 条副线超过五个故事块未推进"
            if sleeping
            else None
        ),
    }


def scan_kishotenketsu(
    project_root: Path, cluster_id: str, body: str
) -> dict:
    """对单人低对话故事块检查后半段是否出现“转”。"""
    paragraphs = [paragraph for paragraph in re.split(r"\n\s*\n", body) if paragraph.strip()]
    if len(paragraphs) < 4:
        return {
            "cluster_id": cluster_id,
            "status": "n/a",
            "reason": f"段落数 {len(paragraphs)} < 4，无法分四段",
            "warning": None,
        }
    narrative_mode = detect_narrative_mode(
        project_root, cluster_id, body, paragraphs
    )
    if narrative_mode != "solo_atmospheric":
        return {
            "cluster_id": cluster_id,
            "status": "n/a",
            "narrative_mode": narrative_mode,
            "reason": "当前故事块不是单人低对话氛围模式",
            "warning": None,
        }
    total = len(paragraphs)
    q1_end = max(1, total // 4)
    q2_end = max(q1_end + 1, total // 2)
    q3_end = max(q2_end + 1, (3 * total) // 4)
    back_half = "\n".join(paragraphs[q2_end:])
    realization_hits = TEN_REALIZATION_PATTERN.findall(back_half)
    time_hits = TEN_TIME_PIVOT_PATTERN.findall(back_half)
    pivot_count = len(realization_hits) + len(time_hits)
    return {
        "cluster_id": cluster_id,
        "status": "active",
        "narrative_mode": narrative_mode,
        "paragraphs_total": total,
        "quarters": {
            "ki": [0, q1_end],
            "sho": [q1_end, q2_end],
            "ten": [q2_end, q3_end],
            "ketsu": [q3_end, total],
        },
        "ten_pivots": {
            "realization_hits": realization_hits[:5],
            "time_pivot_hits": time_hits[:5],
            "total": pivot_count,
        },
        "warning": (
            None
            if pivot_count
            else "⚠️ 单人低对话故事块后半部缺少认知或时间转折信号"
        ),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("cluster_id")
    parser.add_argument("--draft", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true")
    group.add_argument("--checks")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    project_root = Path(args.project)
    cluster_id = cluster_lookup.normalize_cluster_id(args.cluster_id)
    draft_path = Path(args.draft)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    if not cluster_id:
        print(f"[FATAL] 非法 cluster_id: {args.cluster_id}", file=sys.stderr)
        raise SystemExit(2)
    try:
        body = draft_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[FATAL] 无法读取故事块草稿: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    checks = (
        [item.strip() for item in args.checks.split(",") if item.strip()]
        if args.checks
        else list(ALL_CHECKS)
    )
    unknown = sorted(set(checks) - set(ALL_CHECKS))
    if unknown:
        print(f"[FATAL] 未知检查项: {', '.join(unknown)}", file=sys.stderr)
        raise SystemExit(2)

    report = {
        "schema_version": "2.0",
        "scanner": "plot_structure_scanner",
        "cluster_id": cluster_id,
        "draft": str(draft_path),
        "checks_run": checks,
    }
    scanners = {
        "beat": lambda: scan_beat(project_root, cluster_id, body),
        "tryfail": lambda: scan_try_fail(project_root, cluster_id, body),
        "midpoint": lambda: scan_midpoint(project_root, cluster_id, body),
        "knowledge": lambda: scan_knowledge_graph(project_root, cluster_id),
        "arc": lambda: scan_character_arc(project_root, cluster_id),
        "subplot": lambda: scan_subplot_threads(project_root, cluster_id),
        "kishotenketsu": lambda: scan_kishotenketsu(
            project_root, cluster_id, body
        ),
    }
    warnings = []
    for check in checks:
        result = scanners[check]()
        if result.get("warning"):
            result["gate_level"] = "advisory"
            warnings.append(f"  [{check}] {result['warning']}")
        report[check] = result

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if warnings:
        print("\n=== 汇总建议 ===", file=sys.stderr)
        for warning in warnings:
            print(warning, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
