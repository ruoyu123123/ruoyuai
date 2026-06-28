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


def test_volume_arc_story_content_from_card_not_style_ref():
    """🔴 2026-06-28 W6：故事内容 vs 笔法 权威分离回归锁（治污染 bug）。

    实证翻车：诡秘风格档含「沙盒天道/燧明部」示例·模型偷懒抄成大纲·无视「钟楼弃儿」灵感卡。
    修：prompt 必须明确『故事内容(题材/人物/世界/走向)唯一来源=灵感卡·作者档只学笔法·
    示例里的人名/地名/情节是笔法演示禁当故事搬』。本测试锁该指令不被回退掉。"""
    system, user = gc.build_volume_arc_prompt(
        selected_card={"title": "钟楼弃儿", "logline": "守夜人捡到未来遗嘱"}, cluster_count=10,
        framework="Save the Cat", rhythm="混合",
        author_block="（风格档·含「沙盒天道」示例）", research_text="")
    # 必含故事内容来自灵感卡的明确指令
    assert "唯一来源" in system, "缺『故事内容唯一来源=灵感卡』指令"
    assert "只学笔法" in system, "缺『作者风格档只学笔法』分离"
    assert ("禁止" in system or "绝对禁止" in system), "缺禁止从风格档示例搬故事的护栏"
    # 灵感卡确实进了 user prompt
    assert "钟楼弃儿" in user, "选定卡未进 user prompt"


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


def _fake_generate_sequence(*payloads):
    """序列返回：每次调用吐下一个 payload(dict→JSON / str→原文)·末个之后复用末个。
    带 finish_reason='stop'(真实 GenResult 有·诊断 dump 读它)。验 parse-失败重试自愈。"""
    calls = {"n": 0}

    def gen(loader, system, user, **kw):
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        p = payloads[i]
        text = p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)
        return types.SimpleNamespace(text=text, finish_reason="stop")
    gen._calls = calls
    return gen


def test_volume_arc_retry_recovers_from_transient_break():
    """🔴 真机 e2e 抓修 2026-06-15：volume_arc 偶发非 JSON/被限速截断 → parse 失败·原「单次
    失败直接 block exit 1」逼用户手动 --resume(GUI 非技术用户建书致命·不懂 --resume)。实测
    同 prompt 第一次炸第二次过(瞬时根因)。验 parse-失败重试让偶发抖动自愈：第一次缺键(破损)·
    第二次合法 → rc==0 + 落盘(不 block)。"""
    _orig = llm_transport.generate
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        fake = _fake_generate_sequence({"volumes": []}, _VALID)  # 破损 → 合法
        llm_transport.generate = fake
        try:
            rc = gc._run_volume_arc(_args(project=str(proj), emit_to_db=True))
        finally:
            llm_transport.generate = _orig
        assert rc == 0, "第二次合法应自愈 rc==0(不 block)"
        assert fake._calls["n"] == 2, "应重试 1 次(共调 2 次·非首次即弃也非过度重试)"
        assert (proj / "_数据库" / "大势卡.json").exists(), "自愈后应落盘"


def test_volume_arc_retry_exhausted_still_blocks():
    """3 次全破损 → block exit 1(确定性破损不无限重试·不静默吞断链)·留 raw 诊断证据。"""
    _orig = llm_transport.generate
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        fake = _fake_generate_sequence({"volumes": []})  # 每次都破损
        llm_transport.generate = fake
        try:
            rc = gc._run_volume_arc(_args(project=str(proj), emit_to_db=True))
        finally:
            llm_transport.generate = _orig
        assert rc == 1, "3 次全破损应 block 非零退出"
        assert fake._calls["n"] == 3, "应尝试满 3 次(MAX_VOL_ARC_TRIES)"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "破损不落盘"
        dbg = proj / "_数据库" / ".wal" / "volume_arc_block_debug.txt"
        assert dbg.exists(), "block 应留 raw 诊断证据(失败必记录学习·非静默吞证据)"


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
