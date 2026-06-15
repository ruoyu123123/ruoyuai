#!/usr/bin/env python3
"""平台冷启动 SOP 静态模板 schema 测试（一人公司 2.6·P2·零依赖读 .md 断言结构）。

确定性文档·确认 3 平台骨架 + 阈值占位 + 北极星边界免责（防未来误删/漂移）。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SOP = (_ROOT / "core" / "claude-home" / "templates" / "平台冷启动SOP.md")


def test_sop_template_exists():
    assert _SOP.exists(), "平台冷启动SOP.md 模板必须存在"


def test_sop_covers_three_platforms():
    """3 套平台冷启动 SOP（起点/番茄/RoyalRoad）。"""
    md = _SOP.read_text(encoding="utf-8")
    assert "起点中文网" in md
    assert "番茄小说" in md
    assert "RoyalRoad" in md


def test_sop_thresholds_are_placeholders():
    """阈值占位「以平台公告为准」（易变商业规则不写死）。"""
    md = _SOP.read_text(encoding="utf-8")
    assert "以平台公告为准" in md
    assert md.count("以平台公告为准") >= 5         # 多处占位（不写死易变阈值·占位带前缀）


def test_sop_northstar_boundary():
    """北极星边界：SOP 是节奏建议·绝不变 hard_gate·写作主轨不受运营绑架·非批量起号。"""
    md = _SOP.read_text(encoding="utf-8")
    assert "绝不变" in md and "硬约束" in md       # 不变 hard_gate
    assert "单本精写" in md and "非批量起号" in md   # 合规护城河
    assert "不替你上传" in md                      # 机器产草稿人做发布决策


if __name__ == "__main__":
    import sys
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                print(f"  [FAIL] {nm}: {e}")
    sys.exit(1 if fails else 0)
