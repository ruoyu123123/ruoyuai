"""skill_opt — SkillOpt 范式蒸馏闭环

业界源：Microsoft Research arXiv:2605.23904 + microsoft/SkillOpt
核心思想：把 skill.md 当可训练"权重",冻结目标模型,独立 optimizer 据 trajectory 改 skill,
held-out validation 严格优于才升级,bounded edit 控破坏,reject buffer 防重蹈覆辙。

模块清单:
- dataset        : train/select/test 集划分 (cluster_index → 60/20/20)
- rollout        : 驱动 distill_replicate 跑 batch 收 trajectory + reward
- reward         : 多信号聚合成 binary reward (audit/voice/truth/SFS)
- reject_buffer  : 失败编辑持久化 + prepend 反哺提案任务上下文
- validation_gate: 严格 `>` 判定 + 平局拒绝
- optimizer      : patch 提案任务素材组装 + novel-skill-author 提案验收 (≤L_t 截断)
- optimizer_jobs : patch 提案任务合同 (manifest 登记/required 拦停/批次回执)
- patch_applier  : difflib 兜底 apply patch 到 skill.md
- skill_compactor: 把现有臃肿 skill_FINAL.md 切 SLOW/FAST/REFERENCE 三段
- train          : 主循环 epoch=4 rollout=40 minibatch=8 Lt=4→2

北极星纪律:
- ① 优化对象=作者风格档(skill.md),不引入新硬约束
- ⑤ reward 只看 binary 现有信号,不干涉模型创作判断
- ⑥ SkillOpt 是风格 skill 的唯一训练入口
"""

__version__ = "0.1.0-stage0"
