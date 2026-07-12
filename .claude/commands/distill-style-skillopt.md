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
5. 训练按 epoch=4、rollout=40、minibatch=8、L_t=4→2 执行。动态候选缺稿时 checkpoint 记录 epoch/step/candidate digest 后 exit 2；主代理补齐 Agent 草稿后以同一 run_id 恢复。训练完成后 `scene_jobs.py --verify-all` 生成 step 4 批次回执，逐项绑定 job key、digest、cluster 与原始 Agent 回执。
6. 提升 `best_skill.md` 为 `skill_FINAL.md`，再 spawn `novel-replica-writer` 写最终场景稿，required 运行 `distill_finalize_verify.py` 与 `distill_av_verify.py`，agent report 和两份验证报告缺一不可。

训练完成后可运行
`python core/scripts/skill_opt/train_dashboard.py --project workspace/styles/<书名>/`
只读查看收敛曲线与健康告警；看板不改变训练状态，也不参与验收判定。

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

## Plan

```bash
python core/scripts/plan_tracker.py create --command distill-style-skillopt --project "<风格库名>"
```

模板以 `core/claude-home/plans/distill-style-skillopt.plan.json` 为唯一步骤来源。
