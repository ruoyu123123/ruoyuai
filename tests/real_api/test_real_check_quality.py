#!/usr/bin/env python3
"""/check-quality 真 API 端到端冒烟（2026-06-22 G2 P0a 实装）。

文档原话（任务）：「step 3: 跑 judge_runner audit + voice judge 真 API(elysia gen-model)
+ 综合产 quality_report.json」+「真 API 烟测·跑 check-quality 全 3 step·预算 ~$1-2」。
本测试用**真存在的 cluster_001（凿窍纪 · 完整 cluster mode 项目布局）** 跑全 3 step
orchestrator 真驱动，验证：
  ① step1 audit_hub 落盘 cluster_NNN_audit.json（机械层 13+ scanner advisory）
  ② step2 check_quality_validate 落盘 cluster_NNN_validate.json（跨章 hard_gate）
  ③ step3 双 judge 真 API（reading-reflector + voice-checker）+ 综合 quality_report.json
  ④ orchestrator 不再把 check-quality 当 NOT-YET 空壳拒绝（_step_is_shell=False）
  ⑤ end_plan ok=True · plan.steps[] 全 completed

🔴 隔离/运行：tests/real_api/ 子目录不被默认 runner glob + RUOYU_RUN_REAL_API=1 门控。
环境变量：RUOYU_REAL_API_PROFILE 覆盖默认 profile（默认 .env 当前 active · elysiver/pie-xian）。
预算：~$1-2（两 judge 真调，无 round 循环 · max_tokens 用 spec 满值）。
预期失败模式：① 项目缺失 → SKIP（数据漂移防过敏）；② 真 API key 缺 → 走 .env active；
              ③ gen-model 截断/拒绝 → JudgeOutcome.degraded=True · synthesize 仍出报告。

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_check_quality.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
_REPO = _TESTS.parent
sys.path.insert(0, str(_REPO / "core" / "scripts"))

import orchestrator as orch  # noqa: E402
import plan_tracker as pt    # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
PROJECT_PATH = _REPO / "workspace" / "novels" / "凿窍纪"
CLUSTER_KEY = "001"


def _has_real_cluster(project: Path, key: str) -> bool:
    """探测项目是否真存在 cluster_{key}（事件簇.json 有该条目 + draft 文件存在）。"""
    if not project.is_dir():
        return False
    db = project / "_数据库"
    if not (db / "事件簇.json").exists():
        return False
    draft = project / "章节" / f"cluster_{key}_draft" / f"cluster_{key}_draft.txt"
    return draft.exists()


def test_check_quality_real_api_e2e():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 /check-quality 真 API 烟测")
        return
    if not _has_real_cluster(PROJECT_PATH, CLUSTER_KEY):
        print(f"[SKIP] 真 cluster 数据不存在: {PROJECT_PATH} cluster_{CLUSTER_KEY}"
              " → 跳过烟测（数据漂移防过敏）")
        return

    # ① orchestrator 不再把 check-quality 当 NOT-YET 拒绝（关键回归锁）
    tpl = pt.load_template("check-quality")
    assert tpl, "check-quality plan template 加载失败"
    steps_tpl = tpl.get("steps", [])
    assert len(steps_tpl) == 3, f"check-quality 应 3 步实装，实际 {len(steps_tpl)}"
    assert not all(orch._step_is_shell(s) for s in steps_tpl), \
        "check-quality plan 仍是 NOT-YET 空壳（所有 step 零 scripts）·orchestrator 会拒绝"
    for s in steps_tpl:
        assert s.get("scripts"), \
            f"check-quality step {s.get('n')} 缺 scripts（空壳）"
        assert s.get("expected_outputs"), \
            f"check-quality step {s.get('n')} 缺 expected_outputs"

    # ② 跑 orchestrator 真驱动 3 步
    print(f"\n[REAL] /check-quality {PROJECT_PATH.name} cluster_{CLUSTER_KEY}"
          " · 真 API · 预算 ~$1-2", file=sys.stderr)
    summary = orch.run_command(
        "check-quality",
        str(PROJECT_PATH),
        key=CLUSTER_KEY,
        auto_pilot=True,
    )

    # ③ end_plan ok + steps 全 completed
    assert summary.end_report is not None, "end_plan 未跑（plan 早停）"
    assert summary.end_report.get("ok"), \
        f"end_plan 未通过: {json.dumps(summary.end_report, ensure_ascii=False)[:500]}"
    assert summary.paused_at is None, f"plan 半路停在 step {summary.paused_at}"
    completed_ns = [c.n for c in summary.completed if c.status in ("completed", "skipped")]
    assert set(completed_ns) >= {1, 2, 3}, \
        f"3 步未全跑：completed={completed_ns}"

    # ④ 验证落盘产物
    db = PROJECT_PATH / "_数据库"
    audit_path = db / ".audit" / f"cluster_{CLUSTER_KEY}_audit.json"
    validate_path = db / ".qa" / f"cluster_{CLUSTER_KEY}_validate.json"
    audit_judge_path = db / ".qa" / f"cluster_{CLUSTER_KEY}_audit_judge.json"
    voice_judge_path = db / ".qa" / f"cluster_{CLUSTER_KEY}_voice_judge.json"
    quality_path = db / ".qa" / f"cluster_{CLUSTER_KEY}_quality_report.json"

    for p in (audit_path, validate_path, audit_judge_path, voice_judge_path, quality_path):
        assert p.exists(), f"step 产出未落盘: {p}"
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"产出非合法 JSON: {p}: {e}") from e
        assert isinstance(data, dict), f"产出 JSON 顶层非 dict: {p}"

    # ⑤ quality_report 结构关键字段
    qr = json.loads(quality_path.read_text(encoding="utf-8-sig"))
    assert qr.get("command") == "check-quality"
    assert qr.get("cluster_id") == f"cluster_{CLUSTER_KEY}"
    assert "verdict" in qr
    assert qr.get("verdict") in {"pass", "fail", "needs_agent"}, \
        f"非法 verdict: {qr.get('verdict')}"
    mods = qr.get("modules", {})
    for k in ("mechanical_audit_step1", "cross_chapter_validate_step2",
              "llm_reading_reflector_judge", "llm_voice_checker_judge"):
        assert k in mods, f"quality_report 缺 module: {k}"

    # ⑥ judge 真 API 真发了（degraded=False 且有 violations / new_issues 字段）
    audit_judge = json.loads(audit_judge_path.read_text(encoding="utf-8-sig"))
    voice_judge = json.loads(voice_judge_path.read_text(encoding="utf-8-sig"))
    # exception 兜底空壳会带 _judge_exception · 真 API 跑通不应有
    assert "_judge_exception" not in audit_judge, \
        f"reading-reflector judge 抛异常: {audit_judge.get('_judge_exception')}"
    assert "_judge_exception" not in voice_judge, \
        f"voice-checker judge 抛异常: {voice_judge.get('_judge_exception')}"
    # required_keys 结构存活（feedback_real_api_tests_no_economize · 真 API 不省 max_tokens）
    assert "verdict" in audit_judge or audit_judge.get("_degraded"), \
        "reading-reflector 无 verdict 也非 degraded"
    assert "violations" in voice_judge or voice_judge.get("_degraded"), \
        "voice-checker 无 violations 也非 degraded"

    print(f"\n[REAL OK] verdict={qr.get('verdict')} · "
          f"audit_issues={mods['mechanical_audit_step1'].get('issues_total', '?')} · "
          f"validate_fatal={mods['cross_chapter_validate_step2'].get('fatal_count', '?')}",
          file=sys.stderr)


def test_check_quality_plan_template_structure_only():
    """非真 API 也跑（结构断言不烧钱）：plan 模板形态自包含正确 · 防回归到 NOT-YET。"""
    tpl = pt.load_template("check-quality")
    assert tpl, "check-quality plan template 加载失败"
    steps = tpl.get("steps", [])
    assert len(steps) == 3
    # 三 step 名字必须能反映任务：mechanical-audit / cross-chapter-validate / judge-llm
    names = [s.get("name", "") for s in steps]
    assert any("audit" in n for n in names), f"step1 应含 audit 关键字: {names}"
    assert any("validate" in n for n in names), f"step2 应含 validate 关键字: {names}"
    assert any("judge" in n or "quality" in n for n in names), \
        f"step3 应含 judge/quality 关键字: {names}"
    # 全 step 都有 scripts + expected_outputs（_step_is_shell=False）
    assert not all(orch._step_is_shell(s) for s in steps), \
        "check-quality plan 不应是 NOT-YET 空壳（_step_is_shell 全 True）"
    # required_steps 必须包含 1/2/3
    assert set(tpl.get("required_steps", [])) >= {1, 2, 3}
    # step1 audit_hub 必须 --mode cluster · 不带 --auto-fix（检查模式）
    s1_cmd = " ".join(steps[0].get("scripts", []))
    assert "audit_hub.py" in s1_cmd, "step1 应调用 audit_hub.py"
    assert "--mode cluster" in s1_cmd, "step1 应跑 cluster 模式"
    assert "--auto-fix" not in s1_cmd, "step1 不应 --auto-fix（检查模式纯报告）"
    # step3 必须调 check_quality_judge 综合 wrapper
    s3_cmd = " ".join(steps[2].get("scripts", []))
    assert "check_quality_judge.py" in s3_cmd, \
        "step3 应通过 check_quality_judge.py 调双 judge + 综合"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            import traceback
            print(f"  [FAIL] {nm}: {e}")
            traceback.print_exc()
    sys.exit(1 if fails else 0)
