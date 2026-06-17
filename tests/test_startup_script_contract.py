# -*- coding: utf-8 -*-
"""start.sh/start.cmd 启动脚本契约回归（第四轮 Workflow #2·2026-06-17）。

start.sh/start.cmd 是非技术用户/BYOK 唯一启动入口·自初始发布从未更新·穿越 v26→v28 架构演进：
  · 引用 core/claude-home/commands/（2026-05-15 已删）→ 命令静默不部署
  · 硬编码 'save-state must run all 11 steps'（实为 12·cluster-save-state.plan.json len）
  · 'Agent sub-tasks for writing chapters'（违 v26 必走 /cluster-write·chapter-mode 废弃措辞）

静态契约断言（对标 test_plan_script_contract·把脚本当文本读·零依赖）。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return (_ROOT / name).read_text(encoding="utf-8")


def test_no_deleted_commands_dir():
    """🔴 不引用已删 core/claude-home/commands（2026-05-15 删·应用 .claude/commands 真实命令源）。"""
    for f in ("start.sh", "start.cmd"):
        txt = _read(f)
        assert "claude-home/commands" not in txt, f"{f} 仍引用已删 core/claude-home/commands"
        assert "claude-home\\commands" not in txt, f"{f} 仍引用已删 core\\claude-home\\commands"


def test_save_state_steps_match_plan():
    """🔴 cluster-save-state 步数串 == plan.json len(steps)（防硬编码漂移·动态读 plan）。"""
    plan = json.loads((_ROOT / "core" / "claude-home" / "plans"
                       / "cluster-save-state.plan.json").read_text(encoding="utf-8"))
    n = len(plan["steps"])
    for f in ("start.sh", "start.cmd"):
        txt = _read(f)
        assert "11 steps" not in txt, f"{f} 仍含过期 '11 steps'"
        assert f"{n} steps" in txt, f"{f} 应含 'all {n} steps'（plan len={n}）"


def test_no_chapter_mode_phrasing():
    """🔴 无 v26 废弃 chapter-mode 措辞（writing chapters·必走 /cluster-write）。"""
    for f in ("start.sh", "start.cmd"):
        txt = _read(f)
        assert "sub-tasks for writing chapters" not in txt, f"{f} 仍含废弃 chapter-mode 措辞"


def test_cluster_write_phrasing_present():
    """正向：含 v26 cluster mode 正确措辞（/cluster-write）。"""
    for f in ("start.sh", "start.cmd"):
        assert "/cluster-write" in _read(f), f"{f} 应含 /cluster-write 措辞"


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
