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
import os
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
# [E] retrieve_embedding：门控 = _has_real_embedding_backend()（2026-07-02 接线 embedding_store
#     取代此前误导性的 OPENAI_API_KEY/openai 包检测——那条检测路径从未真正调用过 OpenAI）
# ════════════════════════════════════════════════════════════════════
def _clear_embed_env(monkeypatch):
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for k in [k for k in os.environ if k.startswith("GEN_EMBED__")]:
        monkeypatch.delenv(k, raising=False)


def _char_freq_embedding(text: str, dim: int = 64) -> list:
    """确定性、内容感知的假 embedding（字符频率向量）——同 test_topic_drift_scanner 手法。"""
    import math as _math
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = _math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class TestHasRealEmbeddingBackend:
    """_has_real_embedding_backend 门控判断（跟 topic_drift_scanner 同款逻辑）。"""

    def test_no_env_returns_false(self, monkeypatch):
        _clear_embed_env(monkeypatch)
        assert rr._has_real_embedding_backend() is False

    def test_hash_returns_false(self, monkeypatch):
        monkeypatch.setenv("EMBED_BACKEND", "hash")
        assert rr._has_real_embedding_backend() is False

    def test_real_backend_name_returns_true(self, monkeypatch):
        monkeypatch.setenv("EMBED_BACKEND", "mstyle")
        assert rr._has_real_embedding_backend() is True

    def test_gen_embed_env_returns_true(self, monkeypatch):
        monkeypatch.delenv("EMBED_BACKEND", raising=False)
        monkeypatch.setenv("GEN_EMBED__test__API_KEY", "fake")
        assert rr._has_real_embedding_backend() is True


def test_retrieve_embedding_falls_back_to_tfidf_without_real_backend(monkeypatch):
    """无真 embedding 后端（默认环境）→ 降级 TF-IDF，结果与 TF-IDF 一致。"""
    _clear_embed_env(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意"}]})
        # 吞掉 [INFO] 降级提示（写在 stderr）
        with redirect_stderr(io.StringIO()):
            res = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)
        # 降级路径返回与 TF-IDF 同样的结果（chapter=1）
        assert res and res[0]["chapter"] == 1


def test_retrieve_embedding_gate_off_never_calls_compute_embedding(monkeypatch):
    """零回归证明：门控关（默认环境）时 retrieve_embedding 绝不调用 compute_embedding，
    且输出与「retrieve_tfidf + fallback 标签」逐字节一致。"""
    _clear_embed_env(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢淬炼")
        _write_chapter(root, 2, "厨房里炖着汤水柴米油盐")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意剑冢"}]})

        import embedding_store
        calls = {"n": 0}

        def _counting_embed(text):
            calls["n"] += 1
            return [0.0]

        monkeypatch.setattr(embedding_store, "compute_embedding", _counting_embed)
        with redirect_stderr(io.StringIO()):
            res_embed = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)
        assert calls["n"] == 0, "无真后端时 retrieve_embedding 不应调用 compute_embedding"

        res_tfidf = rr.retrieve_tfidf(root, current_ch=3, top_k=2, use_mmr=False)
        expected = [dict(r, mode="tfidf_fallback (embedding not implemented yet)") for r in res_tfidf]
        assert res_embed == expected


def test_retrieve_embedding_real_backend_uses_semantic_path(monkeypatch):
    """真后端命中：EMBED_BACKEND 配置 + compute_embedding 内容感知假向量 → 语义路径生效
    （不是 TF-IDF 降级），且能正确检出语义相关章节。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑修在剑冢里淬炼剑心剑意剑光浩荡当空")
        _write_chapter(root, 2, "厨房里炖着汤水柴米油盐生活气息温暖惬意")
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 3, "summary": "剑修剑光剑意大战剑冢"}],
        })

        import embedding_store
        calls = {"n": 0}

        def _counting_char_freq(text):
            calls["n"] += 1
            return _char_freq_embedding(text)

        monkeypatch.setattr(embedding_store, "compute_embedding", _counting_char_freq)
        with redirect_stderr(io.StringIO()):
            res = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)

        assert calls["n"] > 0, "真后端应真调用 compute_embedding"
        assert res, "应至少检出 1 章"
        assert res[0]["chapter"] == 1          # 剑主题章语义最相关
        assert res[0]["mode"] == "embedding"   # 走的是语义路径而非 tfidf_fallback
        assert set(res[0].keys()) >= {"chapter", "score", "snippet", "mode"}


def test_retrieve_embedding_batches_prefetch_once(monkeypatch):
    """🔴 2026-07-03 Wave-4：语义分支对『历史章语料+当前章 query』只触发一次批量
    prefetch_embeddings（而非逐条各自撞真后端），且不影响既有排序结果。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑修在剑冢里淬炼剑心剑意剑光浩荡当空")
        _write_chapter(root, 2, "厨房里炖着汤水柴米油盐生活气息温暖惬意")
        _write_progress_blueprint(root, {
            "cluster_001": [{"ch": 3, "summary": "剑修剑光剑意大战剑冢"}],
        })

        import embedding_store
        prefetch_calls = []

        def _recording_prefetch(texts):
            prefetch_calls.append(list(texts))
            return {"total": len(texts), "unique": len(set(texts)),
                    "cache_hits": 0, "computed": len(set(texts))}

        monkeypatch.setattr(embedding_store, "prefetch_embeddings", _recording_prefetch)
        monkeypatch.setattr(embedding_store, "compute_embedding", _char_freq_embedding)
        with redirect_stderr(io.StringIO()):
            res = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)

        assert len(prefetch_calls) == 1, "语义分支应只触发一次批量 prefetch"
        # docs = 2 条历史章语料 + 1 条当前章 query
        assert len(prefetch_calls[0]) == 3
        # 排序结果不受批量改造影响：剑主题章仍排第一
        assert res and res[0]["chapter"] == 1
        assert res[0]["mode"] == "embedding"


def test_retrieve_embedding_dimension_mismatch_falls_back(monkeypatch):
    """embedding 维度不一致（模拟部分条目降级 hash）→ 不冒充语义，退 TF-IDF。"""
    monkeypatch.setenv("EMBED_BACKEND", "fake-real")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_chapter(root, 1, "剑光剑意剑冢淬炼")
        _write_progress_blueprint(root, {"cluster_001": [{"ch": 3, "summary": "剑光剑意"}]})

        import embedding_store

        def _mixed_dim(text):
            if "剑光剑意" in text and "淬炼" not in text:  # query 命中，维度故意不同
                return [0.1] * 8
            return _char_freq_embedding(text, dim=64)

        monkeypatch.setattr(embedding_store, "compute_embedding", _mixed_dim)
        with redirect_stderr(io.StringIO()):
            res = rr.retrieve_embedding(root, current_ch=3, top_k=2, use_mmr=False)
        # 维度混用 → 降级 TF-IDF fallback（标签可辨识，不是语义 mode）
        assert all(r.get("mode") != "embedding" for r in res)


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
