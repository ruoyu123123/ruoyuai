"""cross_cluster_data_consumption_aggregate 确定性单元测试（纯函数·零 LLM·零联网）。

钉死「数据声明 vs 实际写入」四类对账扫描的确定性行为：
- _extract_ids：list[dict] / list[str] 归一成 id 集合（防 M7 误报回归）
- get_chapters：第N章 目录解析 + last_n 截断
- scan_aspect_continuity：连续 ≥3 章未呼应 → advisory（ledger 路径）
- scan_clock_addressing：urgent clock（remaining ≤2）近 2 章未提 → warning
- scan_heart_event_consistency：consumed reveal 后 NPC 出现但关键词缺失 → advisory（磁盘路径）
- scan_fate_dice_consumption：未声明 → warning；evidence 命中率 <0.3 → advisory

被测脚本依赖 cluster_summary_reader，但本测试只直接调四个 scan_* + 两个 helper，
传入构造好的 ledger_by_ch / 磁盘文件，不触发 LLM / 网络 / cluster 模式分支。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_data_consumption_aggregate as mod  # noqa: E402


# ---------------------------------------------------------------------------
# 磁盘脚手架
# ---------------------------------------------------------------------------

def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "章节").mkdir(parents=True, exist_ok=True)
    return proj


def _write_db(proj: Path, name: str, data: dict) -> None:
    (proj / "_数据库" / name).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_chapter(proj: Path, ch: int, text: str = "", changes: dict | None = None) -> None:
    d = proj / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    if text:
        (d / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")
    if changes is not None:
        (d / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# _extract_ids —— 归一 helper（M7 修复点）
# ---------------------------------------------------------------------------

def test_extract_ids_mixed_dict_and_str():
    """list 里 dict（取首个命中 id_key）+ str（直收）+ 非法元素（忽略）混合。"""
    seq = [
        {"aspect_id": "a1", "extra": 1},   # dict → 取 aspect_id
        "a2",                               # str  → 直收
        {"id": "a3"},                       # dict → fallback 到 id
        {"nope": "x"},                      # dict 无任何 id_key → 忽略
        "",                                 # 空 str → 忽略
        123,                                # 非 str/dict → 忽略
    ]
    out = mod._extract_ids(seq, "aspect_id", "id")
    assert out == {"a1", "a2", "a3"}


def test_extract_ids_first_key_wins():
    """多个 id_key 时取第一个命中的，不重复收。"""
    seq = [{"aspect_id": "primary", "id": "secondary"}]
    out = mod._extract_ids(seq, "aspect_id", "id")
    assert out == {"primary"}


def test_extract_ids_non_list_returns_empty():
    """非 list/tuple（None / dict / str）→ 空集合，不崩。"""
    assert mod._extract_ids(None, "id") == set()
    assert mod._extract_ids({"id": "x"}, "id") == set()
    assert mod._extract_ids("a1", "id") == set()


# ---------------------------------------------------------------------------
# get_chapters —— 目录名解析 + last_n 截断
# ---------------------------------------------------------------------------

def test_get_chapters_parse_and_last_n():
    """解析「第NNN章」目录为整数，升序排序，last_n 截最后 N 个。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        for ch in (3, 1, 12, 5):
            _write_chapter(proj, ch)
        # 干扰目录（非 第N章 格式）不应被收
        (proj / "章节" / "cluster_001_draft").mkdir()
        assert mod.get_chapters(proj, last_n=10) == [1, 3, 5, 12]
        assert mod.get_chapters(proj, last_n=2) == [5, 12]


