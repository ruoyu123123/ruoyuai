# -*- coding: utf-8 -*-
"""A3+A14 回归锁（2026-07-07·二轮移植 A 批）。

A3 前块结尾偏重注入 + 开头回响契约（PlotPilot recent_chapter_context「章末完整保留提升连贯」）：
  · build_manifest._collect_prev_cluster_tail：cluster_002+ 读上一 cluster 草稿末尾 N 字原文
    （默认 800 CJK·env PREV_TAIL_CJK 可调）→ manifest["prev_cluster_tail"]（T1 创作载荷）。
    cluster_001 / 前块草稿缺失 → 键不注入（零变化）。前块由 cluster_lookup 权威反查
    （① 章区间 roundtrip ② 事件簇列表序兜底·禁机械拼接）。
  · gen_writer._build_prev_tail_echo_section：manifest 有 prev_cluster_tail 时注入回响指令
    （advisory·不必逐句衔接）+ 结尾原文引文（compressed 截断时回未压缩 manifest 取全量）。

A14 近期活跃实体 LRU 兜底（Ex3 Entity_info Recent_Visit）：
  · build_manifest._collect_recently_active_entities：故事块摘要最近 3 个历史 cluster 确定性
    抽取出场实体简表（名字+最后出场 cluster+一句话状态·零 LLM），与 storyboard 白名单去重，
    上限 15 行 → manifest["recently_active_entities"]（T2 状态库）。无数据 → 键不注入。
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import gen_writer as gw  # noqa: E402
import manifest_budget as mb  # noqa: E402


# ============ 夹具 ============

def _para(i: int) -> str:
    return f"段{i:02d}" + "钟楼的雾又漫上来了他沿着湿冷的石阶往上走" * 3


def _draft_text(n_paras: int = 30) -> str:
    return "\n\n".join(_para(i) for i in range(1, n_paras + 1))


def _mk_project(tmp_path, *, with_range=True, prev_draft=None):
    """最小项目骨架：事件簇（cluster_001/002）+ 可选上一 cluster 草稿。"""
    root = tmp_path / "书"
    db = root / "_数据库"
    db.mkdir(parents=True)
    c1 = {"cluster_id": "cluster_001", "scene_storyboard": []}
    c2 = {"cluster_id": "cluster_002",
          "scene_storyboard": [{"ch": 4, "characters": ["林昭"]}]}
    if with_range:
        c1["chapter_range"] = [1, 3]
        c2["chapter_range"] = [4, 6]
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": [c1, c2]}, ensure_ascii=False), encoding="utf-8")
    if prev_draft is not None:
        d = root / "章节" / "cluster_001_draft"
        d.mkdir(parents=True)
        (d / "cluster_001_draft.txt").write_text(prev_draft, encoding="utf-8")
    return root


def _mk_summary_project(tmp_path, summary_doc, char_cards=None, cur_storyboard_chars=None):
    root = tmp_path / "书A14"
    db = root / "_数据库"
    db.mkdir(parents=True)
    (db / "故事块摘要.json").write_text(
        json.dumps(summary_doc, ensure_ascii=False), encoding="utf-8")
    if char_cards is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": char_cards}, ensure_ascii=False), encoding="utf-8")
    clusters = [{"cluster_id": "cluster_004",
                 "scene_storyboard": [{"ch": 10, "characters": cur_storyboard_chars or []}]}]
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return root


# ============ A3：prev_cluster_tail 注入 ============

def test_prev_tail_injected_for_cluster_002(tmp_path):
    """正例：cluster_002 注入 cluster_001 草稿末尾（默认 800 CJK·整段对齐·原文精确后缀）。"""
    text = _draft_text()
    root = _mk_project(tmp_path, prev_draft=text)
    s = bm.DatabaseScanner(root, 4)
    out = bm._collect_prev_cluster_tail(s, "cluster_002")
    assert isinstance(out, dict)
    assert out["source_cluster"] == "cluster_001"
    assert out["tail_cjk"] >= 800
    assert text.endswith(out["tail_text"])          # 末尾原文·零改写
    assert out["tail_text"].startswith("段")        # 对齐到段首（不半句起头）
    assert out["tail_text"] != text                 # 只取末尾非全文


def test_prev_tail_not_injected_for_cluster_001(tmp_path):
    """cluster_001 无前块 → None（键不注入）。"""
    root = _mk_project(tmp_path, prev_draft=_draft_text())
    s = bm.DatabaseScanner(root, 1)
    assert bm._collect_prev_cluster_tail(s, "cluster_001") is None


def test_prev_tail_not_injected_when_draft_missing(tmp_path):
    """前块草稿不存在（断点恢复异态）→ None（键不注入·零变化）。"""
    root = _mk_project(tmp_path, prev_draft=None)
    s = bm.DatabaseScanner(root, 4)
    assert bm._collect_prev_cluster_tail(s, "cluster_002") is None


def test_prev_tail_not_injected_when_draft_blank(tmp_path):
    """前块草稿为空白 → None。"""
    root = _mk_project(tmp_path, prev_draft="  \n\n  ")
    s = bm.DatabaseScanner(root, 4)
    assert bm._collect_prev_cluster_tail(s, "cluster_002") is None


def test_prev_tail_env_adjusts_length(tmp_path, monkeypatch):
    """env PREV_TAIL_CJK 可调末尾长度（100 → 远短于默认 800）。"""
    root = _mk_project(tmp_path, prev_draft=_draft_text())
    s = bm.DatabaseScanner(root, 4)
    monkeypatch.setenv("PREV_TAIL_CJK", "100")
    out = bm._collect_prev_cluster_tail(s, "cluster_002")
    assert out is not None
    assert 100 <= out["tail_cjk"] <= 300   # 段首对齐只多不少·远短于默认
    monkeypatch.setenv("PREV_TAIL_CJK", "not_a_number")
    out2 = bm._collect_prev_cluster_tail(s, "cluster_002")
    assert out2 is not None and out2["tail_cjk"] >= 800  # 非法值回默认


def test_prev_tail_short_draft_returns_full(tmp_path):
    """全文不足 target → 全量返回（不造假截断）。"""
    text = _draft_text(3)  # ~180 CJK < 800
    root = _mk_project(tmp_path, prev_draft=text)
    s = bm.DatabaseScanner(root, 4)
    out = bm._collect_prev_cluster_tail(s, "cluster_002")
    assert out is not None and out["tail_text"] == text.strip()


def test_prev_cluster_resolution_fallback_event_list_order(tmp_path):
    """fluid 常态：事件簇未回填 chapter_range → range 反查空 → 列表序兜底反查前块。"""
    root = _mk_project(tmp_path, with_range=False, prev_draft=_draft_text())
    s = bm.DatabaseScanner(root, 4)
    assert bm._resolve_prev_cluster_id(s, "cluster_002") == "cluster_001"
    out = bm._collect_prev_cluster_tail(s, "cluster_002")
    assert out is not None and out["source_cluster"] == "cluster_001"


# ============ A3：gen_writer 回响指令 ============

def test_echo_section_present_with_prev_tail():
    """manifest 有 prev_cluster_tail → 回响指令段非空（advisory 措辞 + 引文 + 键名指引）。"""
    pre = {"prev_cluster_tail": {"source_cluster": "cluster_003",
                                 "tail_text": "他推开门，风把灯吹灭了。"}}
    sec = gw._build_prev_tail_echo_section(Path("Z:/不存在/ch_004.json"), pre)
    assert "回响" in sec
    assert "prev_cluster_tail" in sec
    assert "cluster_003" in sec
    assert "他推开门，风把灯吹灭了。" in sec
    assert "advisory" in sec           # 北极星⑤：advisory 措辞·不硬锁


def test_echo_section_absent_without_prev_tail():
    """键缺席（cluster_001 常态）/ tail 空 → ""（不注入·零回归）。"""
    assert gw._build_prev_tail_echo_section(Path("Z:/不存在.json"), {}) == ""
    assert gw._build_prev_tail_echo_section(
        Path("Z:/不存在.json"), {"prev_cluster_tail": {"tail_text": "  "}}) == ""
    assert gw._build_prev_tail_echo_section(
        Path("Z:/不存在.json"), {"prev_cluster_tail": "bad"}) == ""


def test_echo_section_recovers_full_tail_from_uncompressed(tmp_path):
    """compressed manifest 盲切标记 → 回未压缩 manifest 取全量（A3=章末完整保留·截断即失效）。"""
    full_tail = "雾还没散。" * 60
    mp = tmp_path / "ch_004.json"
    mp.write_text(json.dumps({"prev_cluster_tail": {
        "source_cluster": "cluster_001", "tail_text": full_tail}}, ensure_ascii=False),
        encoding="utf-8")
    truncated = full_tail[:200] + "...(+100 chars)"
    pre = {"prev_cluster_tail": {"source_cluster": "cluster_001", "tail_text": truncated}}
    sec = gw._build_prev_tail_echo_section(mp, pre)
    assert full_tail in sec
    assert "...(+100 chars)" not in sec


def test_echo_section_wired_into_build_prompt():
    """接线锁：回响段挂进 build_prompt 两个 join 分支（reorder active + off/shadow 回退）。"""
    src = (_ROOT / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")
    assert "_build_prev_tail_echo_section(manifest_path, _manifest_dict)" in src
    assert src.count("{prev_tail_echo_block}") == 2


# ============ A14：recently_active_entities ============

def test_recent_entities_lru_extraction_and_whitelist_dedup(tmp_path):
    """LRU 抽取正确性：最后出场 cluster/状态正确·白名单（active_chars+storyboard·含 id 归一）去重。"""
    doc = {"clusters": [
        {"cluster_id": "cluster_001", "characters": ["char_linzhao", "陈默"],
         "summary": "陈默在钟楼底层发现血字，林昭赶到。"},
        {"cluster_id": "cluster_002", "characters": ["陈默", "白鹤"],
         "summary": "白鹤带陈默进入档案馆，交出旧照片。"},
        {"cluster_id": "cluster_003", "characters": ["白鹤"],
         "summary": "白鹤独自烧掉照片。"},
    ]}
    cards = [{"id": "char_linzhao", "name": "林昭"}]
    root = _mk_summary_project(tmp_path, doc, char_cards=cards)
    s = bm.DatabaseScanner(root, 10)
    out = bm._collect_recently_active_entities(s, ["林昭"], "cluster_004")
    assert isinstance(out, dict) and out["entities"]
    by_name = {e["name"]: e for e in out["entities"]}
    assert "林昭" not in by_name                       # active_chars 白名单去重（id 归一后）
    assert by_name["陈默"]["last_seen_cluster"] == "cluster_002"   # LRU：最后出场为准
    assert "档案馆" in by_name["陈默"]["status_hint"]              # 一句话状态=最后出场章摘要
    assert by_name["白鹤"]["last_seen_cluster"] == "cluster_003"
    assert out["entities"][0]["name"] == "白鹤"        # 最近出场优先排前


def test_recent_entities_storyboard_whitelist_dedup(tmp_path):
    """当前 cluster storyboard 出场角色（已注入全卡）不重复列。"""
    doc = {"clusters": [
        {"cluster_id": "cluster_003", "characters": ["白鹤", "沈栎"],
         "summary": "白鹤和沈栎分头行动。"},
    ]}
    root = _mk_summary_project(tmp_path, doc, cur_storyboard_chars=["白鹤"])
    s = bm.DatabaseScanner(root, 10)
    out = bm._collect_recently_active_entities(s, [], "cluster_004")
    names = [e["name"] for e in out["entities"]]
    assert "白鹤" not in names and "沈栎" in names


def test_recent_entities_lookback_window(tmp_path):
    """只看最近 3 个历史 cluster：更早 cluster 独有实体不进简表·当前/未来 cluster 条目不读。"""
    doc = {"clusters": [
        {"cluster_id": "cluster_001", "characters": ["远古人"], "summary": "远古人只在第一块出现。"},
        {"cluster_id": "cluster_002", "characters": ["甲"], "summary": "甲出场。"},
        {"cluster_id": "cluster_003", "characters": ["乙"], "summary": "乙出场。"},
        {"cluster_id": "cluster_004", "characters": ["丙"], "summary": "丙出场。"},
        {"cluster_id": "cluster_005", "characters": ["未来人"], "summary": "当前块之后的条目不该被读。"},
    ]}
    root = _mk_summary_project(tmp_path, doc)
    s = bm.DatabaseScanner(root, 13)
    out = bm._collect_recently_active_entities(s, [], "cluster_005")
    names = {e["name"] for e in out["entities"]}
    assert names == {"甲", "乙", "丙"}   # lookback=3 · 远古人出窗 · 未来人 ≥ 当前块被滤


def test_recent_entities_cap_15(tmp_path):
    """上限 15 行防膨胀。"""
    chars = [f"配角{i:02d}" for i in range(20)]
    doc = {"clusters": [{"cluster_id": "cluster_003", "characters": chars,
                          "summary": "群像大场面。"}]}
    root = _mk_summary_project(tmp_path, doc)
    s = bm.DatabaseScanner(root, 10)
    out = bm._collect_recently_active_entities(s, [], "cluster_004")
    assert len(out["entities"]) == 15


def test_recent_entities_none_when_no_data(tmp_path):
    """无账本 / 空 clusters / 全被白名单去重 → None（键不注入·零变化）。"""
    root = _mk_summary_project(tmp_path, {"clusters": []})
    s = bm.DatabaseScanner(root, 10)
    assert bm._collect_recently_active_entities(s, [], "cluster_004") is None
    root2 = _mk_summary_project(tmp_path.joinpath("b"), {"clusters": [
        {"cluster_id": "cluster_003", "characters": ["白鹤"], "summary": "白鹤。"}]})
    s2 = bm.DatabaseScanner(root2, 10)
    assert bm._collect_recently_active_entities(s2, ["白鹤"], "cluster_004") is None


def test_recent_entities_char_mention_counts_fallback(tmp_path):
    """章记录缺 characters 时回退 char_mention_counts 键（账本已有字段·确定性）。"""
    doc = {"clusters": [{"cluster_id": "cluster_003",
                          "char_mention_counts": {"哑巴更夫": 6},
                          "summary": "更夫敲了六下梆子。"}]}
    root = _mk_summary_project(tmp_path, doc)
    s = bm.DatabaseScanner(root, 10)
    out = bm._collect_recently_active_entities(s, [], "cluster_004")
    assert out["entities"][0]["name"] == "哑巴更夫"
    assert "梆子" in out["entities"][0]["status_hint"]


# ============ S1 预算契约 + 接线锁 ============

def test_new_sections_registered_in_budget_tiers():
    """S1 预算契约：新顶层段必须归 tier——prev_cluster_tail=T1 创作载荷·
    recently_active_entities=T2 状态库（漏归 → test_manifest_budget 覆盖测试测红）。"""
    assert mb.SECTION_TIERS.get("prev_cluster_tail") == mb.TIER_T1
    assert mb.SECTION_TIERS.get("recently_active_entities") == mb.TIER_T2


def test_build_manifest_wires_collectors():
    """接线锁：build_manifest 源码把两个 collector 挂进 manifest 组装（防孤儿函数·条件注入）。"""
    src = (_ROOT / "core" / "scripts" / "build_manifest.py").read_text(encoding="utf-8")
    assert 'manifest["prev_cluster_tail"] = _prev_tail' in src
    assert 'manifest["recently_active_entities"] = _recent_ents' in src
    assert "_collect_prev_cluster_tail(s, current_cluster_id)" in src
    assert "_collect_recently_active_entities(s, active_chars, current_cluster_id)" in src
