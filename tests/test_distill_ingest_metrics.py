#!/usr/bin/env python3
"""蒸馏 phase-0 脚本测试：ingest_author_text（raw→原文/第N章.txt）+ distill_chapter_metrics
（逐章→蒸馏进度/ch{N}_metrics.json·契约对齐 consolidate）。zero-dep。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import ingest_author_text as it  # noqa: E402
import distill_chapter_metrics as cm  # noqa: E402
import distill_prep_cluster_text as pc  # noqa: E402

_RAW = ("序章导言（章标题前·应丢弃）\n"
        "第1章 开场\n正文一行一\n正文一行二\n"
        "第2章 推进\n正文二行一\n"
        "第三章 中文章号\n正文三行一\n")


def test_cn_to_int():
    assert it._cn_to_int("100") == 100
    assert it._cn_to_int("一百二十三") == 123
    assert it._cn_to_int("一千零五") == 1005
    assert it._cn_to_int("三") == 3


def test_split_chapters_mixed_numbering():
    chs = it.split_chapters(_RAW)
    assert [n for n, _ in chs] == [1, 2, 3]
    assert chs[0][1].startswith("第1章 开场")
    assert "导言" not in chs[0][1]   # 首章标题前导言丢弃


def test_ingest_writes_chapters_idempotent():
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        src.write_text(_RAW, encoding="utf-8")
        assert it.ingest(tmp, src) == 0
        files = sorted(p.name for p in (tmp / "原文").glob("*.txt"))
        assert files == ["第1章.txt", "第2章.txt", "第3章.txt"]
        # 幂等：已有 → 跳过（返回 0·不毁）
        (tmp / "原文" / "第1章.txt").write_text("我是手改内容", encoding="utf-8")
        assert it.ingest(tmp, src) == 0
        assert (tmp / "原文" / "第1章.txt").read_text(encoding="utf-8") == "我是手改内容"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_ingest_no_chapter_marker_returns_2():
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        src.write_text("没有任何章标题的一大段文本", encoding="utf-8")
        assert it.ingest(tmp, src) == 2
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_chapter_metrics_contract_with_consolidate():
    """chapter_metrics 产 ch{N}_metrics.json 须含 profile.sentence_stats（consolidate :85,88）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "raw.txt"
        src.write_text(_RAW, encoding="utf-8")
        it.ingest(tmp, src)
        assert cm.run(tmp) == 0
        mfiles = sorted((tmp / "蒸馏进度").glob("ch*_metrics.json"))
        assert len(mfiles) == 3
        d = json.loads(mfiles[0].read_text(encoding="utf-8"))
        assert "profile" in d
        assert "sentence_stats" in d["profile"]
        assert "paragraph_stats" in d["profile"]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_chapter_metrics_missing_raw_returns_1():
    tmp = Path(tempfile.mkdtemp())
    try:
        assert cm.run(tmp) == 1   # 无 原文/
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _prep_proj():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "原文").mkdir(parents=True)
    for ch in (1, 2, 3):
        (tmp / "原文" / f"第{ch}章.txt").write_text(
            f"第{ch}章 标题\n第{ch}章正文内容", encoding="utf-8")
    (tmp / "cluster_index.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "chapter_range": [1, 2]},
        {"cluster_id": "cluster_002", "chapter_range": [3, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    return tmp


def test_prep_cluster_concatenates_range():
    """cluster_001(章1-2) → 拼第1+2章全文·不含第3章。"""
    tmp = _prep_proj()
    try:
        out = tmp / "full.txt"
        assert pc.prep(tmp, "cluster_001", out) == 0
        txt = out.read_text(encoding="utf-8")
        assert "第1章正文" in txt and "第2章正文" in txt
        assert "第3章正文" not in txt
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_prep_cluster_index_fallback_and_missing():
    tmp = _prep_proj()
    try:
        assert pc.prep(tmp, "cluster_002", tmp / "f2.txt") == 0   # 章3
        assert pc.prep(tmp, "cluster_999", tmp / "f3.txt") == 2   # 未找到
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_prep_cluster_no_index_returns_1():
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "原文").mkdir()
        assert pc.prep(tmp, "cluster_001", tmp / "o.txt") == 1
    finally:
        import shutil
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
