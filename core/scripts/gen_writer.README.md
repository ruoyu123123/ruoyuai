# gen_writer.py · 故事块 gemini 分段润色引擎（v29）

`gen_writer.py` 是 `/cluster-write` 第 2b 步的内部脚本。v29 正文生成分两阶段，本脚本只承载**第二阶段的 gemini 分段润色**：

1. **step 2a（Claude 亲笔）**：`novel-writer` agent 读 manifest / 风格 skill / brief / research 后**逐场景亲笔写作**，分场景落盘到 `<project>/章节/cluster_<key>_draft/claude_scenes/scene_*.txt`，并产 `changes_claude.json`。
2. **step 2b（gemini 润色）· 本脚本**：`gen_writer.py` 自动发现 `claude_scenes/` 里的场景稿，逐场景段调当前 active gen-model（gemini）按作者风格档**等体量重写润色**，两道段级确定性守恒：字数守恒带 `[0.85, 1.30]`（超界带字数指令重试 1 次）+ **引号占比守恒**（润色段引号内 CJK 占比净降超界——低于原段 ×0.85 且绝对降幅 ≥2 个百分点——带引号守恒指令重试 1 次，重试仍降则**整段保留 Claude 亲笔原稿**，遥测记 `dialogue_telemetry.quote_guard`），拼接出终稿 `cluster_<key>_draft.txt`。落盘前还有一道**段长对症**（确定性·作者档 `single_sentence_para_ratio < 0.5` 的密实长段作者才启用）：`enforce_short_paragraphs` 把过长非对话段按句末切短、`reflow_merge_dense_paragraphs` 把 gemini 碎化的连续叙述短段合并回作者厚段基线——修 gemini 逐段润色对段长/单句独行率的破坏；**过并态**（句子并成超长逗号长链）由 writer step 2c 作者金标准自验做拆句最后一里（见 novel-writer 合约）。

本脚本**只做 gemini 分段润色**——不从零生成正文（v29 已删除从零生成路径），不切章、不写标题、不回写事实/伏笔、不做状态保存。缺 `claude_scenes/` 目录直接 `[FATAL]` 响亮失败退出（不兼容不降级：没有亲笔场景稿就不回退到从零生成）。

## 所属链路

```
/cluster-write
  1.  build_manifest.py 生成 manifest 和 style_directive
  2a. novel-writer agent 逐场景亲笔写 claude_scenes/scene_*.txt
  2b. gen_writer.py 分段 gemini 润色 → cluster_<key>_draft.txt
  3.  audit_hub / reading / voice / foreshadow / reflect / summarize
  4.  splitter 和 titles 在审核完成后执行
```

`gen_writer.py` 不是用户入口，也不是单章写作入口。

## 配置

```bash
python core/scripts/gen_model.py add <profile_name>
python core/scripts/gen_model.py switch <profile_name>
python core/scripts/gen_model.py show
```

`.env` 使用 OpenAI 兼容 profile：

```env
GEN_MODEL_ACTIVE=gemini_example
GEN_MODEL_FALLBACK_CHAIN=

GEN__gemini_example__MODEL=gemini-3.1-pro-preview
GEN__gemini_example__BASE_URL=https://example.com/v1
GEN__gemini_example__API_KEY=sk-...
GEN__gemini_example__TEMPERATURE=1.0
GEN__gemini_example__MAX_TOKENS=
```

## 调用

正式写作入口只有 `/cluster-write`。本脚本核心 CLI 参数只有两个：

```bash
python core/scripts/gen_writer.py \
  --project "workspace/novels/<book>" \
  --cluster 6
```

| 参数 | 说明 |
|---|---|
| `--project` | 小说项目根目录（required） |
| `--cluster` | cluster 序号，整数（required） |
| `--dry-run` | 开发用：只输出首段润色 prompt，不调 API |

脚本据 `--project` / `--cluster` 反查 cluster key 与起始章号，自动定位 `claude_scenes/` 场景稿；**不接收**目标章数、目标字数或章首/章末参数（旧的 `--chapter-end` / `--target-cjk` / `--chapter-start` 已随 v29 删除，章节数由后续 splitter 按字数决定）。

## 输出

```text
<project>/章节/cluster_<key>_draft/cluster_<key>_draft.txt          # gemini 润色终稿
<project>/章节/cluster_<key>_draft/cluster_<key>_changes.json       # 自评 + 遥测
```

`cluster_<key>_changes.json` 合并：

- `novel-writer` agent 的创作期自评 / waivers（`self_eval`，step 2a 产）；
- 本脚本的确定性润色遥测（`cjk_actual` / `word_count_cjk` / `length_telemetry` / 段级守恒留痕含 `quote_guard` 引号守恒核查、使用的 gen-model profile / model）；字数与对话遥测（`polished_dialogue_cjk` / `polished_dialogue_ratio`）由 `changes_io.sync_cjk_actual` 从磁盘草稿真值回写（与 `gen_fixer` 改稿后共用同一入口，杜绝两处各写各的）；
- 标记 `writer_mode: "claude_draft_gemini_polish_v29"`。

角色、道具、关系、locked facts、伏笔等 factual 状态由 Claude agent（novel-archivist / foreshadower）在 `/cluster-save-state` 阶段读正文梳理 → `apply_archive.py` 确定性回库，本脚本不自报任何 factual 状态。

## Fallback

active profile 调用失败时，脚本按 `GEN_MODEL_FALLBACK_CHAIN` 顺序尝试可用 profile。全链失败时抛 `GenModelExhaustedError` 并返回非零退出码。

## 故障排查

| 报错 | 处理 |
|---|---|
| `缺 claude_scenes/ 目录`（`[FATAL]`） | 先跑 step 2a 让 `novel-writer` agent 亲笔逐场景写作落盘 `claude_scenes/scene_*.txt`，再跑本脚本 |
| `GEN_MODEL_ACTIVE 字段未设置` | 运行 `gen_model.py list`，再 `gen_model.py switch <name>` |
| active profile 不存在 | 检查 `.env` profile 名 |
| API key 为空或错误 | 更新 `GEN__<name>__API_KEY` |
| API 429 / timeout | 配置 fallback profile |
| 某段守恒比超 `[0.85, 1.30]` | 脚本带字数指令重试 1 次；仍超界交 `/cluster-write` 修复流程处理 |
| 某段引号占比净降超界 | 脚本带引号守恒指令重试 1 次；仍降则整段保留 Claude 亲笔原稿（`dialogue_telemetry.quote_guard.scenes_kept_claude` 可查） |
