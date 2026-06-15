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


def test_build_aliases_no_hardcoded_items():
    """🔴 _build_aliases 去硬编码物件名(2026-06-15 删 for kw in ["废票","铁皮盒",...] 列表)：
    对任意书物件名靠通用提取(括号内容/去括号核心/首末字)·不特殊对待特定旧书物件。"""
    # 通用提取：括号内容 + 去括号核心(任意书都工作)
    al = cc._build_aliases("女娲补天遗石（混沌余烬）")
    assert "混沌余烬" in al, "应提取括号内容"
    assert "女娲补天遗石" in al, "应提取去括号核心"
    # 旧书物件"废票（彩票）"靠去括号核心通用提取得"废票"·非硬编码列表
    al2 = cc._build_aliases("废票（彩票）")
    assert "废票" in al2 and "彩票" in al2, "废票应靠去括号核心通用提取(非硬编码)"
    # 凿窍纪物件(非旧书硬编码列表)通用工作
    assert "建木枝" in cc._build_aliases("建木枝")


def test_object_continuity_severity_by_gap_not_hardcoded():
    """🔴 物件持续性 severity 按 gap 分级(2026-06-15 取代硬编码物件名"废票/铁皮盒/..."决定
    warning)：gap≥5 = warning·否则 advisory·与具体物件名无关(任意书一致)。"""
    # scan_object_continuity 产 gap → main 据 gap 分级(逻辑：gap>=5 warning)
    # 验逻辑契约：同一 gap 阈值对任意物件名一致(不再因物件名是"废票"特殊升级)
    f = cc.scan_object_continuity(
        {1: {"factual": {"item_transfers": [{"item": "凿窍刀"}]}},
         2: {"factual": {}}, 3: {"factual": {}}, 4: {"factual": {}},
         5: {"factual": {}}, 6: {"factual": {}}, 7: {"factual": {}}},
        {1: "凿窍刀出现", 2: "", 3: "", 4: "", 5: "", 6: "", 7: ""}, 7)
    assert f, "凿窍刀 ch1 出现后长期未提及应被检出(非硬编码物件名也检出)"
    assert f[0]["item"] == "凿窍刀" and f[0]["gap"] >= 5, f"gap 应≥5: {f}"


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
