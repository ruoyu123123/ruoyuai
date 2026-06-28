#!/usr/bin/env python3
"""outline.plan.json 程序驱动 e2e（阶段2 创建书籍·dev·不调 API）。

fake script_runner/judge_dispatch + auto_pilot pause → 验 orchestrator 能机械驱动 12 步
DAG 全过（字段格式/data_flow/pause spec/judge_report_path 命名正确）。真 exe e2e 另跑。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402

_SUBSYS = json.loads((_ROOT / "core" / "claude-home" / "plans" / "outline.plan.json")
                     .read_text(encoding="utf-8"))
_34_FILES = next(s["expected_outputs"] for s in _SUBSYS["steps"]
                 if s.get("name") == "scaffold-34-subsystems")


def _fake_runner(project_root: Path):
    """模拟各步脚本：产出 expected_outputs + pause source·不真跑（不调 API）。"""
    db = project_root / "_数据库"

    def runner(cmd, *, repo_root=None, label="step"):
        (db / ".wal").mkdir(parents=True, exist_ok=True)
        (db / ".research_cache").mkdir(parents=True, exist_ok=True)
        if "--emit-style-options" in cmd:
            (db / ".wal" / "style_options.json").write_text(json.dumps(
                {"styles": [{"name": "测试风格", "title": "测试风格", "description": "x"}]},
                ensure_ascii=False), encoding="utf-8")
        elif "init_project.py" in cmd and "--style" in cmd:
            (db / "作者风格.json").write_text("{}", encoding="utf-8")
            (db / "作者风格_skill.md").write_text("# skill", encoding="utf-8")
        elif "--mode brainstorm" in cmd:
            (db / ".wal" / "inspiration_cards.json").write_text(json.dumps(
                {"cards": [{"title": "卡1", "logline": "梗概"}]},
                ensure_ascii=False), encoding="utf-8")
        elif "scaffold_subsystems.py emit" in cmd:
            for rel in _34_FILES:
                f = project_root / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                if not f.exists():
                    f.write_text("{}", encoding="utf-8")
        elif "--mode volume_arc" in cmd:
            # 🔴 2026-06-27 C03：outline plan-end 现 hard content_check（载荷非空）·volume_arc
            #   产物必须填 大势卡.major_events + 事件簇.clusters[0].scene_storyboard（真 outline
            #   由 gen-model 填·此 fake 须同形态否则地板正确拦下「outline 未填载荷」）。
            (db / "大势卡.json").write_text(json.dumps(
                {"volumes": [{"vol": 1}],
                 "major_events": [{"id": "ME-V1-01", "volume": 1,
                                   "is_volume_finale": True}]},
                ensure_ascii=False), encoding="utf-8")
            (db / "事件簇.json").write_text(json.dumps(
                {"clusters": [{"cluster_id": "cluster_001",
                               "scene_storyboard": [{"scene": 1, "summary": "灾难开场"}]}]},
                ensure_ascii=False), encoding="utf-8")
        elif "world_seed_init.py" in cmd:
            # 🔴 C03：world_seed_init 播 涟漪规则（载荷·让 world_evolution_engine 点火）。
            (db / "涟漪规则.json").write_text(json.dumps(
                {"ripple_rules": [{"id": "R1", "trigger_type": "auto_tick"}]},
                ensure_ascii=False), encoding="utf-8")
            (db / "世界状态.json").write_text(json.dumps(
                {"state": {"day": 1}}, ensure_ascii=False), encoding="utf-8")
        elif "cluster_choice_apply.py" in cmd:
            pass  # 事件簇.json 已存在
        # scaffold verify / db_schema_validate / research → no-op 成功
        return 0
    return runner


def _fake_judge(project_root: Path):
    def dispatch(agent, step, ctx):
        jrp = step.get("judge_report_path")
        if jrp:
            p = project_root / orc.resolve_placeholders(jrp, ctx)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"agent": agent, "scope_summary": "x",
                                     "scene_storyboard": []}, ensure_ascii=False),
                         encoding="utf-8")

        class O:
            data, ok, output_path = {}, True, None
        return O()
    return dispatch


def test_outline_plan_drives_12_steps():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        (proj / "_数据库" / ".wal").mkdir(parents=True)
        (proj / "_数据库" / ".wal" / "book_meta.json").write_text(
            json.dumps({"topic": "修仙"}, ensure_ascii=False), encoding="utf-8")
        summary = orc.run_command(
            "outline", str(proj), auto_pilot=True,
            script_runner=_fake_runner(proj), judge_dispatch=_fake_judge(proj),
            pause_handler=None)
        # auto_pilot 全自动·应跑完不停在 pause
        assert summary.paused_at is None, f"卡在 step {summary.paused_at}"
        # 34 子系统 + 大势卡 + 事件簇 落地
        assert (proj / "_数据库" / "大势卡.json").exists()
        assert (proj / "_数据库" / "事件簇.json").exists()
        assert (proj / "_数据库" / "作者风格.json").exists()
        # pause 答案 artifact 落盘（auto_pilot 取 default/options[0]）
        assert (proj / "_数据库" / ".wal" / "outline_choice_framework.json").exists()


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
