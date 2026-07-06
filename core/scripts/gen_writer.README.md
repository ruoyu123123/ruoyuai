# gen_writer.py · 故事块正文生成器

`gen_writer.py` 是 `/cluster-write` 第 2 步的内部脚本。它只负责调用当前 active gen-model 写出**整块 cluster 草稿**和 writer 创作期自评；切章、标题、事实回写、伏笔回写和状态保存都不在本脚本内完成。

## 所属链路

```
/cluster-write
  1. build_manifest.py 生成 manifest 和 style_directive
  2. gen_writer.py 写 cluster_<key>_draft.txt
  3. audit_hub / reading / voice / foreshadow / reflect / summarize
  4. splitter 和 titles 在审核完成后执行
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
GEN_MODEL_ACTIVE=deepseek_example
GEN_MODEL_FALLBACK_CHAIN=

GEN__deepseek_example__MODEL=deepseek-v4-pro
GEN__deepseek_example__BASE_URL=https://example.com/v1
GEN__deepseek_example__API_KEY=sk-...
GEN__deepseek_example__TEMPERATURE=0.8
GEN__deepseek_example__MAX_TOKENS=
```

模型能力探测：

```bash
python core/scripts/model_probe.py
```

## 内部调试调用

正式写作入口只有 `/cluster-write` step 2。下面命令只用于开发者复现 prompt、排查 profile 或跑 dry-run，不作为用户写作入口。

```bash
python core/scripts/gen_writer.py \
  --project "workspace/novels/<book>" \
  --cluster 1
```

参数：

| 参数 | 说明 |
|---|---|
| `--project` | 小说项目根目录 |
| `--cluster` | cluster 序号 |
| `--dry-run` | 只输出 prompt，不调用 API |

默认是 v27 freestyle：writer 不接收目标章数，也不接收目标字数；它按 `cluster.scope_summary`、`scene_storyboard`、作者风格档、manifest 和调研 cache 写完整故事块。章节数由后续 splitter 按字数决定。

## 输出

```text
<project>/章节/cluster_<key>_draft/cluster_<key>_draft.txt
<project>/章节/cluster_<key>_draft/cluster_<key>_changes.json
```

`cluster_<key>_changes.json` 只承载：

- writer 创作期自评；
- waivers；
- 确定性遥测；
- 使用的 gen-model profile / model。

角色、道具、关系、locked facts、伏笔等 factual 状态由 Claude agent 在 `/cluster-save-state` 阶段读正文梳理并回库，writer 不自报事实。

## Fallback

active profile 调用失败时，脚本按 `GEN_MODEL_FALLBACK_CHAIN` 顺序尝试可用 profile。全链失败时抛 `GenModelExhaustedError` 并返回非零退出码。

## 故障排查

| 报错 | 处理 |
|---|---|
| `GEN_MODEL_ACTIVE 字段未设置` | 运行 `gen_model.py list`，再 `gen_model.py switch <name>` |
| active profile 不存在 | 检查 `.env` profile 名 |
| API key 为空或错误 | 更新 `GEN__<name>__API_KEY` |
| API 429 / timeout | 配置 fallback profile |
| 输出缺 changes JSON | 由脚本续写兜底；仍失败则交 `/cluster-write` 停止并报告 |
| 正文过短 | 脚本会触发 expand 续写；仍不足则由 `/cluster-write` 进入修复流程 |
