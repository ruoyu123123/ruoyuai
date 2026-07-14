"""build_manifest 对话双配比 hard_constraints 注入回归锁（H2 · advisory · 作者档第一权威）。

作者档 quantitative.dialogue_only_ratio 存在时，_build_hard_constraints 必须注入
「对话双配比」advisory（外部对白是主体 · 禁止用引号心声顶替外部对白凑总占比 · 分开达标），
堵「引号心声顶替外部对话凑总占比」缺陷路径（长恨 cluster_001 实证：外部对白 4.77% vs
作者 20.45%，靠心声 11.74% 顶地板）。

断言锚定语义关键词而非全句，防措辞微调误红。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import scaffold_subsystems as scaf  # noqa: E402

_FORESHADOW_EMPTY = {"tier1_due_count": 0, "must_reveal_this_ch": 0}


def _project_with_style(tmp: Path, quantitative: dict) -> Path:
    proj = tmp / "dual_ratio_book"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert scaf.cmd_emit([str(proj)]) == 0, "scaffold emit 应成功"
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"quantitative": quantitative}, ensure_ascii=False),
        encoding="utf-8")
    return proj


def _hard_constraints(proj: Path) -> list[str]:
    s = bm.DatabaseScanner(proj, 1)
    return bm._build_hard_constraints(s, dict(_FORESHADOW_EMPTY))


def test_dual_ratio_injected_with_inner_monologue_aux():
    """dialogue_only_ratio + inner_monologue_ratio 齐备 → 注入双配比措辞 + 心声辅助段。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _project_with_style(Path(tmp), {
            "dialogue_only_ratio": {"mean": 0.20},
            "inner_monologue_ratio": {"mean": 0.10},
        })
        joined = "\n".join(_hard_constraints(proj))
        # 语义关键锚（非全句）：双配比标识 / 外部对白主体 / 禁心声顶替 / 分开达标 / advisory 顾问定级
        for anchor in ("对话双配比", "外部角色对白", "引号心声", "顶替", "分开达标", "advisory"):
            assert anchor in joined, f"双配比 hard_constraints 缺关键措辞锚: {anchor}"
        assert "只作辅助" in joined, "inner_monologue_ratio 存在时应注入心声辅助定位措辞"


def test_dual_ratio_injected_without_inner_monologue():
    """只有 dialogue_only_ratio（无 inner_monologue_ratio）→ 主体措辞仍注入，无心声辅助段。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _project_with_style(Path(tmp), {
            "dialogue_only_ratio": {"mean": 0.20},
        })
        joined = "\n".join(_hard_constraints(proj))
        for anchor in ("对话双配比", "外部角色对白", "顶替"):
            assert anchor in joined, f"双配比 hard_constraints 缺关键措辞锚: {anchor}"
        assert "只作辅助" not in joined


def test_dual_ratio_absent_without_dialogue_only_ratio():
    """作者档没有 dialogue_only_ratio → 不注入双配比（不机械兜底·北极星⑤作者档第一权威）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _project_with_style(Path(tmp), {
            "dialogue_ratio": {"mean": 0.30},
        })
        joined = "\n".join(_hard_constraints(proj))
        assert "对话双配比" not in joined
        assert "顶替" not in joined


def test_dual_ratio_non_numeric_mean_skipped_no_crash():
    """LLM 蒸馏产物 mean 可能是 "约20%" 字符串 → _num 守卫跳过该约束且不崩
    （build_manifest 是 cluster-write step1 强制必跑·崩则中断整条流水线）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _project_with_style(Path(tmp), {
            "dialogue_only_ratio": {"mean": "约20%"},
            "inner_monologue_ratio": {"mean": True},  # bool 也不是合法数值
        })
        joined = "\n".join(_hard_constraints(proj))
        assert "对话双配比" not in joined


def test_dual_ratio_reaches_full_manifest():
    """接线锁：双配比措辞经 build_manifest 主入口真实到达 manifest（非只在函数级存在）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _project_with_style(Path(tmp), {
            "dialogue_only_ratio": {"mean": 0.20},
            "inner_monologue_ratio": {"mean": 0.10},
        })
        # preflight 最小载荷：cluster_blueprint 覆盖 ch=1（骨架默认空 → 预检 fatal 短路）
        prog_path = proj / "_数据库" / "进度.json"
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
        prog["cluster_blueprint"] = {"cluster_001": {"scene_storyboard": [
            {"ch": 1, "scene_type": ["日常"], "characters": []},
        ]}}
        prog_path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
        # auto_fate_draw 是 cluster-write step1 required 生产者；本测试只测双配比注入链，
        # 写 not_required decision 模拟 step1 已按正式链路执行（同 foreshadow_isolation 夹具口径）。
        manifest_dir = proj / "_数据库" / ".manifest"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "ch_001_fate_draw_decision.json").write_text(json.dumps({
            "_schema": "fate_draw_decision_v1",
            "producer": "auto_fate_draw.py",
            "chapter": 1,
            "status": "not_required",
            "reason": "测试夹具：事件池为骨架空池，无可抽事件",
        }, ensure_ascii=False), encoding="utf-8")
        m = bm.build_manifest(proj, 1)
        assert m.get("preflight", {}).get("passed") is True, \
            f"preflight 应通过（否则走短路 manifest 测不到注入链）: {m.get('preflight')}"
        serialized = json.dumps(m, ensure_ascii=False)
        assert "对话双配比" in serialized, "manifest 序列化文本应含双配比措辞（注入链断裂）"
        assert "顶替" in serialized


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
