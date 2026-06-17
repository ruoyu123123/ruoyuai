#!/usr/bin/env python3
"""distill_prep_cluster_text 专属回归测试 — 锁核心确定性逻辑（_find_cluster 解析 + prep
退出码矩阵 + 部分缺章拼接 + main CLI 相对路径解析）。zero-dep，纯标准库。

间接覆盖已存在于 tests/test_distill_ingest_metrics.py（拼接 happy path / exit1 无 index /
exit2 cluster_id 未找到 / cluster_002 命中）。本文件**只补尚未覆盖的分支**，不重复：
  · _find_cluster 数字兜底（id 不匹配但 cluster_NNN → 第 NNN 个 1-based）
  · _find_cluster index 为裸 list 形态（非 {"clusters": [...]}）
  · _find_cluster clusters 非 list → None
  · prep 缺 原文/ 目录 → exit1（区别于缺 cluster_index）
  · prep cluster_index JSONDecodeError → exit1
  · prep chapter_range 缺失/长度异常 → exit2
  · prep 部分缺章仍返回 0（只拼存在的章 + missing 提示）
  · prep range 内全缺章 → exit2
  · prep 输出用 "\\n\\n" 拼接 + parent 目录自动创建
  · main() 相对 --output 解析到 project_root 下 + SystemExit 码
"""
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import distill_prep_cluster_text as mod  # noqa: E402


def _mk_proj(clusters, chapters):
    """建临时项目：写 cluster_index.json + 原文/第N章.txt。
    clusters: 直接写进 index["clusters"] 的 list（或 None 表示裸 list 形态由调用方控）。
    chapters: dict {ch_num: 内容}。返回 (tmp_path, index_obj_written)。
    """
    tmp = Path(tempfile.mkdtemp())
    (tmp / "原文").mkdir(parents=True)
    for ch, content in chapters.items():
        (tmp / "原文" / f"第{ch}章.txt").write_text(content, encoding="utf-8")
    if clusters is not None:
        (tmp / "cluster_index.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _rm(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


# ---------- _find_cluster 单元（纯函数·不碰盘） ----------

def test_find_cluster_exact_id_match():
    """精确 cluster_id 命中优先于数字兜底。"""
    index = {"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 2]},
        {"cluster_id": "cluster_007", "chapter_range": [3, 4]},
    ]}
    got = mod._find_cluster(index, "cluster_007")
    assert got is not None
    assert got["chapter_range"] == [3, 4]


def test_find_cluster_digit_fallback_1based():
    """id 不匹配（无 cluster_id 字段）但 cluster_002 → 取第 2 个（1-based idx=1）。"""
    index = {"clusters": [
        {"name": "甲", "chapter_range": [1, 2]},   # 无 cluster_id
        {"name": "乙", "chapter_range": [3, 4]},   # 第 2 个 ← 应命中
    ]}
    got = mod._find_cluster(index, "cluster_002")
    assert got is not None
    assert got["name"] == "乙"
    assert got["chapter_range"] == [3, 4]


def test_find_cluster_digit_fallback_out_of_range():
    """cluster_999 数字超界 → None（绝不伪造）。"""
    index = {"clusters": [{"name": "甲", "chapter_range": [1, 2]}]}
    assert mod._find_cluster(index, "cluster_999") is None


def test_find_cluster_bare_list_index():
    """index 本身是裸 list（非 {"clusters":[...]}）→ 直接当 clusters 用。"""
    bare = [
        {"cluster_id": "cluster_001", "chapter_range": [1, 1]},
        {"cluster_id": "cluster_002", "chapter_range": [2, 2]},
    ]
    got = mod._find_cluster(bare, "cluster_002")
    assert got is not None
    assert got["chapter_range"] == [2, 2]


def test_find_cluster_non_list_clusters_returns_none():
    """clusters 非 list（dict-of-dicts 脏形态）→ None 不崩。"""
    assert mod._find_cluster({"clusters": {"a": 1}}, "cluster_001") is None
    assert mod._find_cluster({"clusters": None}, "cluster_001") is None
    # 无数字的 ref 走不到兜底 → None
    assert mod._find_cluster({"clusters": [{"name": "x"}]}, "no_digits_here") is None


# ---------- prep 退出码矩阵 ----------

