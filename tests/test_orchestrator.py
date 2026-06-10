#!/usr/bin/env python3
"""orchestrator.py 测试（程序驱动 M3）——纯 mock，不打真 API、不碰真 plans 目录。

覆盖：占位符解析 / data_flow 回填 / 退出码分支 / 端到端走步 / 断点续跑 /
脚本失败停步可续 / 停顿点(handler + auto_pilot) / ROUND 循环 / end_plan 校验。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402


def test_windows_path_survives_tokenize_roundtrip():
    """🔴 Windows 路径 bug 回归（真 end-to-end 暴露）：{project_root} 解析成含反斜杠的
    Windows 路径后，_tokenize_then_resolve 重组 + default_script_runner 二次 shlex.split
    (posix=True) 不得把反斜杠当转义吃掉（C:\\Users → C:Users）。含反斜杠 token 须引号包裹。"""
    winpath = r"C:\Users\ruoyu\AppData\Local\Temp\e2e_full"
    cmd = orc._tokenize_then_resolve(
        "python core/scripts/build_manifest.py {project_root} 1",
        {"project_root": winpath})
    assert '"' + winpath + '"' in cmd, f"含反斜杠路径未被引号包裹: {cmd}"
    toks = orc._strip_python_prefix(cmd)
    assert winpath in toks, f"二次 split 破坏了 Windows 路径反斜杠: {toks}"
    # 含空格+反斜杠的 Windows 路径也要保住
    sp = r"C:\Program Files\ruoyu data"
    cmd2 = orc._tokenize_then_resolve("python x.py {project_root}", {"project_root": sp})
    assert sp in orc._strip_python_prefix(cmd2), "含空格+反斜杠路径被破坏"


def test_forward_slash_path_unquoted_still_works():
    """非反斜杠路径（Unix/forward-slash）不受影响——token 正确。"""
    up = "/home/user/proj"
    cmd = orc._tokenize_then_resolve("python x.py {project_root} 1", {"project_root": up})
    toks = orc._strip_python_prefix(cmd)
    assert up in toks and "1" in toks


# ============ 测试隔离：plan_tracker 目录全部指向临时区 ============
class _Sandbox:
    """monkeypatch plan_tracker 模块常量 → 临时目录（不污染真实 plans/模板）。"""

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


def _basic_template(**overrides):
    t = {
        "command": "test-flow", "total_steps": 2,
        "required_steps": [1, 2], "optional_steps": [],
        "steps": [
            {"n": 1, "name": "run-script", "required": True,
             "skip_output_allowed": True, "expected_outputs": [],
             "scripts": ["# 注释行要被跳过",
                         "python core/scripts/fake.py {project_root} --key {key}"]},
            {"n": 2, "name": "judge-step", "required": True,
             "skip_output_allowed": True, "expected_outputs": [],
             "must_spawn_agent": "novel-summarizer",
             "agent_input": {"MODE": "cluster", "CLUSTER_ID": "cluster_{key}"},
             "judge_report_path": "_数据库/.wal/cluster_{key}_summary.json"},
        ],
    }
    t.update(overrides)
    return t


class _FakeDispatch:
    """记录派发 + 落一个假 JudgeReport 文件。script 可指定逐次返回的 data。"""

    def __init__(self, datas=None):
        self.calls = []
        self.datas = list(datas or [])

    def __call__(self, agent, step, ctx):
        self.calls.append({"agent": agent, "n": step.get("n"),
                           "input": dict(step.get("agent_input") or {}),
                           "round": ctx.get("<round>")})
        data = self.datas.pop(0) if self.datas else {"ok": True}
        jrp = step.get("judge_report_path")
        out_raw = jrp.get(agent) if isinstance(jrp, dict) else jrp
        path = None
        if out_raw:
            p = Path(orc.resolve_placeholders(out_raw, ctx))
            if not p.is_absolute():
                p = Path(ctx["project_root"]) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            path = p

        class _O:  # 模拟 JudgeOutcome 鸭子型
            pass

        o = _O()
        o.data, o.ok, o.output_path = data, True, path
        return o


class _FakeRunner:
    def __init__(self, returncodes=None):
        self.cmds = []
        self.rcs = list(returncodes or [])

    def __call__(self, cmd, *, repo_root=None, label=""):
        self.cmds.append(cmd)
        return self.rcs.pop(0) if self.rcs else 0


# ============ 单元：占位符 / dataflow / 退出码 ============
def test_resolve_placeholders_project_root_and_angle():
    ctx = {"project_root": "D:/x/书", "<range>": "1-5"}
    out = orc.resolve_placeholders("python a.py {project_root} --r <range>", ctx)
    assert out == "python a.py D:/x/书 --r 1-5"


def test_unresolved_angle_detector():
    assert orc.unresolved_angle_placeholders("a <range_from_wal> b <start>") == \
        ["<range_from_wal>", "<start>"]
    assert orc.unresolved_angle_placeholders("if x<3 and y > 2") == []  # 比较符不误报


def test_dataflow_json_field_and_dotted():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "wal.json").write_text(
            json.dumps({"chapter_range": "12-15", "meta": {"start": 12}}),
            encoding="utf-8")
        ctx = {"project_root": str(root)}
        step = {"data_flow": {
            "<range>": {"source_json": "wal.json", "field": "chapter_range"},
            "<start>": {"source_json": "wal.json", "field": "meta.start"},
            "<mode>": {"literal": "freestyle"}}}
        orc.load_dataflow(step, ctx)
        assert ctx["<range>"] == "12-15" and ctx["<start>"] == 12
        assert ctx["<mode>"] == "freestyle"


def test_dataflow_missing_file_errors_unless_optional():
    ctx = {"project_root": tempfile.mkdtemp()}
    step = {"data_flow": {"<x>": {"source_json": "不存在.json", "field": "f"}}}
    try:
        orc.load_dataflow(step, ctx)
        assert False
    except orc.OrchestratorError:
        pass
    step_opt = {"data_flow": {"<x>": {"source_json": "不存在.json", "field": "f",
                                      "optional": True, "default": ""}}}
    orc.load_dataflow(step_opt, ctx)
    assert ctx["<x>"] == ""


def test_exit_code_action_default_and_custom():
    assert orc._exit_code_action({}, 0) == "ok"
    assert orc._exit_code_action({}, 1) == "fail"
    step = {"control_flow": {"exit_codes": {
        "0": "ok", "1": "ok", "2": "dispatch:novel-validator-checker", "3": "fail"}}}
    assert orc._exit_code_action(step, 1) == "ok"
    assert orc._exit_code_action(step, 2) == "dispatch:novel-validator-checker"
    assert orc._exit_code_action(step, 3) == "fail"


def test_tokenize_then_resolve_quotes_spaced_path():
    ctx = {"project_root": "D:/有 空格/书"}
    out = orc._tokenize_then_resolve("python a.py {project_root}/章节", ctx)
    assert '"D:/有 空格/书/章节"' in out


# ============ 端到端 ============
def test_e2e_happy_path():
    with _Sandbox() as sb:
        sb.write_template("test-flow", _basic_template())
        runner, dispatch = _FakeRunner(), _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report and s.end_report.get("ok")
        assert [o.status for o in s.completed] == ["completed", "completed"]
        # 脚本：注释行被跳过 · {project_root}/{key} 已解析
        assert len(runner.cmds) == 1
        assert "--key 001" in runner.cmds[0] and "fake.py" in runner.cmds[0]
        assert "{project_root}" not in runner.cmds[0]
        # 判断派发：agent_input 透传
        assert dispatch.calls == [{"agent": "novel-summarizer", "n": 2,
                                   "input": {"MODE": "cluster",
                                             "CLUSTER_ID": "cluster_001"},
                                   "round": None}]


def test_e2e_resume_skips_completed():
    with _Sandbox() as sb:
        sb.write_template("test-flow", _basic_template())
        plan_id = pt.create_plan("test-flow", "测试书", key="001")
        pt.step_complete(plan_id, 1, skip_output=True)  # 预完成 step1
        runner, dispatch = _FakeRunner(), _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            resume_plan_id=plan_id,
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.plan_id == plan_id
        assert [o.status for o in s.completed] == ["skipped", "completed"]
        assert runner.cmds == []  # step1 没重跑（断点续跑）
        assert len(dispatch.calls) == 1


def test_e2e_script_failure_stops_then_resumable():
    with _Sandbox() as sb:
        sb.write_template("test-flow", _basic_template())
        runner = _FakeRunner(returncodes=[7])
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
            assert False, "退出码 7 必须 fail-stop"
        except orc.OrchestratorError as e:
            assert "退出码 7" in str(e)
        # 修复后续跑：用 find_active_plans 找到停着的 plan
        actives = [a for a in pt.find_active_plans()
                   if a["plan"].get("command") == "test-flow"]
        assert len(actives) == 1
        plan_id = actives[0]["plan"]["id"]
        runner2, dispatch2 = _FakeRunner(), _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            resume_plan_id=plan_id,
                            script_runner=runner2, judge_dispatch=dispatch2)
        assert s.end_report.get("ok")
        assert len(runner2.cmds) == 1  # step1 重跑（之前没完成）


def test_e2e_unresolved_placeholder_fails_before_run():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["scripts"] = ["python a.py --range <range_from_splitter_wal>"]
        sb.write_template("test-flow", t)
        runner = _FakeRunner()
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
            assert False
        except orc.OrchestratorError as e:
            assert "占位符未解析" in str(e)
        assert runner.cmds == []  # 绝不带着占位符跑命令


def test_e2e_dataflow_feeds_script():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["data_flow"] = {"<range>": {
            "source_json": "_数据库/wal.json", "field": "chapter_range"}}
        t["steps"][0]["scripts"] = ["python a.py --chapters <range>"]
        sb.write_template("test-flow", t)
        (sb.proj_root / "_数据库" / "wal.json").write_text(
            json.dumps({"chapter_range": "3-7"}), encoding="utf-8")
        runner = _FakeRunner()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert runner.cmds[0] == "a.py --chapters 3-7" or \
            "--chapters 3-7" in runner.cmds[0]


def test_e2e_exit_code_dispatch_branch():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["control_flow"] = {"exit_codes": {
            "0": "ok", "2": "dispatch:novel-validator-checker"}}
        sb.write_template("test-flow", t)
        runner = _FakeRunner(returncodes=[2])
        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        agents = [c["agent"] for c in dispatch.calls]
        assert "novel-validator-checker" in agents  # 退出码 2 → 派单


# ============ 停顿点 ============
def test_pause_handler_receives_options_and_answer_recorded():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["pause_for_user"] = {
            "type": "choice", "source": "_数据库/candidates.json",
            "options_field": "candidates",
            "answer_artifact": "_数据库/.wal/answer.json"}
        sb.write_template("test-flow", t)
        (sb.proj_root / "_数据库" / "candidates.json").write_text(
            json.dumps({"candidates": [{"label": "A"}, {"label": "B"}]},
                       ensure_ascii=False), encoding="utf-8")
        seen = {}

        def handler(step, spec, options):
            seen["options"] = options
            return options[1]

        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(),
                            judge_dispatch=_FakeDispatch(), pause_handler=handler)
        assert s.end_report.get("ok")
        assert [o["label"] for o in seen["options"]] == ["A", "B"]
        saved = json.loads((sb.proj_root / "_数据库" / ".wal" / "answer.json")
                           .read_text(encoding="utf-8"))
        assert saved["answer"] == {"label": "B"}


def test_pause_auto_pilot_takes_first_candidate():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["pause_for_user"] = {
            "type": "choice", "source": "_数据库/candidates.json",
            "options_field": "candidates",
            "answer_artifact": "_数据库/.wal/answer.json"}
        sb.write_template("test-flow", t)
        (sb.proj_root / "_数据库" / "candidates.json").write_text(
            json.dumps({"candidates": [{"label": "第一"}, {"label": "第二"}]},
                       ensure_ascii=False), encoding="utf-8")
        s = orc.run_command("test-flow", "测试书", key="001", auto_pilot=True,
                            script_runner=_FakeRunner(),
                            judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        saved = json.loads((sb.proj_root / "_数据库" / ".wal" / "answer.json")
                           .read_text(encoding="utf-8"))
        assert saved["answer"] == {"label": "第一"}  # 引擎排序第一候选


def test_pause_without_handler_or_autopilot_errors():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["pause_for_user"] = {"type": "integer", "prompt": "几个?"}
        sb.write_template("test-flow", t)
        try:
            orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(),
                            judge_dispatch=_FakeDispatch())
            assert False, "停顿点不允许静默跳过"
        except orc.OrchestratorError as e:
            assert "停顿点" in str(e)


# ============ ROUND 循环 ============
def test_round_loop_until_consecutive_clean():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["must_spawn_agent"] = "novel-reading-reflector"
        t["steps"][1]["judge_report_path"] = \
            "_数据库/.reading_reflection/cluster_{key}_round_<round>.json"
        t["steps"][1]["control_flow"] = {"round_loop": {
            "agent": "novel-reading-reflector", "max_rounds": 5,
            "consecutive_clean": 2, "pass_field": "verdict", "pass_value": "pass"}}
        sb.write_template("test-flow", t)
        dispatch = _FakeDispatch(datas=[
            {"verdict": "fail", "new_issues_this_round": [{"id": "RR_001"}]},
            {"verdict": "pass", "new_issues_this_round": []},
            {"verdict": "pass", "new_issues_this_round": []},
        ])
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(), judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        rounds = [c["round"] for c in dispatch.calls]
        assert rounds == [1, 2, 3]  # fail → clean ×2 连续 → 放行


def test_round_loop_soft_release_at_max_rounds():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["must_spawn_agent"] = "novel-reading-reflector"
        t["steps"][1]["judge_report_path"] = "_数据库/.wal/r_<round>.json"
        t["steps"][1]["control_flow"] = {"round_loop": {
            "agent": "novel-reading-reflector", "max_rounds": 3,
            "consecutive_clean": 3}}
        sb.write_template("test-flow", t)
        dispatch = _FakeDispatch(datas=[
            {"verdict": "fail", "new_issues_this_round": [1]}] * 3)
        # 3 轮全 fail → 软预算放行不抛错（advisory 非门禁·北极星⑤）
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(), judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        assert len(dispatch.calls) == 3


# ============ v28 增量能力 ============
def test_lazy_dataflow_same_step_producer_consumer():
    """同 step 内：脚本 1 产 WAL → 脚本 2 消费（splitter → gen_chapter_titles 模式）。
    eager 解析会在脚本 1 跑之前因 WAL 不存在而崩——必须 just-in-time。"""
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["data_flow"] = {"<range_dash>": {
            "source_json": "_数据库/.wal/decisions.json", "field": "chapter_range",
            "join_range": True}}
        t["steps"][0]["scripts"] = [
            "python splitter.py {project_root}",
            "python titles.py --chapters <range_dash>"]
        sb.write_template("test-flow", t)
        wal = sb.proj_root / "_数据库" / ".wal" / "decisions.json"

        class _ProducerRunner(_FakeRunner):
            def __call__(self, cmd, *, repo_root=None, label=""):
                rc = super().__call__(cmd, repo_root=repo_root, label=label)
                if "splitter.py" in cmd:  # 模拟 splitter 产 WAL
                    wal.parent.mkdir(parents=True, exist_ok=True)
                    wal.write_text(json.dumps({"chapter_range": [12, 15]}),
                                   encoding="utf-8")
                return rc

        runner = _ProducerRunner()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert "--chapters 12-15" in runner.cmds[1]


def test_advisory_script_line_nonzero_continues():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["scripts"] = ["? python style_injector.py {project_root} 1",
                                    "python real.py"]
        sb.write_template("test-flow", t)
        runner = _FakeRunner(returncodes=[1, 0])  # advisory 行 exit 1 → 不拦
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=runner, judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok") and len(runner.cmds) == 2


def test_touch_outputs_creates_placeholder():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][0]["touch_outputs"] = ["_数据库/.reading_reflection/.placeholder"]
        sb.write_template("test-flow", t)
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(),
                            judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert (sb.proj_root / "_数据库" / ".reading_reflection"
                / ".placeholder").exists()


def test_dict_judge_report_path_per_agent():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["must_spawn_agent"] = ["novel-reflector", "novel-summarizer"]
        t["steps"][1]["judge_report_path"] = {
            "novel-reflector": "_数据库/.wal/cluster_{key}_reflection.json",
            "novel-summarizer": "_数据库/.wal/cluster_{key}_summary.json"}
        sb.write_template("test-flow", t)
        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_FakeRunner(), judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        assert [c["agent"] for c in dispatch.calls] == \
            ["novel-reflector", "novel-summarizer"]


def test_agent_executor_script_skips_judge_dispatch():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["agent_executor"] = "script"
        t["steps"][1]["scripts"] = ["python gen_writer.py --cluster 1"]
        # end_plan 校验 judge_report_path 声明文件 → 让脚本产出它
        sb.write_template("test-flow", t)
        report = (sb.proj_root / "_数据库" / ".wal" / "cluster_001_summary.json")

        class _R(_FakeRunner):
            def __call__(self, cmd, *, repo_root=None, label=""):
                rc = super().__call__(cmd, repo_root=repo_root, label=label)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("{}", encoding="utf-8")
                return rc

        dispatch = _FakeDispatch()
        s = orc.run_command("test-flow", "测试书", key="001",
                            script_runner=_R(), judge_dispatch=dispatch)
        assert s.end_report.get("ok")
        assert dispatch.calls == []  # 创意 wrapper 不派 judge


def test_empty_optional_flag_pair_dropped():
    ctx = {"project_root": "D:/x", "<prev_pending_tail>": ""}
    out = orc._tokenize_then_resolve(
        "python splitter.py --draft a.txt --previous-pending-tail <prev_pending_tail>",
        ctx)
    assert "--previous-pending-tail" not in out and "--draft a.txt" in out


def test_after_pause_scripts_run_with_answer():
    with _Sandbox() as sb:
        t = _basic_template()
        t["steps"][1]["pause_for_user"] = {
            "type": "choice", "source": "_数据库/c.json", "options_field": "candidates",
            "answer_artifact": "_数据库/.wal/choice.json"}
        t["steps"][1]["after_pause_scripts"] = [
            "python apply.py {project_root} --choice _数据库/.wal/choice.json"]
        sb.write_template("test-flow", t)
        (sb.proj_root / "_数据库" / "c.json").write_text(
            json.dumps({"candidates": [{"label": "X"}]}), encoding="utf-8")
        runner = _FakeRunner()
        s = orc.run_command("test-flow", "测试书", key="001", auto_pilot=True,
                            script_runner=runner, judge_dispatch=_FakeDispatch())
        assert s.end_report.get("ok")
        assert any("apply.py" in c for c in runner.cmds)  # 选择落定后 apply 跑了


def test_prime_cluster_context_first_cluster():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_数据库").mkdir()
        ctx = {"project_root": str(root)}
        orc.prime_cluster_context(ctx, root, "001")
        assert ctx["<cluster_id>"] == "cluster_001"
        assert ctx["<cluster_num>"] == 1
        assert ctx["<prev_key>"] == "" and ctx["<prev_pending_tail>"] == ""
        assert ctx["<cluster_start_ch>"] == 1  # 新书首 cluster 从第 1 章起


def test_prime_cluster_context_from_prev_range():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_数据库").mkdir()
        (root / "_数据库" / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}]},
            ensure_ascii=False), encoding="utf-8")
        tail_dir = root / "章节" / "cluster_001_draft"
        tail_dir.mkdir(parents=True)
        (tail_dir / "cluster_001_pending_tail.txt").write_text("尾", encoding="utf-8")
        ctx = {"project_root": str(root)}
        orc.prime_cluster_context(ctx, root, "002")
        assert ctx["<cluster_start_ch>"] == 6  # 上一 cluster hi+1（cluster_lookup 权威）
        assert ctx["<prev_key>"] == "001"
        assert ctx["<prev_pending_tail>"].endswith("cluster_001_pending_tail.txt")


# ============ frozen in-process runner（根因 F）+ mojibake env（根因 H） ============
def test_default_runner_dev_passes_utf8_env(monkeypatch=None):
    """dev 子进程必须传 PYTHONIOENCODING=utf-8（GBK Windows 防日志乱码·根因 H）。"""
    import subprocess as sp
    captured = {}

    def _fake_run(full, **kw):
        captured["env"] = kw.get("env")

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    saved = sp.run
    saved_frozen = getattr(sys, "frozen", False)
    sys.frozen = False
    orc.subprocess.run = _fake_run
    try:
        rc = orc.default_script_runner("python core/scripts/x.py --a 1")
    finally:
        orc.subprocess.run = saved
        sys.frozen = saved_frozen
    assert rc == 0
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"


def test_frozen_uses_in_process_runner_not_subprocess():
    """frozen 时 default_script_runner 走进程内 runner（不起子进程·根因 F）。"""
    saved_frozen = getattr(sys, "frozen", False)
    calls = {"subprocess": 0, "inproc": 0}

    def _fake_run(*a, **k):
        calls["subprocess"] += 1

        class _P:
            returncode = 0
            stdout = stderr = ""
        return _P()

    def _fake_inproc(tokens, **kw):
        calls["inproc"] += 1
        return 0

    saved_sp = orc.subprocess.run
    saved_ip = orc.run_script_in_process
    sys.frozen = True
    orc.subprocess.run = _fake_run
    orc.run_script_in_process = _fake_inproc
    try:
        orc.default_script_runner("python core/scripts/x.py")
    finally:
        orc.subprocess.run = saved_sp
        orc.run_script_in_process = saved_ip
        sys.frozen = saved_frozen
    assert calls["inproc"] == 1 and calls["subprocess"] == 0


def test_run_script_in_process_calls_main_and_captures_systemexit():
    """进程内 runner 真 import 有 main() 的脚本、调 main()、捕 SystemExit 取退出码、
    还原 argv（证明 frozen 机制可用·无需建 exe）。cluster_choice_apply.py 有 main()，
    缺参 → argparse SystemExit(2)。"""
    saved_argv = list(sys.argv)
    rc = orc.run_script_in_process(["core/scripts/cluster_choice_apply.py"],
                                   repo_root=orc.REPO_ROOT, label="t")
    assert rc == 2                       # argparse 缺必填参 → SystemExit(2)
    assert sys.argv == saved_argv        # 退出后 argv 必还原（不污染后续步）


def test_run_script_in_process_no_main_returns_3():
    """无 main() 的脚本（cluster_lookup 用 inline __main__ 自测）→ 返回 3 明确失败，
    不静默成功。"""
    rc = orc.run_script_in_process(["core/scripts/cluster_lookup.py"],
                                   repo_root=orc.REPO_ROOT, label="t")
    assert rc == 3


def test_run_script_in_process_import_fail_returns_3():
    rc = orc.run_script_in_process(["core/scripts/__nonexistent_xyz.py"],
                                   repo_root=orc.REPO_ROOT, label="t")
    assert rc == 3


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
