"""rag_retriever 专属回归测试（2026-06-17·确定性·零依赖·只用标准库）。

聚焦尚未被现有间接覆盖的核心确定性逻辑：
  - 现有 test_b1_importance.py / test_mmr_voice.py 已钉死 `mmr_rerank` + `tier_to_importance`。
  - 本文件补：纯 TF-IDF 算法块（_chinese_tokens / _tfidf_vectors / _cosine / _snippet）、
    端到端 retrieve_tfidf（文件 IO + 故事块摘要 v2 拍平 + blueprint 归一 + MMR/top-k 分支 +
    各空态早退）、retrieve_embedding 无 key 降级、main() CLI 退出码。

全程不打 LLM、不联网（TF-IDF 纯 Python；embedding 走无 key 降级路径）。
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import rag_retriever as rr  # noqa: E402
import chapter_io as cio    # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 工具：搭一个最小可检索的小说项目
# ════════════════════════════════════════════════════════════════════
def _write_chapter(root: Path, ch: int, body: str):
    """用被测脚本同款的 chapter_io.write_body 落正文（走标准 第NNN章/第NNN章.txt 布局）。"""
    cio.write_body(root, ch, body)


def _write_summaries_v2(root: Path, cluster_chapters: dict):
    """写 v2 账本形态 故事块摘要.json：clusters[].chapters{ "ch": {summary,...} }。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    clusters = [{"cluster_id": "cluster_001", "chapters": cluster_chapters}]
    (db / "故事块摘要.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")


def _write_progress_blueprint(root: Path, scenes_by_cluster: dict):
    """写 进度.json，cluster_blueprint 为规范 dict 形态（cid -> {scene_storyboard:[...]}）。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    bp = {cid: {"scene_storyboard": scenes} for cid, scenes in scenes_by_cluster.items()}
    (db / "进度.json").write_text(
        json.dumps({"cluster_blueprint": bp}, ensure_ascii=False), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# [A] _chinese_tokens：2/3/4-gram + 词级 token
# ════════════════════════════════════════════════════════════════════
def test_chinese_tokens_ngrams_and_words():
    toks = rr._chinese_tokens("剑光闪过")
    # 4 个汉字 → 2-gram 有 3 个(剑光/光闪/闪过), 3-gram 2 个, 4-gram 1 个 = 6 个 ngram
    assert "剑光" in toks
    assert "剑光闪" in toks
    assert "剑光闪过" in toks
    # findall [一-鿿]{2,4} 追加词级 token（整串命中）
    assert toks.count("剑光闪过") >= 1
    # 标点/空白被剥掉，不产生跨标点 token
    assert "光过" not in toks


def test_chinese_tokens_strips_punct_and_short():
    # 纯标点 → 清洗后空串 → 无 token
    assert rr._chinese_tokens("。，！？") == []
    # 单字不足 2-gram，findall {2,4} 也要 ≥2 → 单字返回空
    assert rr._chinese_tokens("剑") == []


# ════════════════════════════════════════════════════════════════════
# [B] _tfidf_vectors + _cosine：TF-IDF 向量化 + 余弦
# ════════════════════════════════════════════════════════════════════
def test_tfidf_vectors_shape_and_df():
    docs = ["剑光剑光", "剑光闪过"]
    vectors, df = rr._tfidf_vectors(docs)
    assert len(vectors) == 2
    # "剑光" 同时出现在两个文档 → df 计 2（按文档去重计 df）
    assert df["剑光"] == 2
    # 仅出现在 doc1 的 token df 计 1
    assert df.get("闪过", 0) == 1
    # 每个向量都是 token->权重 的 dict，权重为正
    assert all(isinstance(v, dict) and v for v in vectors)
    assert all(w > 0 for w in vectors[0].values())


def test_cosine_identical_and_orthogonal():
    # 同一文档余弦应为 1.0（数值容差）
    vecs, _ = rr._tfidf_vectors(["剑光闪过血溅当场"])
    a = vecs[0]
    assert abs(rr._cosine(a, a) - 1.0) < 1e-9
    # 完全无共享 token → 0.0
    b = {"风雪": 1.0, "夜归": 2.0}
    c = {"朝阳": 1.0, "暖意": 2.0}
    assert rr._cosine(b, c) == 0.0
    # 空向量 → 0.0（norm 为 0 的保护分支）
    assert rr._cosine({}, b) == 0.0


def test_cosine_partial_overlap_between_0_and_1():
    a = {"共有": 1.0, "甲独": 1.0}
    b = {"共有": 1.0, "乙独": 1.0}
    s = rr._cosine(a, b)
    assert 0.0 < s < 1.0      # 部分重叠 → 严格落在 (0,1)


# ════════════════════════════════════════════════════════════════════
# [C] _snippet：按累计长度截行
# ════════════════════════════════════════════════════════════════════
def test_snippet_truncates_by_length():
    text = "第一行内容\n第二行内容\n第三行很长很长很长很长很长很长很长很长很长很长内容"
    out = rr._snippet(text, length=12)
    # 第一+第二行 = 10 字 ≤ 12，第三行加上去超 12 → 停在前两行
    assert out == "第一行内容\n第二行内容"


def test_snippet_no_full_line_fits_falls_back_to_slice():
    # 单行就超长度 → result 为空 → fallback 切前 length 字符
    text = "这是一整段超过限制长度的连续文字没有换行所以无法逐行装入"
    out = rr._snippet(text, length=8)
    assert out == text[:8]
    assert len(out) == 8


# ════════════════════════════════════════════════════════════════════
# [D] retrieve_tfidf：端到端（含 v2 摘要拍平 + blueprint 归一 + 排序）
# ════════════════════════════════════════════════════════════════════
def test_retrieve_tfidf_ranks_relevant_chapter_first():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 历史章 1/2，正文主题不同
        _write_chapter(root, 1, "剑修在剑冢里淬炼剑心剑意剑光浩荡当空")
        _write_chapter(root, 2, "厨房里炖着汤水柴米油盐生活气息温暖")
        # 当前要写第 3 章，其 scene plan 讲剑——应检出第 1 章
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 3, "summary": "剑修剑光剑意大战剑冢"}],
        })
        res = rr.retrieve_tfidf(root, current_ch=3, top_k=2, use_mmr=False)
        assert res, "应至少检出 1 章"
        assert res[0]["chapter"] == 1          # 剑主题章排第一
        # 字段契约：chapter / score / snippet 齐全
        assert set(res[0].keys()) >= {"chapter", "score", "snippet"}
        assert isinstance(res[0]["score"], float)


def test_retrieve_tfidf_excludes_current_and_future():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢淬炼")
        _write_chapter(root, 5, "剑光剑意剑冢淬炼")   # 未来章（>= current_ch）
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 3, "summary": "剑光剑意"}],
        })
        res = rr.retrieve_tfidf(root, current_ch=3, top_k=5, use_mmr=False)
        chapters = {r["chapter"] for r in res}
        assert 1 in chapters       # 历史章在
        assert 5 not in chapters   # 当前章及之后被 ch_num >= current_ch 排除
        assert 3 not in chapters


def test_retrieve_tfidf_flattens_v2_summary_ledger():
    """v2 账本 clusters[].chapters{} 拍平后摘要被用作检索文本（北极星复审修复点）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 正文故意写成无关主题，摘要里才有剑主题 → 命中只能来自 v2 摘要拍平
        _write_chapter(root, 1, "无关无关无关无关填充正文内容")
        _write_summaries_v2(root, {"1": {"summary": "剑光剑意剑冢决战的关键一章"}})
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 2, "summary": "剑光剑意剑冢"}],
        })
        res = rr.retrieve_tfidf(root, current_ch=2, top_k=2, use_mmr=False)
        assert res and res[0]["chapter"] == 1
        # snippet 取自摘要（拍平生效），含剑字
        assert "剑" in res[0]["snippet"]


