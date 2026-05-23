# Lessons Extractor Agent · Prompt 模板

> 阶段 6（出货终版）完成后由主代理自动 spawn 此 agent，从本次蒸馏的 distillation_log + lessons_learned_*.md 中提取跨项目通用教训，追加到经验库。

## 使用方式

主代理在阶段 6 出货完成后，spawn 一个 Agent：

```
Agent({
  description: "提取蒸馏教训到经验库",
  prompt: <下面的 EXTRACTOR_PROMPT 模板，替换 {project_dir} 占位符>,
  subagent_type: "general-purpose"
})
```

## EXTRACTOR_PROMPT 模板（替换 {project_dir}）

```
PROJECT: <项目名>
CHAPTER: lessons-extraction
MODE: lessons-self-learning

开工前用 Skill 工具加载 pua skill。

任务：从本次蒸馏的项目日志中提取跨项目通用教训，追加到全局经验库。

**输入文件**：
- 本次蒸馏的 distillation_log.md：{project_dir}/distillation_log.md
- 本次蒸馏的所有 lessons_learned_*.md：{project_dir}/lessons_learned_*.md
- 现有经验库（必读，用于去重）：<REPO_ROOT>/core/claude-home/lessons/distill-style-lessons.md

**任务步骤**：

1. **读取现有经验库**：完整读取 distill-style-lessons.md，建立现有教训索引（按 L1.x / L2.x / L3.x / L4.x / L5.x 分类）

2. **读取本次蒸馏的全部日志**：扫描所有 distillation_log + lessons_learned 文件，标识"发现 / 教训 / 救援记录 / 问题"等关键段落

3. **提取通用教训**（关键判定）：
   每条候选教训用以下规则过滤：

   ✅ **应该提取**：
   - 调度层问题（hook / agent / 工作目录 / 配额）
   - Agent 执行模式（turn 管理 / 过度优化 / 救援规则）
   - JSON / 数据质量问题（转义 / 校验 / 损坏修复）
   - 蒸馏算法本身的方法论发现（章型 / 区间 / 颗粒度）
   - 评估工具盲点
   - 自动化流程的可复用模式

   ❌ **不要提取**：
   - 项目特定的角色名（如「林七夜」「赵空城」）
   - 项目特定的术语（如「精神病院」「神墟」）
   - 项目特定的章节号 / 数值
   - 已经写在 distill-style.md 命令文档里的硬性规则（不要复制）
   - 已经在经验库现有条目里相同根因的教训（不重复）

4. **去重规则**（必须严格执行）：
   - 相同根因 → 在原条目下补 `**补充细节（{date}, {project}）**：...` 行
   - 不同根因相同现象 → 新增子条目（L1.4.1 / L1.4.2）
   - 完全相同 → 跳过

5. **格式要求**（每条必须四要素）：
   ```
   ### LX.Y [优先级图标] 标题
   - **现象**：...
   - **影响**：...
   - **修复**：...
   - **预防**：...
   ```
   优先级：⛔ 红线 / ⚠️ 重要 / 💡 提示

6. **追加方式**：
   - 用 Edit 工具在对应分类（### 1./2./3./...）末尾追加新条目
   - 更新文件底部「最后更新」行
   - 添加本次提取记录到「7. 元教训」下：「### 自动提取记录 {date} / {project} / 追加 N 条 / 补充 M 条」

7. **输出报告**：返回：
   - 本次提取的教训数（新增 N 条 / 补充 M 条）
   - 跳过的重复条目数
   - 关键发现清单（≤5 条）

**严格纪律**：
- ⛔ 不要写项目特定信息进经验库
- ⛔ 不要重复已有教训
- ⛔ 不要 fabricate 教训（必须基于真实 log）
- ✅ 模糊化数值（如 "200 章" → "大批量章节"）
- ✅ 保留可执行的修复方法（具体到 prompt 文本 / 命令）

完成报告：「✅ Lessons 提取完成 → 新增 N / 补充 M / 跳过 K」+ 关键发现。失败 [PUA-REPORT]。
```

## 触发条件

`/distill-style` 命令在阶段 6 出货完成后**自动**触发 lessons extractor，不需要用户手动调用。

## 防失控

- Extractor 只能 Edit `distill-style-lessons.md`，不能 Write 新文件覆盖
- Extractor 没有 git commit 权限（已经在阶段 6 commit 过）
- Extractor 失败不阻塞蒸馏主流程（视为非关键任务）
