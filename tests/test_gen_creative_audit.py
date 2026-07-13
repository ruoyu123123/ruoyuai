"""gen_creative deterministic regression tests.

outline_card is not a public CLI mode: direction cards must flow through
cluster_emergence_engine + novel-outline-planner + cluster user_choice
artifacts. Tests lock that rejection, the brainstorm --verify machine gate
(cards are authored by novel-outline-planner MODE=brainstorm), plus the
volume_arc structural guards.
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import gen_creative as gc  # noqa: E402
import gen_creative_volume_arc as gva  # noqa: E402  volume_arc 实现


# ════════════════════════════════════════════════════════════════
# Helper
# ════════════════════════════════════════════════════════════════

def _run_main(argv: list[str]) -> int | None:
    """Run main() with argv and return SystemExit code; normal return is None."""
    old_argv = sys.argv[:]
    sys.argv = argv
    try:
        gc.main()
        return None
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old_argv


# ════════════════════════════════════════════════════════════════
# Hard rejection: outline_card is not a public CLI mode
# ════════════════════════════════════════════════════════════════

def test_outline_card_mode_removed_exit2():
    """单章走向卡入口必须被 argparse 硬拒，不能绕过 cluster 选择链路。"""
    code = _run_main(["gen_creative.py", "--mode", "outline_card", "--count", "2"])
    assert code == 2, f"outline_card 旧入口应被硬拒 exit 2，实得 {code}"


def test_voice_sample_mode_removed_exit2():
    code = _run_main(["gen_creative.py", "--mode", "voice_sample"])
    assert code == 2


# ════════════════════════════════════════════════════════════════
# _emit_volume_arc_to_db major_events isinstance 守卫
# ════════════════════════════════════════════════════════════════

def _emit(tmp: Path, data: dict) -> dict:
    """调 _emit_volume_arc_to_db 落盘并读回 大势卡.json（断言不崩 + 内容正确）。"""
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    p_major, p_cluster = gva._emit_volume_arc_to_db(proj, data, rhythm="", framework="")
    assert p_major.exists() and p_cluster.exists(), "大势卡/事件簇 未落盘"
    return json.loads(p_major.read_text(encoding="utf-8"))


def test_emit_volume_arc_filters_non_dict_major_events():
    """major_events 含裸串 / None / 整数（gen-model 软约束下合法可达）→
    isinstance(me, dict) 守卫过滤掉坏元素，只投影合法 dict ME
    （否则 `{**'str'}` TypeError: object is not a mapping 崩建书单点调用）。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {"final_image": "末法最后一人"},
            "volumes": [{"vol": 1, "title": "卷一"}],
            "major_events": [
                {"id": "ME-V1-01", "volume": 1, "summary": "开局", "is_volume_finale": False},
                "我是一个不该出现的裸串 ME",   # 非 dict → 必须被过滤
                None,                          # 非 dict → 必须被过滤
                42,                            # 非 dict → 必须被过滤
                {"id": "ME-V1-02", "volume": 1, "status": "active", "is_volume_finale": True},
            ],
            "cluster_001": {"scope_summary": "倒叙开场"},
        }
        major = _emit(Path(td), data)   # 不抛 TypeError 即守卫生效
        mes = major["major_events"]
        # 只剩 2 条合法 dict（裸串/None/int 被剔除）
        assert len(mes) == 2, f"非 dict ME 未被过滤，实得 {len(mes)} 条: {mes}"
        ids = {m["id"] for m in mes}
        assert ids == {"ME-V1-01", "ME-V1-02"}
        # 缺 status 的补 pending；自带 status 的保留
        by_id = {m["id"]: m for m in mes}
        assert by_id["ME-V1-01"]["status"] == "pending"
        assert by_id["ME-V1-02"]["status"] == "active"


