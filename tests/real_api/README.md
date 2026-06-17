# 真 gen-model API 测试（real_api/）

> 用**真 gen-model API** 驱动管线的端到端测试，与 `tests/` 的 fake-LLM 测试互补：
> fake-LLM 验**确定性骨架**（DAG 串接 / 产物落盘·快·免费·进默认套件），
> 这里验**真模型行为**（截断 / schema 合规 / 限流 / 真实正文长度 / judge 字段完整性）——
> fake 结构性掩盖不了的东西。
>
> 缘起：用户 2026-06-17 定调「需要 API 的测试就老老实实用 API·不节省·max_tokens 拉满」。
> 详见 memory `feedback_real_api_tests_no_economize`。

## 🔴 为什么单独放这个目录

- **不被默认 `python tests/run_tests.py` 自动 glob**（它只扫 `tests/` 顶层 `test_*.py`·不进子目录）
  → 默认快速套件**不会花钱 / 不会变慢 / 不会因模型非确定性 flaky**。
- 再加 `RUOYU_RUN_REAL_API=1` env 门控双保险（误跑也只 SKIP·不调 API）。

## 怎么跑

```bash
# 跑单个（默认 profile = gemini_pro_preview·见下方 profile 说明）
RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_cluster_write_e2e.py

# 指定 profile（活跃 profile 端点故障时切已知可用的）
RUOYU_RUN_REAL_API=1 RUOYU_REAL_API_PROFILE=gemini_pro_preview \
  python tests/real_api/test_real_judge_golden.py

# 跑全部 4 个
for t in tests/real_api/test_*.py; do
  echo "=== $t ==="; RUOYU_RUN_REAL_API=1 python "$t" || echo "FAIL: $t"
done
```

> ⚠️ 全跑 4 个约 **40-60 min** + 真实 API 花费（写作循环最久）。按需单跑。

## 🔴 profile 说明（elysiver 端点故障）

`.env` 的 `GEN_MODEL_ACTIVE=elysiver` 在 2026-06-17 **端点故障**（500 upstream_stream_error /
挂死）。这些测试**默认用 `gemini_pro_preview`**（pie-xian·memory `project_genmodel_flash_locked`
记录的锁定良好配置·thinking_level=LOW·实测可用）。

- 测试内经 `RUOYU_REAL_API_PROFILE` 或 `_force_profile()` 切 profile·**不改全局 `.env`**（尊重用户配置权）。
- elysiver 恢复后想用它：`RUOYU_REAL_API_PROFILE=elysiver`。

## 4 个测试

| 文件 | 验什么 | 实测结论（2026-06-17 · gemini_pro_preview） |
|---|---|---|
| `test_real_cluster_write_e2e.py` | 真 gen-model 跑 cluster-write 7 步（真 writer/judge/splitter） | ✅ 14655 CJK 健康稿·expand 兜底·切章·reading-reflector 截断被 transport 续写兜住 |
| `test_real_judge_golden.py` | **judge 金标准 M2**：真作者原文（惊悚乐园）+ 真作者档喂 5 judge·验字段完整性 + 作者档第一权威注入 | ✅ summarizer/foreshadower/voice/validator/reading-reflector 全 ok·author_profile_missing=False |
| `test_real_judge_truncation.py` | **§3 深 schema judge 截断率**：10669 CJK 长 draft 喂 validator/voice/reading-reflector | ✅ 长输入全 ok·retries=0·无截断（截断只在完整管线 manifest 撑大总 prompt 时触发·transport 续写兜住） |
| `test_real_write_save_loop.py` | **完整写作循环**：真 cluster-write → 真 save-state → 真 emergence·验唯一没测的 outline-planner 真走向卡 | ✅ 真风格档种子 → 12246 CJK 健康稿·切 3 章·outline-planner 涌现 cluster_002 3 候选·全循环跑通 |

闭合 PROGRAM_DRIVEN.md「上线前必验」§1（M2 金标准）+ §3（深 schema 截断率）。

## 关键纪律（写新真 API 测试照搬）

- **max_tokens 拉满**：用 active profile 的满值（gemini_pro_preview=65536）·绝不为省钱传小 `--target-cjk`。
- **限流防 520**：`GEN_MIN_INTERVAL_S=4.5`（pie-xian <15rpm 端点）。
- **复用 fake-LLM 夹具**：`_Sandbox` / `_seed_min_subsystems` / `_inprocess_runner` / `_frozen_environ`
  （从 `tests/test_cluster_write_fake_llm_e2e.py` import）·但**不引入** fake seam / 网络兜底（要真调用）。
- **真风格档种子**：`_seed_real_style()` 拷真实作者档（惊悚乐园 44K）→ writer 有料产健康稿
  （空脚手架种子 → 模型无料 → 短稿假象·见 memory 0 章讨论）。
- **ASCII-only print**（在 `_utf8_io` 上下文之外的 print）：Windows GBK 控制台编码不了 emoji（✓ 等）会崩。
- **测完别泄漏真 key**进 git / 日志（gemini key 在 URL · 经 `secrets_store.redact()`）。
