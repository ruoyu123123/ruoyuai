#!/usr/bin/env python3
"""gen_chapter_titles.py 专属回归测试（零依赖 · 绝不出网）。

被测脚本 core/scripts/gen_chapter_titles.py 是章标题三段式的两个确定性端：
  --emit-brief（splitter WAL → novel-titler 任务合同）
  --apply（fake agent 产物 titles.json → 确定性验收/章头重写/blueprint 回填/receipt）
中段的标题创作由 novel-titler agent（Claude 亲笔）完成，不在本测试范围
（北极星⑤：只测确定性验收层，不断言创作质量）。

测试策略：
- 纯 helper：parse_chapters / parse_high_list / strip_existing_title / classify_tier /
  read_chapter / read_changes / _load_title_style / _is_clean_title /
  _clean_fallback_title / _title_conflict —— 直接真调真断言。
- emit-brief：构造最小项目（splitter WAL + 章正文 + 进度.json blueprint + 历史章）→
  断言 brief 契约字段（章集合/档位/hint/正文路径/历史标题全集/fallback_title）。
- apply：手写 fake titles.json（模拟 novel-titler 产物）→ 断言验收接受/拒绝、
  「第NNN章 标题」头重写、blueprint 回填、pending 退回轮回、receipt、幂等重跑。
- 回归锁：模块源码零 LLM 调用（openai / GenModelLoader 不得复活）。

零依赖约定：只用标准库 · test_* 无参 · 断言失败 raise AssertionError。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_chapter_titles as mod  # noqa: E402


# ============================================================================
# 项目脚手架（splitter WAL + 章正文 + 进度.json + 可选历史章）
# ============================================================================
def _tmp():
    return Path(tempfile.mkdtemp())


def _write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_chapter(project: Path, ch: int, body: str = None, titled: str = None):
    d = project / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    text = body if body is not None else f"ch{ch} 的正文。\n他把杯子摔在地上。"
    if titled:
        text = f"第{ch:03d}章 {titled}\n\n{text}"
    (d / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")


def _mk_project(chapter_range=(1, 3), pending_tail=False, history=None,
                hints=None) -> Path:
    """最小可 emit-brief 项目。history={章号: 标题} 落成带头的历史章。"""
    proj = _tmp()
    key = "001"
    chs = list(range(chapter_range[0], chapter_range[1] + 1)) if chapter_range else []
    for ch in chs:
        _write_chapter(proj, ch)
    for ch, title in (history or {}).items():
        _write_chapter(proj, ch, titled=title)
    wal = {
        "schema_version": "1.0",
        "cluster_id": "cluster_001",
        "chapter_range": list(chapter_range) if chapter_range else [],
        "pending_tail": {"exists": bool(pending_tail), "cjk": 0, "path": None},
    }
    _write_json(proj / "_数据库" / ".wal" / f"splitter_cluster_{key}_decisions.json", wal)
    storyboard = [{"ch": ch, "title": (hints or {}).get(ch, "")} for ch in chs]
    _write_json(proj / "_数据库" / "进度.json",
                {"cluster_blueprint": {"cluster_001": {"scene_storyboard": storyboard}}})
    return proj


def _brief_path(proj: Path) -> Path:
    return proj / "_数据库" / ".wal" / "cluster_001_title_brief.json"


def _titles_path(proj: Path) -> Path:
    return proj / "_数据库" / ".wal" / "cluster_001_titles.json"


def _receipt_path(proj: Path) -> Path:
    return proj / "_数据库" / ".wal" / "cluster_001_title_apply_receipt.json"


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _fake_titles(proj: Path, titles: dict, **overrides):
    """模拟 novel-titler agent 产物落盘（OUTPUT schema 见 novel-titler.md）。"""
    payload = {
        "schema_version": "novel-titler.v1",
        "cluster_id": "cluster_001",
        "titles": {str(k): v for k, v in titles.items()},
        "agent": "novel-titler",
        "plan_id": "plan-test-001",
        "step": 6,
    }
    payload.update(overrides)
    _write_json(_titles_path(proj), payload)


# ============================================================================
# 1. 纯 helper：parse_chapters / parse_high_list
# ============================================================================
def test_parse_chapters_range_and_list():
    assert mod.parse_chapters("1-4") == [1, 2, 3, 4]
    assert mod.parse_chapters("5,6,7") == [5, 6, 7]
    assert mod.parse_chapters("1-3, 10 , 12") == [1, 2, 3, 10, 12]


def test_parse_high_list_empty_and_set():
    assert mod.parse_high_list("") == set()
    assert mod.parse_high_list(None) == set()
    assert mod.parse_high_list("11,40") == {11, 40}
    assert mod.parse_high_list("5-7") == {5, 6, 7}


# ============================================================================
# 2. 纯 helper：classify_tier（high_set > tail > normal 优先级）
# ============================================================================
def test_classify_tier_priority():
    ch_in_high = mod.classify_tier(
        11, {"ecas_metadata": {"cluster_position": "tail"}}, {11})
    assert ch_in_high == "high"
    tail = mod.classify_tier(3, {"ecas_metadata": {"cluster_position": "tail"}}, set())
    assert tail == "mid"
    assert mod.classify_tier(2, {}, set()) == "normal"
    assert mod.classify_tier(2, {"ecas_metadata": {"cluster_position": "body"}}, set()) == "normal"


# ============================================================================
# 3. 纯 helper：strip_existing_title / read_chapter / read_changes
# ============================================================================
def test_strip_existing_title():
    with_title = "第003章 断牙\n\n正文第一段。\n再来一段。"
    stripped = mod.strip_existing_title(with_title)
    assert stripped.startswith("正文第一段")
    assert "第003章" not in stripped
    plain = "他把杯子摔在地上。\n碎了。"
    assert mod.strip_existing_title(plain) == plain


def test_read_chapter_and_changes():
    proj = _tmp()
    chdir = proj / "章节" / "第005章"
    chdir.mkdir(parents=True)
    (chdir / "第005章.txt").write_text("正文内容", encoding="utf-8")
    (chdir / "第005章_changes.json").write_text(
        json.dumps({"ecas_metadata": {"cluster_position": "tail"}}, ensure_ascii=False),
        encoding="utf-8")

    p, body = mod.read_chapter(proj, 5)
    assert body == "正文内容"
    assert p.name == "第005章.txt"
    _, missing = mod.read_chapter(proj, 99)
    assert missing == ""

    changes = mod.read_changes(proj, 5)
    assert changes["ecas_metadata"]["cluster_position"] == "tail"
    assert mod.read_changes(proj, 99) == {}


def test_read_changes_corrupt_json_returns_empty():
    proj = _tmp()
    chdir = proj / "章节" / "第006章"
    chdir.mkdir(parents=True)
    (chdir / "第006章_changes.json").write_text("{ 这不是合法 json", encoding="utf-8")
    assert mod.read_changes(proj, 6) == {}


# ============================================================================
# 4. 纯 helper：_load_title_style
# ============================================================================
def test_load_title_style_no_style_file():
    proj = _tmp()
    (proj / "_数据库").mkdir(parents=True)
    assert mod._load_title_style(proj) is None
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"meta": {}}, ensure_ascii=False), encoding="utf-8")
    assert mod._load_title_style(proj) is None


def test_load_title_style_resolves_from_workspace():
    proj = _tmp()
    (proj / "_数据库").mkdir(parents=True)
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"meta": {"work": "测试书"}}, ensure_ascii=False), encoding="utf-8")
    style_dir = proj / "workspace" / "styles" / "测试书"
    style_dir.mkdir(parents=True)
    payload = {"tier_distribution_pct": {"normal": 0.7, "mid": 0.2, "high": 0.1}}
    (style_dir / "title_style.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded = mod._load_title_style(proj)
    assert loaded is not None
    assert loaded["tier_distribution_pct"]["normal"] == 0.7


# ============================================================================
# 5. 纯 helper：_is_clean_title / _clean_fallback_title / _title_conflict
# ============================================================================
def test_is_clean_title_rejects_directive_and_accepts_clean():
    assert mod._is_clean_title("孤峰")
    assert mod._is_clean_title("命运母题")
    assert mod._is_clean_title("「断牙」")  # 包裹符剥后干净
    dirty = "倒叙强冲突开场。铁十字街一间逼仄昏暗的出租屋里，主角顶着占卜"
    assert not mod._is_clean_title(dirty), "含句号+主角占位的 storyboard 指令不该判干净"
    assert not mod._is_clean_title("主角登场")
    assert not mod._is_clean_title("他来了，她走了")
    assert not mod._is_clean_title("一二三四五六七八九十甲乙丙丁戊")
    assert not mod._is_clean_title("")


def test_clean_fallback_title_rejects_storyboard_hint():
    dirty = "倒叙强冲突开场。铁十字街一间逼仄昏暗的出租屋里，主角顶着占卜"
    assert mod._clean_fallback_title(1, dirty) == "第1章"
    assert mod._clean_fallback_title(3, "孤峰") == "孤峰"
    assert mod._clean_fallback_title(3, "「断牙」") == "断牙"
    assert mod._clean_fallback_title(7, "") == "第7章"


def test_title_conflict_exact_and_containment():
    # 精确相等
    assert mod._title_conflict("断牙", ["断牙", "孤峰"]) == "断牙"
    # 母题包含（「葬」用过就不许「葬礼」·双向）
    assert mod._title_conflict("葬礼", ["葬"]) == "葬"
    assert mod._title_conflict("葬", ["葬礼"]) == "葬礼"
    # 无冲突
    assert mod._title_conflict("夜雾", ["断牙", "孤峰"]) is None
    # 「第7章」不是「第17章」的连续子串 → 不冲突
    assert mod._title_conflict("第17章", ["第7章"]) is None


# ============================================================================
# 6. --emit-brief：契约产出
# ============================================================================
def test_emit_brief_produces_contract():
    proj = _mk_project(chapter_range=(1, 3), hints={1: "孤峰", 2: "倒叙强冲突开场。主角登场"})
    rc = mod.emit_brief(proj, "cluster_001")
    assert rc == 0, f"emit_brief 应成功，rc={rc}"
    brief = _read_json(_brief_path(proj))
    assert brief["schema_version"] == "title_brief.v1"
    assert brief["cluster_id"] == "cluster_001"
    assert brief["agent"] == "novel-titler"
    assert brief["no_op"] is False
    assert brief["accepted"] == {}
    assert brief["output_path"].endswith("cluster_001_titles.json")
    chs = brief["chapters"]
    assert [e["ch"] for e in chs] == [1, 2, 3]
    # 每章条目契约：档位/hint/正文路径/fallback_title
    e1 = chs[0]
    assert e1["tier"] == "normal"
    assert e1["hint"] == "孤峰"
    assert e1["body_path"] == "章节/第001章/第001章.txt"
    assert e1["fallback_title"] == "孤峰"  # 干净 hint 直接当兜底候选
    # 脏 hint 的兜底候选退「第N章」
    assert chs[1]["fallback_title"] == "第2章"
    # cluster 标识归一：裸数字 / 无零填充也接受
    rc2 = mod.emit_brief(proj, "1")
    assert rc2 == 0


def test_emit_brief_tier_rules():
    # 无 pending_tail：WAL 末章 = cluster 收尾章 → mid
    proj = _mk_project(chapter_range=(1, 3))
    assert mod.emit_brief(proj, "001", high_chapters="2") == 0
    tiers = {e["ch"]: e["tier"] for e in _read_json(_brief_path(proj))["chapters"]}
    assert tiers == {1: "normal", 2: "high", 3: "mid"}, tiers
    # 有 pending_tail：实切末章不是收尾 → 不升 mid
    proj2 = _mk_project(chapter_range=(1, 3), pending_tail=True)
    assert mod.emit_brief(proj2, "001") == 0
    tiers2 = {e["ch"]: e["tier"] for e in _read_json(_brief_path(proj2))["chapters"]}
    assert tiers2 == {1: "normal", 2: "normal", 3: "normal"}, tiers2
    # high 显式指定优先于末章 mid
    proj3 = _mk_project(chapter_range=(1, 3))
    assert mod.emit_brief(proj3, "001", high_chapters="3") == 0
    tiers3 = {e["ch"]: e["tier"] for e in _read_json(_brief_path(proj3))["chapters"]}
    assert tiers3[3] == "high"


def test_emit_brief_history_collection_excludes_own_cluster():
    # 历史章 ch90「断牙」进 history；本 cluster ch1 已带头「旧名」被排除（重命名不卡自己）
    proj = _mk_project(chapter_range=(1, 3), history={90: "断牙"})
    _write_chapter(proj, 1, titled="旧名")
    assert mod.emit_brief(proj, "001") == 0
    brief = _read_json(_brief_path(proj))
    assert brief["history_titles"] == ["断牙"], brief["history_titles"]


def test_emit_brief_zero_chapters_noop():
    # splitter 整稿退 pending_tail（chapter_range=[]）→ no-op brief · exit 0
    proj = _mk_project(chapter_range=None, pending_tail=True)
    rc = mod.emit_brief(proj, "001")
    assert rc == 0
    brief = _read_json(_brief_path(proj))
    assert brief["no_op"] is True
    assert brief["chapters"] == []


def test_emit_brief_missing_wal_fatal():
    proj = _tmp()
    assert mod.emit_brief(proj, "001") == 3


def test_emit_brief_missing_body_fatal():
    proj = _mk_project(chapter_range=(1, 3))
    (proj / "章节" / "第002章" / "第002章.txt").unlink()
    assert mod.emit_brief(proj, "001") == 3


def test_emit_brief_invalid_cluster_fatal():
    proj = _mk_project()
    assert mod.emit_brief(proj, "not-a-cluster") == 3


# ============================================================================
# 7. --apply：验收接受路径（fake agent 产物 → 章头重写 + 回填 + receipt）
# ============================================================================
def test_apply_accepts_clean_titles_end_to_end():
    proj = _mk_project(chapter_range=(1, 3), hints={1: "旧提示"})
    assert mod.emit_brief(proj, "001", high_chapters="2") == 0
    _fake_titles(proj, {1: "断牙", 2: "徇射穿了第二个太阳", 3: "凿齿夜袭"})
    rc = mod.apply_titles(proj, "001")
    assert rc == 0, f"全干净标题应验收通过，rc={rc}"
    # 章头重写
    body1 = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert body1.startswith("第001章 断牙\n\n"), body1[:30]
    # blueprint 回填（title 更新 + _old_title 保留）
    bp = _read_json(proj / "_数据库" / "进度.json")["cluster_blueprint"]
    scenes = {p["ch"]: p for p in bp["cluster_001"]["scene_storyboard"]}
    assert scenes[1]["title"] == "断牙"
    assert scenes[1]["_old_title"] == "旧提示"
    # receipt
    receipt = _read_json(_receipt_path(proj))
    assert receipt["schema_version"] == "gen_chapter_titles.receipt.v1"
    assert receipt["agent"] == "novel-titler"
    assert receipt["completed"] is True
    assert receipt["no_op"] is False
    assert receipt["titles"] == {"1": "断牙", "2": "徇射穿了第二个太阳", "3": "凿齿夜袭"}
    assert receipt["tier_counts"] == {"normal": 1, "mid": 1, "high": 1}
    assert receipt["plan_id"] == "plan-test-001"
    # brief 终态：pending 清空 + accepted 齐全
    brief = _read_json(_brief_path(proj))
    assert brief["chapters"] == []
    assert set(brief["accepted"]) == {"1", "2", "3"}


def test_apply_strips_wrappers_before_write():
    # agent 给标题带引号包裹 → 剥后落盘
    proj = _mk_project(chapter_range=(1, 1), pending_tail=True)
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "「断牙」"})
    assert mod.apply_titles(proj, "001") == 0
    body = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert body.startswith("第001章 断牙\n\n")


def test_apply_missing_titles_json_pending():
    proj = _mk_project(chapter_range=(1, 3))
    assert mod.emit_brief(proj, "001") == 0
    rc = mod.apply_titles(proj, "001")
    assert rc == 2, f"缺 titles.json 应 exit 2=pending_titles，rc={rc}"
    assert not _receipt_path(proj).exists()


# ============================================================================
# 8. --apply：验收拒绝路径（退回 pending 轮回）
# ============================================================================
def test_apply_rejects_history_duplicate_and_keeps_clean_ones():
    proj = _mk_project(chapter_range=(1, 3), history={90: "断牙"})
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "断牙", 2: "孤峰", 3: "凿齿夜袭"})
    rc = mod.apply_titles(proj, "001")
    assert rc == 2, "与历史重复的章应退回 pending"
    # ch1 未写头（拒绝），ch2/ch3 已写头（增量落地）
    body1 = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert not body1.startswith("第001章"), "被拒章不得写头"
    body2 = (proj / "章节" / "第002章" / "第002章.txt").read_text(encoding="utf-8")
    assert body2.startswith("第002章 孤峰\n\n")
    # brief：ch1 留 pending 带 rejected 原因；accepted 收 ch2/ch3
    brief = _read_json(_brief_path(proj))
    assert [e["ch"] for e in brief["chapters"]] == [1]
    rej = brief["chapters"][0]["rejected"]
    assert rej["last_title"] == "断牙"
    assert "断牙" in rej["reason"]
    assert set(brief["accepted"]) == {"2", "3"}
    assert not _receipt_path(proj).exists(), "有退回章时不得写完成 receipt"


def test_apply_rejects_dirty_and_overlong_titles():
    proj = _mk_project(chapter_range=(1, 3))
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {
        1: "倒叙强冲突开场。主角顶着占卜",   # storyboard 指令文本（标点+占位词）
        2: "一二三四五六七八九十甲乙丙丁戊己庚",  # 17 字超长
        3: "凿齿夜袭",
    })
    rc = mod.apply_titles(proj, "001")
    assert rc == 2
    brief = _read_json(_brief_path(proj))
    rejected = {e["ch"]: e["rejected"]["reason"] for e in brief["chapters"]}
    assert set(rejected) == {1, 2}
    assert "标点" in rejected[1] or "指令" in rejected[1]
    assert "超长" in rejected[2]
    assert set(brief["accepted"]) == {"3"}


def test_apply_rejects_batch_internal_duplicate():
    # 本批内互查：ch1/ch2 同名 → 章号小者先到先得，后者退回
    proj = _mk_project(chapter_range=(1, 2), pending_tail=True)
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "断牙", 2: "断牙"})
    rc = mod.apply_titles(proj, "001")
    assert rc == 2
    brief = _read_json(_brief_path(proj))
    assert set(brief["accepted"]) == {"1"}
    assert [e["ch"] for e in brief["chapters"]] == [2]


def test_apply_schema_and_key_mismatch_pending_without_side_effects():
    proj = _mk_project(chapter_range=(1, 3))
    assert mod.emit_brief(proj, "001") == 0
    # 键集合缺 ch3 → 契约不符 → exit 2 且零副作用（schema 挡在验收前）
    _fake_titles(proj, {1: "断牙", 2: "孤峰"})
    assert mod.apply_titles(proj, "001") == 2
    body1 = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert not body1.startswith("第001章"), "契约不符时不得写任何章头"
    # schema_version 错 → exit 2
    _fake_titles(proj, {1: "断牙", 2: "孤峰", 3: "凿齿夜袭"},
                 schema_version="wrong.v0")
    assert mod.apply_titles(proj, "001") == 2
    # cluster_id 错 → exit 2
    _fake_titles(proj, {1: "断牙", 2: "孤峰", 3: "凿齿夜袭"},
                 cluster_id="cluster_999")
    assert mod.apply_titles(proj, "001") == 2
    # agent 错 → exit 2
    _fake_titles(proj, {1: "断牙", 2: "孤峰", 3: "凿齿夜袭"}, agent="someone-else")
    assert mod.apply_titles(proj, "001") == 2


def test_apply_second_round_completes_after_rename():
    """pending 轮回闭环：第一轮 ch1 重复被退 → agent 重命名 → 第二轮全过 receipt 落盘。"""
    proj = _mk_project(chapter_range=(1, 3), history={90: "断牙"})
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "断牙", 2: "孤峰", 3: "凿齿夜袭"})
    assert mod.apply_titles(proj, "001") == 2
    # 第二轮：agent 只对 pending 章（ch1）重产（键集合=当前 brief 待命名集合）
    _fake_titles(proj, {1: "夜雾"})
    rc = mod.apply_titles(proj, "001")
    assert rc == 0, f"重命名后应全过，rc={rc}"
    receipt = _read_json(_receipt_path(proj))
    assert receipt["titles"] == {"1": "夜雾", "2": "孤峰", "3": "凿齿夜袭"}
    body1 = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert body1.startswith("第001章 夜雾\n\n")


def test_apply_second_round_rejects_same_title_again():
    # 重 spawn 后 agent 给了同一个坏标题 → 幂等再拒（不静默放行）
    proj = _mk_project(chapter_range=(1, 1), history={90: "断牙"}, pending_tail=True)
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "断牙"})
    assert mod.apply_titles(proj, "001") == 2
    assert mod.apply_titles(proj, "001") == 2, "同一坏产物重跑必须仍拒"


# ============================================================================
# 9. --apply：no-op 与幂等
# ============================================================================
def test_apply_noop_receipt_for_zero_chapters():
    proj = _mk_project(chapter_range=None, pending_tail=True)
    assert mod.emit_brief(proj, "001") == 0
    rc = mod.apply_titles(proj, "001")
    assert rc == 0
    receipt = _read_json(_receipt_path(proj))
    assert receipt["no_op"] is True
    assert receipt["completed"] is True
    assert receipt["titles"] == {}


def test_apply_idempotent_rerun_after_complete():
    proj = _mk_project(chapter_range=(1, 2), pending_tail=True)
    assert mod.emit_brief(proj, "001") == 0
    _fake_titles(proj, {1: "断牙", 2: "孤峰"})
    assert mod.apply_titles(proj, "001") == 0
    _receipt_path(proj).unlink()  # receipt 丢失后重跑应补写
    rc = mod.apply_titles(proj, "001")
    assert rc == 0
    assert _read_json(_receipt_path(proj))["titles"] == {"1": "断牙", "2": "孤峰"}
    # 章头保持单份（strip_existing_title 幂等）
    body1 = (proj / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
    assert body1.count("第001章") == 1


def test_apply_missing_brief_fatal():
    proj = _tmp()
    assert mod.apply_titles(proj, "001") == 3


# ============================================================================
# 10. 🔴 回归锁：LLM 生成路径不得复活（标题创作只属 novel-titler agent）
# ============================================================================
def test_no_llm_call_path_in_module():
    src = (_ROOT / "core" / "scripts" / "gen_chapter_titles.py").read_text(encoding="utf-8")
    for forbidden in ("import openai", "from openai", "GenModelLoader",
                      "chat.completions", "gen_one_title"):
        assert forbidden not in src, f"LLM 生成路径复活嫌疑: {forbidden!r}"
