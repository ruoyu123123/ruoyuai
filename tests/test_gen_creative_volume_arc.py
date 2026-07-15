#!/usr/bin/env python3
"""gen_creative volume_arc mode 测试（agent 亲笔单元 + 确定性验收合并·scene_jobs 范式）。

零 LLM：单元 JSON 由测试直接落 WAL（模拟 novel-outline-planner MODE=volume_arc_unit
亲笔产物）。验：
- 单元缺失/破损 → 写 volume_arc_jobs.json 任务清单（unit/输入材料路径/期望产物路径/
  输入 digest/诊断）+ exit 2=pending；合法 WAL 直接复用（幂等续跑）
- 骨架就绪后才列出逐卷单元 job；破损单元退回 pending 重写
- 全部单元合法 → 确定性合并 → emit 两文件（与一把梭结构等价）
- 合并 ME id 跨卷重复 → 硬报错 exit 1 + 隔离 .dup_broken（不静默覆盖）
- _metadata.cluster_count_per_volume 确定性覆盖（不靠 agent 自觉回填）
- 创作约束已迁 agent 合约（novel-outline-planner.md）：权威分离/结构契约回归锁
- 旧 LLM 生成管线清零（不兼容不降级）
"""
import json
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_creative_volume_arc as gva  # noqa: E402  volume_arc 实现

_AGENT_MD_PATH = _ROOT / ".claude" / "agents" / "novel-outline-planner.md"


def _args(**kw):
    a = types.SimpleNamespace(
        project=None, selected_card=None, cluster_count=8, framework="三幕",
        rhythm=None, style_ref=None, research=None, emit_to_db=True)
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
    """产一个合法单卷 ME 池单元（末个 is_volume_finale·每次调用产新对象防原地污染）。"""
    return {"volume": vol, "major_events": [
        {"id": f"ME-V{vol}-{i:02d}", "volume": vol, "title": f"走向{vol}-{i}",
         "is_volume_finale": i == n_me, "stakes_delta": "递增",
         "prerequisites": [], "physical_evidence": []} for i in range(1, n_me + 1)]}


def _mkproj(tmp: str) -> Path:
    proj = Path(tmp)
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _mkcard(proj: Path) -> Path:
    card = proj / "card.json"
    card.write_text(json.dumps({"answer": {"title": "钟楼弃儿"}}, ensure_ascii=False),
                    encoding="utf-8")
    return card


def _write_wal(proj: Path, name: str, doc) -> Path:
    wal = proj / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    p = wal / name
    text = doc if isinstance(doc, str) else json.dumps(doc, ensure_ascii=False, indent=2)
    p.write_text(text, encoding="utf-8")
    return p


def _run(proj: Path, **kw):
    kw.setdefault("selected_card", str(_mkcard(proj)))
    return gva._run_volume_arc(_args(project=str(proj), **kw))


def _manifest(proj: Path) -> dict:
    return json.loads((proj / "_数据库" / ".wal" / gva.VOLUME_ARC_JOBS_WAL)
                      .read_text(encoding="utf-8"))


def _jobs_by_unit(proj: Path) -> dict:
    return {j["unit"]: j for j in _manifest(proj)["jobs"]}


# ════════ 确定性结构层单元（单元验收唯一裁决·原样保留）════════

def test_normalize_volume_chunk_backfill_and_reject():
    """ME 缺 volume → 确定性回填本卷号（C19 同源结构修补）；卷号错位/缺 id/chunk 内撞 id
    → 判破损（None·job 退回 pending 重写）。"""
    ok, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "title": "无volume字段",
                           "is_volume_finale": True}]}, 3)
    assert ok is not None and ok["major_events"][0]["volume"] == 3, diag
    bad_vol, _ = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "volume": 4, "is_volume_finale": True}]}, 3)
    assert bad_vol is None, "卷号错位应判破损"
    no_id, _ = gva._normalize_volume_chunk(
        {"major_events": [{"title": "缺id", "is_volume_finale": True}]}, 3)
    assert no_id is None, "ME 缺 id 应判破损（id 是合并去重锚）"
    dup_in, _ = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True},
                          {"id": "ME-V3-01", "is_volume_finale": False}]}, 3)
    assert dup_in is None, "chunk 内 id 重复应判破损"


