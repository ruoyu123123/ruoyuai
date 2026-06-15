#!/usr/bin/env python3
"""cross_cluster_continuity_aggregate 主角名动态化测试（2026-06-15 去硬编码"陆衍"）。

原 extract_keywords 的 stop 表硬编码主角名"陆衍"——只对某本旧书有效，对其他书
（如凿窍纪主角"重黎"）主角名不被过滤 → cliffhanger 关键词重叠虚高 → 回应度失真。
修：照 relationship_evaluator.get_protagonist 范式从 人物卡.json 动态读主角名
（北极星⑥清硬编码 + ①不绑特定书）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import cross_cluster_continuity_aggregate as cc  # noqa: E402


def test_extract_keywords_no_hardcoded_protagonist():
    """🔴 去硬编码"陆衍"：陆衍不再被无条件过滤(非主角名时是普通词)。"""
    kw = cc.extract_keywords("陆衍 重黎 建木 绝顶")
    assert "陆衍" in kw, "陆衍 不该再被硬编码过滤(已去硬编码 stop)"
    # 通用 stop 仍生效
    assert "他的" not in cc.extract_keywords("他的 建木"), "通用 stop 词应保留过滤"


def test_extract_keywords_dynamic_protagonist_filter():
    """protagonist 参数动态过滤主角名(主角名几乎每段出现·不滤则重叠虚高)。"""
    kw = cc.extract_keywords("重黎 建木 绝顶 星河", protagonist="重黎")
    assert "重黎" not in kw, "主角名(重黎)应被动态过滤"
    assert "建木" in kw and "绝顶" in kw, "非主角词应保留"
    # 不传 protagonist → 主角名保留(向后兼容)
    assert "重黎" in cc.extract_keywords("重黎 建木"), "不传 protagonist 时主角名不滤"


def test_get_protagonist_reads_renwu_card():
    """get_protagonist 从 人物卡.json 读 role==主角·取代硬编码·缺文件不崩·两形态兼容。"""
    # 形态一：{"characters":[...]}
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "人物卡.json").write_text(json.dumps(
            {"characters": [{"name": "重黎", "role": "主角"},
                            {"name": "颛顼", "role": "反派"}]},
            ensure_ascii=False), encoding="utf-8")
        assert cc.get_protagonist(proj) == "重黎"
    # 形态二：{name:{...}}
    with tempfile.TemporaryDirectory() as d2:
        proj2 = Path(d2)
        db2 = proj2 / "_数据库"
        db2.mkdir(parents=True)
        (db2 / "人物卡.json").write_text(json.dumps(
            {"重黎": {"role": "protagonist"}, "颛顼": {"role": "antagonist"}},
            ensure_ascii=False), encoding="utf-8")
        assert cc.get_protagonist(proj2) == "重黎"
    # 无人物卡 → None(不崩)
    with tempfile.TemporaryDirectory() as d3:
        assert cc.get_protagonist(Path(d3)) is None


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