def test_prep_missing_raw_dir_returns_1():
    """有 cluster_index 但无 原文/ → exit1（区别于缺 index）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "cluster_index.json").write_text(
            json.dumps({"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 1]}]}),
            encoding="utf-8")
        # 故意不建 原文/
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = mod.prep(tmp, "cluster_001", tmp / "o.txt")
        assert rc == 1
        assert "原文" in buf.getvalue()
    finally:
        _rm(tmp)


def test_prep_corrupt_index_json_returns_1():
    """cluster_index.json 非法 JSON → exit1（JSONDecodeError 被吞）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "原文").mkdir()
        (tmp / "cluster_index.json").write_text("{ this is not : valid json ]", encoding="utf-8")
        rc = mod.prep(tmp, "cluster_001", tmp / "o.txt")
        assert rc == 1
    finally:
        _rm(tmp)


def test_prep_missing_chapter_range_returns_2():
    """命中 cluster 但无 chapter_range → exit2。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001"}], {1: "x"})
    try:
        rc = mod.prep(tmp, "cluster_001", tmp / "o.txt")
        assert rc == 2
    finally:
        _rm(tmp)


def test_prep_bad_range_length_returns_2():
    """chapter_range 长度 != 2（如 [1]）→ exit2。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1]}], {1: "x"})
    try:
        assert mod.prep(tmp, "cluster_001", tmp / "o.txt") == 2
    finally:
        _rm(tmp)


def test_prep_all_chapters_missing_returns_2():
    """range 指向 [5,6] 但原文里这些章都不存在 → parts 空 → exit2。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [5, 6]}],
                   {1: "存在但不在 range"})
    try:
        assert mod.prep(tmp, "cluster_001", tmp / "o.txt") == 2
    finally:
        _rm(tmp)


# ---------- prep 拼接行为 ----------

def test_prep_partial_missing_chapters_returns_0_with_existing():
    """range [1,3] 但第 2 章缺 → 仍返回 0，只拼第 1+3 章，stderr 报缺 1 章。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1, 3]}],
                   {1: "第一章正文ALPHA", 3: "第三章正文GAMMA"})  # 故意缺第2章
    try:
        out = tmp / "out.txt"
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = mod.prep(tmp, "cluster_001", out)
        assert rc == 0
        txt = out.read_text(encoding="utf-8")
        assert "ALPHA" in txt and "GAMMA" in txt
        # 缺 1 章提示落 stderr
        err = buf.getvalue()
        assert "缺 1 章" in err
        assert "[2]" in err  # missing[:5] 含第 2 章
    finally:
        _rm(tmp)


def test_prep_double_newline_separator():
    """多章用 '\\n\\n' 连接（章间空行）。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1, 2]}],
                   {1: "AAA", 2: "BBB"})
    try:
        out = tmp / "out.txt"
        assert mod.prep(tmp, "cluster_001", out) == 0
        assert out.read_text(encoding="utf-8") == "AAA\n\nBBB"
    finally:
        _rm(tmp)


def test_prep_creates_output_parent_dir():
    """output 父目录不存在时自动创建。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1, 1]}], {1: "ZZZ"})
    try:
        out = tmp / "深" / "层" / "out.txt"
        assert not out.parent.exists()
        assert mod.prep(tmp, "cluster_001", out) == 0
        assert out.exists()
        assert out.read_text(encoding="utf-8") == "ZZZ"
    finally:
        _rm(tmp)


# ---------- main() CLI ----------

def test_main_relative_output_resolves_under_project_root():
    """main(): 相对 --output 解析到 project_root 下，SystemExit 码 = prep 返回值。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1, 1]}], {1: "MAINTEST"})
    try:
        argv_bak = sys.argv[:]
        sys.argv = ["distill_prep_cluster_text.py", str(tmp),
                    "--cluster-ref", "cluster_001", "--output", "rel/out.txt"]
        code = None
        try:
            with redirect_stderr(io.StringIO()):
                mod.main()
        except SystemExit as e:
            code = e.code
        assert code == 0
        # 相对路径落到 project_root 下
        resolved = tmp / "rel" / "out.txt"
        assert resolved.exists()
        assert resolved.read_text(encoding="utf-8") == "MAINTEST"
    finally:
        sys.argv = argv_bak
        _rm(tmp)


def test_main_not_found_exits_2():
    """main(): cluster_ref 未找到 → SystemExit(2)。"""
    tmp = _mk_proj([{"cluster_id": "cluster_001", "chapter_range": [1, 1]}], {1: "x"})
    try:
        argv_bak = sys.argv[:]
        sys.argv = ["distill_prep_cluster_text.py", str(tmp),
                    "--cluster-ref", "cluster_055", "--output", "o.txt"]
        code = None
        try:
            with redirect_stderr(io.StringIO()):
                mod.main()
        except SystemExit as e:
            code = e.code
        assert code == 2
    finally:
        sys.argv = argv_bak
        _rm(tmp)


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
