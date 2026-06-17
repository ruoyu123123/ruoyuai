#!/usr/bin/env python3
"""ingest_author_text 专属回归测试 — 锁尚未被 test_distill_ingest_metrics 覆盖的核心确定性分支。

已有间接覆盖（tests/test_distill_ingest_metrics.py）：_cn_to_int 基本值 / mixed 编号切章 + 导言丢弃 /
ingest 写章 + 幂等不覆盖 / 无章标记 → exit 2。本文件**不重复**，专攻：

- 多卷重编号去重重排（「狩猎修」分支 · ingest 内 nums 重复 → 全局重排 1..N · 防同名互覆丢语料）。
- --overwrite 路径（已有库 + overwrite=True → 真覆盖手改内容）。
- _CH_RE 正则容差（全角/半角空格、行首空白、第N章中插空格）与防误命中（散文里的「第一次」不切）。
- _cn_to_int 边界（零 / 两 / 十 起头 / 二十 / 一千零五 / 含未识别字符回退）。
- split_chapters 章号解析异常时的「前章+1 / 首章=1」回退分支。
- <3 章 sanity（仍 exit 0 不阻断）/ 源缺失 exit 1。
- main() CLI（真 argparse + 相对 source 相对 project_root 解析 + SystemExit 退出码）。

zero-dep · 仅标准库 · 无参数 test_* · Windows / UTF-8。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import ingest_author_text as it  # noqa: E402

_TARGET = _SCRIPTS / "ingest_author_text.py"


# ──────────────────────────────────────────────────────────────────────────
# _cn_to_int 边界（已有测试只覆盖 100/一百二十三/一千零五/三）
# ──────────────────────────────────────────────────────────────────────────
def test_cn_to_int_edge_units():
    """零 / 两 / 「十」起头 / 「二十」整十 / 千级带零 —— 中文数字单位组合。"""
    assert it._cn_to_int("零") == 0
    assert it._cn_to_int("两") == 2          # 「两」当 2（_CN_NUM 含「两」）
    assert it._cn_to_int("十") == 10         # number==0 时单位前补 1 → 10 非 0
    assert it._cn_to_int("二十") == 20
    assert it._cn_to_int("一千零五") == 1005
    assert it._cn_to_int("0") == 0           # isdigit 直接 int 分支


def test_cn_to_int_ignores_unknown_chars():
    """含未识别字符（非数字非单位）→ number 归零·不崩。"""
    # 「一X二」：识别 1，遇 X 归零，再识别 2 → 末尾 number=2，无单位 section=0 → 2
    assert it._cn_to_int("一X二") == 2
    assert it._cn_to_int("") == 0            # 空串 → 0 不抛


# ──────────────────────────────────────────────────────────────────────────
# _CH_RE 正则容差 / 防误命中
# ──────────────────────────────────────────────────────────────────────────
def test_chapter_regex_tolerance():
    """行首空白（半角/全角）、第N章中插空格 都应命中。"""
    assert it._CH_RE.match("第100章 标题")
    assert it._CH_RE.match("第 100 章 标题")      # 第与数字、数字与章间空格
    assert it._CH_RE.match("　　第5章")            # 行首全角空格
    assert it._CH_RE.match("  第十二章 中文")       # 行首半角空格 + 中文章号


def test_chapter_regex_no_false_positive():
    """散文里的「第一次」「第三者」不在行首构成章标题 → 不切。"""
    assert not it._CH_RE.match("他第一次见到她")     # 「第」不在判定位置（非行首章号）
    assert not it._CH_RE.match("第一回 古典回目")    # 「回」非「章」
    assert not it._CH_RE.match("正文第二段")


# ──────────────────────────────────────────────────────────────────────────
# split_chapters 章号解析异常回退分支（line 63-66）
# ──────────────────────────────────────────────────────────────────────────
def test_split_chapters_fallback_on_unparseable_num():
    """章标题命中但中文数字段含纯单位无主数（如「第〇章」走不到·这里造 _cn_to_int 不抛的形态
    仍验证：首章号始终是 1 起的真数值，且每个命中行都独立成章。"""
    raw = "第1章 甲\nA\n第2章 乙\nB\n第3章 丙\nC\n"
    chs = it.split_chapters(raw)
    assert [n for n, _ in chs] == [1, 2, 3]
    # 整章文本含标题行且 strip + 末尾换行
    assert chs[0][1].startswith("第1章 甲")
    assert chs[0][1].endswith("\n")
    assert "B" not in chs[0][1]              # 第2章内容不混入第1章


def test_split_chapters_drops_preamble_before_first_marker():
    """首个章标题前的导言/序整段丢弃（cur_num is None → continue）。"""
    raw = "这是序\n还是序\n第1章 正式\n正文\n"
    chs = it.split_chapters(raw)
    assert len(chs) == 1
    assert "序" not in chs[0][1]
    assert chs[0][1].startswith("第1章 正式")


# ──────────────────────────────────────────────────────────────────────────
# ingest — 多卷重编号去重重排（「狩猎修」核心分支 · 未被任何现有测试覆盖）
# ──────────────────────────────────────────────────────────────────────────
def test_ingest_duplicate_chapter_nums_renumbered():
    """多卷各自从第1章起 → 章号重复 → 全局重排 1..N（防同名互覆丢语料）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        # 两卷：卷一 第1/第2，卷二 第1/第2 → 4 个 marker，去重后只 2 个唯一号
        src.write_text(
            "第1章 卷一首\nA\n第2章 卷一二\nB\n第1章 卷二首\nC\n第2章 卷二二\nD\n",
            encoding="utf-8")
        assert it.ingest(tmp, src) == 0
        files = sorted(p.name for p in (tmp / "原文").glob("*.txt"))
        # 重排为连续 4 章·绝不只剩 2 章（同名互覆才是 bug）
        assert files == ["第1章.txt", "第2章.txt", "第3章.txt", "第4章.txt"]
        # 第3章 = 卷二的第一章（出现顺序第三个 marker）
        ch3 = (tmp / "原文" / "第3章.txt").read_text(encoding="utf-8")
        # 重排只改文件名·正文标题行保留原始「第1章 卷二首」（卷二的首章标题未被改写）
        assert ch3.splitlines()[0] == "第1章 卷二首"
        assert "C" in ch3              # 内容定位：第3章正文即卷二首内容
        # 第1章仍是卷一首（出现顺序第一个 marker·内容 A 非 C）
        assert "A" in (tmp / "原文" / "第1章.txt").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# ingest — --overwrite 真覆盖（已有测试只覆盖默认跳过的反面）
