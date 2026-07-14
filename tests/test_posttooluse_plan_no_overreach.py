# -*- coding: utf-8 -*-
"""回归锁：PostToolUse plan-check hook 不得越权自动完成 plan step。

铁证背景（真机 2026-07-14）：`cluster-save-state` step14(wal-end) 的 expected_outputs 是裸
目录 `_数据库/.wal`，hook 用双向子串匹配 → 任何写入 .wal/ 下的文件都命中 step14 → 一次
Write 同时 auto-complete step5+step14（completed_at 同秒 · verified_outputs 都为 [] =
skip_output=True 指纹）。且 auto-complete 无条件走 skip_output=True，绕过 expected_outputs
校验，与 pretooluse 防跳步守卫（required + skip_output_allowed=false → 拦）直接矛盾。

本测试钉死修复：
  1. `_path_matches` 精确文件级——目录形态永不命中、子串永不命中；
  2. required 且 skip_output_allowed != true 的主链 step，hook 一律不自动完成（只提示）；
  3. 非主链 step 自动完成走**正常** step_complete（不传 skip_output）；
  4. 全部 plan 模板的 expected_outputs 无裸目录形态。
"""
import importlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "claude-home" / "hooks"))
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

hook = importlib.import_module("posttooluse_plan_check")
PLANS_DIR = _ROOT / "core" / "claude-home" / "plans"


# ════════════════════════ 1. _path_matches 精确文件级 ════════════════════════
def test_dir_shaped_expected_never_matches_file_under_it():
    """裸目录 expected（`_数据库/.wal`）绝不被其下任意文件命中——这是被根治的越权 bug。"""
    fp = "D:/proj/_数据库/.wal/cluster_001_state_delta.json"
    assert hook._path_matches(fp, "_数据库/.wal") is False
    assert hook._path_matches(fp, "_数据库/.wal/") is False
    assert hook._path_matches(fp, "_数据库\\.wal\\") is False
    # 任意别的 .wal 写入同样不命中裸目录
    assert hook._path_matches("D:/p/_数据库/.wal/whatever.json", "_数据库/.wal") is False


def test_substring_no_longer_matches():
    """子串匹配已废除：state_delta.json 不得命中更短的 state.json expected。"""
    fp = "D:/proj/_数据库/.wal/cluster_001_state_delta.json"
    assert hook._path_matches(fp, "_数据库/.wal/cluster_001_state.json") is False
    # 反向子串（expected 更长）也不命中
    assert hook._path_matches("D:/proj/x.json", "_数据库/.wal/cluster_001_x.json") is False


def test_exact_and_suffix_file_matches_still_work():
    """精确文件级正命中：完全相等 / `/` 边界对齐的路径后缀。"""
    fp = "D:/proj/_数据库/.wal/cluster_001_x.json"
    assert hook._path_matches(fp, "_数据库/.wal/cluster_001_x.json") is True
    assert hook._path_matches(fp, fp) is True
    # 边界必须对齐：非 `/` 边界的尾部巧合不算命中
    assert hook._path_matches("D:/proj/xcluster_001_x.json", "cluster_001_x.json") is False


# ════════════════════════ 2/3. step_complete 越权纪律 ════════════════════════
def _base_step(**over):
    s = {"n": 14, "name": "wal-end", "required": True,
         "skip_output_allowed": False, "expected_outputs": ["_数据库/.wal"]}
    s.update(over)
    return s


def test_locked_required_step_not_auto_completed(monkeypatch):
    """required + skip_output_allowed=false（全部主链 step）→ hook 不调 step_complete。"""
    calls = []
    monkeypatch.setattr(hook, "step_complete",
                        lambda *a, **k: calls.append((a, k)))
    step = _base_step()
    hook._observe_step({"project": None}, "PID", step,
                       "D:/proj/_数据库/.wal/cluster_001_x.json")
    assert calls == [], "主链 locked step 被 hook 越权自动完成"


def test_nonlocked_step_uses_normal_step_complete(monkeypatch):
    """非 required / 显式 skip_output_allowed=true 的 step 自动完成走正常 step_complete。

    关键：绝不传 skip_output=True（旧实现的越权指纹）。expected_outputs 为空 → 无缺失。
    """
    calls = []
    monkeypatch.setattr(hook, "step_complete",
                        lambda *a, **k: calls.append((a, k)))
    step = {"n": 3, "name": "aux", "required": False, "expected_outputs": []}
    hook._observe_step({"project": None}, "PID", step, "D:/proj/x.json")
    assert len(calls) == 1, "非主链 step 应正常自动完成"
    args, kwargs = calls[0]
    assert kwargs.get("skip_output") is not True, "禁止无条件 skip_output=True 绕过校验"


