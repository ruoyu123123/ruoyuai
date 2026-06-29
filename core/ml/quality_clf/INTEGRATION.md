# AI 腔判别 NN → 若渝审核体系集成设计

> 🔴 2026-06-29 NN训练:质量AI腔判别
> 把 `quality_clf` 训出的「human vs AI腔」分类器接进现有审核体系。
> **北极星⑤铁律：本模型只做 advisory（顾问），永不 hard_gate。作者风格档是第一权威。**

---

## 0. 现有审核落点（实地 grep · 接入前必读）

| 落点 | 文件:行 | 现状 |
|---|---|---|
| scanner 编排 | `core/scripts/audit_hub.py` ~L1280-1320 | 25+ scanner 经 `subprocess.run` 跑，env 传 `CLUSTER_MODE=1` |
| scanner 解析 | `audit_hub.py::_parse_scanner_json` (L460) | 顶层 key==检测器名，每 block 取 `warning/severity/gate_level/fix_hint` |
| 门级裁决 | `audit_hub.py::_gate_level_for` (L231) | code ∉ `HARD_GATE_CODES` → 自动 advisory |
| hard_gate 清单 | `audit_hub.py::HARD_GATE_CODES` (L176) + `STRUCTURE.md §11` | 单一权威·19 码·**不得另立** |
| 既有 AI 腔层 | `semantic_slop_scanner.py`（语义层 8 检测器·全 advisory）+ `function_word_fingerprint_scanner` + `prose_rhythm_scanner` + `syntactic_diversity_scanner` | 规则/统计层·本 NN 是其**语义补充** |
| 自学习 reward | `learning_loop.py`（学审核 issue 豁免→校准）+ SkillOpt `core/scripts/skill_opt/`（reward 读 judge/scanner binary 信号） | 可加 `AI_TONE_NN` 作新信号 |
| 注册表 | `core/scripts/scanner_registry.json` | scanner 元数据·层级（cluster / cross-cluster）|

**核心结论**：若渝已有「机械层(anti-slop 禁用词) + 语义层(semantic_slop) + 统计层(function_word/
prose_rhythm/syntactic_diversity)」三层 AI 腔检测，**全是 advisory**。本 NN 判别器补的是
「整段语义/节奏的端到端学习信号」——规则抓不到、但人一眼能看出的「塑料感」。定位与现有层**正交互补**，
gate_level 必须一致(advisory)。

---

## 方案 A（推荐）：作为新 advisory scanner 接进 audit_hub

### A.1 落地步骤

1. **训练出模型**（见 README）→ `core/ml/quality_clf/runs/<name>/`（model.pt + config.json + tokenizer + feat_scaler.json）。
2. **复制 scanner 进 core/scripts/**：把 `ai_tone_scanner.py` 复制/软链到 `core/scripts/ai_tone_scanner.py`
   （它依赖 `features.py`·一并复制，或保持 `quality_clf` 在 sys.path）。模型路径经 env `AI_TONE_MODEL_DIR` 注入。
3. **audit_hub 注册**（仿 `semantic_slop_scanner` 那段，~L1286）：
   ```python
   ats = _SCRIPT_DIR / "ai_tone_scanner.py"
   model_dir = os.environ.get("AI_TONE_MODEL_DIR")
   if model_dir and (ns := Path(model_dir) / "model.pt").exists():   # 未训练则跳过·不报错
       out = _run_scanner([sys.executable, str(ats), project, str(ch),
                           "--model-dir", model_dir], env_extra={"CLUSTER_MODE": "1"})
       issues += _parse_scanner_json(out, "ai_tone_scanner", {"ai_tone_nn": "风格"})
   ```
4. **gate_level 自动 advisory**：code `AI_TONE_NN` 不进 `HARD_GATE_CODES` → `_gate_level_for` 返 advisory。
   **绝不把 `AI_TONE_NN` 加进 `HARD_GATE_CODES`**（违北极星⑤·参 `test_thinking_probe_advisory.py` 同款约束）。
5. **注册表登记** `scanner_registry.json`：
   ```json
   "ai_tone_scanner": {"layer": "cluster", "script": "ai_tone_scanner.py",
     "doc": "NN human/AI腔判别·advisory", "issues_emitted": ["AI_TONE_NN"]}
   ```
6. **回归锁**：`tests/test_north_star_invariants.py` 已断言「新检测器默认 advisory」「hard_gate 三方一致」——
   加测：`AI_TONE_NN ∉ HARD_GATE_CODES`，且 scanner 自报 `gate_level=="advisory"`。

### A.2 豁免链（已天然兼容）

- writer 命中 advisory → 在 `第N章_changes.json` 的 `self_eval.waivers: [{code:"AI_TONE_NN", reason}]` 写理由(<300 字)。
- `audit_hub.py --waivers <path>` 收集 → advisory 命中转 `waived`。
- 反复豁免 → `learning_loop.py` 累计 → 产校准建议（反向调 `--threshold`）。

### A.3 阈值与 shadow 上线（北极星纪律）

- 新 scanner **先 shadow**（只记录不影响 verdict）跑 ≥1 整本，对比 NN 高分段 vs 真人金标准/编辑判断，
  校准 `--threshold`（默认 0.65）后再转 active。避免弱模型噪声 ≥ 真实效应。
- 作者风格档规定了的维度（句长/段长/标点基线）→ NN 高分但符合作者档 = 合理豁免，**不反复纠**。

---

## 方案 B：作为自学习 reward 信号

### B.1 写作闭环 reward（SkillOpt / writer feedback）

`core/scripts/skill_opt/` 的 reward 现读 `audit.verdict + reading.verdict + voice.drift==0 + truth.lie==0` 的 binary 信号。
可加一路 **binary** 信号：`ai_tone_ok = (mean P(ai) < threshold)`。

- **只读 binary·不引入新 hard_gate**（SkillOpt 北极星纪律）：把 NN 分二值化成 `ai_tone_ok ∈ {0,1}`，
  并进现有 reward 合取，**不**把连续分当梯度灌进去（防 reward hacking 把文章写成「反检测器」而非「像作者」）。
- 用作 **held-out validation gate 的一个维度**：skill 改动后，若 NN 判 AI 腔率上升 → 该 patch REJECT。

### B.2 校准回灌

`learning_loop.py` 已有 `tool_calibration_suggestions` 机制：同一 code 反复被豁免 → 产建议调阈值。
`AI_TONE_NN` 复用这条路 —— 若某作者(如句式工整的作者)持续被 NN 误判 + 合理豁免 ≥N 次 →
建议**为该作者风格档单独抬高阈值**（写 `_数据库/style_scanner_overrides.json` 的 per-author 覆盖）。

---

## 接入边界（北极星五问自检）

| 问 | 答 |
|---|---|
| ① 更贴近作者风格？ | 是·补端到端「塑料感」检测，规则层捞不到的 |
| ② 以 cluster 为单位？ | 是·cluster 草稿层跑(CLUSTER_MODE=1)，多 chunk 聚合 |
| ③ 涟漪/大势驱动？ | 不涉及·纯文体检测 |
| ④ 章节当纯格式？ | 是·切章后不再跑(只在 cluster 草稿层) |
| ⑤ **干涉模型判断？** | **否**·严格 advisory·作者档第一权威·可豁免·shadow 先行·**永不 hard_gate** |

**最大风险（诚实）**：NN 学的是 gemini 指纹/作者身份捷径而非泛化 AI 腔（见 README §数据局限）。
故**上线必走 shadow + 金标准校准**，且 reward 只取 binary、永不硬卡——错判代价被豁免链 + advisory 完全吸收。
