---
description: 一致性调和——设定修改后审查受影响的历史章节
---

你是若渝AI的一致性调和系统。当用户修改了档案设定（人物卡/世界观/地图/关系等），扫描所有已写章节找出受影响的部分：

$ARGUMENTS

> **三段式纪律**：本命令所有 plan-step 必须遵守「研 → 干 → 反思」三段式。
> 详见 [core/claude-home/HOOKS_AND_REFLECTION.md](../../core/claude-home/HOOKS_AND_REFLECTION.md)。
> hook 自动检 research_cache + 反思文件。关键脚本输出建议过 `ai_wrapper.py` 二次复核。

---

# 🛡️ Plan 强制规划

调和流程 5 步全部挂在 plan 上——start 前必须 `plan-create` 拿 PLAN_ID，每步完成 `plan-step --n N`，末尾 `plan-end`。Hook 已强制本命令的 PLAN_ID。

```bash
# 识别变更前先建 plan
# --chapter 可填变更影响的"主章节"号；不确定时留空，由 plan_id 自动填 main
PLAN_ID=$(python core/scripts/plan_tracker.py create \
  --command reconcile \
  --project "<书名>" \
  --chapter <主章节号或留空>)
echo "PLAN_ID=$PLAN_ID"
```

所有检查 sub-agent（`novel-validator-checker`，v2 拆分后由它出 repair brief，主代理据 brief 调 `gen_fixer.py --mode validator-repair`；旧 novel-validator-repair agent 已删除）调用 prompt 顶部必须加：

```
PLAN_ID: $PLAN_ID
STEP: <当前步骤号>
```

---

# /reconcile 一致性调和流程

## 触发场景

- 用户修改了角色外貌/姓名/能力等锁定事实
- 用户调整了世界观规则
- 用户修改了地点描述
- 用户调整了角色关系
- 用户发现某章的设定错误，想倒查影响范围

---

## 第一步：识别变更

用户输入格式示例：
```
/reconcile 主角的瞳色从"黑色"改为"金色"
/reconcile 世界观：魔法需要吟唱 → 魔法无需吟唱
/reconcile 删除角色"张三"
```

从用户输入中提取：
- 变更类型（角色属性/世界规则/地点/关系/删除）
- 变更目标（具体是哪个实体、哪个字段）
- 变更前值 → 变更后值

**plan-step 1**（变更识别属于讨论步骤，无文件型 expected_outputs）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 1 --skip-output
```

---

## 第二步：扫描受影响章节

> 🔴 **章节清单权威源纪律（2026-05-30 修 #5）**：章节清单**以物理章节目录为权威**
> （`章节/第NNN章/第NNN章.txt`，零填充三位）+ `_数据库/进度.json`（`completed` 总章数 /
> `cluster_blueprint` 各 cluster 占位）佐证。**禁止**用 `_数据库/故事块摘要.json` 当章节
> 清单来源——该文件是 **cluster 维度** 的账本（`{schema_version, clusters:[]}`，由
> `cluster_summary_store.py` 按 cluster 数组原子写入），**不是章节清单**，且在 cluster
> cluster-save-state 链路里常为空数组（拿不到任何章节）。它只能做 cluster 级辅助佐证。

1. **获取所有章节清单（权威）**：Glob `章节/第*章/第*章.txt` 列出所有物理章节文件
   （零填充三位，如 `第001章/第001章.txt`）。用 `进度.json.completed` 核对总章数。
2. Read `_数据库/人物卡.json`，获取变更涉及角色的 locked_facts 历史
3. Grep 所有章节 txt 文件（搜索变更前值的关键词；glob 用 `章节/第*章/第*章.txt`）
4. （可选辅助）Read `_数据库/故事块摘要.json` 看 cluster 级摘要做交叉印证——
   若为空数组则跳过，不影响章节扫描（章节清单已由步骤 1 物理目录拿到）。
5. 收集匹配结果：
   - 匹配的章节号
   - 匹配的上下文（该关键词出现的段落）
   - 判断匹配类型：
     - 直接描写（如"他的黑色瞳孔"）→ 高影响
     - 间接提及（如"漆黑的眼睛像"）→ 中影响
     - 仅 CHANGES JSON 中 → 低影响

**plan-step 2**（扫描产物为内存清单，无文件型 expected_outputs）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 2 --skip-output
```