def test_get_chapters_empty_when_none():
    """无任何章节目录 → 空列表（驱动 main 的 [SKIP] 分支）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        assert mod.get_chapters(proj) == []


# ---------------------------------------------------------------------------
# A. scan_aspect_continuity
# ---------------------------------------------------------------------------

def _aspect_db(acquired_ch: int = 1) -> dict:
    return {
        "characters": {
            "陆参": {
                "active_aspects": [
                    {"aspect_id": "scar_01", "label": "断指之痛",
                     "acquired_ch": acquired_ch,
                     "narrative_constraints": ["右手缺指难以握剑"],
                     "emotional_triggers": ["看到刀光"]},
                ]
            }
        }
    }


def test_aspect_continuity_streak_triggers_advisory():
    """ledger 三章都没呼应 scar_01 → 连续 ≥3 → 1 条 ASPECT_NOT_ADDRESSED advisory。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "角色烙印.json", _aspect_db(acquired_ch=1))
        ledger = {1: {"aspects_addressed": []},
                  2: {"aspects_addressed": []},
                  3: {"aspects_addressed": []}}
        out = mod.scan_aspect_continuity(proj, [1, 2, 3], ledger_by_ch=ledger)
        assert len(out) == 1
        f = out[0]
        assert f["code"] == "ASPECT_NOT_ADDRESSED"
        assert f["severity"] == "advisory"
        assert f["aspect_id"] == "scar_01"
        assert f["current_chapter_physical"] == 3


def test_aspect_continuity_addressed_resets_streak():
    """中间章呼应（list[dict] 形态）→ streak 重置 → 不到 3 → 0 finding。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "角色烙印.json", _aspect_db(acquired_ch=1))
        ledger = {1: {"aspects_addressed": []},
                  2: {"aspects_addressed": [{"aspect_id": "scar_01"}]},  # 命中
                  3: {"aspects_addressed": []}}
        out = mod.scan_aspect_continuity(proj, [1, 2, 3], ledger_by_ch=ledger)
        assert out == []


def test_aspect_continuity_fewer_than_three_relevant_chapters():
    """relevant_chs < 3 直接 continue → 不可能触发。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "角色烙印.json", _aspect_db(acquired_ch=1))
        ledger = {1: {"aspects_addressed": []}, 2: {"aspects_addressed": []}}
        out = mod.scan_aspect_continuity(proj, [1, 2], ledger_by_ch=ledger)
        assert out == []


def test_aspect_continuity_missing_db_returns_empty():
    """角色烙印.json 不存在 → 空，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        assert mod.scan_aspect_continuity(proj, [1, 2, 3], ledger_by_ch={}) == []


def test_aspect_continuity_disk_fallback_text_hit():
    """无 ledger → 回退磁盘：正文含 narrative_constraints 关键词 → 命中不报。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "角色烙印.json", _aspect_db(acquired_ch=1))
        # 正文含 "缺指"（出自 constraint "右手缺指难以握剑"）→ text_hit
        for ch in (1, 2, 3):
            _write_chapter(proj, ch, text="他低头看着右手缺指的伤。", changes={})
        out = mod.scan_aspect_continuity(proj, [1, 2, 3], ledger_by_ch=None)
        assert out == []


# ---------------------------------------------------------------------------
# B. scan_clock_addressing
# ---------------------------------------------------------------------------

def _clock_db(ticks: int, max_v: int = 6, status: str = "active") -> dict:
    return {"clocks": [{"clock_id": "doom_clock", "label": "末日倒计时",
                        "status": status, "ticks": ticks, "max": max_v,
                        "trigger_on_max": "城破"}]}


def test_clock_urgent_ignored_warning():
    """urgent（remaining = max-ticks = 1 ≤2）+ 近 2 章都没提 → warning。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "时钟表.json", _clock_db(ticks=5, max_v=6))  # remaining=1
        ledger = {1: {"clocks_addressed": []}, 2: {"clocks_addressed": []}}
        out = mod.scan_clock_addressing(proj, [1, 2], ledger_by_ch=ledger)
        assert len(out) == 1
        f = out[0]
        assert f["code"] == "URGENT_CLOCK_IGNORED"
        assert f["severity"] == "warning"
        assert f["remaining"] == 1
        assert f["checked_chs"] == [1, 2]


def test_clock_addressed_recently_no_warning():
    """近 2 章之一提到了（list[str] 形态）→ 不报。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "时钟表.json", _clock_db(ticks=5, max_v=6))
        ledger = {1: {"clocks_addressed": []},
                  2: {"clocks_addressed": ["doom_clock"]}}  # 命中
        out = mod.scan_clock_addressing(proj, [1, 2], ledger_by_ch=ledger)
        assert out == []


