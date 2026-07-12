"""chapter_end_anchor_scan 回归测试 — L2 章末 cliffhanger 锚定扫描的纯逻辑。

钉死确定性部分：章末段提取 / 实词关键词抽取（含停用词过滤）/ anchors 采集
（事件簇·伏笔表·进度·人物卡·道具·地图 · blueprint list 归一）/ banned_patterns
分级（剧本体+物理分隔=hard_gate · 语义收束=advisory）/ 锚定率分支（无锚·弱锚）/
章节范围解析。全部不调 LLM、不联网。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_end_anchor_scan as mod  # noqa: E402
import embedding_store  # noqa: E402


# 🔴 本地 autouse 隔离（不碰全局 conftest.py）：content_backend_available() 查真文件系统
# （venv/infer 脚本/模型目录），本机若已备好 bge 模型会恒真——EMBED_BACKEND 有
# conftest._isolate_nn_gates 兜底清零，内容后端没有对应全局兜底，会让本文件里不测
# embedding 的"素"用例跨机器非确定污染（真机上真的算出高于阈值的相似度）。
# 默认关闭·内容路径专项测试自行 monkeypatch 覆盖。
@pytest.fixture(autouse=True)
def _content_backend_off_by_default():
    orig = embedding_store.content_backend_available
    embedding_store.content_backend_available = lambda: False
    try:
        yield
    finally:
        embedding_store.content_backend_available = orig


# ---------- parse_chapter_range ----------

def test_parse_chapter_range_single():
    assert mod.parse_chapter_range("5") == [5]


def test_parse_chapter_range_inclusive():
    # 1-4 必须含尾 4（range(a, b+1)）
    assert mod.parse_chapter_range("1-4") == [1, 2, 3, 4]
    assert mod.parse_chapter_range("3-3") == [3]


# ---------- get_chapter_tail_paragraphs ----------

def test_get_chapter_tail_paragraphs_takes_last_n():
    text = "\n\n".join(f"段{i}" for i in range(1, 9))  # 8 段
    tail = mod.get_chapter_tail_paragraphs(text, n=5)
    assert tail == ["段4", "段5", "段6", "段7", "段8"]


def test_get_chapter_tail_paragraphs_fewer_than_n():
    # 段数 < n 时返回全部非空段（不补空）
    text = "甲\n\n乙"
    tail = mod.get_chapter_tail_paragraphs(text, n=5)
    assert tail == ["甲", "乙"]


def test_get_chapter_tail_paragraphs_drops_blank():
    # 空白段被过滤，且每段 strip
    text = "  头  \n\n   \n\n  尾  "
    tail = mod.get_chapter_tail_paragraphs(text, n=5)
    assert tail == ["头", "尾"]


# ---------- extract_keywords ----------

def test_extract_keywords_basic_and_stopwords():
    # 标点切断连续中文，每段独立成词（正则 [一-鿿]{2,4} 贪婪整段匹配）
    kws = mod.extract_keywords("黑刀，祭坛，然后，时候")
    # 实词被抽出
    assert "黑刀" in kws
    assert "祭坛" in kws
    # 停用词被剔除
    assert "然后" not in kws
    assert "时候" not in kws


def test_extract_keywords_greedy_chunks_not_substrings():
    # 关键行为：连续 8 字被贪婪切成两个 4 字块，而非枚举所有 2 字子串
    kws = mod.extract_keywords("黑刀祭坛守卫玉佩")
    assert kws == {"黑刀祭坛", "守卫玉佩"}


def test_extract_keywords_filters_single_char():
    # 单字（< 2）永不入选；纯标点/空串无关键词
    assert mod.extract_keywords("。，！？ a 1 x") == set()


# ---------- collect_anchors ----------

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write(db: Path, name: str, obj) -> None:
    (db / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_collect_anchors_empty_db_returns_empty_set():
    # _数据库 存在但无任何 JSON → 空集合，不崩
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        assert mod.collect_anchors(db) == set()


def test_collect_anchors_from_event_cluster_and_foreshadowing():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        # 用标点切断，保证每个 2 字实词独立成 keyword（贪婪整段匹配特性）
        _write(db, "事件簇.json", {
            "clusters": [{
                "title": "祭坛",
                "scope_summary": "黑刀，追杀",
                "scene_storyboard": [{"summary": "守卫，密室"}],
                "foreshadowing_to_plant": [{"description": "玉佩，血脉"}],
                "characters_in_cluster": ["陆参", "白衣人"],
            }],
        })
        _write(db, "伏笔表.json", {
            "promises": [{"description": "重铸",
                          "trigger_condition": {"physical_evidence": "铭文"}}],
            "secrets": [{"secret": "叛徒"}],
        })
        anchors = mod.collect_anchors(db)
        # 来自事件簇各字段
        assert "黑刀" in anchors
        assert "祭坛" in anchors
        assert "守卫" in anchors
        assert "玉佩" in anchors
        # 人名（characters_in_cluster join 后经 extract_keywords，2 字保留）
        assert "陆参" in anchors
        # 来自伏笔表 description / physical_evidence / secret
        assert "重铸" in anchors
        assert "铭文" in anchors
        assert "叛徒" in anchors
        # 全部 >= 2 字（过滤生效）
        assert all(len(a) >= 2 for a in anchors)


def test_collect_anchors_character_card_names_and_aliases():
    # 人物卡 name/aliases 走 anchors.add（不经 extract_keywords，故 3 字人名也保留）
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        _write(db, "人物卡.json", {
            "characters": [{"name": "诸葛青", "aliases": ["卧龙", "孔明"]}],
        })
        anchors = mod.collect_anchors(db)
        assert "诸葛青" in anchors      # 3 字整体名（extract_keywords 抽不出完整 3 字这种整体名）
        assert "卧龙" in anchors
        assert "孔明" in anchors


def test_collect_anchors_blueprint_dict_form_surfaces_title_scope():
    # 进度.cluster_blueprint 规范 dict 形态 → title/scope_summary 进 anchors
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        _write(db, "进度.json", {
            "cluster_blueprint": {
                "cluster_001": {"title": "开端", "scope_summary": "深渊，村庄"},
            },
        })
        anchors = mod.collect_anchors(db)
        assert "深渊" in anchors
        assert "村庄" in anchors
        assert "开端" in anchors


def test_collect_anchors_blueprint_list_form_does_not_crash():
    # 进度.cluster_blueprint 是 list 形态（城南实测）→ SC-1 归一守卫保证不崩。
    # 真实行为：normalize_blueprint 把各 list 项收进 scene_storyboard，cluster 级 dict
    # 只剩 scene_storyboard/chapter_range，title/scope_summary 不上浮 → 此源 0 anchor。
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        _write(db, "进度.json", {
            "cluster_blueprint": [
                {"cluster": "cluster_001", "ch": 1,
                 "title": "开端", "scope_summary": "深渊，村庄"},
                {"cluster": "cluster_001", "ch": 2,
                 "title": "开端", "scope_summary": "深渊，村庄"},
            ],
        })
        # 关键：不抛异常（裸 .items() 会崩，SC-1 守卫挡住）
        anchors = mod.collect_anchors(db)
        # list 形态本源不贡献 title/scope anchor（埋在 scene_storyboard 未被下钻）
        assert anchors == set()


def test_collect_anchors_malformed_json_is_swallowed():
    # 坏 JSON 被 try/except 吞掉，不影响其它文件采集
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        (db / "事件簇.json").write_text("{ this is not json", encoding="utf-8")
        _write(db, "伏笔表.json", {"secrets": [{"secret": "禁地，巨龙"}]})
        anchors = mod.collect_anchors(db)
        assert "禁地" in anchors  # 好文件照常采到，坏文件无声跳过
        assert "巨龙" in anchors


def test_collect_anchors_props_and_map_locations_both_forms():
    # 道具 items + 地图 locations（dict 与 list 两种形态都支持）
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        _write(db, "道具.json", {"items": [{"name": "弑神枪"}]})
        _write(db, "地图.json", {
            "locations": {"L1": {"name": "幽冥渡口"}},
        })
        a1 = mod.collect_anchors(db)
        assert "弑神枪" in a1
        assert "幽冥渡口" in a1
        # 地图 locations 改 list 形态
        _write(db, "地图.json", {"locations": [{"name": "无间炼狱"}]})
        a2 = mod.collect_anchors(db)
        assert "无间炼狱" in a2


# ---------- scan_chapter_end ----------

def _write_chapter(tmp: Path, text: str) -> Path:
    p = tmp / "ch.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_scan_chapter_end_screenplay_is_hard_gate():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他抬起头。\n\n（镜头拉远）")
        r = mod.scan_chapter_end(ch, anchors=set())
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_FORBIDDEN_SCREENPLAY" in codes
        sp = next(i for i in r["issues"]
                  if i["code"] == "CHAPTER_END_FORBIDDEN_SCREENPLAY")
        assert sp["gate_level"] == "hard_gate"
        assert sp["severity"] == "fatal"


def test_scan_chapter_end_separator_is_hard_gate():
    # 章末单独 * 分隔符 → CHAPTER_END_FORBIDDEN_TRANSITION (hard_gate)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "正文结束。\n\n***")
        r = mod.scan_chapter_end(ch, anchors=set())
        trans = [i for i in r["issues"]
                 if i["code"] == "CHAPTER_END_FORBIDDEN_TRANSITION"]
        assert trans, "应命中物理分隔符 hard_gate"
        assert trans[0]["gate_level"] == "hard_gate"


def test_scan_chapter_end_closure_is_advisory_not_hard_gate():
    # 语义收束句「一切安静下来」→ advisory（可豁免），绝不升 hard_gate
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "门关上了。\n\n一切安静下来。")
        r = mod.scan_chapter_end(ch, anchors=set())
        closure = [i for i in r["issues"]
                   if i["code"] == "CHAPTER_END_CLOSURE_ADVISORY"]
        assert closure, "应命中语义收束句"
        assert closure[0]["gate_level"] == "advisory"
        # 收束句这条不得是 hard_gate
        hard_codes = {i["code"] for i in r["issues"]
                      if i["gate_level"] == "hard_gate"}
        assert "CHAPTER_END_CLOSURE_ADVISORY" not in hard_codes


def test_scan_chapter_end_no_anchor_advisory():
    # 章末关键词与 anchors 0 命中 → CHAPTER_END_NO_ANCHOR (advisory)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"})
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" in codes
        assert r["anchor_ratio"] == 0.0
        na = next(i for i in r["issues"] if i["code"] == "CHAPTER_END_NO_ANCHOR")
        assert na["gate_level"] == "advisory"


def test_scan_chapter_end_strong_anchor_no_issue():
    # 章末关键词全命中 anchors → 无锚定类 issue + ratio = 1.0
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 标点切词 → keywords = {黑刀, 祭坛, 守卫, 玉佩}，全在 anchors
        ch = _write_chapter(tmp, "黑刀，祭坛，守卫，玉佩。")
        anchors = {"黑刀", "祭坛", "守卫", "玉佩"}
        r = mod.scan_chapter_end(ch, anchors=anchors)
        anchor_codes = {"CHAPTER_END_NO_ANCHOR", "CHAPTER_END_WEAK_ANCHOR"}
        assert not (anchor_codes & {i["code"] for i in r["issues"]})
        assert r["anchor_ratio"] == 1.0
        assert len(r["hit_anchors"]) == 4


def test_scan_chapter_end_weak_anchor_below_threshold():
    # 命中 >0 但 ratio < 0.15 → CHAPTER_END_WEAK_ANCHOR（不是 NO_ANCHOR）
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 标点切出 8 个 2 字实词，只 1 个（黑刀）在 anchors → ratio = 1/8 = 0.125 < 0.15
        ch = _write_chapter(
            tmp,
            "黑刀，铁锅，瓦罐，木桌，竹椅，泥墙，茅檐，石阶。",
        )
        r = mod.scan_chapter_end(ch, anchors={"黑刀"})
        codes = {i["code"] for i in r["issues"]}
        assert r["tail_keywords_count"] == 8
        assert "CHAPTER_END_WEAK_ANCHOR" in codes
        assert "CHAPTER_END_NO_ANCHOR" not in codes  # 有命中故不是 NO_ANCHOR
        assert 0 < r["anchor_ratio"] < 0.15


def test_scan_chapter_end_result_shape():
    # 返回结构契约：必含这些键
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "黑刀出鞘。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀"})
        for key in ("chapter_path", "tail_text_preview", "tail_keywords_count",
                    "hit_anchors", "anchor_ratio", "issues"):
            assert key in r, f"结果缺键 {key}"
        assert isinstance(r["issues"], list)
        assert isinstance(r["anchor_ratio"], float)


# ---------- C06 --hard-gate-only（切章后复扫）----------

def test_hard_gate_only_catches_screenplay_and_separator():
    # --hard-gate-only：SCREENPLAY + SEPARATOR 两族 hard_gate 仍命中
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他抬起头。\n\n（镜头拉远）\n\n***")
        r = mod.scan_chapter_end(ch, anchors=set(), hard_gate_only=True)
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_FORBIDDEN_SCREENPLAY" in codes
        assert "CHAPTER_END_FORBIDDEN_TRANSITION" in codes
        assert all(i["gate_level"] == "hard_gate" for i in r["issues"])


def test_hard_gate_only_skips_closure_advisory():
    # 🔴 防升格回归：--hard-gate-only 下语义收束句「灯熄了」既不报 advisory 也绝不升 hard_gate
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他走出房间。\n\n灯熄了。")
        r = mod.scan_chapter_end(ch, anchors=set(), hard_gate_only=True)
        codes = {i["code"] for i in r["issues"]}
        # closure 在 hard_gate_only 模式被整族跳过（既无 advisory 也无 hard_gate）
        assert "CHAPTER_END_CLOSURE_ADVISORY" not in codes
        assert r["issues"] == []  # 纯收束句 · 无任何 hard_gate


def test_hard_gate_only_skips_anchor_advisory():
    # --hard-gate-only 下锚定类 advisory（NO_ANCHOR/WEAK_ANCHOR）全跳过（纯阻断语义）
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"}, hard_gate_only=True)
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" not in codes
        assert "CHAPTER_END_WEAK_ANCHOR" not in codes
        assert r["issues"] == []


def test_default_mode_still_emits_closure_advisory():
    # 对照：不传 hard_gate_only（默认 False）→ closure advisory 照常 emit（不破坏原行为）
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他走出房间。\n\n灯熄了。")
        r = mod.scan_chapter_end(ch, anchors=set())
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_CLOSURE_ADVISORY" in codes


# ---------- collect_anchor_texts（语义 rescue 原始文本池）----------

def test_collect_anchor_texts_from_event_cluster_and_foreshadowing():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        _write(db, "事件簇.json", {
            "clusters": [{
                "scope_summary": "主角发现古老的封印正在松动",
                "foreshadowing_to_plant": [{"description": "石壁上的符文会发光"}],
            }],
        })
        _write(db, "伏笔表.json", {
            "promises": [{"description": "钥匙将开启地下密室",
                          "trigger_condition": {"physical_evidence": "锈迹斑斑的铜钥匙"}}],
            "secrets": [{"secret": "管家是内奸"}],
        })
        texts = mod.collect_anchor_texts(db)
        assert "主角发现古老的封印正在松动" in texts
        assert "石壁上的符文会发光" in texts
        assert "钥匙将开启地下密室" in texts
        assert "锈迹斑斑的铜钥匙" in texts
        assert "管家是内奸" in texts


def test_collect_anchor_texts_empty_db_returns_empty_list():
    with tempfile.TemporaryDirectory() as d:
        db = _mk_db(Path(d))
        assert mod.collect_anchor_texts(db) == []


# ---------- 语义 rescue（内容后端门控 + 字面法 fallback）----------

def test_content_backend_ready_false_by_default(monkeypatch):
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: False)
    assert mod._content_backend_ready() is False


def test_content_backend_ready_true_when_available(monkeypatch):
    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    assert mod._content_backend_ready() is True


def test_anchor_texts_ignored_without_content_backend():
    """anchor_texts 传了但内容后端不可用（默认·autouse fixture）→ 纯字面法（不会悄悄
    启用语义 rescue）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"},
                                  anchor_texts=["冰封的巨兽正在苏醒"])
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" in codes  # 无内容后端 → 字面法照常报
        assert "semantic_anchor_rescue" not in r