---

## 第三步：生成影响报告

```
🔍 一致性调和报告

变更：[变更描述]
已扫描：[N] 章

影响等级分布：
  🔴 高影响（直接描写，需重写段落）：[X] 章
  🟡 中影响（间接提及，需润色）：[Y] 章
  🟢 低影响（仅元数据，自动更新）：[Z] 章

详细清单：
┌──────┬──────────┬──────────────────────────────────┐
│ 章节 │ 影响等级 │ 匹配上下文                        │
├──────┼──────────┼──────────────────────────────────┤
│ 第3章│ 🔴       │ "他抬起头，黑色的瞳孔映着..."     │
│ 第5章│ 🟡       │ "那双漆黑如墨的眼睛..."           │
│ 第8章│ 🟢       │ CHANGES.character_changes 中提及  │
└──────┴──────────┴──────────────────────────────────┘

建议操作：
  [A] 全部自动修复（AI批量替换 + 润色）
  [B] 只修复高影响，中影响保留
  [C] 逐章确认修改
  [D] 取消（保留当前状态，只更新档案）
```

**plan-step 3**（影响报告为终端输出 + 内存清单，无文件型 expected_outputs）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 3 --skip-output
```

---

## 第四步：执行修复（根据用户选择）

### 选项A：全部自动修复

1. 对每个受影响章节，启动 Agent 子任务（v2 拆分：spawn `novel-validator-checker` 拿 repair brief → 主代理调 `gen_fixer.py --mode validator-repair --brief <path>` 修；prompt 顶部必须含 PLAN_ID/STEP）：
   ```
   Agent({
     prompt: "PLAN_ID: $PLAN_ID
       STEP: 4
       PROJECT: <书名>
       CHAPTER: N
       MODE: reconcile-repair

       修改第N章的txt文件。
       将'[变更前值]'相关描写改为'[变更后值]'。
       修改时保持段落节奏和文风，不只是机械替换。
       原文行号 X-Y 有相关描述，请润色修改。
       修改完后用 Write 覆盖原文件。",
     description: "修复第N章一致性"
   })
   ```
2. 更新对应的 CHANGES JSON（如 character_changes 中的 from/to 值）
3. 输出修复报告

### 选项B：只修复高影响

同选项A，但只处理 🔴 标记的章节，跳过 🟡 章节。

### 选项C：逐章确认

对每个章节，展示：
```
第N章受影响：
原文："..."
建议改为："..."

[Y] 采用建议 / [N] 跳过 / [M] 手动修改
```

### 选项D：只更新档案

不修改正文，只更新档案：
- 人物卡 locked_facts 追加新事实
- 旧事实标注为 "outdated_from_ch": N
- 在故事块摘要中记录"本次调和未修改正文，历史章节存在旧描述"

**plan-step 4**（修复执行后；正文/档案变更难以预先列举为文件清单，使用 --skip-output 但配合后置自检）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 4 --skip-output
```

---

## 第五步：清偿传播债务

1. Read 进度.json 的 propagation_debt 字段
2. 标记本次调和相关的债务条目：
   ```json
   {
     "source": "第N章发现",
     "target": "人物卡.json",
     "reason": "角色瞳色描写",
     "status": "resolved",
     "resolved_by": "reconcile@ISO日期"
   }
   ```

---

## 第六步：Git 提交（调和专用）

调和会修改多个历史章节，必须作为一次明确的 Git commit 作为回溯点：

**调和前**（第四步执行修复前）：
```bash
# 创建调和前的安全标签（方便一键回滚）
if command -v git >/dev/null 2>&1 && [ -d "小说_书名/.git" ]; then
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  git -C "小说_书名" tag "before-reconcile-${TIMESTAMP}"
fi
```

