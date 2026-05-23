# gen_writer.py · Gen-Model 正文生成器

让小说正文生成由**当前选择的生成模型**（gen-model · 用户可一行切换 DeepSeek/Kimi/GLM/Qwen/...）完成，其他流程（调研/校验/反思/save-state）保留 Claude。

## 一次性配置

### 1. 安装依赖（已就绪）
```bash
pip install openai python-dotenv
```

### 2. 编辑 `.env`（多 profile schema）

```env
# 当前激活的 profile 名
GEN_MODEL_ACTIVE=deepseek_kxaug

# Fallback 链（主 profile 429/timeout 时按序尝试）
GEN_MODEL_FALLBACK_CHAIN=

# Profile（按需复制模板新增）
GEN__deepseek_kxaug__MODEL=deepseek-v4-pro
GEN__deepseek_kxaug__BASE_URL=https://pay.kxaug.xyz/v1
GEN__deepseek_kxaug__API_KEY=sk-...
GEN__deepseek_kxaug__TEMPERATURE=0.8
GEN__deepseek_kxaug__MAX_TOKENS=
```

模板复制：
```bash
python core/scripts/gen_model.py add <my_profile_name>
# 编辑 .env 填 model/base_url/key
python core/scripts/gen_model.py switch <my_profile_name>
```

### 3. 跑模型能力探测
```bash
python core/scripts/model_probe.py
```
缓存到 `.claude/.model_capabilities.json`，供 `gen_writer.py` / `gen_fixer.py` / `gen_creative.py` 自动读取 max_tokens。

## 用法

```bash
python core/scripts/gen_writer.py \
  --project "workspace/novels/<book>" \
  --cluster 1 \
  --chapter-start 1 --chapter-end 4 \
  --target-cjk 13000-20000
```

参数：
- `--project`：项目根路径
- `--cluster`：cluster id（整数）
- `--chapter-start / --chapter-end`：本 cluster 覆盖章号范围
- `--target-cjk`：目标 CJK 字数（如 `13000-20000`）
- `--dry-run`：只输出 prompt，不调 API

## 输出

- 正文：`<project>/章节/cluster_NNN_draft/cluster_NNN_draft.txt`
- CHANGES JSON：同目录 `cluster_NNN_changes.json`
- 含 `ecas_metadata.generated_by_profile` / `generated_by_model` 字段记录哪个 profile 生成

## Fallback 链

当 active profile 调用失败（429 / 5xx / timeout）时，自动按 `GEN_MODEL_FALLBACK_CHAIN` 顺序尝试下一个 profile（跳过 active 自身、跳过 API_KEY 空的）。

stderr 强制打印 `[FALLBACK] <from> -> <to> reason=<...>`，方便审计。

全链失败 → 抛 `GenModelExhaustedError` + 列出每个 profile 失败原因 + exit 3。

## Scanner 校验（写完自动跑）

- `narrative_short_sentence_scanner.py`
- `repeat_noun_density_scanner.py`

输出 verdict + violations_count。

## 调用流程（cluster 故事块 · 与 reflector / save-state 协作）

```
1. 调研先行                            spawn novel-researcher → .research_cache/inspiration_*.md
2. plan_tracker create write-chapter   Bash + plan_tracker.py
3. build_manifest                       Bash + build_manifest.py
4. ★ 写正文（cluster 故事块）           **gen_writer.py**（本脚本）
5. 切章                                 spawn novel-chapter-splitter（Claude）
6. Scanner 全量校验                     Bash + python scanner
7. reader-first reflector 1 轮          spawn novel-reading-reflector（Claude）
8. ★ 修复 issue                       **gen_fixer.py --mode comprehensive**
9. 主代理亲读关键段                    主代理（Claude）
10. ★ 主代理亲读后微调（如需）         **gen_fixer.py --mode polish --instructions ...**
11. ★ 字数扩写（如有章 < 2500）        **gen_fixer.py --mode word-count**
12. voice-check                         spawn novel-voice-checker（Claude）→ gen_fixer.py --mode voice-fix
13. save-state 流水线                   Bash + save_state.py
```

★ = gen-model 接管的步骤；其他由 Claude / 本地脚本。

## 故障排查

| 报错 | 原因 | 修复 |
|---|---|---|
| `GEN_MODEL_ACTIVE 字段未设置` | .env 缺关键字段 | 跑 `gen_model.py list` 看 profile，`switch <name>` 激活 |
| `active profile '<name>' 不存在` | 名字写错 | `gen_model.py list` 看可用 profile |
| `active profile '<name>' 缺 API_KEY` | .env 中 GEN__<name>__API_KEY 为空 | 编辑 .env 填入 |
| `API 调用失败: 429` | 中转站限流 | 配 fallback 链 `GEN_MODEL_FALLBACK_CHAIN=<另一 profile>` 自动切换 |
| `API 调用失败: 401` | API key 错 | 核对 key |
| `字数 < 目标下限` | 模型截断或写不够 | 跑 `gen_fixer.py --mode word-count --target-min 2500` 扩写 |
| `CHANGES JSON 解析失败` | 模型未按格式返回 | 临时降 GEN__<name>__TEMPERATURE=0.5 |
