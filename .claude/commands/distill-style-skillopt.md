---
description: 用 SkillOpt 精化已有 skill_FINAL.md，通过独立 Claude 场景稿和 held-out gate 选择提升版本。
---

# /distill-style-skillopt

$ARGUMENTS

适用于已有 `skill_FINAL.md` 的风格库。新书首次蒸馏使用 `/distill-style`。

## Required 流程

1. 校验 `skill_FINAL.md`、`cluster_index.json`，并把 skill 切为 SLOW / FAST / REFERENCE。
2. 确定性切分 train / selection / test 数据集。
3. 用固定 `run_id=skillopt-main --prepare-scene-jobs` 固化 `training_schedule.json`、`training_checkpoint.json` 并生成首批 Claude 场景任务；该步骤独立 exit 0。
4. 主代理读取 jobs manifest，对每个 `pending` 项 spawn `novel-replica-writer`。Agent 必须读取该项 `candidate_skill_path`，为指定 `cluster_id` 亲笔写 `scene_*.txt` 和 `agent_report.json` 到 `scenes_dir`。随后运行 `scene_jobs.py --verify-all` 生成 step 3 Agent 批次回执；jobs manifest 本身不是完成回执。
5. 训练按 epoch=4、rollout=40、minibatch=8、L_t=4→2 执行。每个 optimizer step 先把 trajectory minibatch + PROTECTED 段名 + reject buffer 落成 patch 提案任务（`optimizer_patch_jobs.json` + `trajectory_batch.json`）。动态候选缺场景稿、缺 rollout 内 AV 判别或缺 `novel-skill-author` patch 提案时，checkpoint 记录 epoch/step/candidate digest 后 exit 2；主代理按对应 manifest 的 `pending` 项 spawn 对应 Agent（场景稿 → `novel-replica-writer`，AV 投票任务 → `novel-av-judge` 按 `replicas/<run_id>/<cluster>_av_jobs/av_judge_jobs.json`，patch 提案 → `novel-skill-author` MODE=patch）补齐产物，再以同一 run_id 恢复（replica 幂等复用，AV 输入 digest 稳定）。训练完成后 `scene_jobs.py --verify-all` 与 `optimizer_jobs.py --verify-all` 分别生成 step 4 批次回执，逐项绑定 job key、digest 与 Agent 产物。
6. 提升 `best_skill.md` 为 `skill_FINAL.md`，再 spawn `novel-replica-writer` 写最终场景稿，required 运行 `distill_finalize_verify.py` 与 `distill_av_verify.py`。AV 是两段式：首跑渲染投票任务写 `对比报告/skillopt_av_verify_jobs/av_judge_jobs.json` 后 exit 2=pending，主代理 spawn `novel-av-judge`（PLAN_ID / STEP / JOBS_MANIFEST_PATH）逐票独立判别并写批次回执，重跑做逐票严格验收 + 多数票聚合落 advisory 报告。agent report、批次回执和两份验证报告缺一不可。

训练进度与收敛数据以 `_skillopt/train/skillopt-main/training_checkpoint.json` 为准，直接读取该文件查看。

## 场景任务合同

每项 job 固定包含：

```json
{
  "candidate_skill_path": "...",
  "candidate_skill_digest": "sha256",
  "cluster_id": "cluster_001",
  "run_id": "ep1_step0_mb_cluster_001",
  "scenes_dir": ".../claude_scenes",
  "status": "pending"
}
```

rollout 只消费由实际 `scene_*.txt` 与 `novel-replica-writer.receipt.v1` 联合验证为 ready 的目录。目录缺失、空文件、digest/PLAN_ID 不匹配都属于 required artifact 缺失，不计零分、不跳过。

## patch 提案任务合同

每项 patch job 固定包含：

```json
{
  "job_key": "<skill_digest>:<ep_step>",
  "step_tag": "ep1_step0",
  "skill_digest": "sha256",
  "agent": "novel-skill-author",
  "mode": "patch",
  "trajectory_batch_path": ".../trajectory_batch.json",
  "patches_path": ".../patches.json",
  "max_patches": 4,
  "status": "pending"
}
```

主代理对每个 `pending` 项 spawn `novel-skill-author`（MODE=patch，`TRAJECTORY_BATCH_PATH`=trajectory_batch_path、`MAX_PATCHES`=max_patches、`OUTPUT_PATH`=patches_path）。`trajectory_batch.json` 自带完整任务上下文（skill 全文、rollout 轨迹、PROTECTED 段名、reject buffer）。训练只消费通过严格验收（顶层 `{"patches": [...]}`、条目为 object、超过 MAX_PATCHES 硬截断）的提案；文件缺失或结构不合格都属于 required artifact 缺失，不当空提案、不跳过。单条 patch 的 op/anchor 合法性与 IMMUTABLE 保护由 `patch_applier` 确定性裁决，held-out validation gate 严格优于才升级。

## Plan

```bash
python core/scripts/plan_tracker.py create --command distill-style-skillopt --project "<风格库名>"
```

模板以 `core/claude-home/plans/distill-style-skillopt.plan.json` 为唯一步骤来源。
