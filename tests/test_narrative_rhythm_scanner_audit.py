"""narrative_rhythm_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 中 file==narrative_rhythm_scanner.py 的唯一修复
（北极星⑤克制：advisory 检测器·确定性数据投影健壮性·不干涉模型创作判断）：

  · [L67] _author_rhythm_baseline 读作者档 narrative_rhythm.tension_trajectory
    旧代码：`tt = (data.get("narrative_rhythm") or {}).get("tension_trajectory") or {}`
    `or {}` 惯用法只兜 falsy/None，不兜 **truthy 非 dict**。作者档是混合 producer
    （蒸馏 agent 自由 schema = 契约债来源），narrative_rhythm 字段可能被写成非 dict：
      A. narrative_rhythm 是非空 str（'flatline'）→ L67 `('flatline' or {}).get(...)` 崩
      B. narrative_rhythm 是非空 list（['x']）   → L67 'list' object has no attribute 'get' 崩
      C. tension_trajectory 是非空 str（'high'）  → tt='high'，L68 `tt.get(...)` 崩
      C2. tension_trajectory 是非空 list          → 同 C，L68 崩
    全库读同一字段的兄弟 consumer 都有内层 isinstance 守卫（build_manifest.py /
    prose_rhythm_scanner.py），唯独本函数漏 = 疏漏非有意契约。
    修复：双层 isinstance 守卫——
        nr = data.get("narrative_rhythm")
        tt = (nr.get("tension_trajectory") if isinstance(nr, dict) else None) or {}
        if not isinstance(tt, dict):
            tt = {}

守护点：
  1. A/B/C/C2 四种 truthy 非 dict 输入修复后均优雅回退 post_climax_retention=None（不崩）；
  2. D well-formed dict 仍正确返回 0.9（无回归）；
  3. E/F/G 缺失/null 场景无回归（仍 None）；
  4. H post_climax_retention 为 str（非 int/float）→ 仍 None（类型守卫保持）；
  5. data 整体非 dict（None/list/str）→ 外层 `if isinstance(data, dict)` 兜住，返回 None；
  6. 回归断言：旧表达式对 A/B/C/C2 确实抛 AttributeError（证明 bug 真实存在）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_narrative_rhythm_scanner_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import narrative_rhythm_scanner as nrs  # noqa: E402


# ============================================================
# fixture：把 profile 写进临时 style.json，跑 _author_rhythm_baseline
# ============================================================

def _baseline_from_profile(profile):
    """把 profile 落临时 作者风格.json，经 style_path 调 _author_rhythm_baseline，
    返回其 dict（{"post_climax_retention": ...}）。不触碰真实项目。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        sp.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        return nrs._author_rhythm_baseline(None, sp)


# ============================================================
# [L67] 核心：truthy 非 dict 输入不再崩，优雅回退 None
# ============================================================

def test_narrative_rhythm_nonempty_str_no_crash():
    """[A] narrative_rhythm 为非空 str → 旧代码 `('flatline' or {}).get(...)` 崩；
    修复后优雅回退 post_climax_retention=None。"""
    rep = _baseline_from_profile({"narrative_rhythm": "flatline"})
    assert rep == {"post_climax_retention": None}, rep


def test_narrative_rhythm_nonempty_list_no_crash():
    """[B] narrative_rhythm 为非空 list → 旧代码 'list' has no attribute 'get' 崩；
    修复后回退 None。"""
    rep = _baseline_from_profile({"narrative_rhythm": ["x"]})
    assert rep == {"post_climax_retention": None}, rep


def test_tension_trajectory_nonempty_str_no_crash():
    """[C] tension_trajectory 为非空 str → 旧代码 tt='high'，L68 `'high'.get(...)` 崩；
    修复后回退 None。"""
    rep = _baseline_from_profile({"narrative_rhythm": {"tension_trajectory": "high"}})
    assert rep == {"post_climax_retention": None}, rep


def test_tension_trajectory_nonempty_list_no_crash():
    """[C2] tension_trajectory 为非空 list → 同 C，L68 崩；修复后回退 None。"""
    rep = _baseline_from_profile({"narrative_rhythm": {"tension_trajectory": ["x"]}})
    assert rep == {"post_climax_retention": None}, rep


# ============================================================
# 无回归：well-formed dict 仍返回 0.9
# ============================================================

