#!/usr/bin/env python3
"""gen_creative volume_arc mode 测试（阶段2 创建书籍·P2 分卷 chunk + WAL 断点续跑）。

fake llm_transport·不调真 API。验：
- prompt 脚手架无枚举规训（北极星⑤）+ 故事内容/笔法权威分离（骨架 prompt + 单卷 ME 池 prompt）
- 骨架 + 逐卷 chunk 正常产出 → 确定性合并 → emit 两文件（与旧一把梭结构等价）
- 逐卷 WAL（volume_arc_skeleton.json / volume_arc_v<N>.json）落盘
- 断点续跑：合法 WAL 跳过不重复生成（mock 计数）；损坏 WAL 重生成；全 WAL 命中 = 幂等零调用
- 合并 ME id 跨卷重复 → 硬报错非零退出 + 隔离 .dup_broken（不静默覆盖）
- 单卷失败 = 整 step 失败（required 不降级），已完成卷 WAL 保留供续跑
- --volumes 内部调试子集：只生成指定卷·不合并不落库
- 结构破损 block 非零退出 + parse-失败重试自愈 + 破损诊断 dump
"""
import json
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_creative_volume_arc as gva  # noqa: E402  volume_arc 实现（2026-07-07 从 gen_creative 拆出）
import llm_transport  # noqa: E402,F401  确保进 sys.modules 供 monkeypatch generate


def _args(**kw):
    a = types.SimpleNamespace(
        project=None, selected_card=None, cluster_count=8, framework="三幕",
        rhythm="标准", style_ref=None, research=None, dry_run=False, emit_to_db=False,
        volumes=None)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


_CLUSTER_001 = {
    "narrative_mode": "in_medias_res", "scope_summary": "首块",
    "scene_storyboard": [{"scene": 0, "summary": "灾难开场"}],
    "foreshadowing_to_plant": ["伏笔A"],
    "research_ref": {
        "cache_path": "_数据库/.research_cache/inspiration_cluster_001_test.md",
        "anchors_used": ["anchor_A"],
        "research_topics": ["开场节奏"],
        "researcher_confidence": 0.91,
    },
}

# 旧一把梭形态的完整输出（骨架校验会丢弃其中 major_events；chunk 校验只取 major_events）
_VALID = {
    "story_destiny": {"final_image": "终局", "thematic_resolution": "主题"},
    "_metadata": {"rhythm_profile": "标准"},
    "volumes": [{"vol": 1, "title": "第一卷", "phase": "起", "volume_core_conflict": "冲突",
                 "volume_thread": "线索", "volume_finale_signal": "信号"}],
    "major_events": [{"id": "ME-V1-01", "volume": 1, "title": "走向1",
                      "is_volume_finale": True, "stakes_delta": "起点"}],
    "cluster_001": _CLUSTER_001,
}

_SKELETON_2V = {
    "story_destiny": {"final_image": "终局", "thematic_resolution": "主题"},
    "_metadata": {"rhythm_profile": "标准"},
    "volumes": [
        {"vol": 1, "title": "第一卷", "phase": "起", "volume_core_conflict": "冲突1",
         "volume_thread": "线索1", "volume_finale_signal": "信号1"},
        {"vol": 2, "title": "第二卷", "phase": "承", "volume_core_conflict": "冲突2",
         "volume_thread": "线索2", "volume_finale_signal": "信号2"},
    ],
    "cluster_001": _CLUSTER_001,
}

_SKELETON_1V = {**_SKELETON_2V, "volumes": [_SKELETON_2V["volumes"][0]]}


def _chunk(vol: int, n_me: int = 2) -> dict:
    """产一个合法单卷 ME 池 chunk（末个 is_volume_finale·每次调用产新对象防原地污染）。"""
    return {"volume": vol, "major_events": [
        {"id": f"ME-V{vol}-{i:02d}", "volume": vol, "title": f"走向{vol}-{i}",
         "is_volume_finale": i == n_me, "stakes_delta": "递增",
         "prerequisites": [], "physical_evidence": []} for i in range(1, n_me + 1)]}


def _fake_generate_sequence(*payloads):
    """序列返回：每次调用吐下一个 payload(dict→JSON / str→原文)·末个之后复用末个。
    带 finish_reason='stop'(真实 GenResult 有·诊断 dump 读它)。gen._calls 供 mock 计数。"""
    calls = {"n": 0}

    def gen(loader, system, user, **kw):
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        p = payloads[i]
        text = p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)
        return types.SimpleNamespace(text=text, finish_reason="stop")
    gen._calls = calls
    return gen


