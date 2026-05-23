# Hierarchical Meta-Learning 路线图（v22.5 L14）

## 背景

ICLR 2026 Recursive Self-Improvement Workshop 共识：
- RSI 已 deployed 不是 thought experiment
- Gödel Agent 风格：agents 改自己的 logic
- Hierarchical summary：lessons 多层 abstraction
- meta-meta-learning：可以无限层级

## 我们现状

v22 SE 已实现：
- ✅ SE4 三角共演化（Proposer/Solver/Judge）
- ✅ SE3 meta-prompt-optimizer

**缺**：
- ❌ meta-meta：谁审 meta-prompt-optimizer 自己？
- ❌ Hierarchical summary：lessons flat
- ❌ Gödel Agent 风格 self-referential

## 路线图

### Phase 1: meta-prompt-optimizer 自审查
- ✅ reward_hacking_detector（L15 已实施）
- ❌ quarterly user review（每 90 章用户审 evolution history）

### Phase 2: Hierarchical Skill Abstraction
当前 flat list，升级 3 层：
- Level 0: 具体 ch event
- Level 1: scene_type 级 pattern
- Level 2: 跨 scene_type 元规则

skill_evolver 加 abstract_up()：周期把 N 个低层 → 高层

### Phase 3: Gödel Agent（极高级慎做）
agent 改自己 prompt + logic
必备防御：
- 每改一步必经 reward_hacking_detector
- 每改一步必跑 regression_test
- 失败自动 revert
- 月度审

### Phase 4: Self-Play 训练数据（SWE-RL）
- bug injector + solver
- 累积 → DPO fine-tune

## RSI 4 道防线

1. ✅ Reward hacking detector（L15）
2. ✅ Regression test gold suite（L10）
3. ❌ User quarterly review
4. ⚠️ Rollback（git commit 有但无 auto revert）

## 参考

- [ICLR 2026 RSI Workshop](https://recursive-workshop.github.io/)
- [Gödel Agent arxiv 2410.04444](https://arxiv.org/html/2410.04444v1)
- [SkillRL arxiv 2602.08234](https://arxiv.org/html/2602.08234v1)
