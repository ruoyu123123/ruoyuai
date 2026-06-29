# 风格/声纹嵌入模型 — 集成设计

> 🔴 2026-06-29 NN训练:风格声纹嵌入
> 把 `train.py` 产出的 fine-tune 风格嵌入模型接回若渝AI 的两个落点：
> ① **SFS 作者级风格保真度**（直接服务北极星「写出和作者风格一致的文章」）
> ② **千人千面 角色级声纹互距**（防角色同腔）。

一个模型、两类用法，都建立在同一向量空间：**同作者/同角色靠近，跨作者/跨角色远离**。

---

## 0. 模型怎么被消费（统一入口 = embedding_store 后端）

现状 `core/scripts/embedding_store.py::_detect_backend()` 已有三档后端：
`hash`(默认/384) → `local`(bge-small-zh/512) → `mstyle`(StyleDistance/mstyledistance/768)，
经环境变量 `EMBED_BACKEND` opt-in，全系统的语义/风格 cosine 都走 `compute_embedding()`。

**新增第四档** `EMBED_BACKEND=ruoyu_style`，加载本目录训练产物：

```python
# embedding_store.py · _detect_backend() 内, 在 mstyle 分支后加：
if _eb == "ruoyu_style":
    try:
        import sentence_transformers  # noqa
        from pathlib import Path
        mp = os.environ.get("RUOYU_STYLE_MODEL",
                            str(_embed_repo_root() / "core/ml/style_embed/runs/style_embed_v1/final"))
        global _RUOYU_MODEL
        def _ruoyu_embed(t):
            global _RUOYU_MODEL
            if _RUOYU_MODEL is None:
                from sentence_transformers import SentenceTransformer
                _RUOYU_MODEL = SentenceTransformer(mp)
            return _RUOYU_MODEL.encode(t[:8000], normalize_embeddings=True).tolist()
        dim = json.loads((Path(mp)/"ruoyu_meta.json").read_text(encoding="utf-8")).get("dim", 768)
        _BACKEND = (f"ruoyu_style:{Path(mp).name}", dim, _ruoyu_embed)
        return _BACKEND
    except Exception as e:
        print(f"[embedding_store] ruoyu_style 加载失败降级: {e}", file=sys.stderr)
```

切后端务必 `embedding_store.py <proj> rebuild`（维度变 → 旧缓存 cosine=0）。`.embed_manifest.json`
已记 method+dim，维度混用自动提示重建（现成守卫）。

> 🔴 北极星纪律：默认仍是 `hash`（零回归）。`ruoyu_style` 是 **opt-in**，且只在
> **dev/蒸馏/质检工作站态**启用（frozen 写作态不含 torch）。消费方先 `is_frozen()` 跳过，绝不崩写作流水线。

---

## 1. SFS 作者级风格保真度（落点①）

### 现状（启发式 SFS）
- `core/scripts/style_evaluator.py`：12-14 维**启发式** SFS（句长/段长 JSD、标点/功能词指纹 cosine…），
  输出 `sfs_quick` 0-100。这是**手工特征**，是当前 SFS 的全部。
- `core/scripts/skill_opt/reward_sfs.py`：SkillOpt 训练 reward = `sfs_quick / 100`（蒸馏闭环的奖励信号）。
- `embedding_store.py` 的 `mstyle` 后端是目前唯一的「真模型风格余弦」，但它是**零样本通用**风格模型，
  没在我们这 10 位作者上 fine-tune 过。

### 接法：embedding-SFS 作为新子维 + reward 增强（不替换、先并行）
训练好的模型给出**作者 fine-tune 后的风格向量**，定义：

```
embedding_SFS(gen) = cosine( centroid(chunks(gen)),  author_centroid )
author_centroid    = mean( normalize( embed(原文 chunk_i) ) )   # 离线预算一次, 缓存
```

新增确定性脚本 `core/scripts/style_embed_sfs.py`（薄封装 `compute_embedding`）：
- 入参：`--gen <复刻/生成文>` `--author <书名>`（或 `--ref-dir 原文目录`）。
- 输出：`{"embedding_sfs": 0-1, "method": "ruoyu_style:...", "n_ref_chunks": N}`。
- 作者 centroid 缓存到 `workspace/styles/<书名>/.style_centroid.json`（记 method+dim，维度不符则重算）。

**两个消费方挂载**：
1. `style_evaluator.py`：在 `compute_programmatic_score` 旁**附加**一个 `embedding_sfs` 字段
   （不进加权总分，先 shadow 观察；验证 embedding-SFS 与人工判读相关性 ≥ 启发式后，
   再升为加权维或主信号）。北极星⑤：先顾问、后定夺。
2. `reward_sfs.py`：把 reward 从纯 `sfs_quick/100` 升级为
   `reward = α·(sfs_quick/100) + (1-α)·embedding_sfs`（默认 α=0.5，env `SFS_EMBED_WEIGHT` 可调）。
   让 SkillOpt 的「skill 能不能让目标 LLM 模仿出风格」用**真风格模型**度量，而非只看手工指纹。

### 为什么这是北极星级升级
北极星终极目标 = 写出和作者风格一致的文章。启发式 SFS 只能比「句长分布像不像」，
**比不了整体笔触神似**。fine-tune 的 authorship-verification 模型恰恰学的是「这是不是同一作者写的」——
和北极星目标**同构**。`eval.py` 已证现状 char-3gram 基线 test_unseen AUC=0.83，NN 模型目标是显著超过它。

---

## 2. 千人千面 角色级声纹互距（落点②）