def _run(proj: Path, fake, **kw):
    """monkeypatch llm_transport.generate 跑 _run_volume_arc（还原·防污染后续测试）。"""
    _orig = llm_transport.generate
    llm_transport.generate = fake
    try:
        return gva._run_volume_arc(_args(project=str(proj), emit_to_db=True, **kw))
    finally:
        llm_transport.generate = _orig


def _mkproj(tmp: str) -> Path:
    proj = Path(tmp)
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_wal(proj: Path, name: str, doc) -> Path:
    wal = proj / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    p = wal / name
    text = doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False, indent=2)
    p.write_text(text, encoding="utf-8")
    return p


# ════════ prompt 层（北极星⑤ + 权威分离）════════

def test_volume_arc_skeleton_prompt_no_schema_coercion():
    """北极星⑤：骨架 prompt 含脚手架 + 作者档优先 + 不锁章铁律·不含枚举硬约束·
    ME 池明确「逐卷另行生成」（P2 分卷纪律）。"""
    system, user = gva.build_volume_arc_skeleton_prompt(
        selected_card={"title": "卡", "logline": "梗概"}, cluster_count=8,
        framework="三幕", rhythm="标准", author_block="（作者档）", research_text="")
    for kw in ("story_destiny", "volumes", "cluster_001",
               "in_medias_res", "research_ref", "第一权威", "free_notes"):
        assert kw in system, f"脚手架缺 {kw}"
    assert "绝不写" in system and "target_chapter" in system  # 不锁章铁律
    assert "逐卷另行生成" in system, "骨架 prompt 须声明 ME 池分卷另行生成（不在骨架里产）"
    for bad in ("phase 必须从", "finale_signal 必须含", "ME 数量必须"):
        assert bad not in system, f"违北极星⑤·硬编码枚举规训: {bad}"


def test_volume_arc_story_content_from_card_not_style_ref():
    """🔴 2026-06-28 W6：故事内容 vs 笔法 权威分离回归锁（治污染 bug）。

    实证翻车：诡秘风格档含「沙盒天道/燧明部」示例·模型偷懒抄成大纲·无视「钟楼弃儿」灵感卡。
    修：prompt 必须明确『故事内容(题材/人物/世界/走向)唯一来源=灵感卡·作者档只学笔法·
    示例里的人名/地名/情节是笔法演示禁当故事搬』。本测试锁该指令不被回退掉。"""
    system, user = gva.build_volume_arc_skeleton_prompt(
        selected_card={"title": "钟楼弃儿", "logline": "守夜人捡到未来遗嘱"}, cluster_count=10,
        framework="Save the Cat", rhythm="混合",
        author_block="（风格档·含「沙盒天道」示例）", research_text="")
    assert "唯一来源" in system, "缺『故事内容唯一来源=灵感卡』指令"
    assert "只学笔法" in system, "缺『作者风格档只学笔法』分离"
    assert ("禁止" in system or "绝对禁止" in system), "缺禁止从风格档示例搬故事的护栏"
    assert "钟楼弃儿" in user, "选定卡未进 user prompt"


def test_me_pool_prompt_scaffold_and_separation():
    """单卷 ME 池 prompt：结构契约（id 格式/volume 标号/finale）+ 软提示不硬锁 +
    权威分离 + 不锁章·前卷 digest 进 user（衔接防撞 id）。"""
    system, user = gva.build_volume_me_pool_prompt(
        volume=_SKELETON_2V["volumes"][1],
        volumes_digest=gva._volumes_digest(_SKELETON_2V["volumes"]),
        story_destiny=_SKELETON_2V["story_destiny"], cluster_count=8,
        author_block="（作者档）", selected_card={"title": "钟楼弃儿"},
        prev_me_digest="- 第 1 卷已定 2 个小走向：走向1-1、走向1-2")
    for kw in ("major_events", "is_volume_finale", "stakes_delta", "ME-V2-",
               "只学笔法", "软提示"):
        assert kw in system, f"ME 池 prompt 缺 {kw}"
    assert "绝不写" in system and "target_chapter" in system  # 不锁章铁律（卷 chunk 同守）
    for bad in ("phase 必须从", "finale_signal 必须含", "ME 数量必须"):
        assert bad not in system, f"违北极星⑤·硬编码枚举规训: {bad}"
    assert "钟楼弃儿" in user and "第二卷" in user, "灵感卡/本卷骨架未进 user prompt"
    assert "走向1-1" in user, "前卷 ME digest 未进 user prompt"


