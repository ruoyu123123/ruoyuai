<!-- 🔴 2026-06-29 NN训练:情绪VAD回归 -->
# 情绪 VAD 回归模型 · 接入若渝设计（INTEGRATION）

> **一句话**：训出的 VAD 模型是一个**传感器**，只为现有的 advisory 子系统提供更准的「情绪三维读数」，
> 替换当前的**启发式（词典查表 / 关键词映射 / summarizer 五档手判）**。北极星⑤：全程 advisory，
> 永不 hard_gate，永不覆盖 writer 的创作判断。所有接入走唯一桥 **`vad_infer.py`**（确定性）。

## 0. 唯一入口与确定性契约

- 所有消费方**只调** `vad_infer.get_predictor().predict(text)` → `{valence, arousal, dominance|None, source}`（V/A/D ∈ [0,1]）。
- 确定性：`model.eval()` + `torch.no_grad()` + fp32 + 进程级单例缓存 → 同输入恒同输出。
- 模型/词典切换由 **`RUOYU_VAD_CKPT`** 环境变量控制：
  - 设了且 checkpoint 存在 → `source="model"`（真模型）
  - 未设 → `source="lexicon"`（退**已提交的占位词典**，= 系统现状，**零行为变化**）
- `dominance`：VA 模型时为 `None`（中文无原生 D 真标注，见 §4）。消费方对 D 缺失要显式兜底（保留词典 D 或只用 V/A）。

## 1. 落点 A — Appraisal Beat 的 `vad_bin`（心理 STATE·P0）

**现状**：`vad_bin`（valence/arousal/dominance 各 `VL|L|M|H|VH`）由 **novel-summarizer**（Claude agent）
凭 6 维评价「确定性反推」——本质是 LLM 的整体手判（启发式）。

| 环节 | 文件:符号 | 现在 | 改为 |
|---|---|---|---|
| 产出 schema | `.claude/agents/novel-summarizer.md`（`vad_bin` 规范行 ~111/121/169） | summarizer 五档手判 | 规范不变（仍由 summarizer 产 beat），但 vad_bin 由脚本**确定性重算** |
| 回库 | `core/scripts/save_state.py::cmd_apply_appraisal_beats`（~L865） | 原样 append beat | append 前对每条 beat 调 `vad_infer.predict(trigger_event + derived_emotion + behavior_externalization)`，用模型 V/A 覆盖 `vad_bin.valence/arousal`；`dominance` 保留 summarizer 值（模型为 VA） |
| 注入 | `core/scripts/build_manifest.py::_collect_appraisal_directive`（~L3023）/`_appraisal_beat_to_lines`（~L2984） | 渲染 derived_emotion+appraisal+behavior（不渲染 vad_bin） | **不动**（北极星⑤：注入仍只给「为何感受+如何外化」，绝不注情绪词标签） |

**接法（确定性·幂等·env 门控·默认 off）**：在 `cmd_apply_appraisal_beats` 的 append 循环里加：
```python
# 🔴 2026-06-29 NN训练:情绪VAD回归 — vad_bin 真模型重算（advisory·env 门控·默认 off·零行为变化）
if os.environ.get("RUOYU_VAD_BIN_RECOMPUTE") == "1" and os.environ.get("RUOYU_VAD_CKPT"):
    import sys as _s; _s.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "emotion_vad"))
    from vad_infer import get_predictor, vad_bin as _bin
    p = get_predictor()
    txt = " ".join(str(b.get(k, "")) for k in ("trigger_event", "derived_emotion", "behavior_externalization"))
    r = p.predict(txt)
    if r["source"] == "model":
        old = b.get("vad_bin") or {}
        nb = _bin(r["valence"], r["arousal"])
        b["vad_bin"] = {"valence": nb["valence"], "arousal": nb["arousal"],
                        "dominance": old.get("dominance"),   # D 维保留 summarizer 判断
                        "_source": "model_va+summarizer_d"}
```
- 默认 `RUOYU_VAD_BIN_RECOMPUTE` 不设 → 完全走旧逻辑，存量书零影响。
- 仍是 advisory STATE（`vad_bin` 不进 `HARD_GATE_CODES`）。

## 2. 落点 B — `character_vad_ued_scanner._score_vad`

**现状**：`core/scripts/character_vad_ued_scanner.py::_score_vad`（~L117）对每条 utterance 用
`nrc_vad_v2_placeholder.json`（40 字）+ `cvaw_cvap_placeholder.json`（30 词）查表平均——占位词典极稀。