### 现状（字符 3-gram / 启发式 voice）
- `core/scripts/character_distinctiveness_scanner.py`：code `INTER_CHARACTER_VOICE_COLLAPSE`（advisory）。
  跨角色区分度用**字符 3-gram bootstrap cosine** 算 pairwise distance + Gini。`eval.py` 实测
  此法角色声纹 AUC 仅 **0.757**（全系统最弱处）。
- `core/scripts/cross_scene_voice_drift_scanner.py`：code `VOICE_DRIFT_CROSS_SCENE`（advisory）。
  同角色跨场景漂移，用句长/口癖/标点启发式。
- `embedding_store.py::store_character_baseline / compute_character_drift`：已用 `compute_embedding`
  做角色 baseline 向量 + drift，但默认 hash 后端（风格语义=0）。

### 接法 A：character_distinctiveness_scanner 加 NN 后端（跨角色互距）
在 scanner 里把 `_bootstrap_distance`（字符 3-gram）替换为**声纹嵌入 cosine distance**，env 门控：

```python
# character_distinctiveness_scanner.py
def _pair_distance(a_text, b_text):
    if os.environ.get("CHARACTER_VOICE_EMBED") == "1":
        from embedding_store import compute_embedding, cosine_similarity, assert_mstyle_backend
        # 复用 assert 风格的硬断言：要求 ruoyu_style 后端, 不静默降级 hash 冒充
        va, vb = compute_embedding(a_text), compute_embedding(b_text)
        return round(1.0 - cosine_similarity(va, vb), 4)
    return _bootstrap_distance(a_text, b_text)  # 旧 char-3gram 兜底
```

- 角色级模型用 `train.py --task character` 训（DramaCV 协议：每角色累积台词 → 声纹向量）。
- 新 code 名沿用用户命名 `CROSS_CHARACTER_VOICE_COLLISION`（= 现 `INTER_CHARACTER_VOICE_COLLAPSE` 的
  NN 升级版）：min/mean inter-character cosine 高于作者 baseline → 角色同腔 → advisory。
- 阈值来自作者档 `character_voice_gini_baseline`（第一权威），无则 NN 模型在该作者原文上标定的
  分位数兜底（不是拍脑袋常数）。

### 接法 B：cross_scene_voice_drift + embedding_store drift 用同模型
同角色跨场景/跨 cluster 漂移：`compute_character_drift` 已是 `1 - cosine(baseline, recent)` 框架，
切到 `EMBED_BACKEND=ruoyu_style` 即把「漂移」从 hash 假语义升级为真声纹漂移。`VOICE_DRIFT_CROSS_SCENE`
保持 advisory。

### 北极星边界
- code `CROSS_CHARACTER_VOICE_COLLISION` / `INTER_CHARACTER_VOICE_COLLAPSE` / `VOICE_DRIFT_CROSS_SCENE`
  **绝不进 `audit_hub.HARD_GATE_CODES`**（北极星②/⑤ 顾问非法官）。
- 配角戏分少、独白驱动、群像题材 → 作者档可豁免（advisory 本就可豁免）。
- mode env：`off / shadow（默认） / active`，与现 scanner 一致。

---

## 3. 确定性 / advisory / 北极星⑤ 边界（三条铁律）

| 维度 | 设计 |
|---|---|
| **确定性** | 模型 `eval()` 模式、固定 precision、`normalize_embeddings=True`、固定分块（data_prep 的段落窗口规则）、作者/角色 centroid 缓存带 method+dim 指纹 → 同输入同输出。cosine 是确定函数。`.embed_manifest.json` 维度守卫防混用。 |
| **advisory** | 所有新信号（embedding-SFS 子维、`CROSS_CHARACTER_VOICE_COLLISION`）默认 `gate_level=advisory`，先 shadow 观察。**绝不新增 hard_gate**（SkillOpt 北极星纪律：reward 只读现有 binary 信号，不引入新 hard_gate）。 |
| **北极星⑤ 不干涉模型判断** | 模型是**顾问非法官**：作者风格档仍是第一权威；embedding-SFS 是「贴不贴作者」的度量，不是「好不好」的裁决；声纹互距只提示「可能同腔」，writer 有理由可豁免（< 300 字，具体到本 cluster）。绝不机械覆盖模型创作选择。 |

边界自检（改集成时必答）：
1. 是否让产出更贴近作者风格？✅ embedding-SFS 直接度量作者神似。
2. 是否以 cluster 为单位？✅ 生成文 centroid 在 cluster 草稿上算。
3. 是否 advisory 不越权？✅ 全 advisory，零新 hard_gate。
4. 是否动了 hard_gate 清单 / `_gate_level_for` 裁决？❌ 没动（北极星不变量回归锁 C08 保持绿）。

---

## 4. 落地顺序（建议）
1. 主代理跑 `train.py --task author`（作者级）→ `eval.py` 验 AUC 超基线（char-3gram test_seen 0.88 / unseen 0.83）。
2. 写 `style_embed_sfs.py` + embedding_store `ruoyu_style` 后端；`style_evaluator` 附 `embedding_sfs` 字段（shadow）。
3. `reward_sfs.py` 接 `SFS_EMBED_WEIGHT`（默认 0.5）→ SkillOpt 用真风格度量。
4. 跑 `train.py --task character`（角色级·phase2）→ `eval.py --character` 验声纹 AUC 超 0.757。
5. `character_distinctiveness_scanner` 接 `CHARACTER_VOICE_EMBED=1` NN 后端（shadow → active）。
6. 全程跑 `tests/test_north_star_invariants.py` 确认 hard_gate 清单/裁决不变（零回归）。