def test_semantic_rescue_suppresses_no_anchor_for_paraphrase(monkeypatch):
    """字面 0 命中但 anchor_texts 池里有语义同指条目 → 内容后端下不再误报 CHAPTER_END_NO_ANCHOR。"""
    def _mock_embed(text):
        if "雪山" in text or "冰封的巨兽" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _mock_embed)
    monkeypatch.setattr(embedding_store, "compute_content_embeddings_batch",
                         lambda texts: [_mock_embed(t) for t in texts])
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings",
                         lambda texts: {"total": len(texts), "unique": 0, "cache_hits": 0,
                                        "computed": 0, "available": True})

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        # 前置断言：字面法本该报 NO_ANCHOR（复用既有用例的原句·不传 anchor_texts 时
        # 语义分支的 `... and anchor_texts and ...` 门槛本身就短路，与内容后端是否就绪无关）
        r_literal = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"})
        assert "CHAPTER_END_NO_ANCHOR" in {i["code"] for i in r_literal["issues"]}

        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"},
                                  anchor_texts=["冰封的巨兽正在苏醒"])
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" not in codes
        assert r["semantic_anchor_rescue"]["anchor_text"] == "冰封的巨兽正在苏醒"


def test_semantic_rescue_falls_back_on_embedding_error(monkeypatch):
    """内容后端就绪但编码异常 → 回退字面法（不崩·NO_ANCHOR 照常报）。"""
    def _boom(text):
        raise RuntimeError("模拟内容后端编码失败")

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _boom)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings",
                         lambda texts: {"total": len(texts), "unique": 0, "cache_hits": 0,
                                        "computed": 0, "available": True})

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"},
                                  anchor_texts=["冰封的巨兽正在苏醒"])
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" in codes
        assert "semantic_anchor_rescue" not in r


