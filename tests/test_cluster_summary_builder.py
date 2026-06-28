"""cluster_summary_builder.py 确定性单元测试（纯函数 + 端到端账本组装·零 LLM·零联网）。

钉死 cluster 账本生产者的纯逻辑：
- 防御性读 helper（_db_dir / _load_json / _first / _ids_of）
- 正文派生轻量提取（关键词指纹 / 时间过渡 / 地点命中 / 角色提及 / 结尾行）
- canonical 子系统派生（压力 stress_log 状态机 / coping 关键词 / aspect 关键词集）
- changes 派生（多别名探测 + id 展平 + applied_style 权威源）
- foreshadow / secrets 归一
- build_cluster_summary 端到端：造小项目 → 跑账本 → 断言 rollup 字段

全部用 tempfile 临时目录，绝不写真项目；只用标准库。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cluster_summary_builder as mod  # noqa: E402
import chapter_io as cio  # noqa: E402
import cluster_summary_store as store  # noqa: E402
import cluster_lookup as cl  # noqa: E402


# ============================================================
# 纯 helper：_db_dir / _load_json / _first / _ids_of
# ============================================================

def test_db_dir_appends_or_passes_through():
    """非 _数据库 目录 → 追加 _数据库；已是 _数据库 → 原样返回。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert mod._db_dir(root) == root / "_数据库"
        already = root / "_数据库"
        assert mod._db_dir(already) == already


def test_load_json_defensive():
    """存在合法 JSON → 返回内容；缺文件/损坏 → 返回 default 不崩。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        good = root / "good.json"
        good.write_text(json.dumps({"k": 1}, ensure_ascii=False), encoding="utf-8")
        assert mod._load_json(good) == {"k": 1}
        # 缺失文件
        assert mod._load_json(root / "missing.json", default={"x": 9}) == {"x": 9}
        # 损坏 JSON
        bad = root / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert mod._load_json(bad, default=None) is None


def test_first_picks_first_non_empty_alias():
    """_first 返回第一个存在且非空的 key；空值（None/""/[]/{}）跳过；非 dict → default。"""
    d = {"a": None, "b": "", "c": [], "d": "命中", "e": "备选"}
    assert mod._first(d, "a", "b", "c", "d", "e") == "命中"
    assert mod._first(d, "a", "b", "c") is None          # 全空 → default None
    assert mod._first(d, "miss", default="dft") == "dft"  # 全缺 → default
    assert mod._first("not a dict", "a", default="dft") == "dft"


def test_ids_of_flattens_mixed_list():
    """字符串原样保留；dict 按 id_keys 顺序取首个非空；空串/无匹配键的 dict 丢弃。"""
    items = ["raw_id", {"aspect_id": "asp1"}, {"id": "fallback"},
             {"other": "nope"}, "", {"aspect_id": "", "id": "asp2"}]
    out = mod._ids_of(items, "aspect_id", "id")
    assert out == ["raw_id", "asp1", "fallback", "asp2"]
    assert mod._ids_of(None, "id") == []   # None 输入 → 空


# ============================================================
# 正文派生轻量提取
# ============================================================

def test_extract_text_keywords_min_count_and_stopwords():
    """重复 ≥2 的非停用词 2gram 进指纹；过短文本/纯停用词 → 空。"""
    text = "刀光刀光刀光，剑影剑影剑影"  # 刀光 出现 3 次、剑影 3 次
    kw = mod._extract_text_keywords(text, top=4)
    assert "刀光" in kw and "剑影" in kw
    # 过短（清洗后 < 4 CJK）→ 空
    assert mod._extract_text_keywords("刀", top=4) == []
    assert mod._extract_text_keywords("", top=4) == []


def test_detect_time_transition_only_in_head():
    """时间过渡词在前 1000 字 → True；只出现在尾部 → False。"""
    assert mod._detect_time_transition("翌日清晨，他醒来。") is True
    tail = "起" * 1500 + "翌日"   # 过渡词在 1000 字之后
    assert mod._detect_time_transition(tail) is False
    assert mod._detect_time_transition("") is False


def test_locations_and_char_mentions_and_ending_line():
    """地点命中子串匹配；角色提及计数（只留 >0）；结尾行取最后非空行并截断 120 字。"""
    body = "在青云城外，李四遇见了王五。\n青云城很大。\n   \n最后一行收束。"
    assert mod._locations_mentioned(body, ["青云城", "黑风寨"]) == ["青云城"]
    counts = mod._char_mention_counts(body, ["李四", "王五", "赵六"])
    assert counts == {"李四": 1, "王五": 1}  # 赵六 未出现 → 不在 dict
    assert mod._ending_line(body) == "最后一行收束。"
    assert mod._ending_line("") == ""
    assert mod._char_mention_counts("", ["李四"]) == {}


# ============================================================
# canonical 子系统派生（state machine / 关键词抽取）
# ============================================================

def test_build_stress_index_state_machine():
    """stress_log → 三索引：常规条目记 new_total（同 ch 取最新）、break 条目记 card、trigger 描述。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d)
        (db / "主角压力档.json").write_text(json.dumps({
            "stress_log": [
                {"ch": 1, "new_total": 30, "trigger": "被追杀"},
                {"ch": 1, "new_total": 45},  # 同 ch 后写覆盖
                {"ch": 2, "trigger_type": "mental_break_triggered", "card_id": "break_A"},
                {"ch": 3, "new_total": "脏"},   # 非数字 → 跳过
                "garbage",                      # 非 dict → 跳过
            ]
        }, ensure_ascii=False), encoding="utf-8")
        s_by, b_by, t_by = mod._build_stress_index(db)
        assert s_by == {1: 45}            # 同 ch 取最新 45，ch3 脏值不入
        assert b_by == {2: "break_A"}
        assert t_by == {1: "被追杀"}
        # 缺文件 → 三空 dict 不崩
        assert mod._build_stress_index(Path(d) / "no_such") == ({}, {}, {})


