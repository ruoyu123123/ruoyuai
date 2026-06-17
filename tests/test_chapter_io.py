"""chapter_io 专属回归测试 —— 锁住正文/数据分离模块的核心确定性逻辑。

【与已有间接覆盖的分工】
  tests/test_atomic_text.py 已覆盖 write_body / write_changes 的「原子落盘 + 崩溃语义 +
  内容口径 + 裸 factual 包装」3 个功能级测试（atomic_write_text 落盘点）。
  其余 chapter_io 引用方（test_*_sfs / test_validate_style_author_profile 等）只是
  「读真原文剥 CHANGES 尾巴 / 复用 count_cjk」，并未专测本模块内部算法。

  故本文件聚焦【尚未被锁的核心确定性逻辑】，不重复落盘测试：
    - normalize_changes：B/C→A schema 归一 + HYBRID 回填 + null/非 dict 强制矫正
    - read_changes：缺文件兜底 + 落盘往返经 normalize
    - _strip_changes / read_body：旧混合 txt 分隔符剥离
    - find_body_file / find_chapter_dir：4 布局 + rglob 兜底 + 归档排除
    - count_words / count_cjk：统一口径 + CJK 扩展 A 区间
    - __main__ wordcount CLI 退出路径

零依赖：仅标准库；test_* 无参数；断言失败 raise AssertionError。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import chapter_io as cio  # noqa: E402

_SCRIPT = Path(__file__).resolve().parent.parent / "core" / "scripts" / "chapter_io.py"


# ============ normalize_changes：schema 归一是 P0 高频痛点，本模块的核心算法 ============

def test_normalize_layout_A_passthrough():
    """布局 A（已规范 factual+self_eval）原样透传，含 waivers 不丢。"""
    src = {"factual": {"locked_facts": ["x"]},
           "self_eval": {"waivers": [{"code": "STYLE_x", "reason": "纯独白章"}]}}
    out = cio.normalize_changes(src)
    assert out["factual"] == {"locked_facts": ["x"]}
    assert out["self_eval"]["waivers"][0]["code"] == "STYLE_x"


def test_normalize_layout_B_CHANGES_top_key():
    """布局 B：{"CHANGES": {...}, "ecas_metadata": {...}, "schema_version": ...} → A。"""
    src = {"CHANGES": {"facts_locked": ["规则会还价"]},
           "ecas_metadata": {"cluster": "cluster_001"},
           "schema_version": "v9"}
    out = cio.normalize_changes(src)
    assert out["factual"] == {"facts_locked": ["规则会还价"]}
    assert out["schema_version"] == "v9"
    # 元字段被搬进 self_eval，且 waivers/uncertainty_flags 骨架补齐
    assert out["self_eval"]["ecas_metadata"] == {"cluster": "cluster_001"}
    assert out["self_eval"]["waivers"] == []
    assert out["self_eval"]["uncertainty_flags"] == []


def test_normalize_layout_C_bare_factual_fields():
    """布局 C：裸事实字段散在顶层（无 factual/self_eval/CHANGES 键）→ 全部归到 factual。"""
    src = {"word_count_cjk": 12000, "foreshadowing_planted": ["伏笔甲"]}
    out = cio.normalize_changes(src)
    assert out["factual"]["word_count_cjk"] == 12000
    assert out["factual"]["foreshadowing_planted"] == ["伏笔甲"]
    assert out["schema_version"] == "v2.cluster"   # 默认 schema_version


def test_normalize_null_and_nondict_coerced_to_dict():
    """LLM 自由产出 self_eval:null / factual:"n/a" → 强制矫正为 {} 防下游 AttributeError。

    这是注释里点名的真实崩溃：normalize 短路前若不强制为 dict，
    下游 se.setdefault(...) 在 API 已花钱、draft 已写盘后崩 → changes 永不落盘。
    """
    out = cio.normalize_changes({"factual": "n/a", "self_eval": None})
    assert out["factual"] == {}
    assert isinstance(out["self_eval"], dict)
    # 只有 self_eval 键、值为非 dict 也要矫正
    out2 = cio.normalize_changes({"self_eval": ["列表不是dict"]})
    assert out2["self_eval"] == {}
    assert out2["factual"] == {}


def test_normalize_hybrid_empty_factual_recovers_top_level_facts():
    """HYBRID：空 factual={} + 事实字段散在顶层 → 回填进 factual，杜绝 CHANGES_MISSING 误报。"""
    src = {
        "factual": {},
        "self_eval": {"waivers": []},
        "facts_locked": ["A"],
        "foreshadowing_planted": ["伏笔"],
        "secrets_touched": ["秘密"],
        "无关字段": "不应被回填",
    }
    out = cio.normalize_changes(src)
    assert out["factual"]["facts_locked"] == ["A"]
    assert out["factual"]["foreshadowing_planted"] == ["伏笔"]
    assert out["factual"]["secrets_touched"] == ["秘密"]
    # 只回填已知事实键，非白名单顶层键不进 factual
    assert "无关字段" not in out["factual"]


def test_normalize_non_dict_input_returns_skeleton():
    """传入非 dict（None/str/list）→ 返回标准空骨架而非崩溃。"""
    assert cio.normalize_changes(None) == {"factual": {}, "self_eval": {}}
    assert cio.normalize_changes("garbage") == {"factual": {}, "self_eval": {}}
    assert cio.normalize_changes([1, 2]) == {"factual": {}, "self_eval": {}}


# ============ read_changes：缺文件兜底 + 真落盘往返经 normalize ============

def test_read_changes_missing_file_returns_empty():
    """无 _changes.json → 返回空骨架，不抛 FileNotFoundError。"""
    with tempfile.TemporaryDirectory() as td:
        out = cio.read_changes(Path(td), 5)
        assert out == {"factual": {}, "self_eval": {}}


def test_read_changes_roundtrip_normalizes_layout_B():
    """落盘一个布局 B 的原始 JSON，read_changes 读出时经 normalize 归一到 A。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cp = cio.changes_path(root, 4)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"CHANGES": {"facts_locked": ["x"]}}, ensure_ascii=False),
                      encoding="utf-8")
        out = cio.read_changes(root, 4)
        assert out["factual"] == {"facts_locked": ["x"]}
        assert out["self_eval"]["waivers"] == []