def test_semantic_rescue_prefetches_tail_and_anchor_pool_once(monkeypatch):
    """🔴 批量预取回归锁：_semantic_anchor_match 开头应对
    [tail_text]+去重后的 anchor_texts 池调用**一次** prefetch_content_embeddings（而非每条
    anchor 各自触发后端·内容后端下逐条各起一次子进程暖机不可用）。"""
    calls = []

    def _record_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)), "cache_hits": 0, "computed": len(texts)}

    def _mock_embed(text):
        if "雪山" in text or "冰封的巨兽" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings", _record_prefetch)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _mock_embed)

    # 池含 1 条重复（"冰封的巨兽正在苏醒"）—— 验证 prefetch 传入的是去重后的集合
    pool = ["冰封的巨兽正在苏醒", "无关的伏笔A", "无关的伏笔B", "冰封的巨兽正在苏醒"]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "他望向远方的雪山，沉默不语，雾气弥漫。")
        r = mod.scan_chapter_end(ch, anchors={"黑刀", "祭坛"}, anchor_texts=pool)
        assert "semantic_anchor_rescue" in r  # 确认真走到了语义分支

    assert len(calls) == 1, f"应且只应调用一次 prefetch_content_embeddings，实际 {len(calls)} 次"
    got = calls[0]
    assert got[0] == "他望向远方的雪山，沉默不语，雾气弥漫。"  # tail_text 在第一位
    assert set(got[1:]) == {"冰封的巨兽正在苏醒", "无关的伏笔A", "无关的伏笔B"}
    assert len(got) == 4  # tail + 3 条去重后 anchor（原池 4 条含 1 条重复）