def test_clock_not_urgent_skipped():
    """remaining = 3 > 2 → 非 urgent → 不进 urgent_clocks → 空。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "时钟表.json", _clock_db(ticks=3, max_v=6))  # remaining=3
        ledger = {1: {"clocks_addressed": []}, 2: {"clocks_addressed": []}}
        out = mod.scan_clock_addressing(proj, [1, 2], ledger_by_ch=ledger)
        assert out == []


def test_clock_inactive_status_skipped():
    """status != active → 跳过，即便 remaining 很小。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "时钟表.json", _clock_db(ticks=5, max_v=6, status="resolved"))
        ledger = {1: {"clocks_addressed": []}, 2: {"clocks_addressed": []}}
        out = mod.scan_clock_addressing(proj, [1, 2], ledger_by_ch=ledger)
        assert out == []


def test_clock_single_chapter_no_warning():
    """recent < 2 章 → len(recent) >= 2 不满足 → 不报（边界）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "时钟表.json", _clock_db(ticks=5, max_v=6))
        ledger = {1: {"clocks_addressed": []}}
        out = mod.scan_clock_addressing(proj, [1], ledger_by_ch=ledger)
        assert out == []


# ---------------------------------------------------------------------------
# C. scan_heart_event_consistency（磁盘路径，无 ledger 参数）
# ---------------------------------------------------------------------------

def _ensemble_db(consumed: bool = True, consumed_at: int = 1) -> dict:
    return {
        "characters": {
            "周明": {
                "heart_events": [
                    {"event_id": "he_01", "consumed": consumed,
                     "consumed_at_ch": consumed_at, "tier_label": "身世之谜",
                     "reveal": "他其实是失踪的太子"},
                ]
            }
        }
    }


def test_heart_event_forgotten_advisory():
    """揭密后 NPC 在 ≥2 章出现但 reveal 关键词从不再现 → HEART_EVENT_FORGOTTEN advisory。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "群像档.json", _ensemble_db(consumed=True, consumed_at=1))
        # 后续 3 章 NPC「周明」出场，但都没出现 reveal 关键词（太子/失踪…）
        for ch in (2, 3, 4):
            _write_chapter(proj, ch, text="周明走进屋里，倒了杯茶，坐下不语。")
        out = mod.scan_heart_event_consistency(proj, [1, 2, 3, 4])
        assert len(out) == 1
        f = out[0]
        assert f["code"] == "HEART_EVENT_FORGOTTEN"
        assert f["npc"] == "周明"
        assert f["consumed_at_ch"] == 1
        assert set(f["post_appearance_chs"]) == {2, 3, 4}


def test_heart_event_keyword_recurs_no_finding():
    """reveal 关键词分块在后续章再现 → kw_hit → 不报。

    reveal "他其实是失踪的太子" 经 re.findall([一-鿿]{2,5}) 切成块 "他其实是失"/"踪的太子"；
    后续章正文须含其一才算命中（非按 太子/失踪 拆词）。
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "群像档.json", _ensemble_db(consumed=True, consumed_at=1))
        _write_chapter(proj, 2, text="周明说起踪的太子那段往事。")  # 含分块 "踪的太子"
        _write_chapter(proj, 3, text="周明又来了。")
        _write_chapter(proj, 4, text="周明站着。")
        out = mod.scan_heart_event_consistency(proj, [1, 2, 3, 4])
        assert out == []


def test_heart_event_not_consumed_skipped():
    """consumed=False → 跳过整条 heart_event。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "群像档.json", _ensemble_db(consumed=False, consumed_at=1))
        for ch in (2, 3, 4):
            _write_chapter(proj, ch, text="周明在场。")
        out = mod.scan_heart_event_consistency(proj, [1, 2, 3, 4])
        assert out == []


# ---------------------------------------------------------------------------
# D. scan_fate_dice_consumption
# ---------------------------------------------------------------------------

