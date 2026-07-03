"""memory_layer.py 确定性单元测试 —— 三层记忆系统纯逻辑回归网。

memory_layer 整体是确定性的（TF-IDF 余弦检索 / 正则实体提取 / JSON 聚合 / 遗忘
统计），不调 LLM 不联网。这组测试钉死：
  - 纯函数 _tokens / _cosine / _tfidf_vec / load_json 的边界行为
  - MemoryLayer 的层2摘要拍平（v2 账本 clusters[].chapters 嵌套）+ 层3归档提取
  - search 跨三层 TF-IDF 检索（相关命中 + 空库 + top_k 钳位 + 阈值过滤）
  - extract_entities 正则实体提取（按 count 排序 + 短名过滤）
  - stats 遗忘元素探测（last_ch 距离 >= 5）+ build 计数
零依赖：只用标准库 + tempfile 临时目录。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import memory_layer as mod  # noqa: E402


# ============ 造项目骨架 ============

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ============ 纯函数 ============

def test_tokens_words_and_bigrams():
    """_tokens：中文产 2-4 字词 + 全字符 bigram；标点/空白被剔除。"""
    toks = mod._tokens("许遥 的, 父亲")
    # 应包含 2 字词
    assert "许遥" in toks
    assert "父亲" in toks
    # bigram 跨标点拼接（标点被 sub 掉后 chars="许遥的父亲"）
    assert "许遥" in toks
    assert "遥的" in toks
    # 空串安全
    assert mod._tokens("") == []
    assert mod._tokens("，。！") == []  # 纯标点 → 空


def test_cosine_identity_orthogonal_empty():
    """_cosine：相同向量=1.0、无交集=0.0、空向量=0.0。"""
    v = {"许遥": 1.0, "父亲": 2.0}
    # 同向量余弦 = 1.0（浮点近似）
    assert abs(mod._cosine(v, v) - 1.0) < 1e-9
    # 无公共 key → 0.0
    assert mod._cosine({"a": 1.0}, {"b": 1.0}) == 0.0
    # 空向量 → 0.0（不除零崩）
    assert mod._cosine({}, {}) == 0.0
    assert mod._cosine({"a": 1.0}, {}) == 0.0


def test_tfidf_vec_weights_and_empty():
    """_tfidf_vec：tf 归一 * idf；空文本不崩（total 钳到 >=1）。"""
    idf = {"许遥": 3.0}
    vec = mod._tfidf_vec("许遥", idf)
    # 单 token "许遥" 出现：tf=1/1, idf=3 → 该维 = 3.0（其它维用默认 idf=1.0）
    assert vec["许遥"] == 3.0
    # 空文本 → 空向量，不抛 ZeroDivisionError
    assert mod._tfidf_vec("", idf) == {}


def test_load_json_missing_and_corrupt():
    """load_json：缺文件 → default；损坏 JSON → default（不抛）。"""
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        assert mod.load_json(missing, {"x": 1}) == {"x": 1}
        assert mod.load_json(missing) is None
        bad = Path(d) / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert mod.load_json(bad, []) == []


# ============ 层2 摘要记忆（v2 嵌套拍平 + ch 过滤）============

def test_summary_memory_flattens_nested_clusters():
    """层2：v2 账本 clusters[].chapters{} 嵌套被拍平；ch >= 当前章被过滤。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "故事块摘要.json", {
            "clusters": [
                {"cluster_id": "cluster_001",
                 "chapters": {
                     "1": {"summary": "许遥登场", "emotion": {"joy": 0.5}},
                     "2": {"summary": "父亲失踪"},
                     "5": {"summary": "未来章不该出现"},
                 }},
            ]
        })
        ml = mod.MemoryLayer(tmp, current_ch=5)
        rows = ml._load_summary_memory()
        chs = sorted(r["ch"] for r in rows)
        # ch1, ch2 保留；ch5 == current_ch 被过滤（ch >= self.ch continue）
        assert chs == [1, 2]
        by_ch = {r["ch"]: r for r in rows}
        assert by_ch[1]["content"] == "许遥登场"
        assert by_ch[1]["emotion"] == {"joy": 0.5}
        assert by_ch[1]["layer"] == "summary"


