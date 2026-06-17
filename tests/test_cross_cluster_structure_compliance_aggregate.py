"""cross_cluster_structure_compliance_aggregate 确定性单元测试（零依赖·零 LLM·零联网）。

钉死 2 类结构层跨章合规检测的纯逻辑：
A. BEAT_MAP_PROGRESSION —— 磁盘版 scan_beat_progression + 账本版 scan_beat_progression_ledger
   - beat_keywords_for 模糊匹配 beat 主干 → 关键词清单
   - BEAT_MISSED（正文/changes 无该 beat 信号）
   - BEAT_GAP_TOO_LONG（声明 beat 章号间隔 > 60）
   - [M10-a] 复审 bug：ledger 版 beat_signal_hit=None 必须等同 False（不能 `None is False` 恒假漏判）
B. USER_CHOICE_LANDED —— 磁盘版 scan_user_choice_landed + 账本版 scan_user_choice_landed_ledger
   - cluster_blueprint 是 list（城南项目实测 bug）必须归一成 dict 不崩
   - USER_CHOICE_NOT_LANDED（有 fate_cards 但无 user_choice）
   - CHOICE_LEADS_TO_MISMATCH（选中卡 leads_to 与 turning_point 关键词无重叠）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_structure_compliance_aggregate as mod  # noqa: E402


# ============================================================
# 造测试项目工具
# ============================================================

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _mk_chapter(tmp: Path, ch: int, text: str = "", changes: dict | None = None) -> None:
    """造 章节/第NNN章/{正文.txt, _changes.json}。"""
    cdir = tmp / "章节" / f"第{ch:03d}章"
    cdir.mkdir(parents=True, exist_ok=True)
    if text:
        (cdir / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")
    if changes is not None:
        (cdir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")


# ============================================================
# beat_keywords_for —— 纯映射函数
# ============================================================

def test_beat_keywords_for_exact_and_suffix():
    """beat 主干匹配 BEAT_KEYWORDS（'Set-Up_late' → 'Set-Up' 关键词）。"""
    # 精确命中
    assert mod.beat_keywords_for("Catalyst") == ["催化", "意外", "事件", "异常", "异变", "震惊"]
    # 带 _ 后缀仍取主干命中
    assert mod.beat_keywords_for("Set-Up_late") == ["铺垫", "日常", "介绍"]
    # 空串 / None → 空
    assert mod.beat_keywords_for("") == []
    assert mod.beat_keywords_for(None) == []
    # 完全未知 beat → 空
    assert mod.beat_keywords_for("ZZZ_NotABeat") == []


# ============================================================
# scan_beat_progression（磁盘版）
# ============================================================

def test_scan_beat_progression_missed_when_no_signal():
    """beat_map 声明 ch=5 是 Catalyst，但正文/changes 完全无信号 → BEAT_MISSED warning。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"5": "Catalyst"}}, ensure_ascii=False),
            encoding="utf-8")
        # 正文无任何 catalyst 关键词，changes 也不标
        _mk_chapter(tmp, 5, text="这是一段平静的日常描写完全没有相关信号。",
                    changes={"factual": {"beats_addressed": []}})
        findings = mod.scan_beat_progression(tmp, [5])
        assert len(findings) == 1
        f = findings[0]
        assert f["code"] == "BEAT_MISSED"
        assert f["severity"] == "warning"
        assert f["ch"] == 5
        assert f["expected_beat"] == "Catalyst"


def test_scan_beat_progression_hit_no_finding():
    """正文含 beat 关键词 → 命中 → 不报 BEAT_MISSED。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"5": "Catalyst"}}, ensure_ascii=False),
            encoding="utf-8")
        # 正文含 "意外"/"震惊" 关键词
        _mk_chapter(tmp, 5, text="一场意外发生了，所有人都为之震惊。",
                    changes={"factual": {"beats_addressed": []}})
        findings = mod.scan_beat_progression(tmp, [5])
        assert findings == []


def test_scan_beat_progression_explicit_changes_hit():
    """changes.factual.beats_addressed 显式标 beat → 即使正文无关键词也算命中。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {"7": "Midpoint"}}, ensure_ascii=False),
            encoding="utf-8")
        # 正文无 Midpoint 关键词("转折"/"中途"/"突变")，但 changes 显式标
        _mk_chapter(tmp, 7, text="一段普通的对话与场景。",
                    changes={"factual": {"beats_addressed": ["Midpoint reached"]}})
        findings = mod.scan_beat_progression(tmp, [7])
        assert findings == []


