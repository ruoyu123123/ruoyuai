"""assemble_global_feedback_rules 回归测试 — 钉死 feedback_*.md 机械汇编契约。

被测脚本把开发机 memory/feedback_*.md 汇编成单文件随 exe 出货。
纯确定性逻辑（文件读写 + frontmatter 剥离 + markdown 拼接），零 LLM / 零联网。
这组测试锁定：frontmatter 剥离规则 / 空目录返回 1 / 排序 / name 回退 /
RULE_MARKER 锚点 / description 注入 / 正文逐字保留。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import assemble_global_feedback_rules as mod  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


# ---------------- _split_frontmatter ----------------

def test_split_frontmatter_extracts_top_level_fields():
    """有 frontmatter：抽顶层 name/description 单行字段，正文剥框架后保留。"""
    text = (
        "---\n"
        "name: feedback-foo\n"
        "description: 这是一条规则\n"
        "metadata:\n"
        "  originSessionId: abc123\n"
        "---\n"
        "正文第一行\n正文第二行\n"
    )
    fields, body = mod._split_frontmatter(text)
    assert fields["name"] == "feedback-foo"
    assert fields["description"] == "这是一条规则"
    # 缩进的 metadata 子项 originSessionId 不被抽（只抽顶层）
    assert "originSessionId" not in fields
    # body 剥掉 frontmatter，且 lstrip 掉前导换行
    assert body.startswith("正文第一行")
    assert "正文第二行" in body
    assert "name:" not in body  # frontmatter 不残留在正文里


def test_split_frontmatter_no_frontmatter_returns_whole_body():
    """无 frontmatter（不以 --- 开头）→ 字段空 dict + 全文当正文。"""
    text = "# 标题\n普通正文\n"
    fields, body = mod._split_frontmatter(text)
    assert fields == {}
    assert body == text


def test_split_frontmatter_unterminated_returns_whole_body():
    """以 --- 开头但没有闭合 \\n--- → 字段空 + 原文当正文（不崩）。"""
    text = "---\nname: x\n没有闭合分隔符\n正文混在一起\n"
    fields, body = mod._split_frontmatter(text)
    assert fields == {}
    assert body == text


def test_split_frontmatter_skips_lines_without_colon():
    """frontmatter 里没有冒号的顶层行被跳过，不污染字段表。"""
    text = (
        "---\n"
        "name: feedback-bar\n"
        "this-line-has-no-colon\n"
        "---\n"
        "body\n"
    )
    fields, body = mod._split_frontmatter(text)
    assert fields == {"name": "feedback-bar"}
    assert body.rstrip() == "body"


# ---------------- assemble ----------------

def _mk_memory(files: dict) -> Path:
    """造临时 memory 目录，files = {filename: content}。返回目录路径。"""
    d = Path(tempfile.mkdtemp())
    for name, content in files.items():
        _write(d / name, content)
    return d


def test_assemble_empty_dir_returns_1():
    """目录下没有 feedback_*.md → 打印 [FATAL] 返回 1（非开发机防护）。"""
    mem = Path(tempfile.mkdtemp())
    # 放个非 feedback 文件干扰，确认 glob 不误匹配
    _write(mem / "project_x.md", "无关")
    out = Path(tempfile.mkdtemp()) / "sub" / "global_feedback_rules.md"
    rc = mod.assemble(mem, out)
    assert rc == 1
    assert not out.exists()  # 失败不该写出文件


def test_assemble_happy_path_writes_file_and_returns_0():
    """正常路径：读两个 feedback 文件 → 写出汇编文件，返回 0，含 header + 两节。"""
    mem = _mk_memory({
        "feedback_alpha.md": (
            "---\nname: feedback-alpha\ndescription: 规则A描述\n---\n"
            "规则A正文内容\n"
        ),
        "feedback_beta.md": (
            "---\nname: feedback-beta\ndescription: 规则B描述\n---\n"
            "规则B正文内容\n"
        ),
    })
    out = Path(tempfile.mkdtemp()) / "nested" / "out.md"
    rc = mod.assemble(mem, out)
    assert rc == 0
    assert out.exists()  # 嵌套父目录被 mkdir 出来
    text = out.read_text(encoding="utf-8")
    # header 注入了文件计数
    assert "共 2 条" in text
    # 两节标题（来自 name 字段）
    assert "## feedback-alpha" in text
    assert "## feedback-beta" in text
    # description 注入
    assert "> description: 规则A描述" in text
    assert "> description: 规则B描述" in text
    # 正文逐字保留
    assert "规则A正文内容" in text
    assert "规则B正文内容" in text
    # 每节带机器可读锚点
    assert "<!-- FEEDBACK_RULE: feedback_alpha.md -->" in text
    assert "<!-- FEEDBACK_RULE: feedback_beta.md -->" in text


def test_assemble_sorts_files_deterministically():
    """文件按文件名排序汇编（glob sorted）→ 锚点出现顺序固定。"""
    mem = _mk_memory({
        "feedback_zzz.md": "---\nname: z-rule\n---\nZ正文\n",
        "feedback_aaa.md": "---\nname: a-rule\n---\nA正文\n",
    })
    out = Path(tempfile.mkdtemp()) / "out.md"
    assert mod.assemble(mem, out) == 0
    text = out.read_text(encoding="utf-8")
    idx_a = text.index("feedback_aaa.md")
    idx_z = text.index("feedback_zzz.md")
    assert idx_a < idx_z  # aaa 排在 zzz 前


def test_assemble_name_falls_back_to_stem():
    """无 name 字段（或无 frontmatter）→ title 用文件名 stem 去掉下划线。"""
    mem = _mk_memory({
        # 完全无 frontmatter
        "feedback_no_front.md": "纯正文没有任何frontmatter\n",
    })
    out = Path(tempfile.mkdtemp()) / "out.md"
    assert mod.assemble(mem, out) == 0
    text = out.read_text(encoding="utf-8")
    # stem feedback_no_front → 下划线换成连字符
    assert "## feedback-no-front" in text
    # 无 description 字段时不注入 description 行
    assert "> description:" not in text
    assert "纯正文没有任何frontmatter" in text


def test_assemble_omits_description_when_empty():
    """有 frontmatter 但 description 缺失 → 不写 '> description:' 行。"""
    mem = _mk_memory({
        "feedback_only_name.md": "---\nname: only-name\n---\n正文Z\n",
    })
    out = Path(tempfile.mkdtemp()) / "out.md"
    assert mod.assemble(mem, out) == 0
    text = out.read_text(encoding="utf-8")
    assert "## only-name" in text
    assert "> description:" not in text
    assert "正文Z" in text