def test_summary_memory_legacy_top_level_chapters():
    """层2：兼容旧顶层 chapters[] 平铺布局。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "故事块摘要.json", {
            "chapters": [
                {"ch": 1, "summary": "旧布局第一章"},
                {"chapter": 2, "summary": "用 chapter 键也认"},
                {"ch": 9, "summary": "超当前章过滤"},
            ]
        })
        ml = mod.MemoryLayer(tmp, current_ch=3)
        rows = ml._load_summary_memory()
        chs = sorted(r["ch"] for r in rows)
        assert chs == [1, 2]   # ch9 被过滤
        # 缺文件时层2为空，不崩
        empty = mod.MemoryLayer(Path(d) / "ghost", current_ch=3)
        assert empty._load_summary_memory() == []


# ============ 层3 归档记忆 ============

def test_archive_memory_extracts_characters_and_world():
    """层3：从人物卡（locked_facts + growth_arc 末态）+ 世界观（前10）提取归档事实。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "人物卡.json", {"characters": [
            {"name": "许遥",
             "locked_facts": ["父亲失踪", "会武术", "怕水", "第四条不进卡片"],
             "growth_arc": [{"ch": 1, "state": "懦弱"}, {"ch": 4, "state": "觉醒"}]},
        ]})
        _write_json(db / "世界观.json", {"entries": [
            {"id": "云霄宗", "keywords": ["仙门", "御剑", "丹药", "灵脉", "护山阵", "第六个不取"]},
        ]})
        ml = mod.MemoryLayer(tmp, current_ch=10)
        rows = ml._load_archive_memory()
        char_row = next(r for r in rows if r["source"] == "人物卡")
        # locked_facts 只取前 3
        assert "父亲失踪; 会武术; 怕水" in char_row["content"]
        assert "第四条不进卡片" not in char_row["content"]
        # growth_arc 取末态
        assert "[最新状态ch4: 觉醒]" in char_row["content"]
        world_row = next(r for r in rows if r["source"] == "世界观")
        # keywords 只取前 5
        assert "仙门; 御剑; 丹药; 灵脉; 护山阵" in world_row["content"]
        assert "第六个不取" not in world_row["content"]


def test_archive_memory_empty_db_no_crash():
    """层3：空/缺数据库 → 空列表，不抛。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)  # 空 _数据库
        ml = mod.MemoryLayer(tmp, current_ch=1)
        assert ml._load_archive_memory() == []


# ============ search 跨三层检索 ============

def test_search_relevant_hit_and_topk_and_empty():
    """search：相关查询命中、top_k 钳位、空库返回 []、阈值过滤。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        # 灌入多条摘要供检索
        _write_json(db / "故事块摘要.json", {"chapters": [
            {"ch": 1, "summary": "许遥的父亲在码头失踪了"},
            {"ch": 2, "summary": "云霄宗弟子御剑飞行修炼"},
            {"ch": 3, "summary": "许遥决心去码头寻找父亲下落"},
        ]})
        ml = mod.MemoryLayer(tmp, current_ch=5)
        res = ml.search("许遥的父亲", top_k=5)
        assert len(res) >= 1
        # 含 "许遥" "父亲" 的章应排在前，分数降序
        scores = [r["score"] for r in res]
        assert scores == sorted(scores, reverse=True)
        top = res[0]
        assert "许遥" in top["content"] and "父亲" in top["content"]
        # content 被截断到 200 字
        assert len(top["content"]) <= 200
        # top_k 钳位
        assert len(ml.search("许遥的父亲", top_k=1)) <= 1
        # 空库（无任何记忆文件）→ []
        empty = mod.MemoryLayer(Path(d) / "ghost", current_ch=5)
        assert empty.search("任意查询") == []
        # 完全不相关的查询 → 阈值过滤后为空
        assert ml.search("量子计算机超导芯片") == []


# ============ extract_entities ============

def test_extract_entities_sort_and_short_name_filter():
    """实体提取：命中人物/地点/道具，按 count 降序；地点短名(<2)被过滤。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "人物卡.json", {"characters": [
            {"name": "许遥"}, {"name": "林婉"},
        ]})
        _write_json(db / "世界观.json", {"entries": [
            {"keywords": ["云霄宗", "山"]},  # "山" 单字 <2 → 过滤
        ]})
        _write_json(db / "道具.json", {"items": [
            {"name": "断水剑"},
        ]})
        ml = mod.MemoryLayer(tmp, current_ch=1)
        text = "许遥许遥许遥在云霄宗拿到断水剑，林婉旁观，山很高。"
        ents = ml.extract_entities(text)
        names = {e["name"]: e for e in ents}
        # 许遥 出现 3 次，林婉 1 次
        assert names["许遥"]["count"] == 3
        assert names["许遥"]["type"] == "character"
        assert names["林婉"]["count"] == 1
        assert names["云霄宗"]["type"] == "location"
        assert names["断水剑"]["type"] == "item"
        # "山" 单字地点被过滤
        assert "山" not in names
        # 整体按 count 降序
        counts = [e["count"] for e in ents]
        assert counts == sorted(counts, reverse=True)
        # 文本不含任何已知实体 → 空
        assert ml.extract_entities("毫不相关的随机文字") == []


# ============ build + stats ============

def test_build_counts_layers():
    """build：返回三层计数 + total 一致。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "故事块摘要.json", {"chapters": [
            {"ch": 1, "summary": "a"}, {"ch": 2, "summary": "b"},
        ]})
        _write_json(db / "人物卡.json", {"characters": [{"name": "许遥", "locked_facts": ["x"]}]})
        ml = mod.MemoryLayer(tmp, current_ch=5)
        info = ml.build()
        assert info["summary_count"] == 2
        assert info["archive_count"] == 1
        assert info["chapter_count"] == 0  # 无正文文件
        assert info["total"] == info["chapter_count"] + info["summary_count"] + info["archive_count"]


