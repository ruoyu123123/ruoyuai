# -*- coding: utf-8 -*-
"""test_semantic_threshold_calibrator.py — Wave-6 语义阈值校准 harness 回归测试。

确定性·零依赖·**绝不真调 daemon/venv**（全程 mock compute_embeddings_batch +
mock nn_daemon_client.ensure_daemon/shutdown_daemon，纯内存/临时文件跑）。

覆盖：
  1. 段落切分/过滤（标题行跳过 · CJK<30 过滤 · 全角空格 strip）
  2. 章号提取兼容不同位数补零
  3. 语料缺目录跳过（books_missing）
  4. 五类+content_echo pos/neg 采样确定性（同 seed 两次同对集）
  5. 各类样本对的距离约束真实生效（相邻=1 / 远段≥N / 探针跨章≥M / 跨书确实跨书）
  6. AUC 手算函数对拍（完美分离/完全打平/已知部分分离数值核对）
  7. Youden 最优点手算对拍
  8. percentile/distribution_stats 边界（空列表/单元素/已知中位数）
  9. 章首摘要累加逻辑（k 边界）
  10. mock embedding 下端到端产报告（report 结构 + verdict + relation_families）
  11. 显式证明 mock 路径零接触真后端（monkeypatch 真函数为 raise，仍不报错）
  12. main() 全流程（mock 掉 compute_embeddings_batch + nn_daemon_client，验证落盘 json/md）
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CALIB_DIR = _ROOT / "core" / "ml" / "calibration"
if str(_CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(_CALIB_DIR))

import semantic_threshold_calibrator as mod  # noqa: E402


# ════════════════════════════ 测试语料 fixture ════════════════════════════

def _write_fake_book(styles_root: Path, book: str, n_chapters: int = 20, n_paras: int = 15):
    """每章一个 txt：首行标题 + n_paras 段正文，段落文本内嵌 book/章号/段号方便断言。
    正文模板刻意造 30+ CJK 以通过 MIN_PARA_CJK 过滤。"""
    d = styles_root / book / "原文"
    d.mkdir(parents=True, exist_ok=True)
    for ch in range(1, n_chapters + 1):
        lines = [f"第{ch}章 占位标题"]
        for p in range(n_paras):
            lines.append(f"{book}专属场景第{ch}章第{p}段正文内容足够长足够长足够长以通过三十字过滤门槛测试用。")
        (d / f"第{ch:03d}章.txt").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def fake_corpus_root(tmp_path, monkeypatch):
    """缩小距离常量到合成语料能满足的规模（20 章/15 段每章），三本书。"""
    monkeypatch.setattr(mod, "FAR_CHAPTER_DISTANCE", 5)
    monkeypatch.setattr(mod, "FAR_PARA_DISTANCE", 3)
    styles_root = tmp_path / "styles"
    for book in ("书A", "书B", "书C"):
        _write_fake_book(styles_root, book)
    return styles_root


def _fake_embed_batch(texts):
    """确定性假 embedding：md5(text) → 16 维单位向量。零 torch/零网络。"""
    out = []
    for t in texts:
        h = hashlib.md5(t.encode("utf-8")).digest()
        vec = [(b - 127.5) / 127.5 for b in h]
        norm = sum(v * v for v in vec) ** 0.5
        out.append([v / norm for v in vec] if norm else vec)
    return out


def _extract_book(text: str, books=("书A", "书B", "书C")) -> str:
    return next(b for b in books if b in text)


def _extract_chapter(text: str) -> int:
    return int(re.search(r"第(\d+)章", text).group(1))


def _extract_para(text: str) -> int:
    return int(re.search(r"第(\d+)段", text).group(1))


# ════════════════════════════ 1. 段落切分/过滤 ════════════════════════════

def test_load_book_paragraphs_skips_title_and_filters_short_lines(tmp_path):
    d = tmp_path / "书X" / "原文"
    d.mkdir(parents=True)
    long_line = "这是一段足够长的正文内容用来测试三十字过滤门槛是否正确生效不多不少刚刚好。"
    assert mod._cjk_count(long_line) >= 30
    short_line = "太短了"
    (d / "第001章.txt").write_text(
        "第一章 这是标题行\n" + long_line + "\n" + short_line + "\n　　" + long_line,
        encoding="utf-8")
    chapters = mod.load_book_paragraphs(d)
    assert list(chapters.keys()) == [1]
    paras = chapters[1]
    assert short_line not in paras
    assert paras.count(long_line) == 2   # 标题跳过·全角空格被 strip 后与普通行内容相同


@pytest.mark.parametrize("stem,expected", [
    ("第0001章", 1), ("第1000章", 1000), ("第100章", 100), ("第0042章", 42),
])
def test_chapter_num_regex_handles_padding_variants(stem, expected):
    m = mod.CHAPTER_NUM_RE.search(stem)
    assert m is not None
    assert int(m.group(1)) == expected


def test_load_corpus_skips_missing_book_dir(tmp_path, capsys):
    (tmp_path / "存在的书" / "原文").mkdir(parents=True)
    (tmp_path / "存在的书" / "原文" / "第001章.txt").write_text(
        "标题\n" + "内容足够长内容足够长内容足够长内容足够长内容足够长内容足够长内容足够长" * 1,
        encoding="utf-8")
    corpus = mod.load_corpus(tmp_path, ["存在的书", "不存在的书"])
    assert "存在的书" in corpus
    assert "不存在的书" not in corpus


# ════════════════════════════ 2. 采样确定性 + 距离约束 ════════════════════════════

def test_build_all_pairs_deterministic_same_seed(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    p1 = mod.build_all_pairs(corpus, random.Random(20260704), 20)
    p2 = mod.build_all_pairs(corpus, random.Random(20260704), 20)
    assert p1 == p2


def test_build_all_pairs_different_seed_usually_differs(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    p1 = mod.build_all_pairs(corpus, random.Random(1), 20)
    p2 = mod.build_all_pairs(corpus, random.Random(2), 20)
    assert p1 != p2


def test_pos_adjacent_pairs_are_literally_adjacent(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    pairs = mod._build_pos_adjacent(corpus, random.Random(7), 30)
    assert len(pairs) == 30
    for a, b in pairs:
        assert _extract_chapter(a) == _extract_chapter(b)
        assert _extract_para(b) - _extract_para(a) == 1


def test_pos_same_chapter_far_respects_min_distance(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    pairs = mod._build_pos_same_chapter_far(corpus, random.Random(7), 30)
    assert len(pairs) == 30
    for a, b in pairs:
        assert _extract_chapter(a) == _extract_chapter(b)
        assert _extract_para(b) - _extract_para(a) >= mod.FAR_PARA_DISTANCE


def test_probe_same_book_diff_chapter_respects_min_chapter_distance(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    pairs = mod._build_probe_same_book_diff_chapter(corpus, random.Random(7), 30)
    assert len(pairs) == 30
    for a, b in pairs:
        assert _extract_book(a) == _extract_book(b)   # 同书
        assert abs(_extract_chapter(a) - _extract_chapter(b)) >= mod.FAR_CHAPTER_DISTANCE


def test_neg_cross_book_pairs_are_actually_cross_book(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    pairs = mod._build_neg_cross_book(corpus, random.Random(7), 30)
    assert len(pairs) == 30
    for a, b in pairs:
        assert _extract_book(a) != _extract_book(b)


def test_summary_vs_body_pos_same_chapter_neg_cross_book(fake_corpus_root):
    corpus = mod.load_corpus(fake_corpus_root, ["书A", "书B", "书C"])
    pos, neg = mod._build_summary_vs_body(corpus, random.Random(7), 30)
    assert len(pos) == 30 and len(neg) == 30
    for summary, body in pos:
        assert _extract_chapter(summary.splitlines()[0]) == _extract_chapter(body)
    for summary, other in neg:
        assert _extract_book(summary.splitlines()[0]) != _extract_book(other)


# ════════════════════════════ 3. 章首摘要累加 ════════════════════════════

def test_chapter_summary_accumulates_until_target():
    # 每段 10 CJK，target=25 → 需 3 段(10+10+10=30>=25)，k=3
    paras = ["一二三四五六七八九十"] * 5   # 每段 10 CJK
    summary, k = mod._chapter_summary(paras, target_cjk=25)
    assert k == 3
    assert summary == "\n".join(paras[:3])


def test_chapter_summary_consumes_all_when_never_reaches_target():
    paras = ["短段"] * 3   # 每段 2 CJK，永远达不到 target=999
    summary, k = mod._chapter_summary(paras, target_cjk=999)
    assert k == len(paras)
    assert summary == "\n".join(paras)


# ════════════════════════════ 4. 统计函数 ════════════════════════════

def test_percentile_known_median():
    assert mod.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert mod.percentile([], 0.5) == 0.0
    assert mod.percentile([42.0], 0.9) == 42.0


def test_distribution_stats_empty_and_basic():
    empty = mod.distribution_stats([])
    assert empty == {"n": 0, "mean": 0.0, "p5": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0}
    s = mod.distribution_stats([1, 2, 3, 4, 5])
    assert s["n"] == 5
    assert s["mean"] == 3.0
    assert s["p50"] == 3.0


# ════════════════════════════ 5. AUC 手算对拍 ════════════════════════════

def test_roc_auc_perfect_separation():
    assert mod.roc_auc([0.9, 0.8, 0.85], [0.1, 0.2, 0.15]) == pytest.approx(1.0)


def test_roc_auc_complete_tie_is_chance():
    assert mod.roc_auc([0.5, 0.5, 0.5], [0.5, 0.5, 0.5]) == pytest.approx(0.5)


def test_roc_auc_known_partial_tie_case():
    # 手算：ranks of [0.6,0.4,0.5,0.5] → 0.4=1,0.5=2.5,0.5=2.5,0.6=4
    # pos=[0.6,0.4] ranks sum=4+1=5 → AUC=(5-2*3/2)/(2*2)=0.5
    assert mod.roc_auc([0.6, 0.4], [0.5, 0.5]) == pytest.approx(0.5)


def test_roc_auc_known_nontrivial_value():
    # 手算(见任务设计)：pos=[1,2,3] neg=[0,0.5,1.5] → 8/9 胜对
    assert mod.roc_auc([1, 2, 3], [0, 0.5, 1.5]) == pytest.approx(8 / 9)


def test_roc_auc_empty_side_returns_chance():
    assert mod.roc_auc([], [1, 2, 3]) == 0.5
    assert mod.roc_auc([1, 2, 3], []) == 0.5


# ════════════════════════════ 6. Youden 最优点手算对拍 ════════════════════════════

def test_youden_threshold_known_case():
    # 手算(见任务设计)：pos=[0.9,0.8,0.3] neg=[0.2,0.4,0.7] → 最优 t=0.8, tpr=2/3, fpr=0, j=2/3
    result = mod.youden_threshold([0.9, 0.8, 0.3], [0.2, 0.4, 0.7])
    assert result["threshold"] == pytest.approx(0.8)
    assert result["tpr"] == pytest.approx(2 / 3, abs=1e-4)
    assert result["fpr"] == pytest.approx(0.0)
    assert result["j"] == pytest.approx(2 / 3, abs=1e-4)


def test_youden_threshold_empty_side_safe():
    result = mod.youden_threshold([], [1, 2])
    assert result == {"threshold": None, "tpr": 0.0, "fpr": 0.0, "j": 0.0}


# ════════════════════════════ 7. score_all_pairs：一次批调用 + 正确映射 ════════════════════════════

def test_score_all_pairs_single_batch_call_and_correct_cosine():
    calls = []

    def counting_embed(texts):
        calls.append(len(texts))
        # 正交 one-hot：text 首字符决定维度，保证已知 cosine
        vecs = []
        for t in texts:
            v = [0.0, 0.0]
            v[0 if t[0] == "A" else 1] = 1.0
            vecs.append(v)
        return vecs

    pair_groups = {"cls1": [("A同轴", "A同轴2")], "cls2": [("A同轴3", "B异轴")]}
    scores = mod.score_all_pairs(pair_groups, counting_embed)
    assert len(calls) == 1              # 全部文本一次性批调用
    assert calls[0] == 4                # 2 组 × 2 条
    assert scores["cls1"][0] == pytest.approx(1.0)   # 同方向 → cos=1
    assert scores["cls2"][0] == pytest.approx(0.0)   # 正交 → cos=0


# ════════════════════════════ 8. 端到端 mock 报告 ════════════════════════════

def test_run_calibration_end_to_end_with_mock_embedding(fake_corpus_root):
    report = mod.run_calibration(
        ["书A", "书B", "书C"], n_pairs=25, seed=20260704,
        styles_root=fake_corpus_root, embed_batch_fn=_fake_embed_batch)

    assert report["meta"]["embed_method"] == "injected_mock"
    assert report["meta"]["books_missing"] == []
    assert set(report["meta"]["chapters_loaded"]) == {"书A", "书B", "书C"}

    expected_classes = {
        "pos_adjacent", "pos_same_chapter_far", "probe_same_book_diff_chapter",
        "neg_cross_book", "summary_vs_body_pos", "summary_vs_body_neg",
    }
    assert set(report["distributions"]) == expected_classes
    for cls in expected_classes:
        assert report["distributions"][cls]["n"] == 25

    assert set(report["auc"]) == {
        "pos_adjacent_vs_neg_cross_book", "pos_adjacent_vs_probe_same_book_diff_chapter",
        "probe_vs_neg_cross_book", "summary_vs_body_pos_vs_neg",
    }
    assert set(report["relation_families"]) == {
        "content_relatedness", "content_vs_style_confound",
        "style_signal_strength", "content_echo",
    }
    for fam in report["relation_families"].values():
        assert "usable" in fam and "neg_p95" in fam and "youden" in fam

    assert report["verdict"]["label"] in ("A", "B", "C")
    assert report["verdict"]["reason"]

    # 报告本身可 json 序列化（真机 CLI 落盘前提）
    json.dumps(report, ensure_ascii=False)


def test_run_calibration_reports_missing_books(fake_corpus_root):
    report = mod.run_calibration(
        ["书A", "不存在的书"], n_pairs=10, seed=1,
        styles_root=fake_corpus_root, embed_batch_fn=_fake_embed_batch)
    assert report["meta"]["books_missing"] == ["不存在的书"]


def test_render_markdown_contains_key_sections(fake_corpus_root):
    report = mod.run_calibration(
        ["书A", "书B", "书C"], n_pairs=10, seed=3,
        styles_root=fake_corpus_root, embed_batch_fn=_fake_embed_batch)
    text = mod.render_markdown(report)
    assert "## 五类样本分布" in text
    assert "## 关键 AUC" in text
    assert "## 裁决" in text
    assert "## 各 relation_family 建议 operating point" in text
    assert report["verdict"]["label"] in text


# ════════════════════════════ 9. 显式证明 mock 路径零接触真后端 ════════════════════════════

def test_mock_path_never_touches_real_compute_embeddings_batch(fake_corpus_root, monkeypatch):
    def _boom(_texts):
        raise AssertionError("run_calibration 传了 embed_batch_fn 却仍调用了真 compute_embeddings_batch")
    monkeypatch.setattr(mod, "compute_embeddings_batch", _boom)
    # 不传 embed_batch_fn 时才会打真后端；这里显式传 mock，_boom 绝不该被调用。
    report = mod.run_calibration(
        ["书A", "书B"], n_pairs=10, seed=5,
        styles_root=fake_corpus_root, embed_batch_fn=_fake_embed_batch)
    assert report["meta"]["embed_method"] == "injected_mock"


# ════════════════════════════ 10. main() 全流程（mock 掉真后端 + daemon） ════════════════════════════

def test_main_writes_json_and_md_reports(fake_corpus_root, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "compute_embeddings_batch", _fake_embed_batch)
    monkeypatch.setattr(mod.nn_daemon_client, "ensure_daemon", lambda *a, **k: False)
    monkeypatch.setattr(mod.nn_daemon_client, "shutdown_daemon", lambda *a, **k: False)
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.delenv("RUOYU_NN_DAEMON", raising=False)

    out_dir = tmp_path / "out"
    argv = ["semantic_threshold_calibrator.py",
            "--books", "书A,书B,书C", "--pairs", "8", "--seed", "99",
            "--styles-root", str(fake_corpus_root), "--output-dir", str(out_dir)]
    monkeypatch.setattr(sys, "argv", argv)

    rc = mod.main()
    assert rc == 0
    # main() 自己会 setdefault 两个 env（真实调用点行为·此处只需确认不炸且落盘）
    assert os.environ.get("EMBED_BACKEND") == "ruoyu_style"

    json_files = list(out_dir.glob("ruoyu_style_separability_*.json"))
    md_files = list(out_dir.glob("ruoyu_style_separability_*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["meta"]["embed_method"] == "injected_mock"
    assert payload["verdict"]["label"] in ("A", "B", "C")
