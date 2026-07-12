# 若渝AI · Agent 入口

> 本文件被 sub-agent 读取。完整系统规范见项目根目录 `CLAUDE.md`。
> 本文件只放 agent 专用的补充约束，不重复主文档内容。

## Agent 通用约束

1. **PLAN_ID/STEP 必传**：所有被 plan 调度的 agent prompt 必须含 `PLAN_ID:` 和 `STEP:` 字段，否则 hook 拦截 exit 2。
2. **不读不改 CLAUDE.md**：agent 产出走 `workspace/`，禁止写 `core/` 或 `.claude/`。
3. **cluster 为单位**：章节只是格式输出，所有质检/状态/学习以 cluster 为单位。
4. **作者风格档 = 第一权威**：通用规则仅在作者档未规定时兜底。
5. **advisory vs hard_gate**：风格/工艺走 advisory 可豁免（理由<300字），一致性/穿帮/格式契约 = hard_gate 不可豁免。
6. **蒸馏复刻同栈（v29）**：先 spawn `novel-replica-writer` 按 skill 写 `claude_scenes/scene_*.txt` 与 `agent_report.json`，终稿只能由 `distill_replicate.py --claude-scenes-dir` 经 gemini 分段润色落盘。禁纯 gemini 从零直写复刻，也禁 agent 直产复刻终稿。
7. **正文两阶段（v29）**：novel-writer（Claude 亲笔）逐场景写 `claude_scenes/`，`gen_writer.py` 只做 gemini 分段润色出终稿；写作链不预设章数，splitter 后期按 3000-4500 CJK/章切。
8. **倒叙由 outline 排 storyboard + writer 按序写**：splitter 不重排。

## 路径权威

- 风格库：`workspace/styles/{书名}/`
- 小说项目：`workspace/novels/{书名}/`
- 系统经验：`core/claude-home/lessons/`