def test_normalize_volume_chunk_rejects_bad_finale_flag():
    """is_volume_finale 缺失/非 bool → 判破损（退回 pending·不放行到最终 emit 才炸
    未捕获 ValueError）。"""
    missing, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01"}]}, 3)
    assert missing is None, f"缺 is_volume_finale 应判破损: {diag}"
    non_bool, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": "true"}]}, 3)
    assert non_bool is None, f"is_volume_finale 字符串(非 bool) 应判破损: {diag}"


def test_normalize_volume_chunk_requires_exactly_one_finale():
    """本卷单元必须恰有一个 is_volume_finale=true（0 个或 ≥2 个都判破损·
    与 _normalize_me_pool 的「卷末恰一个」规则对齐）。"""
    zero, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": False},
                          {"id": "ME-V3-02", "is_volume_finale": False}]}, 3)
    assert zero is None, f"0 个 is_volume_finale=true 应判破损: {diag}"
    two, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True},
                          {"id": "ME-V3-02", "is_volume_finale": True}]}, 3)
    assert two is None, f"2 个 is_volume_finale=true 应判破损: {diag}"


def test_normalize_volume_chunk_rejects_bad_prerequisites_type():
    """prerequisites 非 string array（非 list / 含非字符串元素）→ 判破损。"""
    non_list, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True,
                           "prerequisites": "ME-V2-01"}]}, 3)
    assert non_list is None, f"prerequisites 非 list 应判破损: {diag}"
    bad_item, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True,
                           "prerequisites": [123]}]}, 3)
    assert bad_item is None, f"prerequisites 含非字符串元素应判破损: {diag}"


def test_normalize_volume_chunk_rejects_dangling_prerequisites():
    """prerequisites 引用本卷 + known_ids 之外的未知 id → 悬空引用判破损。"""
    dangling, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True,
                           "prerequisites": ["ME-V9-99"]}]}, 3)
    assert dangling is None, f"悬空 prerequisites 应判破损: {diag}"
    assert "悬空" in diag


def test_normalize_volume_chunk_prerequisites_may_reference_known_prior_volume():
    """prerequisites 允许回溯引用 known_ids（已完成前卷）的 ME id——跨卷衔接合法用法·
    不得被当成悬空引用误伤。"""
    ok, diag = gva._normalize_volume_chunk(
        {"major_events": [{"id": "ME-V3-01", "is_volume_finale": True,
                           "prerequisites": ["ME-V2-05"]}]}, 3,
        known_ids={"ME-V2-05"})
    assert ok is not None, f"回溯引用已完成前卷 id 不应判破损: {diag}"
    assert ok["major_events"][0]["prerequisites"] == ["ME-V2-05"]


def test_merge_equivalent_to_monolithic_structure():
    """确定性合并产物与一把梭结构等价：顶层键一致·ME 卷号升序拼接·_wal_meta 不泄漏。"""
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
        assert ja == jb, "单元合并 emit 与一把梭 emit 的大势卡须逐字节一致"


# ════════ 主流程：jobs pending / 续跑验收 / 合并 emit ════════

def test_fresh_run_registers_skeleton_job_and_pends():
    """全新项目：骨架单元缺失 → 任务清单只含 skeleton pending + exit 2（等 agent 补件）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        rc = _run(proj)
        assert rc == 2, "缺骨架单元应 exit 2=pending"
        m = _manifest(proj)
        assert m["contract"] == "volume_arc_jobs.v1"
        assert m["agent"] == "novel-outline-planner"
        assert m["agent_mode"] == "volume_arc_unit"
        assert m["params"] == {"cluster_count": 8, "framework": "三幕", "rhythm": "标准"}
        assert [j["unit"] for j in m["jobs"]] == ["skeleton"]
        job = m["jobs"][0]
        assert job["status"] == "pending" and "未落盘" in job["diag"]
        assert job["expected_output"].endswith(gva.VOLUME_ARC_SKELETON_WAL)
        assert job["inputs"]["selected_card"].endswith("card.json")
        assert isinstance(job["input_digest"], str) and len(job["input_digest"]) == 64
        assert not (proj / "_数据库" / "大势卡.json").exists(), "pending 不得落库"


def test_skeleton_ready_lists_volume_jobs_and_pends():
    """骨架单元合法（agent 已补件）→ 列出逐卷单元 job（含 skeleton/前卷 WAL 输入路径）
    → 仍 pending 等卷单元。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_2V)
        rc = _run(proj)
        assert rc == 2
        jobs = _jobs_by_unit(proj)
        assert set(jobs) == {"skeleton", "v1", "v2"}
        assert jobs["skeleton"]["status"] == "ready"
        for u in ("v1", "v2"):
            assert jobs[u]["status"] == "pending"
            assert jobs[u]["inputs"]["skeleton"].endswith(gva.VOLUME_ARC_SKELETON_WAL)
        assert jobs["v1"]["vol"] == 1 and jobs["v2"]["vol"] == 2
        assert jobs["v1"]["expected_output"].endswith("volume_arc_v1.json")


