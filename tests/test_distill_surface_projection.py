"""distill_surface_runner 阶段0 聚合通路修复测试（2026-06-13）。

背景（已核实 bug）：程序驱动管线产的 蒸馏进度/ch{N}.json 只有扁平 qualitative_dims，
而 consolidate_author_profile.aggregate_narrative 读的是嵌套 cross_chapter/
narrative_craft/narrative_fingerprint → 读空 → 三段全空。

本测试钉死 _project_consumer_dims 确定性投影：把 judge 扁平 dim 归位成消费者期望
的嵌套结构，键名与 judge schema 完全一致（只是层级不同）。纯函数·零 LLM。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import distill_surface_runner as dsr  # noqa: E402


def test_projection_nests_flat_dims():
    """扁平 dim 归位到 cross_chapter/narrative_craft/narrative_fingerprint。"""
    flat = {
        "dim1_句式节奏": "短句连发",      # 非消费维度·不投影
        "dim16_开头类型": "纯对话",
        "dim18_章末类型": "信息炸弹",
        "dim33_情绪节拍图": "低开高走",
        "dim34_叙事距离变化": "前远后近",
        "dim30_留白潜台词": ["段4 愧疚"],
        "dim42_叙事技巧指纹": [{"技巧": "延迟交付"}],
        "dim43_角色行为循环": [{"行为": "摸下巴"}],
        "dim46_场景结构质量": "A完整",
        "dim47_人物丰满度": "A丰满",
    }
    out = dsr._project_consumer_dims(flat)
    assert out["cross_chapter"] == {"dim16_开头类型": "纯对话", "dim18_章末类型": "信息炸弹"}
    assert set(out["narrative_craft"]) == {"dim33_情绪节拍图", "dim34_叙事距离变化", "dim30_留白潜台词"}
    assert set(out["narrative_fingerprint"]) == {
        "dim42_叙事技巧指纹", "dim43_角色行为循环", "dim46_场景结构质量", "dim47_人物丰满度"}
    # 非消费维度不混入
    assert "dim1_句式节奏" not in str(out)


def test_projection_skips_empty_values():
    """空/None/空列表的 dim 不投影（避免落空键污染聚合）。"""
    flat = {"dim16_开头类型": "", "dim18_章末类型": None, "dim34_叙事距离变化": "前远后近",
            "dim43_角色行为循环": []}
    out = dsr._project_consumer_dims(flat)
    assert "cross_chapter" not in out          # 两个键都空 → 整段不出现
    assert out["narrative_craft"] == {"dim34_叙事距离变化": "前远后近"}
    assert "narrative_fingerprint" not in out  # dim43 空列表 → 不出现


def test_projection_non_dict_safe():
    """非 dict 输入安全返回空（弱模型产 None/字符串不崩）。"""
    assert dsr._project_consumer_dims(None) == {}
    assert dsr._project_consumer_dims("乱") == {}
    assert dsr._project_consumer_dims({}) == {}


def test_projection_keys_match_consolidate_reader():
    """投影产出的嵌套键名与 consolidate.aggregate_narrative 读取口径一致（防漂移）。"""
    import consolidate_author_profile  # noqa: F401  确保可 import
    flat = {"dim16_开头类型": "纯对话", "dim34_叙事距离变化": "贴近",
            "dim47_人物丰满度": "A", "dim42_叙事技巧指纹": [{"技巧": "对比锚点"}],
            "dim46_场景结构质量": "B", "dim30_留白潜台词": ["x"],
            "dim33_情绪节拍图": "低开"}
    out = dsr._project_consumer_dims(flat)
    # consolidate 读 cc.dim16 / nc.dim34/33/30 / nf.dim47/43/42/46 —— 嵌套层级对齐
    assert out["cross_chapter"].get("dim16_开头类型")
    assert out["narrative_craft"].get("dim34_叙事距离变化")
    assert out["narrative_craft"].get("dim33_情绪节拍图")
    assert out["narrative_craft"].get("dim30_留白潜台词")
    assert out["narrative_fingerprint"].get("dim47_人物丰满度")
    assert out["narrative_fingerprint"].get("dim42_叙事技巧指纹")
    assert out["narrative_fingerprint"].get("dim46_场景结构质量")


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[distill_surface_projection] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