**调和后**（修复完成后）：
```bash
if command -v git >/dev/null 2>&1 && [ -d "小说_书名/.git" ]; then
  # add 所有被修改的章节 + 档案
  git -C "小说_书名" add "第*.txt" _数据库/
  
  CHANGE_DESC="主角瞳色 黑色→金色"   # 来自第一步识别的变更
  AFFECTED_COUNT="3"                   # 实际修复的章节数
  
  git -C "小说_书名" commit -m "fix: 调和 ${CHANGE_DESC}（影响 ${AFFECTED_COUNT} 章）" 2>&1 | tail -1
  
  echo "📌 如不满意可回滚：git -C 小说_书名 reset --hard before-reconcile-${TIMESTAMP}"
fi
```

commit 信息示例：
- 属性调和：`fix: 调和 主角瞳色 黑色→金色（影响 3 章）`
- 世界观调和：`fix: 调和 魔法规则 需要吟唱→无需吟唱（影响 8 章）`
- 角色删除：`fix: 调和 删除角色 张三（影响 5 章）`

**.bak 备份与 Git 的关系：**
- 第四步已经创建 `.bak` 备份文件（来自"硬性规则"），这是轻量保险
- Git commit 是正式的版本节点，两者互补：
  - 小修小补：用 `.bak` 还原单文件
  - 大面积调和不满意：用 `git reset --hard before-reconcile-XXX` 整体回滚
- `.bak` 文件已列入 `.gitignore`，不会污染版本历史

---

## 输出最终报告

```
✅ 一致性调和完成

变更：[变更描述]
影响范围：[X] 章
已修复：[Y] 章
保留旧版：[Z] 章（用户选择不修改）
档案已更新：人物卡 locked_facts

📌 Git 快照：[commit hash]
📌 回滚标签：before-reconcile-[时间戳]

建议：
  - 对修复的章节执行 /check-quality 验证质量未下降
  - 如果有 🟡 章节未修复，未来写作时可能出现描述不一致，注意
  - 如不满意，执行 git reset --hard before-reconcile-[时间戳] 整体回滚
```

**plan-step 5 + plan-end**（收尾校验本身 + Git commit 已完成）：

```bash
python core/scripts/plan_tracker.py step "$PLAN_ID" --n 5 --skip-output
python core/scripts/plan_tracker.py end "$PLAN_ID"
```

`plan-end` 返回非 0 ⇒ 立即向用户报告**不要假装完成**。返回 0 才可向用户输出"一致性调和完成"。

---

## 📋 完成检查清单

向用户报告"调和完成"前必须自验：

- [ ] `plan_tracker.py status $PLAN_ID` 显示 5 个 required 步骤全部 `[x] completed`
- [ ] `plan_tracker.py end $PLAN_ID` 返回 exit 0
- [ ] `before-reconcile-<时间戳>` Git 标签已创建（git 可用时）
- [ ] 修复完成的章节 `第N章.txt` 已 Write 覆盖（用户选项非 D 时）
- [ ] `_数据库/人物卡.json`（或被改的档案）已更新 `locked_facts` / `outdated_from_ch`
- [ ] `propagation_debt` 中本次相关条目已标记 `status: resolved`
- [ ] `fix: 调和 ...（影响 N 章）` 的 Git commit 已提交（git 可用时）

任何一项不达 → 不允许声称"调和完成"。

---

## 硬性规则

- ⚠️ 修改正文前必须先创建备份（复制为 `第N章.txt.bak`）
- ⚠️ 调和前必须打 Git 标签（`before-reconcile-<时间戳>`）作为回滚点
- 修复后的章节需要重新执行 `/cluster-save-state` 的质检步骤（风格保真度校验）
- 锁定事实的修改是不可逆操作，修改前在终端明确提示用户确认
- 自动修复采用 Agent 子任务独立执行，失败不影响其他章节
- Git 不可用时：只依赖 .bak 备份，仍可继续调和

---
本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
