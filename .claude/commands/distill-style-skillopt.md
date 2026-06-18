---
description: SkillOpt 范式精化已有的 skill_FINAL.md (epoch=4 训练循环 + bounded edit + held-out gate)。适合已蒸过、想进一步贴近作者风格的场景。新书首蒸仍走 /distill-style。
---

你是若渝AI的 SkillOpt 蒸馏精化调度器。

$ARGUMENTS

---

# /distill-style-skillopt · SkillOpt 范式精化

## 业界源
Microsoft Research arXiv:2605.23904 + microsoft/SkillOpt
核心思想:skill.md 当可训练"权重",冻结目标模型,独立 optimizer 据 trajectory 改 skill,
held-out validation 严格优于才升级,bounded edit 控破坏,reject buffer 防重蹈。

## 适用场景
| 场景 | 用 |
|---|---|
| 新书首蒸,从零开始 | `/distill-style` |
| 已有 skill_FINAL.md,想进一步精化 | `/distill-style-skillopt` (本命令) |
| 实测复刻不满意但 SFS 已 A 级 | `/distill-style-skillopt` |

## 前置
- `<风格库>/skill_FINAL.md` 存在 (经 `/distill-style` 蒸出)
- `<风格库>/cluster_index.json` 存在
- `<风格库>/原文/` 含足够章节文本 (SFS 多基线评分用)
- **双路由可选**:
  - `--reward-route distill` (默认): 复刻→SFS 评分,只依赖风格库原文
  - `--reward-route writing`: 读写作产物 audit/judge,需先用该 skill 写过 cluster

## 流程(5 步 · plan_tracker 强制)

### Step 1: preflight + skill 切三段
- 验证 skill_FINAL.md / cluster_index.json 存在
- 跑 `skill_compactor.py` 切 SLOW/FAST/REFERENCE 三段
  - SLOW(量化指纹) → PROTECTED,optimizer 不许动
  - FAST(硬规则+golden) → SkillOpt 训练对象
  - REFERENCE(校准/版本/开发者注释) → 移走不进 prompt

### Step 2: 数据集切分
- 跑 `dataset.py` 切 train/select/test = 60/20/20
- 同 seed 重跑稳定 (论文 SearchQA 范式)

### Step 3: 主训练循环(论文超参)
```
epoch = 4
rollout_batch = 40
minibatch = 8
L_t cosine decay 4 → 2
```
每 epoch 多 step,每 step:
1. rollout: 当前 skill × minibatch → trajectory + reward
2. optimizer: trajectory + reject_buffer → ≤ L_t 条 patch
3. patch_applier: 应用 patch → candidate
4. validation_gate: eval(current, sel) vs eval(candidate, sel) 严格 `>` 才接受
5. ACCEPT → 升级 current_skill,记 best;REJECT → 入 reject_buffer (epoch-local 反哺)

### Step 4: 升级 best_skill → skill_FINAL
- 备份旧版到 skill_FINAL.md.bak
- 把 train 产 best_skill.md 覆写 skill_FINAL.md

### Step 5: 回灌验证(Article 6 严闭环)
- 沿用 `distill_finalize_verify.py`
- 不过 exit 2,plan-end 拦截

## 北极星纪律
| 原则 | SkillOpt 落地 |
|---|---|
| ① 贴近作者风格 | trajectory-driven 比一次性蒸馏更逼近 |
| ② cluster 单位 | 每 rollout=1 cluster |
| ③ 涟漪驱动非预设 | reward 来自真实写作链路 binary 信号 |
| ④ 章节纯格式 | rollout 在 cluster 层 |
| ⑤ 不干涉模型判断 | reward 只读现有 judge/scanner binary,不引入新 hard_gate |
| ⑥ 清旧码 | 替代 dimension_evolver |

## Plan 强制规划
```bash
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command distill-style-skillopt \
  --project "<风格库名>")
```

## 失败处理
- Step 3 全 epoch 全 REJECT → 不升级 skill_FINAL,best_skill 仍是初始,接受失败(说明现 skill 已经局部最优)
- Step 5 回灌 exit 2 → rollback `cp skill_FINAL.md.bak skill_FINAL.md`

## 关联
- 业界源: https://github.com/microsoft/SkillOpt
- 论文: arXiv:2605.23904
- 落地代码: `core/scripts/skill_opt/` (5 模块 + train.py)
- 测试: tests/test_skill_opt_stage{0-3}.py (57 测全绿)