def test_emit_volume_arc_all_dict_major_events_preserved():
    """控制组：全 dict major_events → 全部保留 + status 默认 pending（守卫不误伤正常路径）。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {},
            "volumes": [],
            "major_events": [
                {"id": "ME-A", "volume": 1, "is_volume_finale": False},
                {"id": "ME-B", "status": "done", "volume": 1, "is_volume_finale": False},
                {"id": "ME-C", "volume": 1, "is_volume_finale": True},
            ],
            "cluster_001": {},
        }
        major = _emit(Path(td), data)
        mes = major["major_events"]
        assert len(mes) == 3, f"合法 ME 被误删: {mes}"
        st = {m["id"]: m["status"] for m in mes}
        assert st == {"ME-A": "pending", "ME-B": "done", "ME-C": "pending"}


def test_emit_volume_arc_empty_and_missing_major_events():
    """major_events 缺失 / 为空列表：ME 池是当前卷大势方向的唯一来源，
    _normalize_me_pool 对空池硬拒 ValueError，emit 不落盘（边界）。"""
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="不能为空"):
            _emit(Path(td) / "a", {"major_events": []})
        assert not (Path(td) / "a" / "proj" / "_数据库" / "大势卡.json").exists()
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(ValueError, match="不能为空"):
            _emit(Path(td) / "b", {})   # 无 major_events 键 → .get 默认 []
        assert not (Path(td) / "b" / "proj" / "_数据库" / "大势卡.json").exists()


# ════════════════════════════════════════════════════════════════
# brainstorm --verify 确定性验收门（卡由 novel-outline-planner 亲笔）
# ════════════════════════════════════════════════════════════════

_URL_A = "https://example.com/hot-topic-2026"
_URL_B = "https://example.com/genre-analysis"


def _good_card(cid: str, refs=None) -> dict:
    return {
        "card_id": cid,
        "title": f"卡{cid}",
        "logline": "钟楼守夜人捡到写着自己死期的未来遗嘱",
        "core_mechanism": "每敲一次钟就消耗一段他人记忆，钟声能改写小范围因果",
        "volume_skeleton": [
            {"vol": v, "title": f"卷{v}", "archetype": "守夜人", "climax": f"高潮{v}"}
            for v in range(1, 6)
        ],
        "selling_point": "贴悬疑+规则怪谈线（引用调研：热帖里的时间诡计需求）",
        "risk": "倒叙开场信息量大 → 首块 200 字内丢核心悬念",
        "source_refs": refs if refs is not None else [_URL_A],
    }


def _cards_doc(n: int = 3) -> dict:
    return {"version": 1, "topic": "末世/钟楼/规则怪谈",
            "cards": [_good_card(c) for c in "ABC"[:n]]}


_RESEARCH_TEXT = f"""# Research: 钟楼
## Findings
- 热帖趋势 — 时间诡计需求上升。Source: [t]({_URL_A})
## Sources
1. [t]({_URL_A})
2. [g]({_URL_B})
"""


def _verify_doc(doc, count=3, research_text=_RESEARCH_TEXT):
    return gc.verify_brainstorm_cards(doc, count=count, research_text=research_text)


def test_brainstorm_verify_accepts_valid_cards():
    assert _verify_doc(_cards_doc()) == []


def test_brainstorm_verify_rejects_wrong_count():
    errs = _verify_doc(_cards_doc(2))
    assert errs and any("恰 3 张" in e for e in errs)


def test_brainstorm_verify_rejects_missing_keys_and_dup_ids():
    doc = _cards_doc()
    del doc["cards"][0]["risk"]
    doc["cards"][1]["card_id"] = "A"   # 与 cards[0] 撞 id
    errs = _verify_doc(doc)
    assert any("缺必备键" in e and "risk" in e for e in errs)
    assert any("card_id 重复" in e for e in errs)


def test_brainstorm_verify_rejects_over_length():
    doc = _cards_doc()
    doc["cards"][0]["logline"] = "长" * 51
    doc["cards"][1]["core_mechanism"] = "长" * 101
    errs = _verify_doc(doc)
    assert any("logline 超长" in e for e in errs)
    assert any("core_mechanism 超长" in e for e in errs)


def test_brainstorm_verify_rejects_bad_volume_skeleton():
    doc = _cards_doc()
    doc["cards"][0]["volume_skeleton"] = doc["cards"][0]["volume_skeleton"][:4]  # 4 卷
    doc["cards"][1]["volume_skeleton"] = [{"vol": v} for v in range(1, 8)]       # 7 卷
    doc["cards"][2]["volume_skeleton"] = ["裸串"]                                 # 非 object
    errs = _verify_doc(doc)
    assert sum("volume_skeleton" in e for e in errs) >= 3


def test_brainstorm_verify_rejects_fabricated_source_url():
    """source_refs 逐条 URL 必须真实存在于调研缓存——凭记忆编造 = 机器门拒收。"""
    doc = _cards_doc()
    doc["cards"][0]["source_refs"] = ["https://made-up.example.org/fake"]
    errs = _verify_doc(doc)
    assert any("不存在于调研缓存" in e for e in errs)
    # 真实存在的第二条 URL 合法
    doc2 = _cards_doc()
    doc2["cards"][0]["source_refs"] = [_URL_A, _URL_B]
    assert _verify_doc(doc2) == []


def test_brainstorm_verify_rejects_empty_source_refs():
    doc = _cards_doc()
    doc["cards"][0]["source_refs"] = []
    errs = _verify_doc(doc)
    assert any("source_refs" in e for e in errs)


def test_brainstorm_verify_cli_end_to_end(tmp_path=None):
    """CLI 端到端：agent 落盘的卡 + 调研缓存 → exit 0 + 盖 _meta 验收章（归一严格 JSON）；
    破损卡 → exit 2（主代理重 spawn planner）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        research = d / "inspiration_synthesis.json"
        research.write_text(_RESEARCH_TEXT, encoding="utf-8")
        cards = d / "inspiration_cards.json"
        cards.write_text(json.dumps(_cards_doc(), ensure_ascii=False), encoding="utf-8")
        code = _run_main(["gen_creative.py", "--mode", "brainstorm", "--verify",
                          "--cards", str(cards), "--count", "3",
                          "--research", str(research)])
        assert code in (None, 0), f"合法卡应验收通过，实得 {code}"
        stamped = json.loads(cards.read_text(encoding="utf-8"))
        assert stamped["_meta"]["verified_by"] == "gen_creative.brainstorm.verify"
        assert stamped["_meta"]["authored_by"] == "novel-outline-planner"
        # 破损卡（少 1 张）→ exit 2
        cards.write_text(json.dumps(_cards_doc(2), ensure_ascii=False), encoding="utf-8")
        code2 = _run_main(["gen_creative.py", "--mode", "brainstorm", "--verify",
                           "--cards", str(cards), "--count", "3",
                           "--research", str(research)])
        assert code2 == 2, f"破损卡应 exit 2 供重 spawn，实得 {code2}"


