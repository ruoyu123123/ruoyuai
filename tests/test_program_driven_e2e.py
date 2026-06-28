#!/usr/bin/env python3
"""程序驱动端到端冒烟（v28）：用**真实** cluster-write / cluster-save-state 模板
跑通 orchestrator 全流程（fake runner 模拟脚本副作用 + fake dispatch 模拟 judge 产出，
不打 API）——证明两个主轨模板是机器可执行的（每个占位符可解析、每步产物可校验、
end_plan 全绿）。模板/driver 任何一边改坏，这里立刻红。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402
import plan_tracker as pt  # noqa: E402

REAL_PLANS = _ROOT / "core" / "claude-home" / "plans"


class _Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.templates = self.tmp / "templates"
        self.projects = self.tmp / "novels"
        for d in (self.templates, self.projects, self.tmp / "styles",
                  self.tmp / ".plans"):
            d.mkdir(parents=True)
        # 真实模板原样拷入沙盒
        for f in REAL_PLANS.glob("*.plan.json"):
            (self.templates / f.name).write_text(
                f.read_text(encoding="utf-8"), encoding="utf-8")
        self.proj = self.projects / "冒烟书"
        (self.proj / "_数据库").mkdir(parents=True)
        self._saved = {}

    def __enter__(self):
        for k, v in [("TEMPLATES_DIR", self.templates),
                     ("PROJECTS_DIR", self.projects),
                     ("STYLES_DIR", self.tmp / "styles"),
                     ("GLOBAL_PLANS_DIR", self.tmp / ".plans"),
                     ("ATTEST_KEY_PATH", self.tmp / ".plans" / ".attest_key")]:
            self._saved[k] = getattr(pt, k)
            setattr(pt, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            setattr(pt, k, v)


class _SideEffectRunner:
    """模拟每个真实脚本的产物副作用（脚本本身的正确性由各自测试负责，
    这里只验证「driver 按模板把它们串对了」）。"""

    def __init__(self, proj: Path):
        self.proj = proj
        self.cmds = []

    def __call__(self, cmd: str, *, repo_root=None, label="") -> int:
        self.cmds.append(cmd)
        db = self.proj / "_数据库"
        if "build_manifest.py" in cmd:
            (db / ".manifest").mkdir(parents=True, exist_ok=True)
            (db / ".manifest" / "ch_001.json").write_text("{}", encoding="utf-8")
        elif "style_injector.py" in cmd:
            return 1  # 无风格档 → 条件不适用（advisory 行必须不拦）
        elif "gen_writer.py" in cmd:
            d = self.proj / "章节" / "cluster_001_draft"
            d.mkdir(parents=True, exist_ok=True)
            (d / "cluster_001_draft.txt").write_text("正文" * 3000, encoding="utf-8")
            (d / "cluster_001_changes.json").write_text(
                json.dumps({"factual": {}, "self_eval": {}}), encoding="utf-8")
        elif "audit_hub.py" in cmd:
            (db / ".audit").mkdir(parents=True, exist_ok=True)
            (db / ".audit" / "cluster_001_audit.json").write_text("{}",
                                                                  encoding="utf-8")
        elif "chapter_splitter.py" in cmd:
            assert "--cluster-start-ch 1" in cmd, f"首 cluster 起始章应为 1: {cmd}"
            assert "--previous-pending-tail" not in cmd, "无 pending_tail 时整对应丢弃"
            (db / ".wal").mkdir(parents=True, exist_ok=True)
            (db / ".wal" / "splitter_cluster_001_decisions.json").write_text(
                json.dumps({"chapter_range": [1, 4], "pending_tail": {"exists": False}}),
                encoding="utf-8")
        elif "gen_chapter_titles.py" in cmd:
            assert "--chapters 1-4" in cmd, f"chapter_range 应 lazy 回填 1-4: {cmd}"
        elif "cluster_emergence_engine.py" in cmd:
            assert "--after-cluster cluster_001" in cmd, cmd
            (db / ".wal").mkdir(parents=True, exist_ok=True)
            (db / ".wal" / "cluster_002_emergence.json").write_text(
                json.dumps({"candidates_raw": 2}), encoding="utf-8")
        return 0


class _JudgeDispatch:
    """按 agent 写出模板声明的 judge_report_path 产物（含多轮 reading-reflector）。"""

    def __init__(self, proj: Path):
        self.proj = proj
        self.calls = []

    def __call__(self, agent, step, ctx):
        self.calls.append((agent, ctx.get("<round>")))
        data = {"free_notes": ""}
        if agent == "novel-reading-reflector":
            data = {"verdict": "pass", "new_issues_this_round": []}
        elif agent == "novel-outline-planner":
            data = {"candidates": [
                {"label": "走向A·主线推进", "_emergence_score": 0.9},
                {"label": "走向B·支线碰撞", "_emergence_score": 0.7}]}
        elif agent == "novel-summarizer":
            data = {"cluster_id": "cluster_001", "summary": "冒烟摘要",
                    "emotion": {"value": 2}}
        elif agent == "novel-foreshadower":
            data = {"judge_id": "foreshadower", "specific_findings": {}}
        elif agent == "novel-reflector":
            data = {"entries": []}
        elif agent == "novel-voice-checker":
            data = {"violations": [], "judge_report": {"judge_id": "vc"}}
        jrp = step.get("judge_report_path")
        out_raw = jrp.get(agent) if isinstance(jrp, dict) else jrp
        if out_raw:
            p = Path(orc.resolve_placeholders(out_raw, ctx))
            if not p.is_absolute():
                p = Path(ctx["project_root"]) / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        class _O:
            pass

        o = _O()
        o.data, o.ok, o.output_path = data, True, None
        return o


def test_real_cluster_write_template_machine_executable():
    with _Sandbox() as sb:
        runner = _SideEffectRunner(sb.proj)
        dispatch = _JudgeDispatch(sb.proj)
        s = orc.run_command("cluster-write", "冒烟书", key="001",
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok"), s.end_report
        done = [o for o in s.completed if o.status == "completed"]
        assert len(done) == 7  # 7 步全过
        # reading-reflector 连续 3 轮 clean 放行
        rr_rounds = [r for a, r in dispatch.calls if a == "novel-reading-reflector"]
        assert rr_rounds == [1, 2, 3]
        # 三 judge + voice 都派发了
        agents = {a for a, _ in dispatch.calls}
        assert {"novel-voice-checker", "novel-foreshadower", "novel-reflector",
                "novel-summarizer"} <= agents
        # 创意 wrapper 没被派成 judge
        assert "novel-writer" not in agents and "novel-chapter-splitter" not in agents
        # splitter 跑了真实形态命令
        assert any("chapter_splitter.py" in c and "--mode ecas_freestyle" in c
                   for c in runner.cmds)


def test_real_cluster_save_state_template_machine_executable():
    with _Sandbox() as sb:
        # 前置：cluster-write 的产物已存在（draft + changes + 切章 WAL）
        d = sb.proj / "章节" / "cluster_001_draft"
        d.mkdir(parents=True)
        (d / "cluster_001_draft.txt").write_text("正文" * 3000, encoding="utf-8")
        (d / "cluster_001_changes.json").write_text(
            json.dumps({"factual": {}, "self_eval": {}}), encoding="utf-8")
        runner = _SideEffectRunner(sb.proj)
        dispatch = _JudgeDispatch(sb.proj)
        s = orc.run_command("cluster-save-state", "冒烟书", key="001",
                            auto_pilot=True,  # 走向卡显式全自动取第一候选
                            script_runner=runner, judge_dispatch=dispatch)
        assert s.end_report.get("ok"), s.end_report
        done = [o for o in s.completed if o.status == "completed"]
        assert len(done) == 14  # 14 步全过（2026-06-28：+archivist judge step5 + apply-archive step6）
        # archivist 判断步真被派发（factual 权威源·非 writer 自报）
        assert "novel-archivist" in {a for a, _ in dispatch.calls}
        # step1 WAL 骨架由 driver 建（Claude 流程里 Claude 建）
        wal = sb.proj / "_数据库" / ".wal" / "cluster_001_save_state.json"
        assert wal.exists() and json.loads(wal.read_text(encoding="utf-8")) == {}
        # 走向卡：auto_pilot 取 _emergence_score 第一候选 + 写回脚本被调
        choice = json.loads((sb.proj / "_数据库" / ".wal"
                             / "cluster_002_user_choice.json")
                            .read_text(encoding="utf-8"))
        assert choice["answer"]["label"] == "走向A·主线推进"
        assert any("cluster_choice_apply.py" in c and "--next-key 002" in c
                   for c in runner.cmds)
        # save-state 全链脚本按模板顺序真的跑了（抽查关键几个）
        joined = "\n".join(runner.cmds)
        for must in ("db_schema_validate.py", "--apply-cluster-changes 001",
                     "apply_archive.py", "run_cross_cluster_aggregates.py",
                     "--git-commit-cluster 001", "cluster_emergence_engine.py"):
            assert must in joined, f"模板脚本未被执行: {must}"


def test_cluster_choice_apply_real_script():
    """cluster_choice_apply.py 真跑（非 mock）：用户选择 → 事件簇.json upsert
    + status=in_progress（白名单成员·根治 active 静默 off 坑）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import cluster_choice_apply as cca
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_数据库" / ".wal").mkdir(parents=True)
        (root / "_数据库" / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "status": "done"}]},
            ensure_ascii=False), encoding="utf-8")
        choice = root / "_数据库" / ".wal" / "cluster_002_user_choice.json"
        choice.write_text(json.dumps({"step": 11, "answer": {
            "title": "新走向", "scope_summary": "...",
            "estimated_chapters": 5,  # v27 禁预锁 → 必须被剥掉
            "scene_storyboard": [{"scene": 1}]}}, ensure_ascii=False),
            encoding="utf-8")
        result = cca.apply_choice(root, "002", choice)
        assert result["cluster_id"] == "cluster_002" and not result["replaced"]
        data = json.loads((root / "_数据库" / "事件簇.json")
                          .read_text(encoding="utf-8"))
        c2 = next(c for c in data["clusters"] if c["cluster_id"] == "cluster_002")
        assert c2["status"] == "in_progress"      # 白名单成员
        assert "estimated_chapters" not in c2      # v27 fluid 不预锁
        assert c2["narrative_mode"] == "linear"
        # 同 key 再 apply → 替换不重复
        result2 = cca.apply_choice(root, "002", choice)
        assert result2["replaced"] is True
        data2 = json.loads((root / "_数据库" / "事件簇.json")
                           .read_text(encoding="utf-8"))
        assert len([c for c in data2["clusters"]
                    if c["cluster_id"] == "cluster_002"]) == 1


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