def test_coping_and_aspect_keyword_extraction():
    """coping 行为抽 2-4 字关键词；aspect 从 narrative_constraints/emotional_triggers 抽词。"""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d)
        (db / "主角压力档.json").write_text(json.dumps({
            "coping_mechanisms": {"high_stress_behaviors": ["反复擦拭佩刀", "独自饮酒"]}
        }, ensure_ascii=False), encoding="utf-8")
        kws = mod._coping_keywords(db)
        assert all(len(k) >= 2 for k in kws) and kws  # 非空且都 >= 2 字

        (db / "角色烙印.json").write_text(json.dumps({
            "characters": {
                "李四": {"active_aspects": [
                    {"aspect_id": "asp_fear",
                     "narrative_constraints": ["遇火必然惊恐退缩"],
                     "emotional_triggers": ["明火炙烤的气味"]},
                    {"narrative_constraints": ["无 id 应被跳过"]},  # 缺 aspect_id → 跳
                ]}
            }
        }, ensure_ascii=False), encoding="utf-8")
        sets = mod._aspect_keyword_sets(db)
        ids = [aid for aid, _ in sets]
        assert ids == ["asp_fear"]                # 只有带 aspect_id 的入
        assert all(len(k) >= 2 for _, kws in sets for k in kws)


# ============================================================
# changes 派生 / foreshadow / secrets
# ============================================================

def test_build_changes_derived_aliases_and_id_flatten():
    """多别名探测 + aspects/clocks 展平成 id 字符串列表 + applied_style 权威 ending。"""
    factual = {
        "relationship_changes": [{"from": "A", "to": "B"}],          # 别名命中
        "aspects_addressed": [{"aspect_id": "asp1"}, "asp2"],         # dict + str 混
        "clocks_addressed": [{"clock_id": "clk1"}],
        "ending_type": "factual_fallback_type",                      # 仅当 applied 缺时兜底
    }
    self_eval = {
        "applied_style": {"ending_type": "cliffhanger", "ending_line": "他回头。"},
        "moves_used": ["威逼", "利诱"],
    }
    rec = mod._build_changes_derived(factual, self_eval)
    assert rec["relationships"] == [{"from": "A", "to": "B"}]
    assert rec["aspects_addressed"] == ["asp1", "asp2"]   # 展平
    assert rec["clocks_addressed"] == ["clk1"]
    assert rec["ending_type"] == "cliffhanger"            # applied_style 优先于 factual
    assert rec["ending_line"] == "他回头。"
    assert rec["moves_used"] == ["威逼", "利诱"]            # self_eval 权威源