def test_retrieve_tfidf_empty_when_no_chapters():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 无任何历史章 → 早退空
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光"}]})
        assert rr.retrieve_tfidf(root, current_ch=3, top_k=3) == []


def test_retrieve_tfidf_empty_when_no_current_plan():
    """有历史章但 blueprint 里没有当前章的 scene → current_plan 空 → 返回 []。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢")
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 99, "summary": "和当前章无关的别章计划"}],
        })
        assert rr.retrieve_tfidf(root, current_ch=3, top_k=3) == []


def test_retrieve_tfidf_normalizes_list_blueprint():
    """城南实测 cluster_blueprint 是 list 形态 → 归一后仍能取出当前章 scene（不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢淬炼")
        db = root / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        # list 形态：每项是逐章 scene 记录（带 cluster + ch）
        bp_list = [
            {"cluster": "cluster_001", "ch": 1, "summary": "开篇"},
            {"cluster": "cluster_001", "ch": 3, "summary": "剑光剑意剑冢"},
        ]
        (db / "进度.json").write_text(
            json.dumps({"cluster_blueprint": bp_list}, ensure_ascii=False), encoding="utf-8")
        res = rr.retrieve_tfidf(root, current_ch=3, top_k=2, use_mmr=False)
        assert res and res[0]["chapter"] == 1   # list 归一成功 → 检索照常


def test_retrieve_tfidf_accepts_str_path():
    """v17.5：project_root 接受 str 也接受 Path。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意"}]})
        res = rr.retrieve_tfidf(d, current_ch=3, top_k=2, use_mmr=False)  # 传 str
        assert res and res[0]["chapter"] == 1


# ════════════════════════════════════════════════════════════════════
# [E] retrieve_embedding：无 OPENAI_API_KEY → 降级 TF-IDF
# ════════════════════════════════════════════════════════════════════
def test_retrieve_embedding_falls_back_to_tfidf_without_key():
    import os
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意"}]})
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            # 吞掉 [INFO] 降级提示（写在 stderr）
            with redirect_stderr(io.StringIO()):
                res = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old
        # 降级路径返回与 TF-IDF 同样的结果（chapter=1）
        assert res and res[0]["chapter"] == 1


# ════════════════════════════════════════════════════════════════════
# [F] main()：CLI 退出码 + JSON 输出
# ════════════════════════════════════════════════════════════════════
def test_main_usage_error_exits_2():
    """参数不足 → 用法错误 → sys.exit(2)。"""
    old_argv = sys.argv
    try:
        sys.argv = ["rag_retriever.py"]   # 缺项目路径 + 章节号
        raised = None
        with redirect_stderr(io.StringIO()):
            try:
                rr.main()
            except SystemExit as e:
                raised = e
        assert raised is not None
        assert raised.code == 2
    finally:
        sys.argv = old_argv


def test_main_prints_json_list():
    """正常 CLI：打印一个 JSON 列表（无可检索内容时为 [] 但仍是合法 JSON）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢淬炼")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意"}]})
        old_argv = sys.argv
        buf = io.StringIO()
        try:
            sys.argv = ["rag_retriever.py", str(root), "3", "--top-k", "2", "--no-mmr"]
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rr.main()
        finally:
            sys.argv = old_argv
        parsed = json.loads(buf.getvalue())
        assert isinstance(parsed, list)
        assert parsed and parsed[0]["chapter"] == 1