# ════════ 确定性结构层单元 ════════

def test_normalize_volume_chunk_backfill_and_reject():
    """ME 缺 volume → 确定性回填本卷号（C19 同源结构修补）；卷号错位/缺 id/chunk 内撞 id
    → 判破损（None·触发重试/重生成）。"""
    ok, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "title": "无volume字段"}]}, 3)
    assert ok is not None and ok["major_events"][0]["volume"] == 3, diag
    bad_vol, _ = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "volume": 4}]}, 3)
    assert bad_vol is None, "卷号错位应判破损"
    no_id, _ = gva._normalize_volume_chunk({"major_events": [{"title": "缺id"}]}, 3)
    assert no_id is None, "ME 缺 id 应判破损（id 是合并去重锚）"
    dup_in, _ = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01"}, {"id": "ME-V3-01"}]}, 3)
    assert dup_in is None, "chunk 内 id 重复应判破损"


def test_merge_equivalent_to_monolithic_structure():
    """确定性合并产物与旧一把梭结构等价：顶层键一致·ME 卷号升序拼接·_wal_meta 不泄漏。"""
    skeleton = {**_SKELETON_2V, "_wal_meta": {"unit": "skeleton"}}
    data, dups = gva._merge_volume_arc_chunks(skeleton, {1: _chunk(1), 2: _chunk(2)})
    assert not dups
    mono = dict(_SKELETON_2V)
    mono["major_events"] = _chunk(1)["major_events"] + _chunk(2)["major_events"]
    assert set(data) == set(mono), "合并产物顶层键须与一把梭等价"
    assert data["major_events"] == mono["major_events"], "ME 须按卷号升序确定性拼接"
    assert "_wal_meta" not in data, "_wal_meta 不得泄漏进最终产物"
    # emit 两条路径产完全一致的 大势卡.json（结构等价的最终裁决）
    with tempfile.TemporaryDirectory() as ta, tempfile.TemporaryDirectory() as tb:
        gva._emit_volume_arc_to_db(Path(ta), json.loads(json.dumps(data)),
                                  rhythm="标准", framework="三幕")
        gva._emit_volume_arc_to_db(Path(tb), json.loads(json.dumps(mono)),
                                  rhythm="标准", framework="三幕")
        ja = (Path(ta) / "_数据库" / "大势卡.json").read_text(encoding="utf-8")
        jb = (Path(tb) / "_数据库" / "大势卡.json").read_text(encoding="utf-8")
        assert ja == jb, "chunk 合并 emit 与一把梭 emit 的大势卡须逐字节一致"


# ════════ 主流程：正常产出 / 破损 block / 重试自愈 ════════

def test_volume_arc_emit_splits_two_files():
    """骨架 + 单卷 chunk（2 次调用）→ 合并 emit 大势卡/事件簇·WAL 双落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        card = proj / "card.json"
        card.write_text(json.dumps({"answer": {"title": "卡"}}, ensure_ascii=False),
                        encoding="utf-8")
        fake = _fake_generate_sequence(_VALID)   # 骨架/chunk 都能吃 _VALID
        rc = _run(proj, fake, selected_card=str(card))
        assert rc == 0
        assert fake._calls["n"] == 2, "应恰 2 次调用（骨架 + 第 1 卷 chunk）"
        wal = proj / "_数据库" / ".wal"
        assert (wal / "volume_arc_skeleton.json").exists(), "骨架 WAL 未落盘"
        assert (wal / "volume_arc_v1.json").exists(), "第 1 卷 chunk WAL 未落盘"
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        assert len(major["volumes"]) == 1 and len(major["major_events"]) == 1
        assert major["major_events"][0]["status"] == "pending"
        cluster = json.loads((proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
        c0 = cluster["clusters"][0]
        assert c0["cluster_id"] == "cluster_001" and c0["narrative_mode"] == "in_medias_res"
        assert c0["status"] == "pending"  # step6 cluster_choice_apply 才改 in_progress
        assert len(c0["scene_storyboard"]) == 1
        assert c0["research_ref"]["cache_path"].endswith("inspiration_cluster_001_test.md")
        assert c0["research_ref"]["anchors_used"] == ["anchor_A"]


def test_volume_arc_broken_json_block_nonzero():
    """契约3：结构破损(缺顶层键) block → 非零退出(不静默吞断链)。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        rc = _run(proj, _fake_generate_sequence({"volumes": []}))  # 缺键+卷空
        assert rc == 1, "结构破损应 block 非零退出"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "破损不该落盘"


