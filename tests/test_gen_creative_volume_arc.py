#!/usr/bin/env python3
"""gen_creative volume_arc mode 测试（阶段2 创建书籍·解死锁①）。

fake llm_transport·不调真 API。验：dry-run 脚手架无枚举规训(北极星⑤) / 正常产出 emit 两文件 /
结构破损 block 非零退出 / emit 拆分正确(大势卡 volumes+ME·事件簇 cluster_001)。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_creative as gc  # noqa: E402
import llm_transport  # noqa: E402,F401  确保进 sys.modules 供 monkeypatch generate


def _args(**kw):
    a = types.SimpleNamespace(
        project=None, selected_card=None, cluster_count=8, framework="三幕",
        rhythm="标准", style_ref=None, research=None, dry_run=False, emit_to_db=False)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


_VALID = {
    "story_destiny": {"final_image": "终局", "thematic_resolution": "主题"},
    "_metadata": {"rhythm_profile": "标准"},
    "volumes": [{"vol": 1, "title": "第一卷", "phase": "起", "volume_core_conflict": "冲突",
                 "volume_thread": "线索", "volume_finale_signal": "信号"}],
    "major_events": [{"id": "ME-V1-01", "volume": 1, "title": "走向1",
                      "is_volume_finale": False, "stakes_delta": "起点"}],
    "cluster_001": {"narrative_mode": "in_medias_res", "scope_summary": "首块",
                    "scene_storyboard": [{"scene": 0, "summary": "灾难开场"}],
                    "foreshadowing_to_plant": ["伏笔A"]},
}


def _fake_generate(returns):
    def gen(loader, system, user, **kw):
        return types.SimpleNamespace(text=json.dumps(returns, ensure_ascii=False))
    return gen


def test_volume_arc_prompt_no_schema_coercion():
    """北极星⑤：prompt 含脚手架 + 作者档优先 + 不锁章铁律·不含枚举硬约束。"""
    system, user = gc.build_volume_arc_prompt(
        selected_card={"title": "卡", "logline": "梗概"}, cluster_count=8,
        framework="三幕", rhythm="标准", author_block="（作者档）", research_text="")
    for kw in ("story_destiny", "volumes", "major_events", "cluster_001",
               "in_medias_res", "第一权威", "free_notes"):
        assert kw in system, f"脚手架缺 {kw}"
    assert "绝不写" in system and "target_chapter" in system  # 不锁章铁律
    for bad in ("phase 必须从", "finale_signal 必须含", "ME 数量必须"):
        assert bad not in system, f"违北极星⑤·硬编码枚举规训: {bad}"


def test_volume_arc_emit_splits_two_files():
    _orig = llm_transport.generate
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        card = proj / "card.json"
        card.write_text(json.dumps({"answer": {"title": "卡"}}, ensure_ascii=False),
                        encoding="utf-8")
        llm_transport.generate = _fake_generate(_VALID)
        try:
            rc = gc._run_volume_arc(_args(project=str(proj), selected_card=str(card),
                                          emit_to_db=True))
        finally:
            llm_transport.generate = _orig   # 还原·防污染后续 llm_transport 测试
        assert rc == 0
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        assert len(major["volumes"]) == 1 and len(major["major_events"]) == 1
        assert major["major_events"][0]["status"] == "pending"
        cluster = json.loads((proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
        c0 = cluster["clusters"][0]
        assert c0["cluster_id"] == "cluster_001" and c0["narrative_mode"] == "in_medias_res"
        assert c0["status"] == "pending"  # step6 cluster_choice_apply 才改 in_progress
        assert len(c0["scene_storyboard"]) == 1


def test_volume_arc_broken_json_block_nonzero():
    """契约3：结构破损(缺顶层键) block → 非零退出(不静默吞断链)。"""
    _orig = llm_transport.generate
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        llm_transport.generate = _fake_generate({"volumes": []})  # 缺 3 键
        try:
            rc = gc._run_volume_arc(_args(project=str(proj), emit_to_db=True))
        finally:
            llm_transport.generate = _orig
        assert rc == 1, "结构破损应 block 非零退出"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "破损不该落盘"


def test_volume_arc_dry_run_no_api():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        rc = gc._run_volume_arc(_args(project=str(proj), dry_run=True))
        assert rc == 0


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
    sys.exit(1 if fails else 0)


def test_emit_writes_rhythm_to_user_pref():
    """轮次4 契约审计回归：用户 pause 答的节奏档必须落 用户偏好.json.rhythm_profile
    （cluster-write step6 data_flow 的 source·此前无 producer → splitter 永收「标准」）。"""
    import tempfile
    import gen_creative as gc
    from pathlib import Path as _P
    tmp = _P(tempfile.mkdtemp())
    try:
        data = {"story_destiny": {}, "volumes": [], "major_events": [],
                "cluster_001": {}, "_metadata": {}}
        gc._emit_volume_arc_to_db(tmp, data, rhythm="紧凑", framework="三幕")
        pref = json.loads((tmp / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        assert pref.get("rhythm_profile") == "紧凑"
        mj = json.loads((tmp / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        assert mj["_metadata"].get("rhythm_profile") == "紧凑"   # 确定性覆盖非 LLM 自觉
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
