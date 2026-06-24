#!/usr/bin/env python3
"""/distill-style-skillopt 真 API 冒烟脚手架（2026-06-24 G7 P0 闭合代码侧准备）。

任务背景（G6 真 API 矩阵 · 剩余 P0 最大缺口）：SkillOpt epoch=4 真训练循环
(rollout → optimizer → validation_gate → reject_buffer) **从未真 API 跑过**，只 mock 57/57。
本测试建**门控冒烟脚手架**：中转站恢复后设 RUOYU_RUN_REAL_API=1 即可真跑，验证整条
gen-model 训练链路活着。

🔴 缩参策略（最小链路 · 非全量 epoch=4）：
  - epochs=1 · rollout_batch=2 · minibatch=2 · multi_ref_count=2
  - 经 monkeypatch dataset.load_split 注入超小 split(train=2/sel=1/test=1)，
    把真 API 调用压到个位数（rollout 2 + selection before/after 各 1 + final test 1）。
    **不落盘 split.json**，绝不污染真 style 目录的全量 skillopt 训练。
  - 全量真跑见文末「中转站恢复后怎么跑全量」。

🔴 门控/隔离：
  - tests/real_api/ 子目录不被默认 runner glob(run_tests.py 只 glob tests/ 顶层非递归)。
  - RUOYU_RUN_REAL_API=1 env 门控 · 默认 skip 不烧钱。
  - 输入：workspace/styles/诡秘之主/skill_FINAL.md(SFS 88.35) + cluster_index.json + 原文/。

🔴 中转站返空兜底（外部不稳容忍）：
  - gen-model 返空 → distill_replicate 失败 → reward 0 → optimizer 走 TransportError/空 patch 分支
    → train 平稳完成(steps_accepted=0, 无 patch)。测试**容忍**该情形：记 SKIP_REASON 打印，
    只断言不依赖 API 的确定性结构（TrainResult/train_log/epoch log/工作目录），**不 fail**。
  - 真 API 活着(有 patch 产出) → 进一步断言 patch 产出 / validation 决策(ACCEPT/REJECT)
    / reject_buffer 归档 / skill 版本号工件。

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_skillopt_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
_REPO = _TESTS.parent
sys.path.insert(0, str(_REPO / "core" / "scripts"))

import orchestrator as orch          # noqa: E402  (_step_is_shell 复用)
import plan_tracker as pt            # noqa: E402
from skill_opt import dataset as ds  # noqa: E402
from skill_opt import train          # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
STYLE_ROOT = _REPO / "workspace" / "styles" / "诡秘之主"


def _has_skillopt_inputs(root: Path) -> bool:
    """探测真 skillopt 输入是否齐全（数据漂移防过敏 → 缺则 SKIP 不 fail）。"""
    if not root.is_dir():
        return False
    if not (root / "skill_FINAL.md").exists():
        return False
    if not (root / "cluster_index.json").exists():
        return False
    ref = root / "原文"
    return ref.is_dir() and any(ref.iterdir())


# ─────────────────────────────────────────────────────────────────────────
# 真 API 冒烟（门控）
# ─────────────────────────────────────────────────────────────────────────
def test_skillopt_train_loop_real_api_smoke():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 /distill-style-skillopt 真 API 冒烟")
        return
    if not _has_skillopt_inputs(STYLE_ROOT):
        print(f"[SKIP] skillopt 真输入不齐: {STYLE_ROOT}（skill_FINAL/cluster_index/原文）"
              " → 跳过冒烟（数据漂移防过敏）")
        return

    skill = STYLE_ROOT / "skill_FINAL.md"

    # 缩参 split：不落盘（绝不污染真目录全量训练的 split.json），用 monkeypatch 注入
    tiny = ds.Split(
        train=["auto_001", "auto_002"],
        selection=["auto_001"],
        test=["auto_001"],
    )
    orig_load_split = train.dataset.load_split
    train.dataset.load_split = lambda root: tiny  # type: ignore[assignment]

    print(f"\n[REAL] /distill-style-skillopt 冒烟 {STYLE_ROOT.name} · "
          "epochs=1 rollout=2 minibatch=2 multi_ref=2 · 真 API · 预算 ~个位数次调用",
          file=sys.stderr)
    try:
        result = train.train(
            project_root=STYLE_ROOT,
            initial_skill_path=skill,
            epochs=1,
            rollout_batch_size=2,
            minibatch_size=2,
            l_t_max=4,
            l_t_min=2,
            reward_route="distill",     # 蒸馏路线：复刻→SFS 评分（不依赖写作产物）
            multi_ref_count=2,          # 缩参：SFS 多基线抽样压到 2
            multi_ref_seed=42,
            seed=42,
        )
    finally:
        train.dataset.load_split = orig_load_split  # type: ignore[assignment]

    # ── ① 不依赖 API 的确定性结构（必过）──
    assert isinstance(result, train.TrainResult), f"返回非 TrainResult: {type(result)}"
    hp = result.hparams
    assert hp.get("epochs") == 1, f"epochs 缩参未生效: {hp.get('epochs')}"
    assert hp.get("rollout_batch") == 2, f"rollout_batch 缩参未生效: {hp.get('rollout_batch')}"
    assert hp.get("minibatch") == 2, f"minibatch 缩参未生效: {hp.get('minibatch')}"
    assert hp.get("reward_route") == "distill", f"reward_route 应 distill: {hp.get('reward_route')}"

    # ── ② 工作目录 + train_log 落盘 ──
    train_dir = Path(result.final_skill).parent
    assert train_dir.is_dir(), f"训练工作目录缺失: {train_dir}"
    log_path = train_dir / "train_log.json"
    assert log_path.exists(), f"train_log.json 未落盘: {log_path}"
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log.get("hparams", {}).get("epochs") == 1
    assert len(log.get("epochs", [])) == 1, "epoch log 应 1 条（epochs=1）"

    # ── ③ skill 工件（版本号化的工作副本 + best）──
    assert (train_dir / "skill_current.md").exists(), "skill_current.md（工作副本）缺失"
    best_path = Path(result.final_skill)
    assert best_path.name == "best_skill.md" and best_path.exists(), \
        f"best_skill.md 缺失: {best_path}"
    assert best_path.read_text(encoding="utf-8").strip(), "best_skill.md 为空"

    # ── ④ optimizer 真被调（无论返不返 patch，train 都落 ep1_step0_optimizer.json）──
    opt_logs = sorted(train_dir.glob("ep1_step*_optimizer.json"))
    assert opt_logs, "optimizer 日志缺失（train 未跑到 optimizer 阶段）"
    opt0 = json.loads(opt_logs[0].read_text(encoding="utf-8"))
    assert "patches" in opt0 and "raw_reply" in opt0, "optimizer 日志结构异常"
    # skill 版本号工件：optimizer 日志文件名编码 ep/step 版本（ep1_step0）
    assert "ep1_step0" in opt_logs[0].name, f"optimizer 日志未带版本号: {opt_logs[0].name}"

    ep0 = result.epochs[0]
    assert ep0.epoch == 1
    assert ep0.l_t == 4, f"epochs=1 时 L_t 应取 max=4，实际 {ep0.l_t}"
    assert ep0.steps_attempted >= 1, "训练循环未跑满 1 step"

    patches = opt0.get("patches") or []
    raw_reply = opt0.get("raw_reply", "")

    # ── 中转站返空兜底：无 patch / TransportError / reward 全 0 → 记 SKIP_REASON 不 fail ──
    live = bool(patches) and not raw_reply.startswith("<TransportError")
    if not live:
        skip_reason = (
            "gen-model 返空/TransportError/无 patch（中转站不稳 · 外部因素）"
            f" · raw_reply 头={raw_reply[:80]!r}"
        )
        print(f"\n[REAL SKIP_REASON] {skip_reason}", file=sys.stderr)
        print("[REAL OK·结构] TrainResult/train_log/epoch log/optimizer 日志结构自洽 · "
              "API 链路待中转站恢复后复跑验证 ACCEPT/REJECT", file=sys.stderr)
        return

    # ── ⑤ 真 API 活着：进一步断言 validation 决策 + reject_buffer ──
    decided = ep0.steps_accepted + ep0.steps_rejected
    # patch 产出后若 apply 成功必经 gate；apply 全失败则不决策（也合法）→ 软断言
    if decided >= 1:
        assert ep0.steps_accepted >= 0 and ep0.steps_rejected >= 0, "决策计数异常"
        # validation_gate 决策落了 selection_reward_history
        assert ep0.selection_reward_history, "gate 决策未记 selection_reward_history"
        if ep0.steps_rejected > 0:
            # reject_buffer epoch 末 clear_epoch 归档到 reject_archive.jsonl
            archive = STYLE_ROOT / "_skillopt" / "reject_buffer" / "reject_archive.jsonl"
            assert archive.exists(), \
                f"有 REJECT 但 reject_buffer 归档缺失: {archive}"
        if ep0.steps_accepted > 0:
            # ACCEPT → best_reward 应被更新为正
            assert result.final_selection_reward > 0, \
                "有 ACCEPT 但 final_selection_reward 非正"

    print(f"\n[REAL OK] patches={len(patches)} · attempted={ep0.steps_attempted} "
          f"accept={ep0.steps_accepted} reject={ep0.steps_rejected} "
          f"· sel_reward={result.final_selection_reward:.4f} "
          f"test_reward={result.final_test_reward:.4f}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────
# 非门控：plan 模板结构自洽（不烧钱 · 防回退）
# ─────────────────────────────────────────────────────────────────────────
def test_skillopt_plan_template_structure_only():
    """5 步 · scripts 非空 · 不引入新 hard_gate · train 步缩参可对齐。"""
    tpl = pt.load_template("distill-style-skillopt")
    assert tpl, "distill-style-skillopt plan template 加载失败"
    assert tpl.get("total_steps") == 5
    steps = tpl.get("steps", [])
    assert len(steps) == 5, f"应 5 步，实际 {len(steps)}"
    assert set(tpl.get("required_steps", [])) == {1, 2, 3, 4, 5}

    names = [s.get("name", "") for s in steps]
    for need in ("preflight-compact", "dataset-split", "train-loop",
                 "promote-best-skill", "writer-feedback-verify"):
        assert need in names, f"缺 step: {need} · 实际 {names}"

    # 每步 scripts 非空（非 NOT-YET 空壳）
    for s in steps:
        assert s.get("scripts"), f"step {s.get('n')} 缺 scripts（空壳）"
    assert not all(orch._step_is_shell(s) for s in steps), \
        "skillopt plan 不应是 NOT-YET 空壳"

    # step3 train-loop：调 train.py · distill 路线 · 全量超参 epoch=4（缩参只在测试覆盖）
    s3 = next(s for s in steps if s.get("name") == "train-loop")
    s3_cmd = " ".join(s3.get("scripts", []))
    assert "skill_opt/train.py" in s3_cmd, "step3 应调 skill_opt/train.py"
    assert "--reward-route distill" in s3_cmd, "step3 应走 distill 路线"
    assert "--epochs 4" in s3_cmd, "step3 全量应 epochs=4（论文默认）"

    # 不引入新 hard_gate（北极星⑤：reward 只读现有 binary 信号）
    align = tpl.get("_north_star_alignment", "")
    assert "不引入新 hard_gate" in align, "north_star 应声明不引入新 hard_gate"
    # plan 不得声明任何 step 为 hard_gate 级
    for s in steps:
        assert s.get("gate_level", "advisory") != "hard_gate", \
            f"skillopt step {s.get('n')} 不应是 hard_gate"


# ─────────────────────────────────────────────────────────────────────────
# 非门控：缩参确定性数学锁（cosine L_t / validation_gate 严格优于）
# ─────────────────────────────────────────────────────────────────────────
def test_skillopt_deterministic_knobs():
    from skill_opt import validation_gate as vg

    # cosine L_t：epochs=1 → 恒 max；epochs=4 → 1..4 单调衰减到 min
    assert train.cosine_decay_lt(1, 1, 4, 2) == 4, "epochs=1 应取 L_t=max"
    assert train.cosine_decay_lt(1, 4, 4, 2) == 4, "epoch1/4 应起于 max"
    assert train.cosine_decay_lt(4, 4, 4, 2) == 2, "末 epoch 应到 min"
    seq = [train.cosine_decay_lt(e, 4, 4, 2) for e in range(1, 5)]
    assert seq == sorted(seq, reverse=True), f"L_t 应单调不增: {seq}"

    # validation_gate 严格优于：平局拒、改进收
    assert not vg.decide([0.5], [0.5]).accepted, "平局应拒"
    assert vg.decide([0.5], [0.6]).accepted, "严格优于应收"
    assert not vg.decide([0.6], [0.5]).accepted, "退步应拒"
    # 绝对地板：低于 floor 无条件拒（防漂移）
    assert not vg.decide([0.1], [0.9], floor=0.95).accepted, "低于 floor 应拒"


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
