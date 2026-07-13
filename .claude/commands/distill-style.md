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
3. step 2.5 spawn `novel-skill-author MODE=draft`：从作者档亲笔撰写 `skill_v0.md`（无 gap 首版）；`gen_creative.py --mode distill_reflect --verify` 确定性小节验收（五必备小节+≥200 字），不过 exit 2 = 重 spawn 重写。
4. step 3 spawn `novel-replica-writer`：读取 `skill_v0.md` 和首个 cluster 素材，亲笔写 `claude_scenes/scene_*.txt` 与符合 `novel-replica-writer.receipt.v1` 的 `agent_report.json`。
5. `distill_replicate.py` 用 gemini 分段润色场景稿，生成 v0 复刻终稿；`distill_av_verify.py` required 做两段式 AV 验收：首跑渲染投票任务（默认 3 票）写 `对比报告/av_v0_jobs/av_judge_jobs.json` 后 exit 2=pending → 主代理按 manifest spawn `novel-av-judge` 逐票独立判别并写批次回执 → 重跑做逐票严格验收 + 多数票聚合落 AV 报告。AV 结论是 advisory，判别补件或报告缺失是 required 失败。
6. `style_evaluator.py --multi-ref-from-dir` 对 v0 做多参考 SFS 评分。
7. step 5 重新 spawn `novel-skill-author MODE=draft`：读 SFS 差距报告（`GAP_REPORT_PATH`）与当前 skill，亲笔精化 `skill_v1.md`（只攻差距维度）；同一 `--verify` 小节验收门。
8. step 5.5 重新 spawn `novel-replica-writer` 写独立 v1 场景稿；gemini 润色后跑同款两段式 AV（jobs 目录 `对比报告/av_v1_jobs/`）、SFS 与收敛闸。严格优于才选择 v1，否则保留 v0；选中结果写 `skill_v2.md` 与 `eval_ship.json`。
9. 定稿 `skill_FINAL.md`、`作者风格_FINAL.json`，写蒸馏日志与 Git 快照；随后运行 PID advisory 阈值回测并落 `pid_bootstrap_receipt.json`。未收敛时不写阈值状态，但 required 回执仍必须存在。
10. step 7 再次 spawn `novel-replica-writer` 按 `skill_FINAL.md` 写独立回灌场景稿。`distill_finalize_verify.py --strict` 生成 arc/SFS 验证，`distill_av_verify.py` 走两段式 AV（jobs 目录 `对比报告/verify_av_jobs/`）生成 AV 报告。Agent/脚本产物齐全才可结束 plan。

## Skill author 输入（step 2.5 / 5）

```text
PLAN_ID: <plan id>
STEP: <2.5 | 5>
PROJECT: <风格库路径>
MODE: draft
AUTHOR_PROFILE_PATH: <风格库路径>/作者风格.json
GAP_REPORT_PATH: <对比报告/eval_v0.json·step 5 才传，step 2.5 省略=首版 v0>
CURRENT_SKILL_PATH: <skill_v0.md·step 5 才传>
SKILL_VERSION: <0 | 1>
OUTPUT_PATH: <风格库路径>/skill_v{N}.md
```

Agent 亲笔写纯 markdown skill（五必备小节·只写笔法不写故事内容·量化指纹逐字取自作者档）；落盘后由 `gen_creative.py --mode distill_reflect --verify --skill <OUTPUT_PATH>` 做确定性小节验收，缺节/过短 exit 2 = 重 spawn 重写。

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

## AV judge 输入（step 3 / 5.5 / 7 · distill_av_verify exit 2 后）

```text
PLAN_ID: <plan id>
STEP: <3 | 5.5 | 7>
JOBS_MANIFEST_PATH: <对比报告/<报告 stem>_jobs/av_judge_jobs.json>
```

Agent 按 manifest 逐票读 `prompt_path` 独立判别（不许互相参考、不许刻意求一致），verdict JSON 落 `output_path`，全部完成后按 manifest 的 `receipt_schema` 写 `agent_receipt.json`（agent 身份 + PLAN_ID/STEP + 逐 job 产物 SHA-256）。补件后重跑同一条 `distill_av_verify.py` 命令：逐票严格验收（4 维 verdict 必须「命中/走味」二选一、判走味必须给指证 reason，不符 = 该票退回 pending）+ 多数票聚合落 advisory 报告。

## 验收

- 所有 plan required step 为 completed，且 expected outputs 存在。
- step 2.5、5 的 `skill_v{N}.md` 由 `novel-skill-author` 亲笔产出，且通过 `gen_creative --mode distill_reflect --verify` 小节验收。
- step 3、5.5、7 的 Agent 回执内容、PLAN_ID、STEP 与场景文件一致；AV 批次回执逐 job SHA-256 与 verdict 文件一致。
- 每轮复刻终稿都由 `distill_replicate.py --claude-scenes-dir` 落盘；每份 AV 报告都由 `distill_av_verify.py` 聚合 novel-av-judge 票落盘。
- SFS 使用多参考原文；AV 走味不改 hard gate。
- PID 回测回执标明 `converged` 或 `not_converged`；只有有效执行才能完成该 required step。
- `skill_FINAL.md`、`作者风格_FINAL.json`、最终 verify 与 AV 报告、Git marker 全部存在。

缺输入、Agent 回执、gemini 调用或 required 报告时立即停止当前 plan，不得跳过或伪造产物。
