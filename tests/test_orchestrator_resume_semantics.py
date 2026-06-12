#!/usr/bin/env python3
"""orchestrator 续跑语义回归测试（复验批次4 · 2026-06-13）

钉死两条契约：
  1. paused 续跑防重烧 API：resume 回到停顿步时若声明的 JudgeReport 已落盘且
     可解析 → 不再重派 judge（直接拿既有候选弹卡）。fresh run 不复用（用户期望
     重新生成）；报告损坏（非法 JSON）→ 照常重派。
  2. tampered plan resume 先验：防伪校验不过 → 一步不跑直接 OrchestratorError
     （否则跑若干步烧 API 后才在 step_complete 写盘时炸）。

沙箱范式照搬 tests/test_orchestrator_book_complete.py。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402


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


class _SpyDispatch:
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
            p.write_text(json.dumps({"candidates": ["A", "B"]}), encoding="utf-8")
            path = p

        class _O:
            pass

        o = _O()
        o.data, o.ok, o.output_path = {"ok": True}, True, path
        return o


class _SpyPause:
    def __init__(self, answer="候选A"):
        self.calls = 0
        self.answer = answer

    def __call__(self, step, spec, options):
        self.calls += 1
        if self.answer == "__pause__":
            return None                  # 模拟用户没应答 → orchestrator 暂停退出
        return self.answer


def _pause_template():
    return {"command": "test-resume", "total_steps": 1,
            "required_steps": [1], "optional_steps": [],
            "steps": [{
                "n": 1, "name": "judge+选卡", "required": True,
                "skip_output_allowed": True, "expected_outputs": [],
                "must_spawn_agent": "novel-outline-planner",
                "judge_report_path": "_数据库/.wal/judge_out.json",
                "pause_for_user": {"type": "choice",
                                   "inline_options": ["候选A", "候选B"]},
            }]}


# ============ 1. resume + 报告已在 → 不重派 judge ============
def test_resume_reuses_existing_judge_report():
    with _Sandbox() as sb:
        sb.write_template("test-resume", _pause_template())
        # 第一跑：派 judge（落报告）→ 用户没应答 → 暂停
        d1, p1 = _SpyDispatch(), _SpyPause(answer="__pause__")
        s1 = orc.run_command("test-resume", "测试书", key="002",
                             script_runner=lambda *a, **k: 0,
                             judge_dispatch=d1, pause_handler=p1)
        assert s1.paused_at == 1 and d1.agents == ["novel-outline-planner"]
        # 续跑：报告已在 → 绝不重派 judge·直接弹卡取答案·正常收尾
        d2, p2 = _SpyDispatch(), _SpyPause(answer="候选A")
        s2 = orc.run_command("test-resume", "测试书", key="002",
                             resume_plan_id=s1.plan_id,
                             script_runner=lambda *a, **k: 0,
                             judge_dispatch=d2, pause_handler=p2)
        assert d2.agents == [], "续跑回停顿步绝不能重派 judge（重烧 API）"
        assert p2.calls == 1
        assert s2.end_report and s2.end_report.get("ok")


def test_fresh_run_never_reuses():
    """fresh run（非 resume）即便 WAL 里有同名旧报告也照常派 judge。"""
    with _Sandbox() as sb:
        sb.write_template("test-resume", _pause_template())
        wal = sb.proj_root / "_数据库" / ".wal"
        wal.mkdir(parents=True, exist_ok=True)
        (wal / "judge_out.json").write_text('{"candidates": ["旧"]}',
                                            encoding="utf-8")
        d, p = _SpyDispatch(), _SpyPause()
        orc.run_command("test-resume", "测试书", key="002",
                        script_runner=lambda *a, **k: 0,
                        judge_dispatch=d, pause_handler=p)
        assert d.agents == ["novel-outline-planner"], \
            "fresh run 不复用旧报告（用户期望重新生成）"


def test_resume_with_corrupt_report_redispatches():
    """resume 但报告损坏（非法 JSON）→ 照常重派 judge。"""
    with _Sandbox() as sb:
        sb.write_template("test-resume", _pause_template())
        d1, p1 = _SpyDispatch(), _SpyPause(answer="__pause__")
        s1 = orc.run_command("test-resume", "测试书", key="002",
                             script_runner=lambda *a, **k: 0,
                             judge_dispatch=d1, pause_handler=p1)
        rp = sb.proj_root / "_数据库" / ".wal" / "judge_out.json"
        rp.write_text("{broken json", encoding="utf-8")
        d2, p2 = _SpyDispatch(), _SpyPause()
        s2 = orc.run_command("test-resume", "测试书", key="002",
                             resume_plan_id=s1.plan_id,
                             script_runner=lambda *a, **k: 0,
                             judge_dispatch=d2, pause_handler=p2)
        assert d2.agents == ["novel-outline-planner"], "损坏报告必须重派"
        assert s2.end_report and s2.end_report.get("ok")


# ============ 2. tampered plan resume 先验 ============
def test_tampered_plan_resume_rejected_before_any_step():
    with _Sandbox() as sb:
        sb.write_template("test-resume", _pause_template())
        d1, p1 = _SpyDispatch(), _SpyPause(answer="__pause__")
        s1 = orc.run_command("test-resume", "测试书", key="002",
                             script_runner=lambda *a, **k: 0,
                             judge_dispatch=d1, pause_handler=p1)
        # 外部篡改 plan 文件（绕过 plan_tracker 改 status）
        ppath = pt._find_plan_path(s1.plan_id)
        data = json.loads(ppath.read_text(encoding="utf-8"))
        data["steps"][0]["status"] = "completed"     # 伪造完成
        ppath.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        assert pt.verify_plan(s1.plan_id) == "tampered"
        d2 = _SpyDispatch()
        runner_calls = []
        try:
            orc.run_command("test-resume", "测试书", key="002",
                            resume_plan_id=s1.plan_id,
                            script_runner=lambda *a, **k: runner_calls.append(a) or 0,
                            judge_dispatch=d2, pause_handler=_SpyPause())
            assert False, "tampered plan resume 必须拒跑"
        except orc.OrchestratorError as e:
            assert "防伪校验" in str(e)
        assert d2.agents == [] and runner_calls == [], \
            "先验必须在任何 step 执行前拦截（一步不跑不烧 API）"


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