def test_nonlocked_step_with_missing_output_does_not_complete(monkeypatch):
    """非主链 step 但 expected_output 未落盘 → 只提示，不写状态。"""
    calls = []
    monkeypatch.setattr(hook, "step_complete",
                        lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(hook, "resolve_project_root", lambda p: Path("D:/nope"))
    step = {"n": 3, "name": "aux", "required": False,
            "skip_output_allowed": True,
            "expected_outputs": ["_数据库/.wal/missing_file.json"]}
    hook._observe_step({"project": "x"}, "PID", step, "D:/proj/x.json")
    assert calls == [], "产物缺失时不得自动完成"


def test_real_fingerprint_scenario_no_double_autocomplete(monkeypatch):
    """复刻真机铁证：一次 .wal 写入不得让 step5(state-analysis)+step14(wal-end) 同时完成。

    即便 step14 仍保留旧的裸目录 expected（模拟回退），精确匹配也不命中它；step5 是
    locked required，命中其真实 expected 也只提示不自动完成。
    """
    calls = []
    monkeypatch.setattr(hook, "step_complete",
                        lambda *a, **k: calls.append(a[:2]))
    plan = {
        "id": "PID",
        "project": None,
        "steps": [
            {"n": 5, "name": "cluster-state-analysis", "required": True,
             "skip_output_allowed": False, "status": "pending",
             "expected_outputs": ["_数据库/.wal/cluster_001_state_delta.json"]},
            {"n": 14, "name": "wal-end", "required": True,
             "skip_output_allowed": False, "status": "pending",
             "expected_outputs": ["_数据库/.wal"]},  # 旧裸目录（回退模拟）
        ],
    }
    file_path = "D:/proj/_数据库/.wal/cluster_001_state_delta.json"
    for step in plan["steps"]:
        expected = step.get("expected_outputs") or []
        if any(hook._path_matches(file_path, e) for e in expected):
            hook._observe_step(plan, "PID", step, file_path)
    assert calls == [], "写一个 .wal 文件不得 auto-complete 任何 required step"


# ════════════════════════ 4. 全 plan 模板无裸目录 expected ════════════════════════
def test_no_plan_template_has_dir_shaped_expected_output():
    """任何 plan step 的 expected_outputs 都必须是精确文件（禁裸目录形态）。

    裸目录 = 尾部 `/`/`\\` 或无文件扩展名的目录路径；目录只要存在就永远校验通过（零
    校验），且会被 PostToolUse 观察 hook 误判成任意子文件写入的产物。
    """
    file_suffixes = (".json", ".txt", ".md", ".placeholder")
    offenders = []
    for f in sorted(PLANS_DIR.glob("*.plan.json")):
        plan = json.loads(f.read_text(encoding="utf-8"))
        for step in plan.get("steps", []):
            for e in (step.get("expected_outputs") or []):
                raw = str(e)
                if raw.endswith("/") or raw.endswith("\\") or not raw.endswith(file_suffixes):
                    offenders.append(f"{f.name} step {step.get('n')}: {e}")
    assert not offenders, "plan 模板含裸目录形态 expected_output（零校验 + 触发 hook 越权）:\n  " + "\n  ".join(offenders)


def test_wal_end_steps_produce_verifiable_receipt():
    """cluster-write step7 / cluster-save-state step14 收尾步的产物是可验证回执文件。"""
    checks = [
        ("cluster-write.plan.json", 7, "_数据库/.wal/cluster_{key}_write_end.json"),
        ("cluster-save-state.plan.json", 14, "_数据库/.wal/cluster_{key}_save_state_end.json"),
    ]
    for fname, n, expected in checks:
        plan = json.loads((PLANS_DIR / fname).read_text(encoding="utf-8"))
        step = next(s for s in plan["steps"] if s.get("n") == n)
        assert expected in (step.get("expected_outputs") or []), \
            f"{fname} step {n} 收尾回执 expected_output 缺失"
        assert any("plan_end_receipt.py" in s for s in (step.get("scripts") or [])), \
            f"{fname} step {n} 缺 plan_end_receipt.py 收尾校验脚本"


if __name__ == "__main__":
    fails = 0
    import inspect
    for nm, fn in sorted(globals().items()):
        if nm.startswith("test_") and callable(fn):
            try:
                if "monkeypatch" in inspect.signature(fn).parameters:
                    continue  # 需 pytest fixture，跳过裸跑
                fn()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
