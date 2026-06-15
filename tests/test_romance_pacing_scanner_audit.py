"""romance_pacing_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 一处确定性检测器健壮性修复（北极星⑤：作者风格档=第一权威·advisory）：

  · [L36] scan(text, project, style_path)：旧代码收 --style/--project 却从未引用，
    三个 floor（DIALOGUE_FLOOR/EMOTION_PUNCT_FLOOR/SENSORY_FLOOR）对所有 romance 草稿写死，
    docstring 自报支持 --style 但被静默丢弃 = 活死参 + 契约债，且对低对话基线的文学言情
    作者产生与其自身风格相悖的误报（dialogue_too_sparse 假阳）。
    修复（对齐 prose_rhythm_scanner._author_baseline 范式）：从作者档读
    quantitative.dialogue_ratio.mean（consolidate_author_profile 确定性写入·0-1）作对话占比基线，
    令 DIALOGUE_FLOOR 可被作者基线【放宽】（dia_floor = min(DIALOGUE_FLOOR, mean*0.85)）——
    **relax-only：绝不收紧**（只减误报不新增）。emotion_punct/sensory 两个 floor 无金标准字段，
    保持通用保守兜底不变（范围界定）。

守护点：
  · 无作者档 / 缺 dialogue_ratio 键 / 坏 JSON / 路径不存在 → 安全回退通用 0.18，不崩。
  · 低对话基线（--style 或项目 _数据库/作者风格.json）→ floor 放宽 → 同一草稿从 flag 翻成 PASS。
  · 高对话基线（mean>floor）→ floor 不被收紧（仍 0.18·relax-only）。
  · 对话维度放宽时 emotion_punct/sensory 维度不受影响（范围边界）。
  · 全程 gate_level=advisory（顾问非法官）。
  · metrics 暴露 dialogue_floor_effective 供审计看作者档是否生效。

跑法：PYTHONIOENCODING=utf-8 python tests/test_romance_pacing_scanner_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import romance_pacing_scanner as R  # noqa: E402


# ── 测试夹具 ────────────────────────────────────────
# 叙述驱动 + 五感丰富 + 含情绪标点的言情段落：dialogue_ratio≈0（< 通用 0.18 → 触发
# dialogue_too_sparse），但 emotion_punct/sensory 远超其 floor（隔离出仅对话维度受检）。
_SENSORY_PARA = (
    "她站在窗前看着外面的雨气息很凉指尖触到玻璃感到一阵心跳脸红了耳根也暖暖的"
    "怀里抱着那封信掌心微微出汗眼神飘忽不定温度一点点升上来软软的香气萦绕。"
)


def _low_dialogue_text() -> str:
    """对话占比≈0、五感/情绪标点达标 → 仅 dialogue_too_sparse 一项命中（通用 floor 下）。"""
    return "\n\n".join([_SENSORY_PARA] * 30) + "\n\n他真的会来吗？她不知道……也许吧……"


def _kinds(r) -> set:
    return {v["kind"] for v in r["violations"]}


def _floor_eff(r) -> float:
    return r["metrics"]["dialogue_floor_effective"]


def _write_style(tmp: Path, mean) -> Path:
    """写一个 --style 用的作者风格.json，含 quantitative.dialogue_ratio.mean。"""
    sp = tmp / "作者风格.json"
    sp.write_text(json.dumps(
        {"quantitative": {"dialogue_ratio": {"mean": mean}}}, ensure_ascii=False),
        encoding="utf-8")
    return sp


def _mk_project_with_profile(tmp: Path, mean) -> Path:
    """造一个项目，把作者档落到 _数据库/作者风格.json（验证 project 兜底读取路径）。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    db.joinpath("作者风格.json").write_text(json.dumps(
        {"quantitative": {"dialogue_ratio": {"mean": mean}}}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


# ============================================================
# [L36] 无作者档 → 通用 floor（旧行为不回归）
# ============================================================

def test_no_profile_uses_generic_floor():
    """不传 project/style → dialogue_floor_effective == 通用 DIALOGUE_FLOOR(0.18)，仍报 dialogue_too_sparse。"""
    r = R.scan(_low_dialogue_text())
    assert _floor_eff(r) == R.DIALOGUE_FLOOR
    assert "dialogue_too_sparse" in _kinds(r), f"通用 floor 下低对话应报，violations={r['violations']}"


# ============================================================
# [L36 核心] 低对话基线 → floor 放宽 → 误报消除
# ============================================================

def test_style_low_baseline_relaxes_floor_to_pass():
    """[L36 核心] --style 给低对话基线(mean=0.0) → 有效 floor=0.0 → 同一草稿不再报 dialogue_too_sparse。"""
    base = R.scan(_low_dialogue_text())
    assert "dialogue_too_sparse" in _kinds(base)  # 前提：通用 floor 下确实命中
    with tempfile.TemporaryDirectory() as d:
        sp = _write_style(Path(d), 0.0)
        r = R.scan(_low_dialogue_text(), style_path=sp)
    assert _floor_eff(r) == 0.0, f"作者基线 0.0 → 有效 floor 应放宽到 0，实际 {_floor_eff(r)}"
    assert "dialogue_too_sparse" not in _kinds(r), \
        f"低对话基线作者应豁免 dialogue_too_sparse，实际 {r['violations']}"


def test_project_profile_low_baseline_relaxes_floor():
    """作者档落在项目 _数据库/作者风格.json（非 --style）→ 同样被读取并放宽 floor。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project_with_profile(Path(d), 0.05)
        r = R.scan(_low_dialogue_text(), project=proj)
    # floor = min(0.18, 0.05*0.85) = 0.0425 → round 3 = 0.043
    assert abs(_floor_eff(r) - round(0.05 * 0.85, 3)) < 1e-9, \
        f"项目作者档 0.05 → 有效 floor 应 ≈0.043，实际 {_floor_eff(r)}"
    assert _floor_eff(r) < R.DIALOGUE_FLOOR, "应被放宽到低于通用 floor"


def test_violation_floor_field_tracks_effective_floor():
    """放宽后若仍命中（基线极低但草稿 ratio 更低），violation.floor 字段反映有效 floor 而非常量。"""
    with tempfile.TemporaryDirectory() as d:
        sp = _write_style(Path(d), 0.05)  # floor≈0.043；草稿 ratio≈0 → 仍命中但 floor 更低
        r = R.scan(_low_dialogue_text(), style_path=sp)
    v = [x for x in r["violations"] if x["kind"] == "dialogue_too_sparse"]
    assert v, "ratio≈0 低于 0.043 应仍命中"
    assert v[0]["floor"] == _floor_eff(r) and v[0]["floor"] < R.DIALOGUE_FLOOR, \
        f"violation.floor 应等于有效 floor 且 < 通用，实际 {v[0]['floor']}"


# ============================================================
# [L36] relax-only：高对话基线绝不收紧 floor
# ============================================================

def test_high_baseline_does_not_tighten_floor():
    """作者基线高于通用 floor(mean=0.6) → 有效 floor 仍 == 0.18（relax-only·绝不收紧·只减误报）。"""
    with tempfile.TemporaryDirectory() as d:
        sp = _write_style(Path(d), 0.6)
        r = R.scan(_low_dialogue_text(), style_path=sp)
    assert _floor_eff(r) == R.DIALOGUE_FLOOR, \
        f"高基线不应把 floor 抬高（relax-only），实际 {_floor_eff(r)}"


# ============================================================
# [L36] 健壮性：坏档 / 缺键 / 缺文件 → 安全回退通用 floor，不崩
# ============================================================

def test_malformed_profile_falls_back_to_generic():
    """坏 JSON 作者档 → _load_json 返回 None → 回退通用 floor，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "作者风格.json"
        sp.write_text("{ 这不是合法 json", encoding="utf-8")
        r = R.scan(_low_dialogue_text(), style_path=sp)
    assert _floor_eff(r) == R.DIALOGUE_FLOOR


def test_profile_without_dialogue_ratio_key_falls_back():
    """作者档有 quantitative 但无 dialogue_ratio → 回退通用 floor。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "作者风格.json"
        sp.write_text(json.dumps({"quantitative": {"sentence_length": {"mean": 30}}}), encoding="utf-8")
        r = R.scan(_low_dialogue_text(), style_path=sp)
    assert _floor_eff(r) == R.DIALOGUE_FLOOR


def test_missing_style_and_project_paths_no_crash():
    """style/project 路径不存在 → 安全回退通用 floor，不抛异常。"""
    r = R.scan(_low_dialogue_text(),
               project=Path("Z:/__no_such_project__"),
               style_path=Path("Z:/__no_such_style__.json"))
    assert _floor_eff(r) == R.DIALOGUE_FLOOR


def test_non_numeric_mean_falls_back():
    """dialogue_ratio.mean 为脏字符串 → isinstance 守卫 → 回退通用 floor，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "作者风格.json"
        sp.write_text(json.dumps({"quantitative": {"dialogue_ratio": {"mean": "high"}}}), encoding="utf-8")
        r = R.scan(_low_dialogue_text(), style_path=sp)
    assert _floor_eff(r) == R.DIALOGUE_FLOOR


# ============================================================
# [L36] 范围边界：对话维度放宽不波及 emotion_punct / sensory
# ============================================================

def test_emotion_sensory_floors_unaffected_by_dialogue_override():
    """既缺对话又缺情绪标点的平淡文本：作者对话基线 0.0 只放宽 dialogue 维度，
    emotion_punct_sparse 仍照报（证 emotion/sensory floor 不被 --style 影响）。"""
    flat = "\n\n".join(["他走进房间坐下来翻开桌上的文件看了很久然后合上站起身离开了那个安静的房间。"] * 30)
    base = R.scan(flat)
    assert {"dialogue_too_sparse", "emotion_punct_sparse"} <= _kinds(base), \
        f"平淡文本通用 floor 下应同时报对话+情绪标点，实际 {base['violations']}"
    with tempfile.TemporaryDirectory() as d:
        sp = _write_style(Path(d), 0.0)
        r = R.scan(flat, style_path=sp)
    assert "dialogue_too_sparse" not in _kinds(r), "对话维度应被放宽豁免"
    assert "emotion_punct_sparse" in _kinds(r), "emotion_punct floor 不应被对话 override 波及"


# ============================================================
# 总体契约
# ============================================================

def test_gate_level_always_advisory():
    """北极星⑤：顾问非法官 → gate_level 恒 advisory（不论是否命中）。"""
    r = R.scan(_low_dialogue_text())
    assert r["gate_level"] == "advisory"
    assert r["scanner"] == "romance_pacing"


def test_metrics_exposes_effective_floor():
    """metrics 暴露 dialogue_floor_effective（供 audit 看作者档是否生效）。"""
    r = R.scan(_low_dialogue_text())
    assert "dialogue_floor_effective" in r["metrics"]
    assert isinstance(r["metrics"]["dialogue_floor_effective"], (int, float))


def test_short_text_safe_noop():
    """文本过短（<500 CJK）→ PASS no-op，不崩（含新增 helper 不影响早返回）。"""
    r = R.scan("他点头。", style_path=Path("Z:/nope.json"))
    assert r["verdict"] == "PASS"
    assert r["violations_count"] == 0


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