def test_volume_arc_retry_recovers_from_transient_break():
    """🔴 真机 e2e 抓修 2026-06-15：偶发非 JSON/限速截断 → parse 失败·重试自愈不 block。
    序列：骨架破损 → 骨架合法 → 第 1 卷 chunk 合法（复用末 payload）→ rc==0 + 落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        fake = _fake_generate_sequence({"volumes": []}, _VALID)  # 破损 → 合法
        rc = _run(proj, fake)
        assert rc == 0, "第二次合法应自愈 rc==0(不 block)"
        assert fake._calls["n"] == 3, "骨架重试 1 次 + 骨架成功 + 1 卷 chunk = 共 3 次调用"
        assert (proj / "_数据库" / "大势卡.json").exists(), "自愈后应落盘"


def test_volume_arc_retry_exhausted_still_blocks():
    """3 次全破损 → block exit 1(确定性破损不无限重试·不静默吞断链)·留 raw 诊断证据。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        fake = _fake_generate_sequence({"volumes": []})  # 每次都破损
        rc = _run(proj, fake)
        assert rc == 1, "3 次全破损应 block 非零退出"
        assert fake._calls["n"] == 3, "应尝试满 3 次(MAX_VOL_ARC_TRIES)"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "破损不落盘"
        dbg = proj / "_数据库" / ".wal" / "volume_arc_block_debug.txt"
        assert dbg.exists(), "block 应留 raw 诊断证据(失败必记录学习·非静默吞证据)"


def test_volume_arc_dry_run_no_api():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        rc = gva._run_volume_arc(_args(project=str(proj), dry_run=True))
        assert rc == 0


# ════════ P2：分卷 WAL 断点续跑 ════════

def test_multi_volume_chunk_wals_written_and_merged():
    """逐卷 WAL 落盘：2 卷骨架 → v1/v2 chunk 各自落 WAL（schema 合法的部分产物）→
    合并 emit 的 ME 按卷号升序、全量保留。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        fake = _fake_generate_sequence(_SKELETON_2V, _chunk(1), _chunk(2))
        rc = _run(proj, fake)
        assert rc == 0 and fake._calls["n"] == 3
        wal = proj / "_数据库" / ".wal"
        for name in ("volume_arc_skeleton.json", "volume_arc_v1.json", "volume_arc_v2.json"):
            assert (wal / name).exists(), f"缺 WAL: {name}"
        v1 = json.loads((wal / "volume_arc_v1.json").read_text(encoding="utf-8"))
        norm, diag = gva._normalize_volume_chunk(v1, 1)
        assert norm is not None, f"落盘的卷 WAL 必须 schema 合法（可独立续跑）: {diag}"
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        ids = [m["id"] for m in major["major_events"]]
        assert ids == ["ME-V1-01", "ME-V1-02", "ME-V2-01", "ME-V2-02"], ids
        assert len(major["volumes"]) == 2


def test_resume_skips_completed_volume_wals():
    """中断后续跑：已存在且合法的骨架 WAL + v1 WAL 直接复用（mock 计数=1·只生成 v2）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, "volume_arc_skeleton.json", _SKELETON_2V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        fake = _fake_generate_sequence(_chunk(2))
        rc = _run(proj, fake)
        assert rc == 0
        assert fake._calls["n"] == 1, "已完成卷不得重复生成（续跑只补缺卷）"
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        ids = [m["id"] for m in major["major_events"]]
        assert ids == ["ME-V1-01", "ME-V1-02", "ME-V2-01", "ME-V2-02"], ids


def test_corrupt_volume_wal_regenerated():
    """损坏的卷 WAL（非 JSON）→ 判破损重生成（不复用坏产物）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, "volume_arc_skeleton.json", _SKELETON_1V)
        _write_wal(proj, "volume_arc_v1.json", "{{{ 不是 JSON")
        fake = _fake_generate_sequence(_chunk(1))
        rc = _run(proj, fake)
        assert rc == 0
        assert fake._calls["n"] == 1, "损坏 WAL 应触发恰 1 次重生成"
        doc = json.loads((proj / "_数据库" / ".wal" / "volume_arc_v1.json")
                         .read_text(encoding="utf-8"))
        norm, diag = gva._normalize_volume_chunk(doc, 1)
        assert norm is not None, f"重生成后的 WAL 须合法: {diag}"
        assert (proj / "_数据库" / "大势卡.json").exists()


def test_idempotent_rerun_zero_llm_calls():
    """幂等续跑：全部 WAL 已合法 → 重跑 0 次 LLM 调用·大势卡逐字节一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        rc = _run(proj, _fake_generate_sequence(_SKELETON_2V, _chunk(1), _chunk(2)))
        assert rc == 0
        p_major = proj / "_数据库" / "大势卡.json"
        first = p_major.read_text(encoding="utf-8")
        fake2 = _fake_generate_sequence({"绝不应被调用": True})
        rc2 = _run(proj, fake2)
        assert rc2 == 0
        assert fake2._calls["n"] == 0, "全 WAL 命中时重跑不得再调 LLM（幂等）"
        assert p_major.read_text(encoding="utf-8") == first, "幂等重跑产物须逐字节一致"


