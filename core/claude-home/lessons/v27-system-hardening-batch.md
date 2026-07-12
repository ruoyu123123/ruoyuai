---
name: v27-system-hardening-batch
description: 系统级加固一批 8 件 · CodeBuddy 静态审查 + 6 cluster 实战痛点叠加 · 全部以故事块为单位
metadata:
  type: project
---

# v27 系统加固批次

## 触发

1. 用户提供 CodeBuddy 第三方代码审查报告（`D:\Downloads\history_202605282001.md`）
2. 主代理实地 grep 验证：报告 70% 属实 + 1 误报（scanner 并行已实现）+ 1 臆造（build_manifest 正则不存在）
3. 主代理叠加 6 cluster 实战每天踩的运行时痛点（CodeBuddy 没覆盖）
4. 用户指令：「站在系统角度，统筹全局，都进行修复」
5. 用户全局原则：「以故事块为单位，这是全局要求」

## 8 件修复（按全局原则 cluster-first 重排）

### P0 · 高频契约 / cluster 一致性

| # | 文件 | 修复 | 来源 |
|---|---|---|---|
| 1 | `core/scripts/chapter_io.py` | 加 `normalize_changes(data)` —— 统一 schema A/B/C 三种布局 → `{factual, self_eval}` | 实战 6 cluster 每次手动转 schema |
| 2 | `core/scripts/gen_writer.py::save_output` | writer 写入端调 `cio.normalize_changes(changes)` —— 源头规范化 | 同上 |
| 3 | `core/scripts/chapter_io.py::read_changes` | 读取端兜底调 `normalize_changes` —— 双端规范，杜绝 CHANGES_MISSING 误报 | 同上 |
| 4 | `core/scripts/validate_chapter.py::check_character_mentions` | NER 收敛 —— 扩首字黑名单（代词/否定词/时态副词）+ 整词黑名单（动词短语）+ 第二字「不/没/又」截止 | character_index.false_positives 积到 250+ |
| 5 | `core/scripts/plan_tracker.py::_verify_agent_report` | cluster mode 命名候选扩 4 种变体（cluster_002 / cluster_NNN / ch_cluster_NNN / ch_NNN） | voice-checker 每 cluster 手动 cp 别名 |
| 6 | `core/claude-home/hooks/pretooluse_agent_gate.py` | 严判 `subagent_type` 而非 `prompt/desc` 关键词 —— claude agent 不再被 prompt 含「writer」字眼误判 | 系统加固时 spawn claude agent 修代码被 hook 拦截 |

### P1 · 静态质量（CodeBuddy 报告属实项）

| # | 文件 | 修复 |
|---|---|---|
| 7 | `core/scripts/save_state.py::cmd_git_commit` | 缩进/作用域 bug —— `prog` 在 if 块定义，外面 for 循环引用，title 非空分支会 NameError；重构 if 块内闭环 |
| 7b | `save_state.py` chapter + cluster 两级 git | 全部加 `timeout=30` —— 防大仓库 git add 阻塞 session |
| 7c | `gen_writer.py::run_scanners` + `gen_fixer.py::run_scanners` | `'python'` → `sys.executable` + 静默 except 加 stderr 日志 |
| 7d | `gen_writer.py::save_output` + `gen_fixer.py` 两处 | CJK 计数 `re.findall(r'[一-鿿]', ...)` → `cio.count_cjk()` —— 口径统一覆盖扩展 A |
| 8 | `gen_fixer.py::debug_path` | 时间戳加 `os.getpid()` 防并发文件名冲突 |

## 设计原则

1. **以故事块为单位**（用户全局原则）：每个修复都先问「这是 chapter 单位还是 cluster 单位？」。例如 plan_tracker judge 命名优先按 cluster_id 找，chapter 序号是别名兜底。
2. **向后兼容**：6 个已完成 cluster 不受影响（normalize_changes 对 schema A 直接放行；NER 收敛保留 false_positives 路径；hook subagent_type 缺失时退回旧 desc 关键词）。
3. **双端规范化**：schema fix 在 writer 写入端（gen_writer.save_output）+ audit 读取端（chapter_io.read_changes）都调 normalize，互为兜底。
4. **静态扫描 ≠ 实战痛点**：CodeBuddy 报告找的是「代码 smell」，但每 cluster 实战手动绕的 schema/NER/命名问题是真 P0。两者都修。

## 验证（已跑）

| 测试 | 结果 |
|---|---|
| 7 文件语法 ast.parse | ✅ 全过 |
| normalize_changes 布局 A/B/C/D | ✅ 4 种通过 |
| validate_chapter NER cluster_006 实测样本 | ✅ 0 误识别 |
| plan_tracker judge 命名 cluster_006 实文件 | ✅ True |
| read_changes 自动规范化 schema 端到端 | ✅ 通过 |
| count_cjk 基本 + 扩展 A 覆盖 | ✅ 通过 |

## 不修（统筹判断）

| CodeBuddy 报告项 | 不修原因 |
|---|---|
| P3 「scanner 串行」 | 误报 —— audit_hub.py:852 已用 ThreadPoolExecutor |
| Bug 3 「build_manifest 嵌套 JSON 正则」 | 臆造 —— 该正则在源码中 grep 不到 |
| `load_json` 76 文件重复 | 涉及面太大（76 个独立 CLI），跨次迭代再做 common.py 抽取 |
| `datetime.utcnow()` 7 文件 | Python 3.10 仍支持，非阻塞 |
| 整体单元测试 | 工作量大 · 本次先确保 P0 实战痛点 · 测试单独迭代 |
| SQLite 替代 JSON / 消息队列 | 单用户离线工具的过度工程 |

## 关联 lessons

- [[feedback-no-investigation-no-voice-universal]] —— 报告 70% 属实但有臆造，必须实地验证
- [[feedback-full-system-cluster-centric]] —— 以故事块为单位的全局原则
- [[feedback-default-no-step-skipping-for-new-books]] —— plan_tracker 兼容多种 judge 命名

## 主要可量化收益

- **每 cluster 节省手动操作 3 处**：schema 转换 + judge cp 别名 + fp 扩 → 自动化
- **NER 误报从「每 cluster 几十」→「~0」**（cluster_006 实测）
- **gen_writer subprocess 解释器一致性**：消除多 Python 环境下 import 失败可能