def test_brainstorm_verify_missing_cards_exit2_missing_research_exit1():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        research = d / "syn.json"
        research.write_text(_RESEARCH_TEXT, encoding="utf-8")
        # 卡未落盘（agent 未产出）→ 2
        code = _run_main(["gen_creative.py", "--mode", "brainstorm", "--verify",
                          "--cards", str(d / "nope.json"), "--count", "3",
                          "--research", str(research)])
        assert code == 2
        # 调研缓存缺失（重 spawn planner 修不了）→ 1
        cards = d / "cards.json"
        cards.write_text(json.dumps(_cards_doc(), ensure_ascii=False), encoding="utf-8")
        code2 = _run_main(["gen_creative.py", "--mode", "brainstorm", "--verify",
                           "--cards", str(cards), "--count", "3",
                           "--research", str(d / "gone.json")])
        assert code2 == 1


def test_brainstorm_cli_requires_verify():
    """旧 LLM 生成入口已删：--mode brainstorm 不带 --verify → 响亮 exit 1。"""
    code = _run_main(["gen_creative.py", "--mode", "brainstorm", "--cards", "x.json"])
    assert code == 1, f"缺 --verify 应响亮拒绝 exit 1，实得 {code}"


def test_volume_arc_rejects_verify_flag():
    """--mode volume_arc 自带单元验收（jobs pending 范式）→ --verify 组合响亮拒绝。"""
    code = _run_main(["gen_creative.py", "--mode", "volume_arc", "--verify",
                      "--project", "x"])
    assert code == 1


# ════════════════════════════════════════════════════════════════
# import 守卫
# ════════════════════════════════════════════════════════════════

def test_module_imports():
    """模块可 import（无语法/引用错）+ 验收器 API 存在。"""
    assert hasattr(gc, "main")
    assert hasattr(gc, "verify_brainstorm_cards")
    assert hasattr(gc, "verify_distill_skill_text")
    assert hasattr(gva, "_emit_volume_arc_to_db")


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