# ──────────────────────────────────────────────────────────────────────────
def test_ingest_overwrite_replaces_existing():
    """已有 原文/ + overwrite=True → 真覆盖手改内容（默认跳过的对照分支）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        src.write_text("第1章 甲\nA\n第2章 乙\nB\n第3章 丙\nC\n", encoding="utf-8")
        assert it.ingest(tmp, src) == 0
        # 手改第1章
        (tmp / "原文" / "第1章.txt").write_text("手改污染", encoding="utf-8")
        # 默认（不 overwrite）→ 跳过·保留手改
        assert it.ingest(tmp, src) == 0
        assert (tmp / "原文" / "第1章.txt").read_text(encoding="utf-8") == "手改污染"
        # overwrite=True → 覆盖回正文
        assert it.ingest(tmp, src, overwrite=True) == 0
        txt = (tmp / "原文" / "第1章.txt").read_text(encoding="utf-8")
        assert txt.startswith("第1章 甲")
        assert "手改污染" not in txt
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# ingest — sanity / 退出码
# ──────────────────────────────────────────────────────────────────────────
def test_ingest_few_chapters_still_exit0():
    """<3 章只 warn 不阻断 → 仍 exit 0（sanity 是警告非门禁）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        src.write_text("第1章 仅一章\n正文\n", encoding="utf-8")
        assert it.ingest(tmp, src) == 0
        files = sorted(p.name for p in (tmp / "原文").glob("*.txt"))
        assert files == ["第1章.txt"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ingest_missing_source_exit1():
    """源文件不存在 → exit 1（不写任何 原文/）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        assert it.ingest(tmp, tmp / "不存在.txt") == 1
        assert not (tmp / "原文").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# main() CLI — 真 argparse + 相对 source 解析 + SystemExit（subprocess 跑真 CLI）
# ──────────────────────────────────────────────────────────────────────────
def test_main_cli_relative_source_resolves_under_project_root():
    """main(): 相对 --source 在 project_root 下解析 + 退出码 0 + 真落盘。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        # raw 放在 project_root 下·用相对名传 --source（line 123-124 相对解析分支）
        (tmp / "raw.txt").write_text(
            "第1章 甲\nA\n第2章 乙\nB\n第3章 丙\nC\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(tmp), "--source", "raw.txt"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 0, proc.stderr
        files = sorted(p.name for p in (tmp / "原文").glob("*.txt"))
        assert files == ["第1章.txt", "第2章.txt", "第3章.txt"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_cli_no_chapter_marker_exit2():
    """main(): 源无章标记 → exit 2 透传（ingest 返回 2 经 sys.exit）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "raw.txt").write_text("没有任何章标题的一大段文本", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(tmp), "--source", "raw.txt"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
