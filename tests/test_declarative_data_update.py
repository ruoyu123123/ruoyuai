"""declarative_data_update 回归测试 — 钉死 6 类声明式增量的纯逻辑。

被测脚本读 _changes.json 的 factual 段，自动把 6 类增量写进对应数据库 JSON。
6 个 update_* 函数是纯逻辑（in-place 改 dict + 返回 logs，不触 LLM/网络/文件），
外加 _payload_fingerprint / load_json 两个确定性 helper。这组测试锁它们的真实行为：
happy path / 缺字段 SKIP 分支 / 新记录 ADD 分支 / 找不到分支 / 指纹稳定性。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import declarative_data_update as mod


# ---------------------------------------------------------------- relationships

def test_update_relationships_increment_existing():
    """已存在的关系 → field 做增量叠加（old + delta），打 _last_modified_at_ch。"""
    rel = {"relationships": [{"from": "A", "to": "B", "affinity": 10, "trust": 5}]}
    logs = mod.update_relationships(
        rel, [{"from": "A", "to": "B", "field": "affinity", "delta": 7, "trigger": "救命"}], ch=12
    )
    r = rel["relationships"][0]
    assert r["affinity"] == 17                       # 10 + 7 增量
    assert r["trust"] == 5                            # 未动
    assert r["_last_modified_at_ch"] == 12
    assert any("[OK]" in x for x in logs)


def test_update_relationships_add_new():
    """不存在的关系 → 新建一条，缺省四维度补 0，被指定的 field 置为 delta。"""
    rel = {"relationships": []}
    logs = mod.update_relationships(
        rel, [{"from": "X", "to": "Y", "field": "fear", "delta": 4, "trigger": "威胁"}], ch=3
    )
    assert len(rel["relationships"]) == 1
    new = rel["relationships"][0]
    assert new["fear"] == 4
    assert new["affinity"] == 0 and new["trust"] == 0 and new["respect"] == 0
    assert new["_first_added_ch"] == 3
    assert new["notes"] == "威胁"
    assert any("[ADD]" in x for x in logs)


def test_update_relationships_missing_field_skip():
    """缺字段（无 delta / 无 from 等）→ SKIP，不新建不报错。delta=0 是合法值不跳。"""
    rel = {"relationships": []}
    logs = mod.update_relationships(
        rel,
        [
            {"from": "A", "to": "B", "field": "affinity"},      # 缺 delta → SKIP
            {"from": "A", "field": "trust", "delta": 1},        # 缺 to → SKIP
            {"from": "C", "to": "D", "field": "respect", "delta": 0},  # delta=0 合法 → ADD
        ],
        ch=1,
    )
    assert sum("[SKIP]" in x for x in logs) == 2
    assert len(rel["relationships"]) == 1               # 只有 delta=0 那条真落了
    assert rel["relationships"][0]["respect"] == 0


# ------------------------------------------------------------ faction_standings

def test_update_faction_standings_increment_and_skip():
    """已有 key 做增量；不存在的 key 从 0 起算；缺字段 SKIP。"""
    rel = {"faction_standings": {"宗门": 20}}
    logs = mod.update_faction_standings(
        rel,
        [
            {"factions": "宗门", "delta": -5},     # 20 + (-5)
            {"factions": "魔教", "delta": 8},      # 新 key 从 0
            {"delta": 3},                          # 缺 factions → SKIP
        ],
        ch=4,
    )
    assert rel["faction_standings"]["宗门"] == 15
    assert rel["faction_standings"]["魔教"] == 8
    assert sum("[SKIP]" in x for x in logs) == 1


# -------------------------------------------------------------------- events

def test_update_events_pending_to_triggered():
    """pending 里有匹配 id → 转移到 triggered（pending 移除 + 打 status/triggered_at_ch）。"""
    ev = {"pending_events": [{"id": "E1", "title": "伏击"}], "triggered_events": []}
    logs = mod.update_events(ev, [{"event_id": "E1", "note": "成功"}], ch=9)
    assert ev["pending_events"] == []                            # 已移出 pending
    assert len(ev["triggered_events"]) == 1
    moved = ev["triggered_events"][0]
    assert moved["status"] == "triggered"
    assert moved["triggered_at_ch"] == 9
    assert moved["result_note"] == "成功"
    assert any("[OK]" in x for x in logs)


def test_update_events_new_and_missing_id():
    """不在 pending 的 event_id → 直接 ADD 进 triggered；缺 event_id → SKIP。"""
    ev = {"pending_events": [], "triggered_events": []}
    logs = mod.update_events(
        ev, [{"event_id": "NEW", "note": "突发"}, {"note": "没id"}], ch=2
    )
    assert len(ev["triggered_events"]) == 1
    assert ev["triggered_events"][0]["id"] == "NEW"
    assert sum("[ADD]" in x for x in logs) == 1
    assert sum("[SKIP]" in x for x in logs) == 1


# ------------------------------------------------------------------- travel_log

def test_update_travel_log_append_and_skip():
    """有 character → append 一条带 ch 的行程；缺 character → SKIP。"""
    mp = {"travel_log": []}
    logs = mod.update_travel_log(
        mp,
        [
            {"character": "甲", "from": "城", "to": "山", "time": "黄昏"},
            {"from": "城", "to": "山"},      # 缺 character → SKIP
        ],
        ch=15,
    )
    assert len(mp["travel_log"]) == 1
    entry = mp["travel_log"][0]
    assert entry == {"ch": 15, "character": "甲", "from": "城", "to": "山", "time": "黄昏"}
    assert sum("[SKIP]" in x for x in logs) == 1


# --------------------------------------------------------------------- secrets

def test_update_secrets_status_change_and_leaked_to():
    """匹配 id → 改 status + 打 ch；带 leaked_to → 并进 known_by 去重。"""
    fs = {"secrets": [{"id": "S1", "status": "hidden", "known_by": ["甲"]}]}
    logs = mod.update_secrets(
        fs,
        [{"secret_id": "S1", "new_status": "revealed", "leaked_to": ["乙", "甲"], "trigger": "泄密"}],
        ch=8,
    )
    s = fs["secrets"][0]
    assert s["status"] == "revealed"
    assert s["_last_status_change_ch"] == 8
    assert sorted(s["known_by"]) == ["乙", "甲"]          # 去重，甲不重复
    assert any("[OK]" in x for x in logs)


def test_update_secrets_not_found_and_missing():
    """找不到 id → SKIP；缺 secret_id / 缺 new_status → SKIP；都不崩。"""
    fs = {"secrets": [{"id": "S1", "status": "hidden"}]}
    logs = mod.update_secrets(
        fs,
        [
            {"secret_id": "GHOST", "new_status": "revealed"},  # 找不到 → SKIP
            {"secret_id": "S1"},                               # 缺 new_status → SKIP
        ],
        ch=1,
    )
    assert fs["secrets"][0]["status"] == "hidden"        # 原状态不动
    assert sum("[SKIP]" in x for x in logs) == 2


# ------------------------------------------------------------------- knowledge

def test_update_knowledge_append_dedupe_and_will_learn_removal():
    """匹配角色（按 name 或 id）→ knows 去重 append，并从 will_learn 移除同 fact。"""
    cards = {
        "characters": [
            {
                "name": "丙",
                "knowledge": {"knows": ["天机"], "will_learn": [{"fact": "真名"}, {"fact": "别的"}]},
            }
        ]
    }
    logs = mod.update_knowledge(
        cards,
        [
            {"character": "丙", "fact": "真名"},   # 新增 + 从 will_learn 移除
            {"character": "丙", "fact": "天机"},   # 已知 → 不重复
        ],
        ch=6,
    )
    k = cards["characters"][0]["knowledge"]
    assert k["knows"].count("真名") == 1
    assert k["knows"].count("天机") == 1            # 未重复
    assert k["will_learn"] == [{"fact": "别的"}]    # 真名已被移除
    assert sum("[OK]" in x for x in logs) == 2


def test_update_knowledge_missing_and_not_found():
    """缺字段 → SKIP；找不到角色 → SKIP；按 id 也能匹配。"""
    cards = {"characters": [{"id": "C7", "name": "丁"}]}
    logs = mod.update_knowledge(
        cards,
        [
            {"character": "丁"},                       # 缺 fact → SKIP
            {"character": "幽灵", "fact": "X"},        # 找不到 → SKIP
            {"character": "C7", "fact": "按id命中"},   # id 匹配 → OK
        ],
        ch=2,
    )
    assert cards["characters"][0]["knowledge"]["knows"] == ["按id命中"]
    assert sum("[SKIP]" in x for x in logs) == 2


# --------------------------------------------------------------------- helpers

def test_payload_fingerprint_stable_and_sensitive():
    """指纹：相同 payload（含字段/key 顺序变化）稳定；内容变化则不同。"""
    fp1 = mod._payload_fingerprint([{"a": 1, "b": 2}], [], [], [], [], [])
    fp2 = mod._payload_fingerprint([{"b": 2, "a": 1}], [], [], [], [], [])  # key 序变
    fp3 = mod._payload_fingerprint([{"a": 1, "b": 3}], [], [], [], [], [])  # 内容变
    assert fp1 == fp2                                   # sort_keys 稳定
    assert fp1 != fp3                                   # 内容敏感
    assert len(fp1) == 64                               # sha256 hexdigest


def test_load_json_roundtrip_and_fallbacks():
    """load_json：正常读回 / 不存在返回 default / 坏 JSON 返回 default。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        good = tmp / "good.json"
        good.write_text(json.dumps({"k": "中文值"}, ensure_ascii=False), encoding="utf-8")
        assert mod.load_json(good) == {"k": "中文值"}

        missing = tmp / "missing.json"
        assert mod.load_json(missing, default={"d": 1}) == {"d": 1}
        assert mod.load_json(missing) is None

        bad = tmp / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []
