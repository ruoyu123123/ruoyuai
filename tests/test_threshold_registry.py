"""语义阈值全集注册表回归锁（Wave-6 金标准校准地基 · 2026-07-04）。

【为什么有这个文件】core/scripts 里 47 处"待金标准校准"标记是 Wave-6 校准工作的
唯一权威清单来源。core/ml/calibration/threshold_registry.json 把它们逐一 Read
上下文后结构化整理成单一真理源注册表。本文件锁住两件事，防止注册表和代码现实
悄悄漂移：
  ① 注册表里每条 entry 的 file 必须存在，且 constant 字面必须在该文件里真实出现
     （防止注册表烂尾：常量改名/文件挪动后注册表却没跟着更新）。
  ② 全仓"待金标准校准"标记的（文件, 行号）集合，必须与注册表里所有 entry 的
     marker_lines 并集**恰好一致**（防止新增标记忘记登记，也防止注册表引用了
     不存在的行号）——这比任务要求的"行数一致"更严格，能抓到"数量对但指错行"
     这类静默漂移。

北极星边界：本文件只做只读结构盘点校验，不判断任何阈值取值是否"校准正确"
（那是 W6-B/W6-C 的事），不触碰任何 scanner 的创作判断逻辑。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "core" / "scripts"
_REGISTRY_PATH = _REPO / "core" / "ml" / "calibration" / "threshold_registry.json"

_MARKER = "待金标准校准"

_REGEN_HINT = (
    "\n若因新增/修改了 core/scripts/*.py 里的\"待金标准校准\"标记导致本测试失败："
    "请重新盘点差异并手工更新 core/ml/calibration/threshold_registry.json"
    "（Read 新增标记的上下文，補一条/多条 entry，更新 marker_total），"
    "而不是删掉本测试或放宽断言。"
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _load_registry() -> dict:
    assert _REGISTRY_PATH.exists(), f"注册表缺失：{_REGISTRY_PATH}" + _REGEN_HINT
    return json.loads(_read(_REGISTRY_PATH))


def _live_marker_hits() -> dict[str, list[int]]:
    """实时重扫 core/scripts/*.py，返回 {相对路径(core/scripts/xxx.py): [行号,...]}。"""
    hits: dict[str, list[int]] = {}
    for py in sorted(_SCRIPTS.glob("*.py")):
        lines = []
        for i, line in enumerate(_read(py).splitlines(), 1):
            if _MARKER in line:
                lines.append(i)
        if lines:
            rel = f"core/scripts/{py.name}"
            hits[rel] = lines
    return hits


# ============ 基础结构：注册表本身可解析、字段齐全 ============

def test_registry_loads_and_has_required_top_level_fields():
    reg = _load_registry()
    for key in ("marker_total", "entry_count", "entries", "generated_at"):
        assert key in reg, f"注册表缺顶层字段 {key}"
    assert isinstance(reg["entries"], list) and reg["entries"], "entries 不能为空"
    assert reg["entry_count"] == len(reg["entries"]), (
        f"entry_count 字段({reg['entry_count']}) 与实际 entries 长度({len(reg['entries'])}) 不一致"
    )


def test_every_entry_has_required_fields():
    reg = _load_registry()
    required = {"id", "file", "constant", "kind", "current_value", "env_override",
                "relation_family", "consumer", "compares", "pinned_by_tests", "marker_lines"}
    valid_kinds = {"threshold", "weight", "prototype", "scale"}
    valid_families = {"content_echo", "content_dedup", "style_drift", "zero_shot_class", "other"}
    for e in reg["entries"]:
        missing = required - set(e.keys())
        assert not missing, f"entry {e.get('id')} 缺字段 {missing}"
        assert e["kind"] in valid_kinds, f"entry {e['id']} kind={e['kind']!r} 不在 {valid_kinds}"
        assert e["relation_family"] in valid_families, (
            f"entry {e['id']} relation_family={e['relation_family']!r} 不在 {valid_families}"
        )
        assert isinstance(e["marker_lines"], list) and e["marker_lines"], (
            f"entry {e['id']} marker_lines 不能为空"
        )
        assert isinstance(e["pinned_by_tests"], list), f"entry {e['id']} pinned_by_tests 必须是 list"


# ============ ① file 存在 + constant 字面真实出现（防注册表烂尾漂移）============

def test_entry_file_exists_and_constant_appears_in_file():
    reg = _load_registry()
    problems = []
    for e in reg["entries"]:
        fpath = _REPO / e["file"]
        if not fpath.exists():
            problems.append(f"{e['id']}: file 不存在 -> {e['file']}")
            continue
        text = _read(fpath)
        if e["constant"] not in text:
            problems.append(
                f"{e['id']}: constant {e['constant']!r} 在 {e['file']} 中未找到字面匹配"
            )
    assert not problems, "注册表烂尾漂移：\n" + "\n".join(problems) + _REGEN_HINT


def test_entry_marker_lines_are_real_grep_hits():
    """entry.marker_lines 引用的每一行，该行文本必须真的含有"待金标准校准"标记。

    防止 entry 把 marker_lines 指向了一个"看起来像"但实际不含标记的行号
    （比如常量定义行本身没有标记文本，却被错误当成 marker line 登记）。
    """
    reg = _load_registry()
    live = _live_marker_hits()
    problems = []
    for e in reg["entries"]:
        rel = e["file"]
        live_lines = set(live.get(rel, []))
        for ln in e["marker_lines"]:
            if ln not in live_lines:
                problems.append(
                    f"{e['id']}: marker_lines 里的第 {ln} 行在 {rel} 中并不包含 \"{_MARKER}\" 字面标记"
                )
    assert not problems, "\n".join(problems) + _REGEN_HINT


# ============ ② 全仓标记总数 / 精确（文件,行号）集合 与注册表一致 ============

def test_marker_total_matches_live_grep_count():
    """任务要求的核心不变量：全仓"待金标准校准"标记行数 == 注册表 marker_total。"""
    reg = _load_registry()
    live = _live_marker_hits()
    live_total = sum(len(v) for v in live.values())
    assert reg["marker_total"] == live_total, (
        f"注册表 marker_total={reg['marker_total']} != 实时 grep 命中数={live_total}"
        "（新增/删除/挪动了标记但没有同步注册表）" + _REGEN_HINT
    )


def test_registry_marker_line_union_matches_live_hits_exactly():
    """比"行数一致"更严格：所有 entry.marker_lines 的并集（按文件）必须与实时
    grep 命中的 (文件,行号) 集合逐一相等——既不漏登记，也不指错行。
    """
    reg = _load_registry()
    live = _live_marker_hits()

    registry_union: dict[str, set[int]] = {}
    for e in reg["entries"]:
        registry_union.setdefault(e["file"], set()).update(e["marker_lines"])

    live_set = {rel: set(lines) for rel, lines in live.items()}

    missing_files = set(live_set) - set(registry_union)
    extra_files = set(registry_union) - set(live_set)
    assert not missing_files, f"这些文件里的标记一条都没被注册表登记：{sorted(missing_files)}" + _REGEN_HINT
    assert not extra_files, f"注册表引用了实际并无标记的文件：{sorted(extra_files)}" + _REGEN_HINT

    mismatches = []
    for rel in sorted(live_set):
        if registry_union.get(rel) != live_set[rel]:
            mismatches.append(
                f"{rel}: live={sorted(live_set[rel])} registry_union={sorted(registry_union.get(rel, set()))}"
            )
    assert not mismatches, "标记行集合不一致：\n" + "\n".join(mismatches) + _REGEN_HINT


# ============ pinned_by_tests 字段里点名的测试文件必须真实存在 ============

def test_pinned_test_files_exist():
    reg = _load_registry()
    problems = []
    for e in reg["entries"]:
        for tf in e["pinned_by_tests"]:
            if not (_REPO / tf).exists():
                problems.append(f"{e['id']}: pinned_by_tests 里的 {tf} 文件不存在")
    assert not problems, "\n".join(problems)


# ============ 已知盲区结构完整性（信息性，不做内容强断言）============

def test_known_blind_spots_section_present():
    reg = _load_registry()
    assert "known_blind_spots" in reg, "注册表应保留 known_blind_spots 段（记录 grep 范围外的已知盲区）"
    for spot in reg["known_blind_spots"]:
        assert {"what", "why"} <= set(spot.keys())


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
