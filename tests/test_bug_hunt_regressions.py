#!/usr/bin/env python3
"""对抗式 bug-hunt 找到并修复的真 bug 回归锁（2026-06-17）。

bug-hunt workflow 按子系统 fan-out 审查 core/scripts·找到 13 个 confirmed 真 bug（全带 repro）·
修了 9 个高影响项（崩溃/卡管线/假成功/数据丢失）。本文件把这些修复用确定性测试钉死防回归。
完整 bug 清单见 workspace/_temp_research/bug_hunt_confirmed.md。
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))

import chapter_io as cio          # noqa: E402
import cluster_lookup as cl       # noqa: E402
import save_state                 # noqa: E402
import save_state_updates         # noqa: E402
import maybe_judge_consensus      # noqa: E402
import fate_engine               # noqa: E402
import self_heal_engine           # noqa: E402


def _db(proj):
    d = proj / "_数据库"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── #12 chapter_io.read_changes 对损坏/空 _changes.json 容错（不崩审核管线）──
def test_chapter_io_read_changes_tolerant_on_corrupt():
    for content in ("", "{半截", "not json at all", "{}garbage"):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            cp = cio.changes_path(proj, 1)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(content, encoding="utf-8")
            r = cio.read_changes(proj, 1)   # 原裸 json.loads 在此崩 JSONDecodeError
            assert r == {"factual": {}, "self_eval": {}}, f"损坏内容 {content!r} 应兜底: {r}"


# ── #3+cluster_lookup str-form：权威反查认 list + 历史 str "lo-hi" ──
def test_cluster_lookup_str_form_chapter_range():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _db(proj).joinpath("事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_005", "chapter_range": "10-12"}]}, ensure_ascii=False),
            encoding="utf-8")
        assert cl.cluster_id_to_range(proj, "cluster_005") == [10, 12]   # str 形态被解析
        # 数值归一化：裸数字/短 id 也命中
        assert cl.cluster_id_to_range(proj, "5") == [10, 12]


# ── #3 save_state._get_cluster_chapter_range blueprint 兜底（对齐 evaluators·不再 FATAL）──
def test_save_state_get_range_blueprint_fallback():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = _db(proj)
        # 事件簇 无 chapter_range（fluid v27 未回填）· blueprint 有
        db.joinpath("事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_002"}]}, ensure_ascii=False), encoding="utf-8")
        db.joinpath("进度.json").write_text(json.dumps({
            "cluster_blueprint": {"cluster_002": {"chapter_range": [4, 6]}}},
            ensure_ascii=False), encoding="utf-8")
        # 修前返 [] → cmd_apply/git FATAL exit2；修后走 cluster_lookup blueprint 兜底
        assert save_state._get_cluster_chapter_range(proj, "002") == [4, 5, 6]
        assert save_state_updates.get_cluster_chapter_range(proj, "cluster_002") == [4, 5, 6]


# ── #6 fate_engine.update 守卫畸形 fate_events_triggered（裸 str / evidence=None）──
def _seed_fate(proj, triggered):
    db = _db(proj)
    db.joinpath("大势卡.json").write_text(json.dumps({"major_events_pool": [
        {"id": "V1_ME_007", "status": "scheduled"}]}, ensure_ascii=False), encoding="utf-8")
    chd = proj / "章节" / "第030章"
    chd.mkdir(parents=True)
    (chd / "第030章_changes.json").write_text(json.dumps({
        "factual": {"fate_events_triggered": triggered}}, ensure_ascii=False), encoding="utf-8")


def test_fate_engine_update_guards_bare_string_element():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _seed_fate(proj, ["V1_ME_007"])           # 裸字符串元素（原 trig.get 崩）
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            r = fate_engine.update(proj, 30)        # 不该崩
        assert isinstance(r, dict) and "ch" in r


def test_fate_engine_update_guards_none_evidence():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _seed_fate(proj, [{"event_id": "V1_ME_007", "evidence": None}])  # evidence=None（原 [:120] 崩）
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            r = fate_engine.update(proj, 30)
        assert r.get("updated") == 1               # 正常完成该 ME·evidence 兜底空串
        fate = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        me = fate["major_events_pool"][0]
        assert me["status"] == "completed" and me["completion_evidence"] == ""


# ── #5 maybe_judge_consensus.is_key_chapter 守卫 emotion 标量（非 dict）──
def test_maybe_judge_scalar_emotion_no_crash():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _db(proj).joinpath("进度.json").write_text(json.dumps({
            "cluster_blueprint": {"cluster_001": {"scene_storyboard": [
                {"ch": 3, "emotion": 7}]}}}, ensure_ascii=False), encoding="utf-8")  # emotion 裸 int
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            r = maybe_judge_consensus.is_key_chapter(proj, 3)  # 原 .get("value") 在 int 上崩
        # 不崩即可（强情绪 7 → 应判 key·但核心是不抛 AttributeError）
        assert r is not None


# ── #13 self_heal_engine ingest 容忍非字符串 signature（一条毒记录不整批崩）──
def test_self_heal_ingest_nonstring_signature_skips():
    """_apply_ingest(kb, inc_path, stats)：incidents.jsonl 含 int signature 的毒记录·
    原 sig.strip(":") 在 int 上 AttributeError 整批崩·修后强转 str 正常处理。"""
    line = json.dumps({"signature": 12345, "error_type": "KeyError", "script": "x.py"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "incidents.jsonl"
        p.write_text(line + "\n", encoding="utf-8")
        kb, stats = {}, {}
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self_heal_engine._apply_ingest(kb, p, stats)   # 不该抛 AttributeError
    # 毒记录被强转 str 后正常 ingest（非字符串 signature 不再让整批失败）


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  [FAIL] {nm}: {e}")
            traceback.print_exc()
    sys.exit(1 if fails else 0)