def _pool_db(drawn_ch: int, eid: str = "ev_01", evidence=None) -> dict:
    return {
        "drawn_events_log": [{"ch": drawn_ch, "event_id": eid, "label": "暴雨夜袭"}],
        "events": [{"event_id": eid, "physical_evidence": evidence or ["断裂的桥梁", "焦黑的尸体"]}],
    }


def test_fate_dice_not_declared_warning():
    """抽中 ev_01 但 ledger fate_dice_consumed 未含该 id → FATE_DICE_NOT_DECLARED warning。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json", _pool_db(drawn_ch=5))
        ledger = {5: {"fate_dice_consumed": [{"event_id": "other_ev", "evidence_hit_ratio": 0.9}]}}
        out = mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch=ledger)
        codes = {f["code"] for f in out}
        assert "FATE_DICE_NOT_DECLARED" in codes
        warn = next(f for f in out if f["code"] == "FATE_DICE_NOT_DECLARED")
        assert warn["severity"] == "warning"
        assert warn["ch"] == 5


def test_fate_dice_evidence_low_advisory():
    """声明正确但 evidence_hit_ratio < 0.3 → FATE_DICE_EVIDENCE_LOW advisory（不再触 NOT_DECLARED）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json", _pool_db(drawn_ch=5))
        ledger = {5: {"fate_dice_consumed": [{"event_id": "ev_01", "evidence_hit_ratio": 0.1}]}}
        out = mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch=ledger)
        codes = {f["code"] for f in out}
        assert "FATE_DICE_NOT_DECLARED" not in codes  # 声明正确
        assert "FATE_DICE_EVIDENCE_LOW" in codes
        low = next(f for f in out if f["code"] == "FATE_DICE_EVIDENCE_LOW")
        assert low["severity"] == "advisory"
        assert low["evidence_hit_ratio"] == 0.1


def test_fate_dice_declared_and_high_ratio_clean():
    """声明正确 + ratio ≥ 0.3 → 无任何 finding。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json", _pool_db(drawn_ch=5))
        ledger = {5: {"fate_dice_consumed": [{"event_id": "ev_01", "evidence_hit_ratio": 0.8}]}}
        out = mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch=ledger)
        assert out == []


def test_fate_dice_chapter_out_of_scope_skipped():
    """drawn 在 ch5 但 chapters 不含 5 → continue → 空。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json", _pool_db(drawn_ch=5))
        ledger = {7: {"fate_dice_consumed": [{"event_id": "ev_01", "evidence_hit_ratio": 0.9}]}}
        out = mod.scan_fate_dice_consumption(proj, [7], ledger_by_ch=ledger)
        assert out == []


def test_fate_dice_disk_fallback_evidence_count():
    """无 ledger → 回退磁盘：changes 声明正确 + 正文含部分 evidence 关键词算 ratio。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json",
                  _pool_db(drawn_ch=5, evidence=["断裂的桥梁", "焦黑的尸体"]))
        # 正文含 "桥梁"+"断裂"（出自第一项）但完全没第二项 → 部分命中，应 ≥0.3 不报 LOW
        _write_chapter(proj, 5,
                       text="桥梁断裂，残骸散落一地，桥墩焦黑碎裂。",
                       changes={"factual": {"fate_dice_consumed": "ev_01"}})
        out = mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch=None)
        # 声明匹配（consumed == eid）→ 不报 NOT_DECLARED
        assert "FATE_DICE_NOT_DECLARED" not in {f["code"] for f in out}


def test_fate_dice_no_drawn_log_empty():
    """drawn_events_log 为空 → 直接返回空。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_db(proj, "事件池.json", {"drawn_events_log": [], "events": []})
        assert mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch={}) == []

    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))  # 事件池.json 根本不存在
        assert mod.scan_fate_dice_consumption(proj, [5], ledger_by_ch={}) == []


# ---------------------------------------------------------------------------
# 极简 runner（独立运行用；官方入口为仓库根 py -m pytest）
# ---------------------------------------------------------------------------

def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[cross_cluster_data_consumption_aggregate] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
