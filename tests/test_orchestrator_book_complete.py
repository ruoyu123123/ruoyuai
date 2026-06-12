#!/usr/bin/env python3
"""orchestrator 完本短路回归测试（复验修 · 2026-06-12）

复验发现的 MAJOR：ME 池耗尽时 emerge 写了 .book_complete.json，但 plan step11
仍带 must_spawn_agent=novel-outline-planner + pause_for_user——judge 无输入会被
judge_required_keys 逼着**编造候选**（捏造走向卡）→ 幽灵 cluster 写进事件簇。
钉死三条契约：
  1. 标记存在 + step 是「outline-planner + pause」→ 短路：不派 judge·不弹卡·
     step 以 output="book_complete" 正常完成（plan 不卡死）。
  2. 无标记 → 照常派 judge + 弹卡（不误伤正常涌现）。
  3. 标记存在但 step 不是涌现步（其他 agent / 无 pause）→ 不短路（只精准跳涌现）。

沙箱范式照搬 tests/test_orchestrator_empty_plan.py。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402


# ============ 测试隔离：plan_tracker 目录全部指向临时区 ============
class _Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        self.styles = self.tmp / "styles"
        self.global_plans = self.tmp / ".plans"
        for d in (self.templates, self.projects, self.styles, self.global_plans):
            d.mkdir(parents=True)
        self.proj_root = self.projects / "测试书"
        (self.proj_root / "_数据库").mkdir(parents=True)
        self._saved = {}

    def __enter__(self):
        for k, v in [("TEMPLATES_DIR", self.templates),
                     ("PROJECTS_DIR", self.projects),
                     ("STYLES_DIR", self.styles),
                     ("GLOBAL_PLANS_DIR", self.global_plans),
                     ("ATTEST_KEY_PATH", self.global_plans / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)

    def write_template(self, command: str, template: dict):
        (self.templates / f"{command}.plan.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_marker(self):
        (self.proj_root / "_数据库" / ".book_complete.json").write_text(
            json.dumps({"book_complete": True,
                        "completed_at": "2026-06-12T00:00:00",
                        "reason": "ME 池耗尽·大势已走完",
                        "last_cluster": "cluster_002"}, ensure_ascii=False),
            encoding="utf-8")


class _SpyDispatch:
    """记录被派的 agent·并落 JudgeReport（end_plan anti-skip 校验需要）。"""

    def __init__(self):
        self.agents = []

    def __call__(self, agent, step, ctx):
        self.agents.append(agent)
        jrp = step.get("judge_report_path")
        out_raw = jrp.get(agent) if isinstance(jrp, dict) else jrp
        path = None
        if out_raw:
            p = Path(orc.resolve_placeholders(out_raw, ctx))
            if not p.is_absolute():
                p = Path(ctx["project_root"]) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
            path = p

        class _O:
            pass

        o = _O()
        o.data, o.ok, o.output_path = {"ok": True}, True, path
        return o


class _SpyPause:
    """记录是否弹过卡——完本短路后绝不该再问用户。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, step, spec, options):
        self.calls += 1
        return options[0] if options else spec.get("default")


def _emergence_template():
    """最小化的「涌现+选卡」步（对齐 cluster-save-state step11 形态）。"""
    return {"command": "test-emerge", "total_steps": 1,
            "required_steps": [1], "optional_steps": [],
            "steps": [{
                "n": 1, "name": "涌现下一 cluster + 用户选卡", "required": True,
                "skip_output_allowed": True, "expected_outputs": [],
                "must_spawn_agent": "novel-outline-planner",
                "judge_report_path": "_数据库/.wal/emergence_judge.json",
                "pause_for_user": {"type": "choice",
                                   "options_from": "_数据库/.wal/不存在.json",
                                   "inline_options": ["候选A", "候选B"]},
            }]}


# ============ 1. 完本标记 → 短路（不派 judge·不弹卡·step 正常完成） ============
def test_marker_short_circuits_emergence_step():
    with _Sandbox() as sb:
        sb.write_template("test-emerge", _emergence_template())
        sb.write_marker()
        dispatch, pause = _SpyDispatch(), _SpyPause()
        s = orc.run_command("test-emerge", "测试书", key="002",
                            script_runner=lambda *a, **k: 0,
                            judge_dispatch=dispatch, pause_handler=pause)
        assert dispatch.agents == [], "完本后绝不能再派涌现 judge（会捏造候选）"
        assert pause.calls == 0, "完本后绝不能再弹走向卡"
        done = [o for o in s.completed if o.status == "completed"]
        assert any(o.detail == "book_complete" for o in done), \
            f"step 应以 book_complete 完成: {[(o.status, o.detail) for o in s.completed]}"
        assert s.end_report and s.end_report.get("ok"), "plan 必须正常收尾（不卡死）"


# ============ 2. 无标记 → 照常派 judge + 弹卡（不误伤正常涌现） ============
def test_no_marker_normal_dispatch():
    with _Sandbox() as sb:
        sb.write_template("test-emerge", _emergence_template())
        dispatch, pause = _SpyDispatch(), _SpyPause()
        s = orc.run_command("test-emerge", "测试书", key="002",
                            script_runner=lambda *a, **k: 0,
                            judge_dispatch=dispatch, pause_handler=pause)
        assert dispatch.agents == ["novel-outline-planner"]
        assert pause.calls == 1
        assert s.end_report and s.end_report.get("ok")


# ============ 3. 标记存在但非涌现步 → 不短路（只精准跳涌现） ============
def test_marker_does_not_block_other_agents():
    """完本后其他 agent 步照跑（比如 summarizer 收尾步）——短路条件是
    「outline-planner + pause」双匹配，不是见标记就全跳。"""
    with _Sandbox() as sb:
        t = _emergence_template()
        t["steps"][0]["must_spawn_agent"] = "novel-summarizer"
        t["steps"][0].pop("pause_for_user")
        sb.write_template("test-emerge", t)
        sb.write_marker()
        dispatch = _SpyDispatch()
        s = orc.run_command("test-emerge", "测试书", key="002",
                            script_runner=lambda *a, **k: 0,
                            judge_dispatch=dispatch, pause_handler=_SpyPause())
        assert dispatch.agents == ["novel-summarizer"], \
            "完本标记只跳涌现步·其他 agent 步不受影响"
        assert s.end_report and s.end_report.get("ok")


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
