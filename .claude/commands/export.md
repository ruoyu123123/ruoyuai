---
description: 导出小说为完整文件（TXT/Markdown拼接）
---

你是若渝AI的导出工具。将已完成的章节拼接为完整文件。

$ARGUMENTS

---

# 导出功能（v16 新增 · 对齐 Inkfluence AI 导出能力）

## 用法

```
/export              — 导出全部已完成章节为单个txt
/export markdown     — 导出为Markdown格式（含章节标题）
/export epub         — 导出为EPUB电子书（v16 新增 · 对齐 SidekickWriter）
/export 1-10         — 导出指定范围
```

## 执行流程

0. **导出前收尾检测（advisory · 不阻断）** — 先扫残留 pending_tail 孤儿 + 字数对账：

   ```bash
   python core/scripts/finalize_book.py "<项目路径>"
   ```

   背景（修 #3/#4 静默丢失）：v27 splitter 末章 < 3000 CJK 时把末段退回
   `章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt`，靠下一个 cluster 写完后 prepend 拼接。
   但**全书最后一个 cluster 无后继**（或后继漏拼）→ 它的 pending_tail 永远没人消费，而 export 只
   Glob `第*章*.txt`，扫不到 draft 目录里的 pending_tail → 这段正文（最多 ~3000 CJK）会被**静默丢弃**。

   - exit 始终 0（**顾问制 · 绝不阻断导出**）。
   - 若报告检出孤儿 / 静默丢失下界 > 阈值 → **先告知用户**：「检测到 cluster_NNN 有 X 字未拼接的尾段
     （路径 …pending_tail.txt），导出会漏掉这段，要不要先拼上？」并给两个 opt-in 选项：
       - `python core/scripts/finalize_book.py "<项目路径>" --flush append` —— 把尾段并到该 cluster 已切末章尾部（最保守）
       - `python core/scripts/finalize_book.py "<项目路径>" --flush split` —— 把尾段强切成该 cluster 新末章
   - 用户不处理也可继续导出（advisory 不强制），但报告里要如实标注「本次导出未含 cluster_NNN 尾段 X 字」。

1. 读取 `_数据库/进度.json` 获取已完成章节数
2. 按顺序 Glob 所有 `第*章*.txt` 文件
3. 读取每个文件，只取正文部分（去掉 `---CHANGES---` 之后的内容）
4. 拼接为单文件，章节之间加分隔

## 输出格式

### TXT格式（默认）
```
《书名》

第一章 标题

（正文）

---

第二章 标题

（正文）

---
...
```

### Markdown格式
```markdown
# 《书名》

## 第一章 标题

（正文）

## 第二章 标题

（正文）
...
```

### EPUB格式（v16 新增）

需要用 Bash 生成 EPUB：

```bash
python -c "
from pathlib import Path
import zipfile, os

# EPUB 是特殊的 ZIP 格式
# 1. 生成 mimetype 文件（不压缩）
# 2. 生成 META-INF/container.xml
# 3. 生成 content.opf（元数据+章节列表）
# 4. 每章生成一个 XHTML 文件
# 5. 打包为 .epub

# 如果环境没有专门的 EPUB 库，用纯 Python 的 zipfile 实现
# 具体实现由 Claude 在执行时根据环境动态生成
"
```

如果 EPUB 生成复杂度过高，fallback 为 Markdown 格式并提示用户用 Pandoc 转换：
```bash
pandoc exports/书名_全文.md -o exports/书名.epub --metadata title="书名"
```

## 输出路径

`<项目路径>/exports/《书名》_全文_<日期>.txt`
`<项目路径>/exports/《书名》_全文_<日期>.md`
`<项目路径>/exports/《书名》_<日期>.epub`

## 统计

导出完成后展示：
- 总章节数
- 总字数
- 导出文件路径
- **收尾检测结果**（step 0 的 finalize_book 输出）：残留 pending_tail 孤儿数 + 各自字数；若有未拼接孤儿，明确标注「本次导出未含 cluster_NNN 尾段 X 字」（advisory，不阻断）