# ============ _strip_changes / read_body：旧混合 txt 剥离 ============

def test_strip_changes_factual_separator():
    """旧混合 txt：---CHANGES_FACTUAL--- 之前即纯正文，尾部空白 rstrip。"""
    mixed = "正文最后一句。\n\n---CHANGES_FACTUAL---\n{\"facts_locked\": []}"
    assert cio._strip_changes(mixed) == "正文最后一句。"


def test_strip_changes_legacy_separator_and_no_separator():
    """兼容旧 ---CHANGES--- 分隔符；无分隔符时整体 rstrip 返回。"""
    assert cio._strip_changes("正文。\n---CHANGES---\njunk") == "正文。"
    assert cio._strip_changes("纯正文无尾巴   \n\n") == "纯正文无尾巴"


def test_read_body_strips_changes_from_mixed_file():
    """read_body 读到旧混合 txt（正文+CHANGES 段）自动剥离，只返回正文。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "章节" / "第006章"
        d.mkdir(parents=True)
        (d / "第006章.txt").write_text(
            "这是正文。\n\n---CHANGES_SELF_EVAL---\nshould not appear",
            encoding="utf-8")
        # 注意：SELF_EVAL_SEP 不在 CHANGES_SEPARATORS 内，验证只 FACTUAL/CHANGES 被剥
        body = cio.read_body(root, 6)
        # SELF_EVAL_SEP 不被 _strip_changes 处理 → 整段保留（锁真实分隔符语义边界）
        assert "这是正文。" in body
        assert "---CHANGES_SELF_EVAL---" in body


def test_read_body_missing_raises_filenotfound():
    """正文不存在 → FileNotFoundError（下游靠这个区分缺章 vs 空章）。"""
    with tempfile.TemporaryDirectory() as td:
        raised = False
        try:
            cio.read_body(Path(td), 99)
        except FileNotFoundError:
            raised = True
        assert raised, "缺正文必须抛 FileNotFoundError"


# ============ find_body_file / find_chapter_dir：4 布局 + rglob 兜底 ============

def test_find_body_file_4_layouts_and_rglob_fallback():
    """4 标准布局优先命中；非标准命名走 rglob 兜底；归档目录排除。"""
    # 布局1：嵌套 zero-pad（write_body 标准）
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cio.write_body(root, 3, "正文")
        f = cio.find_body_file(root, 3)
        assert f == root / "章节" / "第003章" / "第003章.txt"

    # 平铺旧布局：根目录 第NNN章.txt
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "第008章.txt").write_text("x", encoding="utf-8")
        assert cio.find_body_file(root, 8) == root / "第008章.txt"

    # rglob 兜底：非标准命名（第012章_终.txt）深埋子目录
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / "随便" / "深"
        sub.mkdir(parents=True)
        (sub / "第012章_终.txt").write_text("x", encoding="utf-8")
        assert cio.find_body_file(root, 12) == sub / "第012章_终.txt"

    # 归档目录 _archive 内的同名 txt 必须被排除
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        arch = root / "_archive"
        arch.mkdir()
        (arch / "第020章.txt").write_text("旧版", encoding="utf-8")
        assert cio.find_body_file(root, 20) is None


def test_find_body_file_excludes_changes_json():
    """rglob 兜底必须排除 _changes.json（否则误把数据文件当正文）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sub = root / "x"
        sub.mkdir()
        (sub / "第030章_changes.json").write_text("{}", encoding="utf-8")
        # 只有 _changes.json，没有真正文 → 必须返回 None
        assert cio.find_body_file(root, 30) is None


