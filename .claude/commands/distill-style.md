---
description: 蒸馏作者风格，使用 Claude 场景草稿、gemini 分段润色、SFS 与 AV 完成同栈验证。
---

# /distill-style

$ARGUMENTS

用于新风格库的首次蒸馏。已有 `skill_FINAL.md` 的继续优化使用 `/distill-style-skillopt`。

## Plan

开工前必须创建 plan：

```bash
python core/scripts/plan_tracker.py create --command distill-style --project "<风格库路径>"
```

步骤、required 产物和编号以 `core/claude-home/plans/distill-style.plan.json` 为唯一来源。每次 spawn Agent 的 prompt 必须含 `PLAN_ID` 与当前 `STEP`。

## 输入与目录

- 风格项目：`workspace/styles/<书名>/`
- 原始正文：`蒸馏进度/.wal/raw_author_text.txt`
- 分章原文：`原文/第N章.txt`
- cluster 索引：`cluster_index.json`
- 复刻草稿：`复刻测试/<轮次>/claude_scenes/scene_*.txt`
- 复刻终稿：`复刻测试/<轮次>/replica.txt`
- 评分与验证：`对比报告/*.json`

## Required 流程

1. 预处理作者正文、计算确定性表层指标并生成 `cluster_index.json`。
2. 按 cluster 运行表层蒸馏与 arc 聚合，生成 `作者风格.json`。
3. 生成 `skill_v0.md`。
4. step 3 spawn `novel-replica-writer`：读取 `skill_v0.md` 和首个 cluster 素材，亲笔写 `claude_scenes/scene_*.txt` 与符合 `novel-replica-writer.receipt.v1` 的 `agent_report.json`。
5. `distill_replicate.py` 用 gemini 分段润色场景稿，生成 v0 复刻终稿；`distill_av_verify.py` required 落 AV 报告。AV 结论是 advisory，执行或报告缺失是 required 失败。
6. `style_evaluator.py --multi-ref-from-dir` 对 v0 做多参考 SFS 评分。
7. 根据差距生成 `skill_v1.md`。
8. step 5.5 重新 spawn `novel-replica-writer` 写独立 v1 场景稿；gemini 润色后跑 AV、SFS 与收敛闸。严格优于才选择 v1，否则保留 v0；选中结果写 `skill_v2.md` 与 `eval_ship.json`。
9. 定稿 `skill_FINAL.md`、`作者风格_FINAL.json`，写蒸馏日志与 Git 快照；随后运行 PID advisory 阈值回测并落 `pid_bootstrap_receipt.json`。未收敛时不写阈值状态，但 required 回执仍必须存在。
10. step 7 再次 spawn `novel-replica-writer` 按 `skill_FINAL.md` 写独立回灌场景稿。`distill_finalize_verify.py --strict` 生成 arc/SFS 验证，`distill_av_verify.py` 生成 AV 报告。三项 Agent/脚本产物齐全才可结束 plan。

## Replica writer 输入

```text
PLAN_ID: <plan id>
STEP: <3 | 5.5 | 7>
PROJECT: <风格库路径>
CLUSTER_ID: <cluster id>
MODE: style-replica-draft
STYLE_SKILL_PATH: <skill 路径>
SCENES_DIR: <本轮唯一场景目录>
```

Agent 只产 Claude 草稿与 `agent_report.json`，不得调用 gemini、写复刻终稿、SFS/AV 报告或最终 skill。

## 验收

- 所有 plan required step 为 completed，且 expected outputs 存在。
- step 3、5.5、7 的 Agent 回执内容、PLAN_ID、STEP 与场景文件一致。
- 每轮复刻终稿都由 `distill_replicate.py --claude-scenes-dir` 落盘。
- SFS 使用多参考原文；AV 走味不改 hard gate。
- PID 回测回执标明 `converged` 或 `not_converged`；只有有效执行才能完成该 required step。
- `skill_FINAL.md`、`作者风格_FINAL.json`、最终 verify 与 AV 报告、Git marker 全部存在。

缺输入、Agent 回执、gemini 调用或 required 报告时立即停止当前 plan，不得跳过或伪造产物。