def test_me_id_duplicate_across_volumes_hard_error():
    """合并 ME id 去重校验：跨卷重复 id → 硬报错非零退出·不静默覆盖·不落大势卡·
    隔离后到卷 WAL 为 .dup_broken（重跑重生成该卷·不死锁）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, "volume_arc_skeleton.json", _SKELETON_2V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        _write_wal(proj, "volume_arc_v2.json", {"volume": 2, "major_events": [
            {"id": "ME-V1-01", "volume": 2, "title": "撞id走向", "is_volume_finale": True}]})
        fake = _fake_generate_sequence({"绝不应被调用": True})
        rc = _run(proj, fake)
        assert rc == 1, "跨卷 ME id 重复应硬报错非零退出"
        assert fake._calls["n"] == 0
        assert not (proj / "_数据库" / "大势卡.json").exists(), "撞 id 不得静默覆盖落盘"
        wal = proj / "_数据库" / ".wal"
        assert (wal / "volume_arc_v2.json.dup_broken").exists(), "撞 id 卷 WAL 应被隔离"
        assert not (wal / "volume_arc_v2.json").exists(), "隔离后原 WAL 应移走（重跑重生成）"
        assert (wal / "volume_arc_v1.json").exists(), "首现卷 WAL 保留"


def test_single_volume_failure_fails_step_keeps_wals():
    """失败语义：v2 三次全破损 → 整 step 失败 rc==1（required 不降级），
    但骨架 + v1 WAL 保留（续跑资产）·大势卡不落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        fake = _fake_generate_sequence(_SKELETON_2V, _chunk(1), {"volumes": []})
        rc = _run(proj, fake)
        assert rc == 1, "单卷失败=整 step 失败"
        assert fake._calls["n"] == 5, "骨架1 + v1成功1 + v2失败3 = 5 次调用"
        wal = proj / "_数据库" / ".wal"
        assert (wal / "volume_arc_skeleton.json").exists(), "骨架 WAL 应保留供续跑"
        assert (wal / "volume_arc_v1.json").exists(), "已完成卷 WAL 应保留供续跑"
        assert not (wal / "volume_arc_v2.json").exists(), "失败卷不得落 WAL"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "未齐全不得合并落盘"


def test_volumes_debug_subset_no_merge():
    """--volumes 内部调试子集：只生成指定卷·不触发合并落库（plan 不用此参数）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, "volume_arc_skeleton.json", _SKELETON_2V)
        fake = _fake_generate_sequence(_chunk(1))
        rc = _run(proj, fake, volumes="1")
        assert rc == 0
        assert fake._calls["n"] == 1, "子集只生成第 1 卷"
        wal = proj / "_数据库" / ".wal"
        assert (wal / "volume_arc_v1.json").exists()
        assert not (wal / "volume_arc_v2.json").exists()
        assert not (proj / "_数据库" / "大势卡.json").exists(), "卷不齐全不得合并落库"


def test_emit_writes_rhythm_to_user_pref():
    """轮次4 契约审计回归：用户 pause 答的节奏档必须落 用户偏好.json.rhythm_profile
    （cluster-write step6 data_flow 的 source·此前无 producer → splitter 永收「标准」）。"""
    import shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        data = {"story_destiny": {}, "volumes": [], "major_events": [
                    {"id": "ME-V1-01", "volume": 1, "is_volume_finale": True}],
                "cluster_001": {}, "_metadata": {}}
        gva._emit_volume_arc_to_db(tmp, data, rhythm="紧凑", framework="三幕")
        pref = json.loads((tmp / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        assert pref.get("rhythm_profile") == "紧凑"
        mj = json.loads((tmp / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        assert mj["_metadata"].get("rhythm_profile") == "紧凑"   # 确定性覆盖非 LLM 自觉
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