def test_scan_beat_progression_gap_too_long():
    """声明 beat 的章号间隔 > 60 → BEAT_GAP_TOO_LONG advisory（边界：>60 报，=60 不报）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        # ch1 → ch62 间隔 61 (>60 报)；ch62 → ch122 间隔 60 (不报)
        (db / "beat_map.json").write_text(
            json.dumps({"chapters_beat": {
                "1": "Opening Image", "62": "Catalyst", "122": "Midpoint"}},
                ensure_ascii=False),
            encoding="utf-8")
        # 不造正文 → BEAT_MISSED 那段因 read_text 为空跳过，只剩 gap 检测
        findings = mod.scan_beat_progression(tmp, [1, 62, 122])
        gaps = [f for f in findings if f["code"] == "BEAT_GAP_TOO_LONG"]
        assert len(gaps) == 1
        assert gaps[0]["from_ch"] == 1
        assert gaps[0]["to_ch"] == 62
        assert gaps[0]["gap"] == 61
        assert gaps[0]["severity"] == "advisory"


def test_scan_beat_progression_no_beat_map():
    """无 beat_map.json → 空列表（不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        assert mod.scan_beat_progression(tmp, [1, 2, 3]) == []


# ============================================================
# scan_beat_progression_ledger（账本版）—— 复审 bug [M10-a]
# ============================================================

def test_ledger_beat_signal_hit_none_treated_as_missed():
    """[M10-a] 关键回归：beat_signal_hit=None（builder 未预算）必须等同未命中 → 报 BEAT_MISSED。

    旧写法 `None is False` 恒假会让 None 永不触发 BEAT_MISSED。
    """
    recs = [
        (5, {"beat": "Catalyst", "beat_signal_hit": None, "beats_addressed": []}),
    ]
    findings = mod.scan_beat_progression_ledger(recs)
    missed = [f for f in findings if f["code"] == "BEAT_MISSED"]
    assert len(missed) == 1
    assert missed[0]["ch"] == 5
    assert missed[0]["severity"] == "warning"


def test_ledger_beat_signal_hit_true_not_missed():
    """beat_signal_hit=True → 命中 → 不报 BEAT_MISSED。"""
    recs = [
        (5, {"beat": "Catalyst", "beat_signal_hit": True, "beats_addressed": []}),
    ]
    findings = mod.scan_beat_progression_ledger(recs)
    assert [f for f in findings if f["code"] == "BEAT_MISSED"] == []


def test_ledger_beat_explicit_addressed_overrides_false_signal():
    """beat_signal_hit=False 但 beats_addressed 显式标 → 算命中 → 不报。"""
    recs = [
        (8, {"beat": "Midpoint", "beat_signal_hit": False,
             "beats_addressed": ["Midpoint done"]}),
    ]
    findings = mod.scan_beat_progression_ledger(recs)
    assert [f for f in findings if f["code"] == "BEAT_MISSED"] == []


def test_ledger_beat_gap_too_long():
    """账本版 gap 算法与磁盘版一致：声明 beat 的章号间隔 > 60 → advisory。"""
    recs = [
        (1, {"beat": "Opening Image", "beat_signal_hit": True}),
        (70, {"beat": "Catalyst", "beat_signal_hit": True}),
        (200, {"beat": "Midpoint", "beat_signal_hit": True}),
    ]
    findings = mod.scan_beat_progression_ledger(recs)
    gaps = [f for f in findings if f["code"] == "BEAT_GAP_TOO_LONG"]
    # ch1→70 (69) 报，ch70→200 (130) 报
    assert len(gaps) == 2
    assert {(g["from_ch"], g["to_ch"]) for g in gaps} == {(1, 70), (70, 200)}


def test_ledger_beat_skips_records_without_beat():
    """无 beat 字段的 record 不参与声明序列也不报。"""
    recs = [
        (3, {"summary": "无 beat 字段"}),
        (4, {"beat": "", "beat_signal_hit": None}),  # 空 beat 也跳过
    ]
    findings = mod.scan_beat_progression_ledger(recs)
    assert findings == []


# ============================================================
# scan_user_choice_landed（磁盘版）—— [SC-1] list→dict 归一
# ============================================================

def _mk_progress(tmp: Path, cluster_blueprint) -> None:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "进度.json").write_text(
        json.dumps({"cluster_blueprint": cluster_blueprint}, ensure_ascii=False),
        encoding="utf-8")


