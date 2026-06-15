"""relationship_evaluator.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 两处确定性状态机记账健壮性修复
（北极星⑤：advisory 揭密信号记账·不干涉创作判断）：

  · [L114] trigger_satisfied：关系维度脏值（手工 /db 或弱模型旁路写 'trust':'high'
    这类字符串）旧代码 `if cur is None or cur < threshold:` 对 str<int 抛 TypeError →
    relationship_evaluator 静默 no-op，该关系 heart_event 揭密机制失效。修复改为
    `if not isinstance(cur, (int, float)) or cur < threshold:`——非数值视作未满足而非崩溃。
    守护点：None/缺失/字符串/None 维度全返回 False 且不崩；合法 int/float 照常比较。

  · [L178] evaluate() 主循环 heart_events：旧 `for he in npc_data.get("heart_events", []):`
    后直接 `if he.get("consumed", False):`，缺 isinstance(he, dict) 守卫；兄弟函数
    mark_consumed_from_changes:91 同类循环已有该守卫。混入裸字符串/null 的 he 元素 →
    AttributeError 崩溃，evaluate 无 try/except → 整个 pending_reveals 计算崩溃（丢该
    cluster 全部 heart_event 揭密信号）。修复加 `if not isinstance(he, dict): continue`。
    守护点：list 含非 dict 杂质不崩、合法 dict 条目仍正常产 pending_reveals。

跑法：PYTHONIOENCODING=utf-8 python tests/test_relationship_evaluator_audit.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import relationship_evaluator as re_eval  # noqa: E402

_MODULE_PATH = _SCRIPTS / "relationship_evaluator.py"


# ============================================================
# [L114] trigger_satisfied — 非数值脏值守卫（直接单元测试）
# ============================================================

def test_trigger_empty_returns_false():
    """trigger_at 为空 → False（保持原行为）。"""
    assert re_eval.trigger_satisfied({}, {"trust": 9}) is False


def test_trigger_all_dims_met():
    """全部维度 ≥ 阈值 → True。"""
    assert re_eval.trigger_satisfied({"trust": 9, "affinity": 6},
                                     {"trust": 9, "affinity": 7}) is True


def test_trigger_one_dim_below_threshold():
    """某维度低于阈值 → False。"""
    assert re_eval.trigger_satisfied({"trust": 9, "affinity": 6},
                                     {"trust": 9, "affinity": 5}) is False


def test_trigger_missing_dim_returns_false_not_crash():
    """关系缺该维度（rel.get→None）→ 旧 `cur < threshold` 前有 None 守卫，返回 False。"""
    assert re_eval.trigger_satisfied({"trust": 9}, {"affinity": 7}) is False


def test_trigger_none_value_returns_false():
    """维度显式写 None → False（不崩）。"""
    assert re_eval.trigger_satisfied({"trust": 9}, {"trust": None}) is False


def test_trigger_string_dirty_value_no_typeerror():
    """[L114 核心] 维度脏值是字符串（'high'）→ 旧代码 'high' < 9 抛 TypeError；
    修复后 isinstance 守卫把非数值视作未满足，返回 False 且不崩。"""
    # 旧代码此处会 TypeError: '<' not supported between 'str' and 'int'
    assert re_eval.trigger_satisfied({"trust": 9}, {"trust": "high"}) is False


def test_trigger_string_value_among_valid_dims():
    """混合：一个数值达标 + 一个字符串脏值 → 脏值维度判未满足 → 整体 False、不崩。"""
    assert re_eval.trigger_satisfied(
        {"trust": 9, "affinity": 6},
        {"trust": 10, "affinity": "max"},
    ) is False


def test_trigger_float_value_compares_ok():
    """float 维度值正常比较（isinstance(.,(int,float)) 放行 float）。"""
    assert re_eval.trigger_satisfied({"trust": 9.0}, {"trust": 9.5}) is True
    assert re_eval.trigger_satisfied({"trust": 9.0}, {"trust": 8.5}) is False


def test_trigger_list_dirty_value_no_crash():
    """维度脏值是 list → 非数值 → False、不崩（TypeError 同样会被旧代码触发）。"""
    assert re_eval.trigger_satisfied({"trust": 9}, {"trust": [1, 2]}) is False


# ============================================================
# [L178] evaluate() heart_events isinstance 守卫 —— 子进程端到端
# ============================================================

def _os_environ():
    import os
    return dict(os.environ)


def _write_project(d: str, ensemble: dict, rels: dict, cards: dict) -> Path:
    proj = Path(d)
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "群像档.json").write_text(json.dumps(ensemble, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps(rels, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    # 至少一个章节目录，让 --auto/缺 --ch 时能解析章号（此处显式传 --ch 也无妨）
    (proj / "章节" / "第001章").mkdir(parents=True, exist_ok=True)
    return proj


def _run_eval(proj: Path, ch: int):
    """跑 relationship_evaluator <项目> --ch N，返回 (returncode, stderr, stdout_json)。
    子进程驱动 = 复刻 save_state_evaluators.py:69 真实调用路径。"""
    env = {**_os_environ(), "PYTHONIOENCODING": "utf-8"}
    cp = subprocess.run(
        [sys.executable, str(_MODULE_PATH), str(proj), "--ch", str(ch)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    out = {}
    if cp.stdout:
        try:
            out = json.loads(cp.stdout)
        except json.JSONDecodeError:
            out = {}
    return cp.returncode, (cp.stderr or ""), out


# 一个会触发 pending（trust 阈值已满足、未 consumed）的合法 heart_event。
_VALID_HE = {
    "event_id": "HE_001",
    "tier_label": "信任·初心",
    "reveal": "他说出了那个秘密",
    "trigger_at": {"trust": 5},
    "consumed": False,
}
_RELS = {"relationships": [{"from": "主角甲", "to": "配角乙", "trust": 8}]}
_CARDS = {"characters": [{"name": "主角甲", "role": "主角"},
                         {"name": "配角乙", "role": "配角"}]}


def test_evaluate_valid_heart_event_produces_pending():
    """基线：合法 heart_event + 阈值满足 → pending_reveals 非空、exit 1、不崩。"""
    ensemble = {"characters": {"配角乙": {"heart_events": [dict(_VALID_HE)]}}}
    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(d, ensemble, _RELS, _CARDS)
        rc, err, out = _run_eval(proj, 3)
    assert "Traceback" not in err, err
    assert rc == 1, f"应有 1 个 pending → exit 1, got rc={rc}, err={err}"
    assert out.get("pending_reveals_count") == 1, out


def test_evaluate_heart_events_with_non_dict_element_no_crash():
    """[L178 核心] heart_events 混入裸字符串/None → 旧代码 he.get() 抛 AttributeError 崩
    （exit 1 但 stderr 有 Traceback）；修复后非 dict 元素被跳过，合法条目仍产 pending。"""
    ensemble = {
        "characters": {
            "配角乙": {
                "heart_events": [
                    "我是一条脏字符串",   # 非 dict → 旧代码 .get() 崩
                    None,                  # 非 dict → 崩
                    12345,                 # 非 dict → 崩
                    dict(_VALID_HE),       # 合法 → 应正常产 pending
                ]
            }
        }
    }
    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(d, ensemble, _RELS, _CARDS)
        rc, err, out = _run_eval(proj, 3)
    # 关键断言：不再崩。
    assert "Traceback" not in err, err
    assert "AttributeError" not in err, err
    # 合法 heart_event 仍被正常评估 → pending 计数=1、exit 1。
    assert rc == 1, f"got rc={rc}, err={err}"
    assert out.get("pending_reveals_count") == 1, out


def test_evaluate_all_non_dict_heart_events_safe_zero_pending():
    """heart_events 全是非 dict 杂质 → 全部跳过 → 0 pending、exit 0、不崩。"""
    ensemble = {"characters": {"配角乙": {"heart_events": ["x", None, 1]}}}
    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(d, ensemble, _RELS, _CARDS)
        rc, err, out = _run_eval(proj, 3)
    assert "Traceback" not in err, err
    assert rc == 0, f"应 0 pending → exit 0, got rc={rc}, err={err}"
    assert out.get("pending_reveals_count") == 0, out


def test_evaluate_dirty_relationship_dim_no_crash():
    """[L114 端到端] 关系维度脏值（trust='high'）+ heart_event trigger_at trust → 旧代码
    trigger_satisfied 内 'high'<5 抛 TypeError 崩；修复后判未满足 → 0 pending、exit 0、不崩。"""
    ensemble = {"characters": {"配角乙": {"heart_events": [dict(_VALID_HE)]}}}
    rels = {"relationships": [{"from": "主角甲", "to": "配角乙", "trust": "high"}]}
    with tempfile.TemporaryDirectory() as d:
        proj = _write_project(d, ensemble, rels, _CARDS)
        rc, err, out = _run_eval(proj, 3)
    assert "Traceback" not in err, err
    assert "TypeError" not in err, err
    # 脏值视作未满足 → 不产 pending。
    assert rc == 0, f"got rc={rc}, err={err}"
    assert out.get("pending_reveals_count") == 0, out


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