**改为**：
```python
def _score_vad(text):
    if os.environ.get("RUOYU_VAD_CKPT"):
        import sys as _s; _s.path.insert(0, <core/ml/emotion_vad>)
        from vad_infer import get_predictor
        r = get_predictor().predict(text)
        if r["source"] == "model" and r["valence"] is not None:
            d = r["dominance"] if r["dominance"] is not None else _lexicon_dominance(text)  # 模型 VA + 词典 D
            return (r["valence"], r["arousal"], d)
    # 未配模型 → 现有占位词典逻辑（不变）
    ...
```
- UED 计算（18 指标）不变，只是 (V,A,D) 序列来源升级。
- `D` 维：模型为 VA 时退词典 D（保 UED 的 D 轴可算）；产品若上 VAD 模型则直接用模型 D。
- mode 仍受 `CHARACTER_VAD_UED_MODE`（off/shadow/active·默认 shadow）控制，`VAD_UED_DRIFT` 仍 advisory。

## 3. 落点 C — `emotion_curve_rescan_scanner.segment_valence_curve`

**现状**：`core/scripts/emotion_curve_rescan_scanner.py::segment_valence_curve`（~L74）把每段用
`EMOTION_KEYWORDS`→`EMOTION_VALENCE`（7 类关键词→固定 valence）映射——粗糙关键词计数。

**改为**：每段调 `vad_infer.predict(segment)["valence"]` 取真 valence（替换关键词加权）。
- 段切分 / resample / `match_reagan_shape` 对账逻辑（复用 `arc_aggregator`）全不变。
- 未配模型 → 现有关键词逻辑（不变）。mode 受 `EMOTION_RESCAN_MODE`（默认 shadow）控制，`EMOTION_CURVE_RESCAN_DRIFT` 仍 advisory。

## 4. 数据局限（诚实）

- **中文无原生句级 dominance 标注**。训练数据（Chinese EmoBank CVAS/CVAT + DimABSA）只有 **valence/arousal**。
- 因此默认产 **VA 模型**：V/A 是真模型预测，**D 维仍走词典/summarizer 判断**（落点 A/B 已做兜底）。
- 想要真 D：① `data_prep.py --nrc-vad <NRC-VAD 中文词典>` 加 D 弱标签（需自行向 NRC 申请下载）→ `train.py --dims vad`；
  ② 或英文 EmoBank（全 VAD）跨语言迁移（XLM-R）。两者都是**弱/迁移监督**，D 维精度天然低于 V/A（业界共识：D 最难）。
- **繁简**：训练数据是繁体（台湾 NYCU/DimABSA）。系统正文是简体 → 生产应 `data_prep.py --simplify`（装 opencc），
  且推理输入也应同口径（可在 `vad_infer` 入口加 opencc t2s，二者一致即可）。

## 5. 灰度与回归

1. **Phase 0（零 GPU·立即可做）**：把 `core/data/*_placeholder.json` 占位词典换成 `data/processed/word_lexicon.jsonl`
   导出的真 CVAW/CVAP 词典（7762 词）——现有词典 scanner 立刻变准，无需模型。
2. **Phase 1（shadow）**：训出 checkpoint → 设 `RUOYU_VAD_CKPT`，scanner 维持 `*_MODE=shadow`（只记不判），跑几个 cluster 看读数。
3. **Phase 2（active）**：确认稳定 → `*_MODE=active`（仍 advisory 上报，不 hard_gate）；`RUOYU_VAD_BIN_RECOMPUTE=1` 开 vad_bin 重算。
4. **回归锁**：模型缺失/加载失败 → `vad_infer` 显式退词典并 stderr 标记，**绝不静默假成功**，scanner 行为退回现状（零回归）。

## 6. 北极星自检

| 问 | 答 |
|---|---|
| 更贴近作者风格？ | 是——情绪曲线/角色情绪指纹对账更准，advisory 提示 writer |
| 以 cluster 为单位？ | 是——scanner 都是 cluster 视野 |
| 涟漪/大势驱动？ | 不冲突——只做读数，不预设情绪 |
| 章节当纯格式？ | 是——不碰切章 |
| 干涉模型创作判断？ | **否**——全 advisory，永不 hard_gate，模型缺失即退现状 |