def test_all_units_ready_merges_and_emits():
    """全部单元合法（agent 亲笔 WAL 齐）→ 确定性合并 emit 大势卡/事件簇 + 清单全 ready。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_2V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        _write_wal(proj, "volume_arc_v2.json", _chunk(2))
        rc = _run(proj)
        assert rc == 0
        assert all(j["status"] == "ready" for j in _manifest(proj)["jobs"])
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        ids = [m["id"] for m in major["major_events"]]
        assert ids == ["ME-V1-01", "ME-V1-02", "ME-V2-01", "ME-V2-02"], ids
        assert len(major["volumes"]) == 2
        assert all(m["status"] == "pending" for m in major["major_events"])
        cluster = json.loads((proj / "_数据库" / "事件簇.json").read_text(encoding="utf-8"))
        c0 = cluster["clusters"][0]
        assert c0["cluster_id"] == "cluster_001" and c0["narrative_mode"] == "in_medias_res"
        assert c0["status"] == "pending"  # step6 cluster_choice_apply 才改 in_progress
        assert c0["research_ref"]["cache_path"].endswith("inspiration_cluster_001_test.md")
        assert c0["research_ref"]["anchors_used"] == ["anchor_A"]


def test_broken_skeleton_unit_goes_back_to_pending():
    """骨架单元破损（缺顶层键）→ 退回 pending + diag 带破损原因·不落库。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, {"volumes": []})
        rc = _run(proj)
        assert rc == 2
        job = _jobs_by_unit(proj)["skeleton"]
        assert job["status"] == "pending" and "破损" in job["diag"]
        assert not (proj / "_数据库" / "大势卡.json").exists()


def test_skeleton_world_seed_bad_ripple_rules_go_back_to_pending():
    """world_seed.ripple_rules 契约破损（2026-07-15 衔石与朝云真机：文本触发词错标 fate_event=
    死规则 / op-note 形态 ripple=apply 硬炸）→ 骨架单元退回 pending 重写·不落库。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        bad_seed = {**_SKELETON_1V, "world_seed": {"ripple_rules": [
            {"id": "RR_001", "trigger_type": "fate_event",
             "trigger_match": "神农以身试毒/尝百草中毒",
             "ripples": [{"op": "narrative", "target": "factions_state.部族", "note": "旧形态"}]},
        ]}}
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, bad_seed)
        rc = _run(proj)
        assert rc == 2
        job = _jobs_by_unit(proj)["skeleton"]
        assert job["status"] == "pending" and "world_seed.ripple_rules" in job["diag"]
        assert not (proj / "_数据库" / "涟漪规则.json").exists()


def test_skeleton_world_seed_canonical_ripple_rules_accepted():
    """world_seed.ripple_rules 全 canonical（minor_event 文本词 / fate_event ME id / auto_tick
    every_cluster + narrative/delta 形态）→ 骨架单元验收通过。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        good_seed = {**_SKELETON_1V, "world_seed": {"ripple_rules": [
            {"id": "RR_001", "trigger_type": "minor_event",
             "trigger_match": "神农以身试毒/尝百草中毒",
             "ripples": [{"target": "factions_state.部族.stability", "delta": -8},
                         {"narrative": "死亡钟摆前移"}]},
            {"id": "RR_F", "trigger_type": "fate_event", "trigger_match": "ME-V1-01",
             "ripples": [{"narrative": "大事件落地"}]},
        ]}}
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, good_seed)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        assert _run(proj) == 0
        rr = json.loads((proj / "_数据库" / "涟漪规则.json").read_text(encoding="utf-8"))
        assert [r["id"] for r in rr["ripple_rules"]] == ["RR_001", "RR_F"]


