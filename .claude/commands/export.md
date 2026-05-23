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
