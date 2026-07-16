---
description: 导出已完成小说为单个 TXT 文件
---

你是若渝AI的导出调度器。你只调用正式导出脚本，不手写拼接逻辑，不在导出阶段修正文。

$ARGUMENTS

---

# 导出功能

## 用法

```bash
/export
```

当前正式实现只支持 TXT 全书导出。Markdown、EPUB、范围导出没有正式脚本入口时不得声明完成，也不得临时手写拼接逻辑。

## 执行流程

1. 可单独运行导出前硬闸，确认没有残留正文债：

   ```bash
   python core/scripts/finalize_book.py "<项目路径>"
   ```

   `finalize_book.py` 只检测，不写章、不删除 `pending_tail`、不提供导出阶段修复。发现未消费的 `章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt` 或正文缺口下界超阈值时直接 exit 2。

2. 调用唯一正式导出脚本：

   ```bash
   python core/scripts/export_book.py "<项目路径>"
   ```

3. `export_book.py` 在写出任何 `exports/*` 成品前完成硬校验。以下情况必须 exit 2，且不得留下导出成品：
   - 缺章、重复章、章目录存在但正文文件缺失；
   - 章节正文读取失败、正文为空、正文混入 `---CHANGES---` 等机器段；
   - `_changes.json` 缺失或没有非空 `title`；
   - 存在未消费 `pending_tail`。

4. 导出通过后写出：

   ```text
   <项目路径>/exports/<书名>_全文_<章数>章.txt
   ```

## 输出格式

```text
第1章 标题

（正文）

第2章 标题

（正文）
```

标题来自每章 `_changes.json` 的顶层 `title` 字段（`gen_chapter_titles --apply` 落盘、`split_cluster_changes` 覆盖时保留）。`gen_chapter_titles --apply` 会把「第NNN章 标题」章头写进正文首行便于逐章阅读；导出时 `export_book` 确定性剥离该首行章头、再按 `_changes.json.title` 自拼 header（字数守恒按纯正文计）。导出脚本只做确定性拼接和全书结构校验。

## 完成汇报

导出完成后汇报：
- 总章节数；
- 总 CJK 字数；
- 导出文件路径；
- integrity verdict，必须为 `ok` 或仅含非阻断的结构提示。

本命令产出位置遵循 [STRUCTURE.md](../../core/claude-home/STRUCTURE.md) 第九节。