def test_broken_chunk_unit_goes_back_to_pending():
    """卷单元破损（非 JSON）→ 退回 pending（重 spawn agent 覆写 expected_output）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_1V)
        _write_wal(proj, "volume_arc_v1.json", "{{{ 不是 JSON")
        rc = _run(proj)
        assert rc == 2
        job = _jobs_by_unit(proj)["v1"]
        assert job["status"] == "pending" and "破损" in job["diag"]
        # agent 补件（模拟）→ 重跑续跑验收通过
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        assert _run(proj) == 0
        assert (proj / "_数据库" / "大势卡.json").exists()


def test_idempotent_rerun_after_complete():
    """幂等续跑：全部单元合法 → 重跑仍 exit 0·大势卡逐字节一致·单元 WAL 不被改写。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_2V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        p2 = _write_wal(proj, "volume_arc_v2.json", _chunk(2))
        assert _run(proj) == 0
        p_major = proj / "_数据库" / "大势卡.json"
        first = p_major.read_text(encoding="utf-8")
        wal_bytes = p2.read_bytes()
        assert _run(proj) == 0
        assert p_major.read_text(encoding="utf-8") == first, "幂等重跑产物须逐字节一致"
        assert p2.read_bytes() == wal_bytes, "合法单元 WAL 不得被改写（agent 产物只读复用）"


def test_me_id_duplicate_across_volumes_hard_error():
    """合并 ME id 去重校验：跨卷重复 id → 硬报错 exit 1·不静默覆盖·不落大势卡·
    隔离后到卷 WAL 为 .dup_broken（重跑该卷退回 pending 重写·不死锁）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_2V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        _write_wal(proj, "volume_arc_v2.json", {"volume": 2, "major_events": [
            {"id": "ME-V1-01", "volume": 2, "title": "撞id走向", "is_volume_finale": True}]})
        rc = _run(proj)
        assert rc == 1, "跨卷 ME id 重复应硬报错非零退出"
        assert not (proj / "_数据库" / "大势卡.json").exists(), "撞 id 不得静默覆盖落盘"
        wal = proj / "_数据库" / ".wal"
        assert (wal / "volume_arc_v2.json.dup_broken").exists(), "撞 id 卷 WAL 应被隔离"
        assert not (wal / "volume_arc_v2.json").exists(), "隔离后原 WAL 应移走"
        assert (wal / "volume_arc_v1.json").exists(), "首现卷 WAL 保留"
        # 重跑：v2 退回 pending（等 agent 重写）
        assert _run(proj) == 2
        assert _jobs_by_unit(proj)["v2"]["status"] == "pending"


def test_pending_units_require_readable_card():
    """有待补单元但选中灵感卡不可读 → exit 1（agent 无故事内容来源·先修输入）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        rc = gva._run_volume_arc(_args(project=str(proj), selected_card=None))
        assert rc == 1


def test_complete_units_do_not_need_card():
    """全部单元已合法时缺灵感卡不阻断（续跑合并无需再读故事来源）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, _SKELETON_1V)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        rc = gva._run_volume_arc(_args(project=str(proj), selected_card=None))
        assert rc == 0


def test_missing_project_exit1():
    assert gva._run_volume_arc(_args(project=None)) == 1


def test_metadata_cluster_count_deterministic_stamp():
    """_metadata.cluster_count_per_volume 由 CLI 参数确定性覆盖（不靠 agent 回填·
    cluster_emergence_engine 消费此键判卷末阈值）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        sk = json.loads(json.dumps(_SKELETON_1V))
        sk["_metadata"] = {"rhythm_profile": "标准", "cluster_count_per_volume": 99}
        _write_wal(proj, gva.VOLUME_ARC_SKELETON_WAL, sk)
        _write_wal(proj, "volume_arc_v1.json", _chunk(1))
        assert _run(proj, cluster_count=7) == 0
        major = json.loads((proj / "_数据库" / "大势卡.json").read_text(encoding="utf-8"))
        assert major["_metadata"]["cluster_count_per_volume"] == 7