def _mk_fate_cards(tmp: Path, ch: int, cards: list) -> None:
    wal = tmp / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / f"第{ch:03d}章_fate_cards.json").write_text(
        json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")


def test_user_choice_not_landed_warning():
    """有 fate_cards 但 cluster_blueprint 无 user_choice → USER_CHOICE_NOT_LANDED warning。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # blueprint dict 形态，scene_storyboard[ch=5] 无 user_choice
        _mk_progress(tmp, {
            "cluster_001": {"scene_storyboard": [{"ch": 5}]},
        })
        _mk_fate_cards(tmp, 5, [{"label": "A", "leads_to": "x"},
                                {"label": "B", "leads_to": "y"}])
        findings = mod.scan_user_choice_landed(tmp, [5])
        assert len(findings) == 1
        f = findings[0]
        assert f["code"] == "USER_CHOICE_NOT_LANDED"
        assert f["severity"] == "warning"
        assert f["card_labels_available"] == ["A", "B"]


def test_user_choice_blueprint_is_list_no_crash():
    """[SC-1] cluster_blueprint 是 list（城南实测 bug）→ 归一成 dict 不崩，仍能检测。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # list 形态，按 cluster_id 归一
        _mk_progress(tmp, [
            {"cluster_id": "cluster_001", "scene_storyboard": [{"ch": 5}]},
        ])
        _mk_fate_cards(tmp, 5, [{"label": "A"}])
        findings = mod.scan_user_choice_landed(tmp, [5])
        assert len(findings) == 1
        assert findings[0]["code"] == "USER_CHOICE_NOT_LANDED"


def test_user_choice_leads_to_mismatch_advisory():
    """选中卡 leads_to 与 turning_point 关键词无重叠 → CHOICE_LEADS_TO_MISMATCH advisory。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_progress(tmp, {
            "cluster_001": {"scene_storyboard": [{
                "ch": 5,
                "user_choice": "A",
                "turning_point": "主角离开城市去往乡村寻找答案",
            }]},
        })
        _mk_fate_cards(tmp, 5, [
            {"label": "A", "leads_to": "黑龙觉醒吞噬星辰毁灭世界"},  # 与 turning 无重叠
        ])
        findings = mod.scan_user_choice_landed(tmp, [5])
        mismatch = [f for f in findings if f["code"] == "CHOICE_LEADS_TO_MISMATCH"]
        assert len(mismatch) == 1
        assert mismatch[0]["severity"] == "advisory"
        assert mismatch[0]["user_choice"] == "A"


def test_user_choice_leads_to_overlap_no_finding():
    """leads_to 与 turning_point 有关键词重叠 → 选择已落地 → 不报。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_progress(tmp, {
            "cluster_001": {"scene_storyboard": [{
                "ch": 5,
                "user_choice": "B",
                "turning_point": "主角决定离开城市寻找真相",
            }]},
        })
        _mk_fate_cards(tmp, 5, [
            {"label": "A", "leads_to": "无关分支"},
            {"label": "B", "leads_to": "离开城市踏上旅途"},  # "离开"/"城市" 与 turning 重叠
        ])
        findings = mod.scan_user_choice_landed(tmp, [5])
        assert [f for f in findings if f["code"] == "CHOICE_LEADS_TO_MISMATCH"] == []


# ============================================================
# scan_user_choice_landed_ledger（账本版）
# ============================================================

def test_ledger_choice_mismatch():
    """账本版：user_choice 齐备且 leads_to 与 turning_point 无重叠 → CHOICE_LEADS_TO_MISMATCH。"""
    recs = [
        (5, {"user_choice": "A",
             "choice_leads_to": "黑龙觉醒吞噬星辰",
             "turning_point": "主角离开城市去往乡村"}),
    ]
    findings = mod.scan_user_choice_landed_ledger(recs)
    assert len(findings) == 1
    assert findings[0]["code"] == "CHOICE_LEADS_TO_MISMATCH"
    assert findings[0]["ch"] == 5


def test_ledger_choice_overlap_and_missing_skipped():
    """账本版：有重叠不报；无 user_choice / 缺字段的 record 直接跳过。"""
    recs = [
        # 有重叠 → 不报
        (5, {"user_choice": "B",
             "choice_leads_to": "离开城市踏上旅途",
             "turning_point": "主角决定离开城市"}),
        # 无 user_choice → 跳过
        (6, {"choice_leads_to": "随便", "turning_point": "随便"}),
        # 有 choice 但缺 turning_point → 跳过（不报）
        (7, {"user_choice": "A", "choice_leads_to": "某分支"}),
    ]
    findings = mod.scan_user_choice_landed_ledger(recs)
    assert findings == []


# ============================================================
# load_json / get_chapters —— IO 辅助
# ============================================================

def test_load_json_missing_and_corrupt():
    """缺文件 → default；损坏 JSON → default（不抛）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        missing = tmp / "nope.json"
        assert mod.load_json(missing, default={"x": 1}) == {"x": 1}
        bad = tmp / "bad.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []
        good = tmp / "good.json"
        good.write_text(json.dumps({"a": [1, 2]}), encoding="utf-8")
        assert mod.load_json(good) == {"a": [1, 2]}


def test_get_chapters_sorted_last_n():
    """get_chapters 按章号升序取最后 N 个；无章节 → 空。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 空项目
        assert mod.get_chapters(tmp, 5) == []
        # 造乱序章节目录
        for ch in [3, 1, 12, 7]:
            (tmp / "章节" / f"第{ch:03d}章").mkdir(parents=True, exist_ok=True)
        # 干扰目录（非 "第N章" 命名）不应被纳入
        (tmp / "章节" / "草稿").mkdir(parents=True, exist_ok=True)
        assert mod.get_chapters(tmp, 10) == [1, 3, 7, 12]
        assert mod.get_chapters(tmp, 2) == [7, 12]


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
    print(f"[structure_compliance_aggregate] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