def test_semantic_rescue_below_floor_does_not_suppress(monkeypatch):
    """语义相似度低于 floor（无真正相关的 anchor_text）→ 不 rescue，字面判定原样保留。"""
    # 每条文本编码各不相同且互相正交 → 余弦相似度恒 0，永远低于 floor
    _dim = {"雪山章末": [1.0, 0.0, 0.0], "无关的伏笔": [0.0, 1.0, 0.0]}

    def _mock_embed(text):
        return _dim.get(text, [0.0, 0.0, 1.0])

    monkeypatch.setattr(embedding_store, "content_backend_available", lambda: True)
    monkeypatch.setattr(embedding_store, "compute_content_embedding", _mock_embed)
    monkeypatch.setattr(embedding_store, "compute_content_embeddings_batch",
                         lambda texts: [_mock_embed(t) for t in texts])
    monkeypatch.setattr(embedding_store, "prefetch_content_embeddings",
                         lambda texts: {"total": len(texts), "unique": 0, "cache_hits": 0,
                                        "computed": 0, "available": True})

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        ch = _write_chapter(tmp, "雪山章末")
        r = mod.scan_chapter_end(ch, anchors={"黑刀"}, anchor_texts=["无关的伏笔"])
        codes = {i["code"] for i in r["issues"]}
        assert "CHAPTER_END_NO_ANCHOR" in codes
        assert "semantic_anchor_rescue" not in r
