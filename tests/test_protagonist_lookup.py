"""protagonist_lookup.py 单一真理源回归锁（零依赖 / 零 LLM / 零联网）。

G4② 根因：全仓 20+ 处 `role == "主角"` 精确匹配，人物卡 role 是自由文本
（"主角（视角人物）" / "男主角" / "炎帝幼女·…执念主角之一"）时全部落空 → scanner 空跑。
本文件锁住：
  · role 分类判定（canonical 前缀 / 含「主角」/ 英文别名 / 否定位排除）；
  · 多源兜底反查优先级（人物卡 → 角色弧线 → character_arc_state → 事件簇 → 角色池）；
  · producer 契约体检 has_canonical_protagonist；
  · override 短路。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import protagonist_lookup as pl  # noqa: E402


def _project(tmp: Path, **files: dict) -> Path:
    proj = tmp / "书"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (db / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return proj


# ── role 分类判定 ────────────────────────────────────────────────────────
def test_is_protagonist_role_positive_cases():
    for role in ("主角", "主角·守夜人", "主角（视角人物）", "男主角", "protagonist", "PROTAGONIST"):
        assert pl.is_protagonist_role(role) is True, role


def test_is_protagonist_role_negative_cases():
    for role in ("配角", "反派", "主角的师父", "非主角", "", None, 123, "守夜人"):
        assert pl.is_protagonist_role(role) is False, role


def test_is_canonical_only_when_starts_with_主角():
    assert pl.is_canonical_protagonist_role("主角") is True
    assert pl.is_canonical_protagonist_role("主角·炎帝幼女") is True
    # 含「主角」但不以「主角」开头 → 非 canonical（需 producer 修）
    assert pl.is_canonical_protagonist_role("炎帝幼女·执念主角之一") is False
    assert pl.is_canonical_protagonist_role("男主角") is False
    # 「主角的师父」以「主角」开头但是关系描述 → 非 canonical
    assert pl.is_canonical_protagonist_role("主角的师父") is False


def test_has_canonical_protagonist_contract():
    assert pl.has_canonical_protagonist([{"name": "甲", "role": "主角·守夜人"}]) is True
    assert pl.has_canonical_protagonist([
        {"name": "甲", "role": "炎帝幼女·执念主角之一"}]) is False
    assert pl.has_canonical_protagonist([]) is False


# ── 多源兜底反查 ─────────────────────────────────────────────────────────
def test_resolve_from_cards_freetext_role():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(Path(d), 人物卡={"characters": [
            {"id": "C_002", "name": "女娃", "role": "炎帝幼女·执念主角之一"},
            {"id": "C_003", "name": "瑶姬", "role": "配角"},
        ]})
        detail = pl.resolve_protagonist_detail(proj)
        assert detail["name"] == "女娃"
        assert detail["source"] == "人物卡.json"


def test_resolve_prefers_canonical_over_looser_signal():
    """同时存在含「主角」的自由 role 和 canonical role → canonical 信号更强者优先。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _project(Path(d), 人物卡={"characters": [
            {"id": "C_1", "name": "阿甲", "role": "少年主角登场"},   # 含主角(弱信号 rank1)
            {"id": "C_2", "name": "阿乙", "role": "主角·真正焦点"},   # canonical(rank3)
        ]})
        assert pl.resolve_protagonist(proj) == "阿乙"


def test_resolve_falls_back_to_arc_state():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(
            Path(d),
            人物卡={"characters": [{"id": "C_9", "name": "配角甲", "role": "配角"}]},
            character_arc_state={"characters": {"林尘": {"role": "主角·剑客"}}},
        )
        detail = pl.resolve_protagonist_detail(proj)
        assert detail["name"] == "林尘"
        assert detail["source"] == "character_arc_state.json"


def test_resolve_arc_maps_id_to_name():
    """角色弧线.json 以 id 为 key 时经人物卡 id→name 归一。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _project(
            Path(d),
            人物卡={"characters": [{"id": "C_PROT", "name": "伊莱", "role": "配角"}]},
            角色弧线={"characters": {"C_PROT": {"role": "protagonist"}}},
        )
        detail = pl.resolve_protagonist_detail(proj)
        assert detail["name"] == "伊莱"
        assert detail["source"] == "角色弧线.json"


def test_resolve_falls_back_to_event_cluster():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(
            Path(d),
            人物卡={"characters": [{"id": "C_9", "name": "配角甲", "role": "配角"}]},
            事件簇={"protagonist": "女娃", "clusters": []},
        )
        assert pl.resolve_protagonist(proj) == "女娃"


def test_resolve_falls_back_to_pool():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(
            Path(d),
            人物卡={"characters": [{"id": "C_9", "name": "配角甲", "role": "配角"}]},
            角色池={"core": [{"id": "C_P", "name": "主人公", "role": "主角"}],
                     "emerged": [], "extras": []},
        )
        detail = pl.resolve_protagonist_detail(proj)
        assert detail["name"] == "主人公"
        assert detail["source"] == "角色池.json"


def test_resolve_unresolved_returns_none_and_default():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(Path(d), 人物卡={"characters": [
            {"id": "C_9", "name": "配角甲", "role": "配角"}]})
        assert pl.resolve_protagonist(proj) is None
        assert pl.resolve_protagonist(proj, default="主角") == "主角"


def test_override_short_circuits():
    with tempfile.TemporaryDirectory() as d:
        proj = _project(Path(d), 人物卡={"characters": [
            {"id": "C_1", "name": "女娃", "role": "主角"}]})
        assert pl.resolve_protagonist(proj, override="别人") == "别人"


def test_protagonist_names_multi():
    """双主角/群像：全部主角位名字按信号强度返回。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _project(Path(d), 人物卡={"characters": [
            {"id": "C_1", "name": "女娃", "role": "主角·刚烈幼妹"},
            {"id": "C_2", "name": "瑶姬", "role": "主角·痴念长姐"},
            {"id": "C_3", "name": "炎帝", "role": "父神"},
        ]})
        assert pl.protagonist_names(proj) == ["女娃", "瑶姬"]
