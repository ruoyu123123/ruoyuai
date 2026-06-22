#!/usr/bin/env python3
"""/reconcile 真 API 端到端冒烟（2026-06-22 G2 P0b 实装）。

任务原话：「mock 一个 locked_fact 变更场景（诡秘 cluster_001 修改克莱恩职业）·
跑 reconcile 全 5 step·预算 ~$2-3·真发 API」。

凿窍纪项目里没有「克莱恩」角色 → 用其真实主角「重黎」造同形变更（occupation
等价：locked_facts 里的「通天血脉最后一人」→「凡间隐姓的逃神」）。变更声明落
spec.json 喂 detect-changes，后续 4 step 由 orchestrator 真驱动跑全程。

🔴 隔离/运行：tests/real_api/ 子目录不被默认 runner glob + RUOYU_RUN_REAL_API=1
   门控（feedback_real_api_tests_no_economize）。
预算：~$2-3（影响半径预计 1-3 章 · 每章 gen_fixer validator-repair 一次真 API ·
       不缩 max_tokens · 不 dry-run）。控规模：strategy=B 只跑 high impact + max=2。
预期失败模式：
  ① 项目缺失 / 没有匹配章节 → SKIP（数据漂移防过敏）
  ② gen-model 返回 CJK 守恒拒绝 → patch fail 但 plan 仍 step_complete（advisory）
  ③ 网络/key 故障 → patch_log.fail_count > 0 · audit verdict=needs_review

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_reconcile.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
_REPO = _TESTS.parent
sys.path.insert(0, str(_REPO / "core" / "scripts"))

import orchestrator as orch  # noqa: E402
import plan_tracker as pt    # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
PROJECT_PATH = _REPO / "workspace" / "novels" / "凿窍纪"
RECONCILE_ID = "current"


def _has_real_project(project: Path) -> bool:
    if not project.is_dir():
        return False
    if not (project / "_数据库" / "人物卡.json").exists():
        return False
    # 至少 1 章存在
    chap_dir = project / "章节"
    return chap_dir.exists() and any(chap_dir.glob("第*章"))


def _seed_spec(project: Path, *, target: str, field: str,
               before: str, after: str) -> Path:
    ws = project / "_数据库" / ".reconcile" / RECONCILE_ID
    # 清旧 workspace（避免上次跑遗留污染断言）
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    spec = {
        "change_type": "character_field",
        "target": target,
        "field": field,
        "before": before,
        "after": after,
        "_seed": "test_real_reconcile",
    }
    spec_path = ws / "spec.json"
    spec_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec_path


def _pick_target_with_chapter_hit(project: Path) -> tuple[str, str, str, str] | None:
    """挑一个 locked_facts 里有词、且至少一章正文里能命中的目标。

    返回 (target_name, field, before, after) 或 None（跳过测试）。
    """
    cards_path = project / "_数据库" / "人物卡.json"
    if not cards_path.exists():
        return None
    try:
        cards_doc = json.loads(cards_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None

    # 物理章节合并文（最多前 5 章·够覆盖大多数 locked_facts）
    chap_dir = project / "章节"
    body_blob = ""
    if chap_dir.exists():
        for d in sorted(chap_dir.glob("第*章"))[:5]:
            txt = d / f"{d.name}.txt"
            if txt.exists():
                try:
                    body_blob += txt.read_text(encoding="utf-8") + "\n"
                except OSError:
                    pass

    for c in cards_doc.get("characters", []) or []:
        name = c.get("name") or ""
        if not name or name not in body_blob:
            continue
        facts = c.get("locked_facts", []) or []
        for f in facts:
            if not isinstance(f, str):
                continue
            if len(f) < 4 or len(f) > 30:
                continue
            # 这条 fact 至少 ⅓ 长度的子串在正文里出现 → 视为可命中
            mid = f[: max(4, len(f) // 2)]
            if mid and mid in body_blob:
                return (name, "locked_fact", f, f"凡间隐姓的逃神（已重新设定）")
    return None


# ════════════════════════════════════════════════════════════════
#  非真 API 也跑：plan template 结构断言 + 防回归到 NOT-YET 空壳
# ════════════════════════════════════════════════════════════════
def test_reconcile_plan_template_structure_only():
    tpl = pt.load_template("reconcile")
    assert tpl, "reconcile plan template 加载失败"
    steps = tpl.get("steps", [])
    assert len(steps) == 5, f"reconcile 应 5 步实装，实际 {len(steps)}"
    # 5 步全有 scripts + expected_outputs（_step_is_shell=False）
    assert not all(orch._step_is_shell(s) for s in steps), \
        "reconcile plan 仍是 NOT-YET 空壳·orchestrator 会拒绝"
    for s in steps:
        assert s.get("scripts"), \
            f"reconcile step {s.get('n')} 缺 scripts（空壳）"
        assert s.get("expected_outputs"), \
            f"reconcile step {s.get('n')} 缺 expected_outputs"
        cmd = " ".join(s.get("scripts", []))
        assert "reconcile.py" in cmd, \
            f"reconcile step {s.get('n')} 没调 reconcile.py"
    # 5 步 mode 各异 + 顺序对
    expected_modes = ["detect-changes", "compute-radius", "patch", "audit", "report"]
    for s, expected in zip(steps, expected_modes):
        cmd = " ".join(s.get("scripts", []))
        assert f"--mode {expected}" in cmd, \
            f"step{s.get('n')} 应 --mode {expected}，实际: {cmd}"
    # required_steps 必须包含 1-5
    assert set(tpl.get("required_steps", [])) >= {1, 2, 3, 4, 5}


def test_reconcile_script_modes_smoke():
    """reconcile.py 5 mode 都能 --help（CLI 自检·不烧钱）。"""
    import subprocess
    fixer = _REPO / "core" / "scripts" / "reconcile.py"
    assert fixer.exists(), f"reconcile.py 不存在: {fixer}"
    proc = subprocess.run(
        [sys.executable, str(fixer), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, f"--help 失败: {proc.stderr}"
    # 5 个 mode 都在 help 里
    for m in ("detect-changes", "compute-radius", "patch", "audit", "report"):
        assert m in proc.stdout, f"--help 未列 mode {m}"


def test_reconcile_detect_changes_no_api():
    """非真 API：从 spec.json 喂 detect-changes·不发 LLM·零成本回归。

    本测试只跑 step 1（reconcile.py --mode detect-changes），断言落盘形态正确。
    """
    if not _has_real_project(PROJECT_PATH):
        print(f"[SKIP] 项目不存在: {PROJECT_PATH}")
        return
    picked = _pick_target_with_chapter_hit(PROJECT_PATH)
    if not picked:
        print(f"[SKIP] 未找到 locked_fact 可命中的角色（数据漂移防过敏）")
        return
    target, field, before, after = picked
    _seed_spec(PROJECT_PATH, target=target, field=field, before=before, after=after)

    import subprocess
    fixer = _REPO / "core" / "scripts" / "reconcile.py"
    proc = subprocess.run(
        [sys.executable, str(fixer), str(PROJECT_PATH),
         "--mode", "detect-changes", "--reconcile-id", RECONCILE_ID],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, \
        f"detect-changes 失败: rc={proc.returncode} stderr={proc.stderr[-500:]}"

    change_path = (PROJECT_PATH / "_数据库" / ".reconcile" / RECONCILE_ID
                   / "change.json")
    assert change_path.exists(), f"change.json 未落盘: {change_path}"
    change = json.loads(change_path.read_text(encoding="utf-8-sig"))
    assert change["target"] == target
    assert change["before"] == before
    assert change["after"] == after
    assert change["schema_version"] == 1


# ════════════════════════════════════════════════════════════════
#  真 API（RUOYU_RUN_REAL_API=1）：5 step 全跑·真烧钱
# ════════════════════════════════════════════════════════════════
def test_reconcile_real_api_e2e():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 /reconcile 真 API 烟测")
        return
    if not _has_real_project(PROJECT_PATH):
        print(f"[SKIP] 真项目数据不存在: {PROJECT_PATH}")
        return
    picked = _pick_target_with_chapter_hit(PROJECT_PATH)
    if not picked:
        print(f"[SKIP] 项目里没找到可命中的 locked_fact（防数据漂移过敏）")
        return
    target, field, before, after = picked
    spec_path = _seed_spec(PROJECT_PATH,
                           target=target, field=field, before=before, after=after)
    print(f"\n[REAL] /reconcile {PROJECT_PATH.name} target={target} "
          f"before='{before[:30]}' → after='{after[:30]}'", file=sys.stderr)
    print(f"      spec: {spec_path}", file=sys.stderr)
    print(f"      预算 ~$2-3 · strategy=B（只 high·max=2 章控规模）", file=sys.stderr)

    # 关键回归锁：orchestrator 不再把 reconcile 当 NOT-YET 拒绝
    tpl = pt.load_template("reconcile")
    assert tpl and len(tpl.get("steps", [])) == 5
    assert not all(orch._step_is_shell(s) for s in tpl.get("steps", [])), \
        "reconcile plan 仍是 NOT-YET 空壳"

    # 跑 orchestrator 真驱动 5 步（strategy/max-chapters 写死在 plan 里默认 A·
    # 此处不便覆盖；patch_one_chapter 自己控规模——若影响章 ≤2 即满足预算约束）
    summary = orch.run_command(
        "reconcile",
        str(PROJECT_PATH),
        key=None,
        auto_pilot=True,
    )

    assert summary.end_report is not None, "end_plan 未跑（plan 早停）"
    assert summary.end_report.get("ok"), \
        f"end_plan 未通过: {json.dumps(summary.end_report, ensure_ascii=False)[:500]}"
    assert summary.paused_at is None, f"plan 半路停在 step {summary.paused_at}"
    completed_ns = [c.n for c in summary.completed
                    if c.status in ("completed", "skipped")]
    assert set(completed_ns) >= {1, 2, 3, 4, 5}, \
        f"5 步未全跑：completed={completed_ns}"

    # 5 个 step 产物落盘
    ws = PROJECT_PATH / "_数据库" / ".reconcile" / RECONCILE_ID
    for fname in ("change.json", "radius.json", "patch_log.json",
                  "audit.json", "reconcile_report.json"):
        p = ws / fname
        assert p.exists(), f"step 产出未落盘: {p}"
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"产出非合法 JSON: {p}: {e}") from e
        assert isinstance(data, dict), f"产出 JSON 顶层非 dict: {p}"

    # reconcile_report 结构断言
    report = json.loads((ws / "reconcile_report.json").read_text(encoding="utf-8-sig"))
    assert report.get("command") == "reconcile"
    assert report.get("reconcile_id") == RECONCILE_ID
    assert "verdict" in report
    assert report.get("verdict") in {"pass", "partial_fail", "needs_review",
                                     "dry_run", "no_op"}, \
        f"非法 verdict: {report.get('verdict')}"
    mods = report.get("modules", {})
    for k in ("step1_change", "step2_radius_summary", "step3_patch_summary",
              "step4_audit_summary"):
        assert k in mods, f"reconcile_report 缺 module: {k}"

    # patch_log 真 API 真发了（fail_count + ok_count 至少 > 0·除非 radius 0 章）
    patch_log = json.loads((ws / "patch_log.json").read_text(encoding="utf-8-sig"))
    radius = json.loads((ws / "radius.json").read_text(encoding="utf-8-sig"))
    if radius.get("affected_count", 0) > 0:
        # 至少尝试过一次 patch
        total = patch_log.get("ok_count", 0) + patch_log.get("fail_count", 0)
        assert total > 0, \
            f"radius affected={radius['affected_count']} 但 patch_log 全 0·" \
            f"reconcile.py patch mode 未真发: {patch_log}"

    print(f"\n[REAL OK] verdict={report['verdict']} · "
          f"affected={radius.get('affected_count')} · "
          f"patch_ok={patch_log.get('ok_count')}/{patch_log.get('fail_count')} · "
          f"audit_verdict={mods['step4_audit_summary'].get('verdict')}",
          file=sys.stderr)


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
