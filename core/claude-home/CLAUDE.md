# 若渝AI · Agent 入口

> 本文件被 sub-agent 读取。完整系统规范见项目根目录 `CLAUDE.md`。
> 本文件只放 agent 专用的补充约束，不重复主文档内容。

## Agent 通用约束

1. **PLAN_ID/STEP 必传**：所有被 plan 调度的 agent prompt 必须含 `PLAN_ID:` 和 `STEP:` 字段，否则 hook 拦截 exit 2。
2. **不读不改 CLAUDE.md**：agent 产出走 `workspace/`，禁止写 `core/` 或 `.claude/`。
3. **cluster 为单位**：章节只是格式输出，所有质检/状态/学习以 cluster 为单位。
4. **作者风格档 = 第一权威**：通用规则仅在作者档未规定时兜底。
5. **advisory vs hard_gate**：风格/工艺走 advisory 可豁免（理由<300字），一致性/穿帮/格式契约 = hard_gate 不可豁免。
6. **gen-model 复刻强制**：蒸馏复刻走 `distill_replicate.py`（gen-model），禁 Claude sub-agent 复刻。
7. **freestyle 默认**：writer 不传章数+字数，splitter 按 3000-4500 CJK/章切。
8. **倒叙由 outline 排 storyboard + writer 按序写**：splitter 不重排。

## 路径权威

- 风格库：`workspace/styles/{书名}/`
- 小说项目：`workspace/novels/{书名}/`
- 系统经验：`core/claude-home/lessons/`

<!-- 2026-06-18 系统整改：从470行精简为指针+补充，消除与根目录CLAUDE.md的双重口径冲突 -->