def test_find_chapter_dir_layouts_and_none():
    """find_chapter_dir：命中嵌套布局返回目录，无则 None。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "章节" / "第005章"
        d.mkdir(parents=True)
        assert cio.find_chapter_dir(root, 5) == d
        assert cio.find_chapter_dir(root, 7) is None


# ============ count_words / count_cjk：统一字数口径 ============

def test_count_words_strips_all_whitespace_keeps_punctuation():
    """count_words = 去所有空白后的字符数（含标点）。"""
    assert cio.count_words("你好，世界！") == 6      # 4 汉字 + 2 标点
    assert cio.count_words("a b\tc\nd") == 4          # 空白全去
    assert cio.count_words("") == 0


def test_count_cjk_pure_han_excludes_punct_and_ascii():
    """count_cjk 只数中日韩文字，标点/字母/数字/空白都不算。"""
    assert cio.count_cjk("你好world123，。") == 2     # 只 你好
    assert cio.count_cjk("abc 123 !@#") == 0


def test_count_cjk_includes_cjk_ext_a():
    """权威口径含 CJK 扩展 A（U+3400-U+4DBF）—— gen_writer_audit 钉死的窄区间 bug 反向锁。"""
    ext_a = "㐀㐁"   # 扩展 A 区两个字
    basic = "字"             # 基本区
    assert cio.count_cjk(ext_a) == 2
    assert cio.count_cjk(basic + ext_a) == 3


# ============ __main__ CLI：wordcount 退出路径 + 用法分支 ============

def _run_cli(args):
    """子进程跑真 CLI，返回 (returncode, stdout)。参照 _run_cli 范式（稳）。

    Windows 控制台默认 code page 非 UTF-8：强制子进程 stdout 走 UTF-8
    （PYTHONIOENCODING），decode 容错（errors=replace）防中文字节解码崩。
    断言只看 ASCII-safe 子串，对编码鲁棒。"""
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode, (proc.stdout or "")


def test_cli_wordcount_reports_both_counts():
    """CLI wordcount：读真正文 → 打印 count_words 与 count_cjk 两个口径。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cio.write_body(root, 1, "你好，世界。abc")   # count_words=9(去空白:你好，世界。abc 共9字符) cjk=4
        rc, out = _run_cli(["wordcount", str(root), "1"])
        assert rc == 0, out
        assert "count_words)=9" in out, out
        assert "count_cjk)=4" in out, out


def test_cli_no_args_prints_usage():
    """无有效子命令 → 打印用法（不崩、退出 0）。"""
    rc, out = _run_cli([])
    assert rc == 0, out
    # 用法行含 ASCII-safe 的 "chapter_io.py wordcount"，对控制台编码鲁棒
    assert "chapter_io.py" in out and "wordcount" in out, out
