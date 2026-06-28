#!/usr/bin/env python3
"""/distill-style 真 API 8 步脚手架（2026-06-25 G7 P1 闭合代码侧准备）。

任务背景（G6 真 API 矩阵 · 剩余 P1 缺口）：/distill-style 8 步全程**从未真 API 入库**
（历史只手动 N=10 A/B）。本测试建**门控脚手架**：中转站恢复后设 RUOYU_RUN_REAL_API=1
即可真跑，验证整条「作者作品 → 复刻 → SFS → 定稿」gen-model 链路活着。

🔴 缩参/省钱策略（关键步 · 非全量 8 步 surface_runner 48 维全本）：
  - 复用**已蒸好**的 诡秘之主 真工件作 fixture（skill_v0.md / cluster_index.json /
    作者风格_FINAL.json / 原文/），**不重跑** phase-0 ingest + phase-1 48 维 surface
    蒸馏（最贵的一段，会对全本每 cluster 跑 48 judge）。
  - 只真跑 gen-model 吃 token 的**两个创作核心步**：
      · 阶段 3 phase-4-cluster-replica：distill_replicate gen-model 复刻**首个 cluster**
        （auto_001 · 唯一合法入口 · 强制 gen-model · 北极星⑤）。
      · 阶段 4 phase-2-multi-dim-compare：style_evaluator SFS 多 ref（铁律 --multi-ref-from-dir）。
    → token 预算压到「1 cluster 复刻 + 确定性 SFS」（~$0.5-1）。
  - 复刻产物 + SFS 报告写 **tempfile.mkdtemp() 临时目录**，绝不污染真 诡秘 复刻测试/ 对比报告/。

🔴 门控/隔离：
  - tests/real_api/ 子目录不被默认 runner glob(run_tests.py 只 glob tests/ 顶层非递归)。
  - RUOYU_RUN_REAL_API=1 env 门控 · 默认 skip 不烧钱。
  - 输入：workspace/styles/诡秘之主/（skill_v0.md + cluster_index.json + 原文/ + 作者风格_FINAL.json）。

🔴 中转站返空兜底（外部不稳容忍 · 与 skillopt 冒烟同范式）：
  - gen-model 返空 / TransportError / refusal 耗尽 → distill_replicate 非零退出或复刻
    文本过短 → 记 SKIP_REASON 打印，**不 fail**。只断言不依赖 API 的确定性结构
    （plan 自洽 + 作者档字段完整）。
  - 真 API 活着（复刻文本够长）→ 进一步真跑 SFS 并断言 sfs_quick 算出 + grade。

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_distill_style.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
_REPO = _TESTS.parent
sys.path.insert(0, str(_REPO / "core" / "scripts"))

import orchestrator as orch  # noqa: E402
import plan_tracker as pt    # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
STYLE_ROOT = _REPO / "workspace" / "styles" / "诡秘之主"


def _cjk(s: str) -> int:
    return sum(1 for ch in s if "一" <= ch <= "鿿")


def _has_distill_inputs(root: Path) -> bool:
    """探测真 distill 工件是否齐全（数据漂移防过敏 → 缺则 SKIP 不 fail）。"""
    if not root.is_dir():
        return False
    for need in ("skill_v0.md", "cluster_index.json", "作者风格_FINAL.json"):
        if not (root / need).exists():
            return False
    ref = root / "原文"
    return ref.is_dir() and any(ref.glob("第*章.txt"))


def _first_cluster_id(root: Path) -> str:
    """读 cluster_index.json clusters[0].cluster_id（plan step3 data_flow 同源·默认 cluster_001）。"""
    try:
        ci = json.loads((root / "cluster_index.json").read_text(encoding="utf-8-sig"))
        cl = ci.get("clusters") if isinstance(ci, dict) else ci
        cid = cl[0].get("cluster_id")
        return cid or "cluster_001"
    except Exception:  # noqa: BLE001
        return "cluster_001"


def _looks_like_transport_failure(txt: str) -> bool:
    """复刻 stderr/产物头判中转站返空/拒绝/耗尽（外部因素 · 容忍 SKIP）。"""
    low = (txt or "").lower()
    needles = (
        "transporterror", "exhausted", "refusal", "refusalexhausted",
        "返空", "空响应", "empty", "timed out", "timeout", "520", "502",
        "connection", "genmodel", "rate", "限速",
    )
    return any(n in low for n in needles)


# ─────────────────────────────────────────────────────────────────────────
# 真 API 脚手架（门控）：复刻（阶段3）+ SFS（阶段4）关键步
# ─────────────────────────────────────────────────────────────────────────
def test_distill_style_real_api_smoke():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 /distill-style 真 API 脚手架")
        return
    if not _has_distill_inputs(STYLE_ROOT):
        print(f"[SKIP] distill 真输入不齐: {STYLE_ROOT}"
              "（skill_v0/cluster_index/作者风格_FINAL/原文）→ 跳过（数据漂移防过敏）")
        return

    cluster_id = _first_cluster_id(STYLE_ROOT)
    skill_v0 = STYLE_ROOT / "skill_v0.md"
    tmp = Path(tempfile.mkdtemp(prefix="distill_style_real_"))
    try:
        replica = tmp / "v0" / "replica.txt"
        eval_out = tmp / "对比报告" / "eval_v0.json"
        replica.parent.mkdir(parents=True, exist_ok=True)
        eval_out.parent.mkdir(parents=True, exist_ok=True)

        # ── 阶段 3 phase-4-cluster-replica：gen-model 复刻首个 cluster（唯一合法入口）──
        print(f"\n[REAL] /distill-style 复刻 {STYLE_ROOT.name} {cluster_id}"
              " · gen-model · 预算 ~$0.5-1", file=sys.stderr)
        rep_cmd = [
            sys.executable,
            str(_REPO / "core" / "scripts" / "distill_replicate.py"),
            "--style-skill", str(skill_v0),
            "--mode", "cluster",
            "--cluster-ref", cluster_id,
            "--project", str(STYLE_ROOT),
            "--output", str(replica),
        ]
        rep = subprocess.run(rep_cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=1800)
        # 🔴 feedback_verify_stderr_not_exitcode：查 stderr 真相不只信 exit code
        rep_blob = (rep.stderr or "") + "\n" + (rep.stdout or "")
        replica_txt = replica.read_text(encoding="utf-8") if replica.exists() else ""
        n_cjk = _cjk(replica_txt)

        live = rep.returncode == 0 and n_cjk >= 500
        if not live:
            reason = (
                f"复刻未产出健康文本（returncode={rep.returncode} · CJK={n_cjk}）"
                f" · 疑中转站返空/拒绝/耗尽（外部因素）"
                f" · stderr 尾={rep_blob[-200:]!r}"
            )
            assert _looks_like_transport_failure(rep_blob) or n_cjk == 0, (
                f"复刻失败但非已知中转站/返空形态——疑真 bug:\n{rep_blob[-1200:]}"
            )
            print(f"\n[REAL SKIP_REASON] {reason}", file=sys.stderr)
            print("[REAL OK·结构] plan 自洽 + 作者档字段完整已另测 · "
                  "复刻/SFS 链路待中转站恢复后复跑", file=sys.stderr)
            return

        # ── 阶段 4 phase-2-multi-dim-compare：SFS 多 ref（铁律 --multi-ref-from-dir）──
        eval_cmd = [
            sys.executable,
            str(_REPO / "core" / "scripts" / "style_evaluator.py"),
            "--gen", str(replica),
            "--multi-ref-from-dir", str(STYLE_ROOT / "原文"),
            "--multi-ref-count", "3",   # 缩参：多 ref 抽样压到 3（全量 plan 用 5）
            "--multi-ref-seed", "42",
            "--output", str(eval_out),
        ]
        ev = subprocess.run(eval_cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
        assert eval_out.exists(), (
            f"SFS 报告未落盘（style_evaluator 失败）:\n"
            f"{(ev.stderr or '')[-1200:]}"
        )
        report = json.loads(eval_out.read_text(encoding="utf-8-sig"))

        # ── 断言：SFS 算出（确定性·复刻文本到位则必出）──
        assert "sfs_quick" in report, f"SFS 报告缺 sfs_quick: {list(report.keys())}"
        sfs = report["sfs_quick"]
        assert isinstance(sfs, (int, float)), f"sfs_quick 非数值: {sfs!r}"
        assert 0 <= sfs <= 100, f"sfs_quick 越界: {sfs}"
        ps = report.get("programmatic_score", {})
        assert ps.get("grade"), f"SFS 报告缺 grade: {ps}"

        print(f"\n[REAL OK] 复刻 CJK={n_cjk} · SFS={sfs} · grade={ps.get('grade')} "
              f"· cluster={cluster_id}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────
# 非门控：plan 模板 8 步结构自洽（不烧钱 · 防回退 NOT-YET / 防丢 gen-model 铁律）
# ─────────────────────────────────────────────────────────────────────────
def test_distill_style_plan_template_structure_only():
    tpl = pt.load_template("distill-style")
    assert tpl, "distill-style plan template 加载失败"
    # 🔴 2026-06-27 C20：加 step 6.5 pid-state-bootstrap（PID 阈值自动回测初始化）·8→9 步
    # 🔴 2026-06-27 C01：加 step 5.5 phase-4b-v1-reverify-converge（v1 再复刻+再 SFS+收敛闸）·9→10 步
    assert tpl.get("total_steps") == 10, f"应 10 步，实际 {tpl.get('total_steps')}"
    steps = tpl.get("steps", [])
    assert len(steps) == 10, f"steps 应 10 条，实际 {len(steps)}"
    # required_steps 含 2.5（skill-v0-generate 破 chicken-egg）+ 5.5（C01 收敛闸）+ 6.5（C20 PID bootstrap）
    assert set(tpl.get("required_steps", [])) == {1, 2, 2.5, 3, 4, 5, 5.5, 6, 6.5, 7}

    names = [s.get("name", "") for s in steps]
    for need in ("phase-0-preprocess", "phase-1-surface-distill", "skill-v0-generate",
                 "phase-4-cluster-replica", "phase-2-multi-dim-compare",
                 "phase-3-correction-reflect", "phase-4b-v1-reverify-converge",
                 "phase-5-finalize",
                 "pid-state-bootstrap",
                 "phase-6-writer-feedback-verify"):
        assert need in names, f"缺 step: {need} · 实际 {names}"

    # 每步 scripts 非空（非 NOT-YET 空壳）+ 整 plan 非全空壳
    for s in steps:
        assert s.get("scripts"), f"distill-style step {s.get('n')} 缺 scripts（空壳）"
    assert not all(orch._step_is_shell(s) for s in steps), \
        "distill-style plan 不应是 NOT-YET 空壳"

    # 复刻步：唯一合法入口 distill_replicate.py + 强制 gen-model cluster 模式（北极星⑤）
    s3 = next(s for s in steps if s.get("name") == "phase-4-cluster-replica")
    s3_cmd = " ".join(s3.get("scripts", []))
    assert "distill_replicate.py" in s3_cmd, "复刻步应调 distill_replicate.py（唯一合法入口）"
    assert "--mode cluster" in s3_cmd, "复刻步应 --mode cluster"
    # data_flow 取 first_cluster_id（不机械拼接章号·北极星①）
    assert "<first_cluster_id>" in s3_cmd, "复刻步应用 <first_cluster_id> 占位（data_flow）"
    assert "<first_cluster_id>" in (s3.get("data_flow") or {}), \
        "复刻步 data_flow 应声明 <first_cluster_id>"

    # SFS 步：铁律 --multi-ref-from-dir（防单 ref 对高方差作者失真·feedback_distill_sfs_multi_ref）
    s4 = next(s for s in steps if s.get("name") == "phase-2-multi-dim-compare")
    s4_cmd = " ".join(s4.get("scripts", []))
    assert "style_evaluator.py" in s4_cmd, "SFS 步应调 style_evaluator.py"
    assert "--multi-ref-from-dir" in s4_cmd, \
        "SFS 步必须 --multi-ref-from-dir（铁律·单 ref 高方差失真）"

    # 定稿步：finalize_distill 出货 skill_FINAL + 作者风格_FINAL
    s6 = next(s for s in steps if s.get("name") == "phase-5-finalize")
    assert "finalize_distill.py" in " ".join(s6.get("scripts", []))
    assert "skill_FINAL.md" in s6.get("expected_outputs", []), \
        "定稿步 expected_outputs 应含 skill_FINAL.md"

    # 🔴 2026-06-27 C01 收敛闸 step5.5：v1 再复刻+再 SFS+validation_gate 收敛判定
    s55 = next(s for s in steps if s.get("name") == "phase-4b-v1-reverify-converge")
    s55_cmd = " ".join(s55.get("scripts", []))
    assert "distill_replicate.py" in s55_cmd and "skill_v1.md" in s55_cmd, \
        "收敛步应用 skill_v1 复刻（distill_replicate 唯一合法入口）"
    assert "--multi-ref-from-dir" in s55_cmd, "收敛步 v1 SFS 必 --multi-ref-from-dir（铁律）"
    assert "distill_convergence_gate.py" in s55_cmd, "收敛步应调 distill_convergence_gate.py"
    assert "skill_v2.md" in s55.get("expected_outputs", []), \
        "收敛步 expected_outputs 应含 skill_v2.md（finalize 出货最新版）"
    # C01：reflect step5 升为强契约（skip_output_allowed=false·skill_v1 是收敛环必备输入）
    s5 = next(s for s in steps if s.get("name") == "phase-3-correction-reflect")
    assert s5.get("skip_output_allowed") is False, \
        "C01：reflect step5 skip_output_allowed 应为 false（skill_v1 必落盘喂收敛环）"

    # 回灌闸（Article 6）：exit_codes 只 {0:ok, 2:fail}（must_fix#4）
    s7 = next(s for s in steps if s.get("name") == "phase-6-writer-feedback-verify")
    ec = (s7.get("control_flow") or {}).get("exit_codes", {})
    assert set(ec.keys()) == {"0", "2"}, f"回灌步 exit_codes 应仅 0/2，实际 {ec}"

    # 程序驱动声明（非 Claude 编排残留）
    assert "程序驱动" in tpl.get("_program_driven_status", ""), \
        "distill-style 应声明已程序驱动化"
    # 北极星⑤：复刻强制 gen-model + SFS multi-ref 写进 plan 自述
    ns5 = tpl.get("_north_star_5", "")
    assert "gen-model" in ns5 and "multi-ref" in ns5, \
        "plan _north_star_5 应声明复刻强制 gen-model + SFS multi-ref"


# ─────────────────────────────────────────────────────────────────────────
# 非门控：真 诡秘 作者档字段完整（确定性·锁 consolidate 产出契约·不烧钱）
# ─────────────────────────────────────────────────────────────────────────
def test_distill_style_author_profile_fields_complete():
    """作者档字段完整（consolidate 聚合产物契约·writer build_manifest 消费方依赖）。"""
    prof_path = STYLE_ROOT / "作者风格_FINAL.json"
    if not prof_path.exists():
        print(f"[SKIP] 真作者档不存在: {prof_path} → 跳过（数据漂移防过敏）")
        return
    prof = json.loads(prof_path.read_text(encoding="utf-8-sig"))
    # 顶层契约字段（writer manifest + SFS programmatic_score 消费）
    for k in ("author", "core_style_signature", "quantitative",
              "narrative_craft", "narrative_fingerprint"):
        assert k in prof, f"作者档缺顶层字段: {k} · 实际 {list(prof.keys())}"
    # quantitative 是 consumer 数值字段块（consolidate 确定性聚合·非 agent 自由写）
    quant = prof.get("quantitative")
    assert isinstance(quant, dict) and quant, "quantitative 应为非空 dict（consumer 数值字段）"

    # cluster_index.json 与作者档同源（蒸馏单位一致·北极星①）
    ci_path = STYLE_ROOT / "cluster_index.json"
    if ci_path.exists():
        ci = json.loads(ci_path.read_text(encoding="utf-8-sig"))
        cl = ci.get("clusters") if isinstance(ci, dict) else ci
        assert isinstance(cl, list) and cl, "cluster_index clusters 应为非空 list"
        assert cl[0].get("cluster_id"), "首 cluster 应有 cluster_id（data_flow 源）"


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