def test_emit_writes_rhythm_to_user_pref():
    """契约回归：用户 pause 答的节奏档必须落 用户偏好.json.rhythm_profile
    （cluster-write step6 data_flow 的 source·缺 producer 则 splitter 永收「标准」）。"""
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
        assert mj["_metadata"].get("rhythm_profile") == "紧凑"   # 确定性覆盖非 agent 自觉
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolve_author_inputs_two_layouts():
    """作者档路径解析：_数据库/作者风格.json 优先·项目根兜底·style_ref 存在才收。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _mkproj(tmp)
        assert gva._resolve_author_inputs(proj, None) == (None, None)
        (proj / "作者风格.json").write_text("{}", encoding="utf-8")
        prof, _ = gva._resolve_author_inputs(proj, None)
        assert prof and prof.endswith("作者风格.json")
        (proj / "_数据库" / "作者风格.json").write_text("{}", encoding="utf-8")
        prof2, _ = gva._resolve_author_inputs(proj, str(proj / "不存在.md"))
        assert prof2 and "_数据库" in prof2
        skill_file = proj / "skill.md"
        skill_file.write_text("# s", encoding="utf-8")
        _, skill = gva._resolve_author_inputs(proj, str(skill_file))
        assert skill and skill.endswith("skill.md")


# ════════ 创作约束已迁 agent 合约（novel-outline-planner.md）·回归锁 ════════

def _agent_md() -> str:
    return _AGENT_MD_PATH.read_text(encoding="utf-8")


def test_agent_contract_story_content_authority_separation():
    """🔴 故事内容 vs 笔法 权威分离回归锁（feedback_volume_arc_style_ref_story_contamination·
    防风格档示例故事污染大纲）。约束已从 prompt 迁入 agent 合约——锁合约文本不被回退掉。"""
    md = _agent_md()
    assert "唯一来源" in md, "缺『故事内容唯一来源=灵感卡』指令"
    assert "只学笔法" in md, "缺『作者风格档只学笔法』分离"
    assert "绝对禁止" in md, "缺禁止从风格档示例搬故事的护栏"
    assert "沙盒天道" in md, "实证翻车案例（诡秘示例污染）应保留在合约里当警示"


def test_agent_contract_volume_arc_unit_structure():
    """volume_arc_unit 合约段：单元产物结构契约 + 不锁章铁律 + jobs 消费方式。"""
    md = _agent_md()
    for kw in ("volume_arc_unit", "UNIT", "JOBS_MANIFEST", "OUTPUT_PATH",
               "story_destiny", "volume_core_conflict", "volume_thread",
               "volume_finale_signal", "in_medias_res",
               "ME-V<N>-<序>", "is_volume_finale", "stakes_delta", "prerequisites",
               "prev_chunk_wals", "reference_patterns_block"):
        assert kw in md, f"volume_arc_unit 合约缺 {kw}"
    assert "target_chapter_count" in md and "绝不写" in md, "缺不锁章铁律"
    assert "不输出 `major_events`" in md, "骨架单元须声明 ME 池逐卷另行亲笔"


def test_agent_contract_brainstorm_constraints():
    """brainstorm 合约段：灵感卡硬约束（长度上限/卷骨架/调研来源落地）齐全。"""
    md = _agent_md()
    for kw in ("brainstorm", "TOPIC", "RESEARCH_PATH", "OUTPUT_PATH",
               "logline ≤50 字", "≤100 字", "5-6 卷", "source_refs",
               "inspiration_cards.json"):
        assert kw in md, f"brainstorm 合约缺 {kw}"
    assert "真实存在于调研缓存" in md or "逐字复制" in md, "缺 source_refs 落地校验承诺"


def test_agent_contract_no_enum_coercion():
    """北极星⑤：合约给脚手架+字段语义·不硬编码枚举规训（惊悚乐园流水账覆辙）。"""
    md = _agent_md()
    for bad in ("phase 必须从", "finale_signal 必须含", "ME 数量必须"):
        assert bad not in md, f"违北极星⑤·硬编码枚举规训: {bad}"


def test_no_llm_pipeline_left_in_module():
    """旧 LLM 生成管线清零（不兼容不降级·旧路径删干净）。"""
    for gone in ("build_volume_arc_skeleton_prompt", "build_volume_me_pool_prompt",
                 "_gen_volume_arc_unit", "_parse_volumes_arg", "MAX_VOL_ARC_TRIES",
                 "_dump_volume_arc_debug", "_volumes_digest", "_prev_me_digest"):
        assert not hasattr(gva, gone), f"旧 LLM 管线残留: {gone}"
    src = (_ROOT / "core" / "scripts" / "gen_creative_volume_arc.py").read_text(encoding="utf-8")
    for token in ("llm_transport", "GenModelLoader", "dry_run", "author_block",
                  "volume_arc_block_debug"):
        assert token not in src, f"gen_creative_volume_arc.py 残留旧管线引用: {token}"


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