# ============ embedding 接线（2026-07-02 · 真后端门控 + TF-IDF fallback）============

def test_has_real_embedding_backend_false_by_default():
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_has_real_embedding_backend_true_when_set():
    old = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "local"
        assert mod._has_real_embedding_backend() is True
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_search_semantic_finds_paraphrase_literal_tfidf_misses(monkeypatch):
    """真后端：query 与记忆内容零字面重叠的同义改写（"阿光他爹没"↔"许遥的父亲失踪了"）
    TF-IDF 查不到（cosine 必为 0·前置断言验证零重叠），embedding 语义路径应命中。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "故事块摘要.json", {"chapters": [
            {"ch": 1, "summary": "许遥的父亲失踪了"},
            {"ch": 2, "summary": "云霄宗弟子御剑飞行修炼"},
        ]})
        ml = mod.MemoryLayer(tmp, current_ch=5)

        # 前置断言：门控关时字面 TF-IDF 完全查不到（"阿光他爹没" 与记忆库零字符重叠）
        assert ml.search("阿光他爹没") == []

        monkeypatch.setenv("EMBED_BACKEND", "mock")
        import embedding_store

        def _mock_embed(text):
            if "许遥的父亲失踪了" in text or "阿光他爹没" in text:
                return [1.0, 0.0]
            return [0.0, 1.0]
        monkeypatch.setattr(embedding_store, "compute_embedding", _mock_embed)

        res = ml.search("阿光他爹没")
        assert len(res) >= 1, "语义路径应命中同义改写"
        assert "许遥的父亲失踪了" in res[0]["content"]
        assert res[0]["method"] == "semantic"


def test_search_semantic_falls_back_on_embedding_error(monkeypatch):
    """真后端配置但 query 编码异常 → 回退 TF-IDF（不崩·不误标 semantic）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "故事块摘要.json", {"chapters": [
            {"ch": 1, "summary": "许遥的父亲在码头失踪了"},
        ]})
        ml = mod.MemoryLayer(tmp, current_ch=5)

        monkeypatch.setenv("EMBED_BACKEND", "mock")
        import embedding_store

        def _boom(text):
            raise RuntimeError("模拟真后端编码失败")
        monkeypatch.setattr(embedding_store, "compute_embedding", _boom)

        res = ml.search("许遥的父亲")
        assert len(res) >= 1
        assert all("method" not in r for r in res)  # 回退 TF-IDF（不带 semantic 标记）


def test_search_default_no_backend_unaffected():
    """🔴 零回归锁：无 EMBED_BACKEND（默认）→ search 结果保持纯 TF-IDF 路径（无 semantic 标记）。"""
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db = _mk_db(tmp)
            _write_json(db / "故事块摘要.json", {"chapters": [
                {"ch": 1, "summary": "许遥的父亲在码头失踪了"},
                {"ch": 2, "summary": "云霄宗弟子御剑飞行修炼"},
                {"ch": 3, "summary": "许遥决心去码头寻找父亲下落"},
            ]})
            ml = mod.MemoryLayer(tmp, current_ch=5)
            res = ml.search("许遥的父亲", top_k=5)
            assert len(res) >= 1
            assert all("method" not in r for r in res)
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_stats_forgotten_elements_threshold():
    """stats：关系.json reference_counts 中 last_ch 距当前 >=5 → 标记遗忘。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_json(db / "关系.json", {"reference_counts": {
            "characters": {
                "许遥": {"last_ch": 9},   # 10-9=1 < 5 → 不遗忘
                "林婉": {"last_ch": 4},   # 10-4=6 >= 5 → 遗忘
                "老王": {"last_ch": 5},   # 10-5=5 >= 5 → 遗忘（边界）
            }
        }})
        ml = mod.MemoryLayer(tmp, current_ch=10)
        info = ml.stats()
        forgotten = info["forgotten_elements"]
        joined = " ".join(forgotten)
        assert "林婉" in joined
        assert "老王" in joined          # 边界恰好命中
        assert "许遥" not in joined      # 距离不足不遗忘
        # 缺关系文件时 stats 仍返回（forgotten 为空），不崩
        empty = mod.MemoryLayer(Path(d) / "ghost", current_ch=10)
        assert empty.stats()["forgotten_elements"] == []
