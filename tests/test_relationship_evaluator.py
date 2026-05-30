"""relationship_evaluator heart_event consumed 状态机闭环回归测试 — 守护 2026-05-30 修 #5。

旧 bug：relationship_evaluator 是 heart_event consumed 字段的**唯一读处**（he.get("consumed") 跳过），
但全仓**无任何脚本写 consumed=true**。关系数值单调累积 → trigger_at 一旦满足永久满足 →
每章 save-state 把同一 heart_event 反复重写进 pending_reveals → build_manifest 反复要求 writer
重揭已揭的秘密；cross_cluster scan_heart_event_consistency（只处理 consumed==true）成死代码。

本测试断言完整状态机闭环：
  · 阈值满足 + 未 consumed → 进 pending_reveals；
  · writer 在 _changes.json factual.heart_events_revealed 报告 event_id 后 → 写回 consumed=true + consumed_at_ch；
  · 同一 event_id 再次评估（阈值仍满足）→ 不再重复 pending（状态机已闭环）；
  · 幂等：已 consumed 的不重复写回（newly_consumed 为空）。

只测确定性纯函数 + evaluate 端到端落盘，不碰 LLM/agent。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import relationship_evaluator as re_mod


PROTAG = "陆衍"
NPC = "陆爸"
EVENT_ID = "HE_lubobo_01"


def _make_project(tmp: Path, *, consumed: bool = False) -> Path:
    """搭一个最小项目：人物卡（主角）+ 群像档（一个 heart_event trigger_at trust:9）
    + 关系（trust=10 已满足阈值）。返回项目根。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)

    (db / "人物卡.json").write_text(json.dumps({
        "characters": [{"name": PROTAG, "role": "主角"}, {"name": NPC, "role": "配角"}]
    }, ensure_ascii=False), encoding="utf-8")

    (db / "群像档.json").write_text(json.dumps({
        "characters": {
            NPC: {
                "heart_events": [{
                    "event_id": EVENT_ID,
                    "trigger_at": {"trust": 9},
                    "tier_label": "确认派",
                    "reveal": "陆爸醉酒后说出身世",
                    "physical_evidence": ["醉态", "夜晚"],
                    "consumed": consumed,
                }]
            }
        }
    }, ensure_ascii=False), encoding="utf-8")

    # 关系数值已超阈值（trust=10 ≥ 9）
    (db / "关系.json").write_text(json.dumps({
        "relationships": [{"from": PROTAG, "to": NPC, "trust": 10, "affinity": 5}]
    }, ensure_ascii=False), encoding="utf-8")

    return tmp


def _write_changes(tmp: Path, ch: int, revealed_event_ids):
    """写本章 _changes.json，factual.heart_events_revealed 报告 writer 实际揭了哪些 event。"""
    ch_dir = tmp / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    factual = {}
    if revealed_event_ids is not None:
        factual["heart_events_revealed"] = [
            {"event_id": eid, "evidence_appeared": ["醉态"]} for eid in revealed_event_ids
        ]
    (ch_dir / f"第{ch:03d}章_changes.json").write_text(
        json.dumps({"factual": factual}, ensure_ascii=False), encoding="utf-8")


def test_threshold_satisfied_yields_pending():
    """阈值满足且未 consumed + writer 本章没报揭密 → 进 pending_reveals。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(Path(d), consumed=False)
        _write_changes(root, 5, revealed_event_ids=[])  # 本章没揭
        r = re_mod.evaluate(root, 5)
        assert r["newly_consumed_count"] == 0, r
        assert r["pending_reveals_count"] == 1, r
        assert r["pending_reveals"][0]["event_id"] == EVENT_ID, r


def test_writeback_marks_consumed_and_clears_pending():
    """完整闭环：writer 报告揭密 → 写回 consumed=true + consumed_at_ch → 不再 pending。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(Path(d), consumed=False)
        # 本章 writer 报告揭了 HE_lubobo_01
        _write_changes(root, 7, revealed_event_ids=[EVENT_ID])
        r = re_mod.evaluate(root, 7)

        # 本次写回 1 个，且因为已 consumed 所以本轮不再 pending
        assert r["newly_consumed_count"] == 1, r
        assert r["newly_consumed"][0]["event_id"] == EVENT_ID, r
        assert r["newly_consumed"][0]["consumed_at_ch"] == 7, r
        assert r["pending_reveals_count"] == 0, r

        # 群像档.json 被真正写回（落盘可见 consumed=true + consumed_at_ch）
        ensemble = json.loads((root / "_数据库" / "群像档.json").read_text(encoding="utf-8"))
        he = ensemble["characters"][NPC]["heart_events"][0]
        assert he["consumed"] is True, he
        assert he["consumed_at_ch"] == 7, he


def test_no_repeat_pending_after_consumed():
    """关键回归：consumed 之后即便关系数值仍超阈值，后续章节也不再重复 pending。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(Path(d), consumed=False)
        # ch7 揭密 → 写回 consumed
        _write_changes(root, 7, revealed_event_ids=[EVENT_ID])
        re_mod.evaluate(root, 7)

        # ch8 关系数值依旧满足阈值（trust=10），writer 本章没再揭
        _write_changes(root, 8, revealed_event_ids=[])
        r8 = re_mod.evaluate(root, 8)
        assert r8["newly_consumed_count"] == 0, r8  # 已 consumed，不重复写回
        assert r8["pending_reveals_count"] == 0, r8  # 不再反复要求重揭


def test_writeback_is_idempotent():
    """幂等：已经 consumed 的 heart_event，writer 再次报告同 event_id 也不重复写回。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project(Path(d), consumed=True)  # 起手就已 consumed
        _write_changes(root, 9, revealed_event_ids=[EVENT_ID])
        r = re_mod.evaluate(root, 9)
        assert r["newly_consumed_count"] == 0, r
        assert r["pending_reveals_count"] == 0, r


def test_mark_consumed_pure_function():
    """纯函数 mark_consumed_from_changes：event_id 命中标 consumed，不命中不动。"""
    ensemble = {
        "characters": {
            NPC: {"heart_events": [
                {"event_id": "HE_a", "consumed": False},
                {"event_id": "HE_b", "consumed": False},
            ]}
        }
    }
    factual = {"heart_events_revealed": [{"event_id": "HE_a", "evidence_appeared": []}]}
    marked = re_mod.mark_consumed_from_changes(ensemble, factual, 12)
    assert len(marked) == 1 and marked[0]["event_id"] == "HE_a", marked
    hes = ensemble["characters"][NPC]["heart_events"]
    assert hes[0]["consumed"] is True and hes[0]["consumed_at_ch"] == 12, hes
    assert hes[1]["consumed"] is False, hes  # HE_b 没被报告，保持未消费

    # 再跑一次：HE_a 已 consumed → 不重复写回
    marked2 = re_mod.mark_consumed_from_changes(ensemble, factual, 13)
    assert marked2 == [], marked2
    assert hes[0]["consumed_at_ch"] == 12, hes  # 章号不被覆盖