def test_wellformed_dict_returns_value():
    """[D] 合法嵌套 dict → 正常返回 post_climax_retention=0.9（无回归）。"""
    rep = _baseline_from_profile(
        {"narrative_rhythm": {"tension_trajectory": {"post_climax_retention": 0.9}}}
    )
    assert rep == {"post_climax_retention": 0.9}, rep


def test_wellformed_int_value_coerced_float():
    """post_climax_retention 为 int → 仍被识别（isinstance int）并 float 化。"""
    rep = _baseline_from_profile(
        {"narrative_rhythm": {"tension_trajectory": {"post_climax_retention": 1}}}
    )
    assert rep == {"post_climax_retention": 1.0}, rep
    assert isinstance(rep["post_climax_retention"], float), rep


# ============================================================
# 无回归：缺失 / null / 错类型值 场景
# ============================================================

def test_missing_narrative_rhythm():
    """[E] 无 narrative_rhythm 键 → None（无回归）。"""
    rep = _baseline_from_profile({"foo": 1})
    assert rep == {"post_climax_retention": None}, rep


def test_null_narrative_rhythm():
    """[F] narrative_rhythm 为 null → None（无回归·falsy 路径）。"""
    rep = _baseline_from_profile({"narrative_rhythm": None})
    assert rep == {"post_climax_retention": None}, rep


def test_null_tension_trajectory():
    """[G] tension_trajectory 为 null → None（无回归·falsy 路径）。"""
    rep = _baseline_from_profile({"narrative_rhythm": {"tension_trajectory": None}})
    assert rep == {"post_climax_retention": None}, rep


def test_post_climax_retention_wrong_type():
    """[H] post_climax_retention 为 str（非 int/float）→ 类型守卫保持 → None。"""
    rep = _baseline_from_profile(
        {"narrative_rhythm": {"tension_trajectory": {"post_climax_retention": "high"}}}
    )
    assert rep == {"post_climax_retention": None}, rep


def test_empty_dict_tension_trajectory():
    """tension_trajectory 为空 dict → None（无 post_climax_retention 键）。"""
    rep = _baseline_from_profile({"narrative_rhythm": {"tension_trajectory": {}}})
    assert rep == {"post_climax_retention": None}, rep


# ============================================================
# data 整体非 dict（外层守卫）
# ============================================================

def test_toplevel_non_dict_profile():
    """作者档顶层是 list / str / 数字 → 外层 `if isinstance(data, dict)` 兜住，返回 None。"""
    for bad in (["x"], "garbage", 42):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "style.json"
            sp.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            rep = nrs._author_rhythm_baseline(None, sp)
            assert rep == {"post_climax_retention": None}, (bad, rep)


def test_no_style_path_no_project():
    """style_path 与 project 均 None → data 恒 None → 返回 None（不崩）。"""
    rep = nrs._author_rhythm_baseline(None, None)
    assert rep == {"post_climax_retention": None}, rep


def test_nonexistent_style_path():
    """style_path 指向不存在文件 → 不读取 → data None → None（不崩）。"""
    rep = nrs._author_rhythm_baseline(None, Path("D:/__nonexistent__/no_such_style.json"))
    assert rep == {"post_climax_retention": None}, rep


# ============================================================
# 反向证明：旧表达式确实会崩（钉死 bug 真实性）
# ============================================================

def test_old_expression_crashed_on_nondict():
    """复刻旧（pre-fix）函数体，证明 A/B/C/C2 四形态确实抛 AttributeError——
    钉死这是真 bug 而非伪命题（修复后的当前代码上面诸用例已证不再崩）。"""
    def _old_full(data):
        ret = None
        if isinstance(data, dict):
            tt = (data.get("narrative_rhythm") or {}).get("tension_trajectory") or {}  # L67
            if isinstance(tt.get("post_climax_retention"), (int, float)):              # L68
                ret = float(tt["post_climax_retention"])
        return {"post_climax_retention": ret}

    crash_cases = [
        {"narrative_rhythm": "flatline"},                       # A
        {"narrative_rhythm": ["x"]},                            # B
        {"narrative_rhythm": {"tension_trajectory": "high"}},   # C
        {"narrative_rhythm": {"tension_trajectory": ["x"]}},    # C2
    ]
    for data in crash_cases:
        raised = False
        try:
            _old_full(data)
        except AttributeError:
            raised = True
        assert raised, f"旧表达式本应对 {data} 抛 AttributeError，却没抛 → bug 复刻失效"


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