def test_foreshadow_actions_and_secrets_normalization():
    """foreshadowing_actions dict 形态 → (planted,paid,reinforced)；secrets 归一字符串。"""
    factual = {
        "foreshadowing_actions": {
            "planted": ["fs1", {"id": "fs2"}, {"description": "无id用描述"}],
            "paid": [{"fid": "fs0"}],
            "reinforced": ["fs3"],
        },
        "secrets_revealed": ["sec1", {"id": "sec2"}, {"sid": "sec3"}],
    }
    planted, paid, reinforced = mod._foreshadow_actions(factual)
    assert planted == ["fs1", "fs2", "无id用描述"]
    assert paid == ["fs0"]
    assert reinforced == ["fs3"]
    assert mod._secrets_revealed(factual) == ["sec1", "sec2", "sec3"]
    # 空 factual → 三空 / 空 secrets
    assert mod._foreshadow_actions({}) == ([], [], [])
    assert mod._secrets_revealed({}) == []


# ============================================================
# 端到端：build_cluster_summary（造小项目 → 跑账本 → 断言 rollup）
# ============================================================

def _mk_project(root: Path, cluster_id="cluster_001", rng=(1, 2)):
    """造最小可跑项目：事件簇.json 给 cluster 范围 + title，写每章正文/changes。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    lo, hi = rng
    # 🔴 2026-06-28 不降级收尾：throughline_progress 改由 archivist→apply_archive 落
    # 事件簇.clusters[].throughline_progress（archive 单一来源·非 writer 自报 changes.factual）。
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": cluster_id, "title": "第一战",
                      "chapter_range": [lo, hi],
                      "throughline_progress": {"OS": True, "MC": False,
                                               "IC": False, "RS": False}}]
    }, ensure_ascii=False), encoding="utf-8")
    # 人物卡 + 地图（供正文派生命中）
    (db / "人物卡.json").write_text(json.dumps({
        "characters": [{"name": "李四"}, {"name": "王五"}]
    }, ensure_ascii=False), encoding="utf-8")
    (db / "地图.json").write_text(json.dumps({
        "locations": [{"name": "青云城"}]
    }, ensure_ascii=False), encoding="utf-8")
    # 逐章正文 + changes
    for ch in range(lo, hi + 1):
        body = ("李四走进青云城，王五已在城门等候。\n" * 30) + "翌日，风雪未停。"
        cio.write_body(root, ch, body)
        cio.write_changes(root, ch, {
            "factual": {
                "secrets_revealed": [f"sec_{ch}"],
            },
            "self_eval": {"applied_style": {"ending_type": "cliffhanger"}},
        })
    return root


def test_build_cluster_summary_end_to_end():
    """端到端：账本写入 + 返回 stats（章数/字数/range/title）+ 落库可被 store 读回。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), "cluster_001", (1, 2))
        res = mod.build_cluster_summary(root, "cluster_001")
        assert res["ok"] is True
        assert res["cluster_id"] == "cluster_001"
        assert res["title"] == "第一战"
        assert res["chapter_range"] == [1, 2]
        assert res["chapters_filled"] == 2
        assert res["word_count"] > 0           # 两章正文有 CJK
        # 账本真落库：从 store 读回，secrets/throughline rollup 都在
        from cluster_summary_reader import load_summary
        summary = load_summary(root)
        rec = next(c for c in summary["clusters"]
                   if cl.normalize_cluster_id(c["cluster_id"]) == "cluster_001")
        assert rec["title"] == "第一战"
        assert rec["chapter_range"] == [1, 2]
        assert rec["word_count"] == res["word_count"]
        # 两章各 sec_1 / sec_2 → 去重排序汇总
        assert rec["secrets_revealed"] == ["sec_1", "sec_2"]
        # throughline 来自 事件簇.clusters[].throughline_progress（archive 源·非 writer 自报）：
        # 仅 OS 命中（MC/IC/RS False 不计）·cluster 级值注入两章 → 分布 100% OS
        assert rec["throughline_distribution"] == {"OS": 1.0}
        # 每章账本记录承载 cluster 级 throughline（aggregator 读 rec.throughline_progress）
        assert rec["chapters"]["1"]["throughline_progress"] == {
            "OS": True, "MC": False, "IC": False, "RS": False}
        # 每章富摘要里有正文派生字段
        ch1 = rec["chapters"]["1"]
        assert ch1["cjk_count"] > 0
        assert "青云城" in ch1.get("locations_mentioned", [])
        assert ch1.get("time_transition_present") is True


def test_build_cluster_summary_unknown_cluster_returns_error():
    """取不到章范围（cluster 不存在）→ ok=False + error，不崩、不写库。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), "cluster_001", (1, 2))
        res = mod.build_cluster_summary(root, "cluster_099")
        assert res["ok"] is False
        assert res["cluster_id"] == "cluster_099"
        assert "error" in res and res["error"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print("OK", fn.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
    print(f"[cluster_summary_builder] {passed} passed / {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)
