"""cross_cluster_timeline_item_location_aggregate.py 确定性单元测试（CCR14）。

钉死 3 类跨章追踪的纯逻辑（零 LLM / 零联网）：
  A. TIMELINE_CONTINUITY — TIME_FROZEN / TIME_GAP_UNEXPLAINED
  B. ITEM_HOLDER_CHAIN  — ITEM_ABANDONED / ITEM_DUPLICATE_HOLDER
  C. LOCATION_VISIT     — LOCATION_OVERFREQ / NEVER_VISITED / RHYTHM_BROKEN

覆盖：账本变体（scan_*_ledger·入参为 recs 列表·最纯）、磁盘变体
（scan_item_chain / scan_location_distribution·tempfile 造项目）、
辅助函数（load_json / get_chapters / read_text / read_changes）、
报告写盘 + 退出码（_emit_report）。
"""
import json
import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts"))
import cross_cluster_timeline_item_location_aggregate as mod  # noqa: E402

Path = pathlib.Path


# ============================================================
# 工具：造临时项目目录
# ============================================================

def _mk_db(root: Path, name: str, data) -> None:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _mk_chapter(root: Path, ch: int, text: str = "", changes: dict = None) -> None:
    cdir = root / "章节" / f"第{ch:03d}章"
    cdir.mkdir(parents=True, exist_ok=True)
    if text:
        (cdir / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")
    if changes is not None:
        (cdir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")


# ============================================================
# A. scan_timeline_ledger（最纯·入参 recs 列表）
# ============================================================

def test_timeline_ledger_time_frozen():
    """≥ 6 章 time_anchor 完全相同 → TIME_FROZEN（streak 算法 same_streak>=5 触发）。"""
    recs = [(ch, {"time_anchor": "周一晚"}) for ch in range(1, 8)]  # 7 章全同
    findings = mod.scan_timeline_ledger(recs)
    frozen = [f for f in findings if f["code"] == "TIME_FROZEN"]
    assert len(frozen) >= 1
    f = frozen[0]
    assert f["severity"] == "advisory"
    assert f["time"] == "周一晚"
    # consecutive_chs 取最后 5 个
    assert len(f["consecutive_chs"]) == 5


def test_timeline_ledger_gap_unexplained_when_no_transition():
    """相邻章时间跳 ≥ 3 天且账本无过渡标志 → TIME_GAP_UNEXPLAINED。"""
    recs = [
        (1, {"time_anchor": "周一上午"}),
        (2, {"time_anchor": "周二下午"}),
        (3, {"time_anchor": "周五清晨", "time_transition_present": False}),  # 周二→周五=3天
    ]
    findings = mod.scan_timeline_ledger(recs)
    gaps = [f for f in findings if f["code"] == "TIME_GAP_UNEXPLAINED"]
    assert len(gaps) == 1
    g = gaps[0]
    assert g["day_gap"] == 3
    assert g["from"]["ch"] == 2 and g["to"]["ch"] == 3


def test_timeline_ledger_gap_suppressed_when_transition_present():
    """跳 ≥ 3 天但账本标记 time_transition_present=True → 不报 GAP（过渡已交代）。"""
    recs = [
        (1, {"time_anchor": "周一上午"}),
        (2, {"time_anchor": "周二下午"}),
        (3, {"time_anchor": "周五清晨", "time_transition_present": True}),
    ]
    findings = mod.scan_timeline_ledger(recs)
    gaps = [f for f in findings if f["code"] == "TIME_GAP_UNEXPLAINED"]
    assert gaps == []


def test_timeline_ledger_too_few_anchors_returns_empty():
    """带 time_anchor 的章 < 3 → 直接返回空（边界守卫）。"""
    recs = [
        (1, {"time_anchor": "周一"}),
        (2, {}),  # 无 anchor
        (3, {"time_anchor": "周三"}),  # 只有 2 个有效 anchor
    ]
    assert mod.scan_timeline_ledger(recs) == []


# ============================================================
# B. scan_item_chain_ledger（入参 recs + 读 道具.json 单例）
# ============================================================

def test_item_ledger_duplicate_holder():
    """同一道具同一章被两个角色持有 → ITEM_DUPLICATE_HOLDER（warning）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "道具.json", {"items": [{"id": "玉佩"}]})
        recs = [
            (3, {"item_changes": [
                {"id": "玉佩", "holder": "张三"},
                {"id": "玉佩", "holder": "李四"},  # 同章双持有
            ]}),
        ]
        findings = mod.scan_item_chain_ledger(root, recs)
        dups = [f for f in findings if f["code"] == "ITEM_DUPLICATE_HOLDER"]
        assert len(dups) == 1
        dup = dups[0]
        assert dup["severity"] == "warning"
        assert dup["item_id"] == "玉佩"
        assert dup["ch"] == 3
        assert set(dup["holders"]) == {"张三", "李四"}


def test_item_ledger_abandoned_needs_10_chapters():
    """道具表里有但 ≥ 10 章无任何变更 → ITEM_ABANDONED；< 10 章则不报。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "道具.json", {"items": [{"id": "古剑"}]})
        # 9 章无变更 → 不触发
        recs9 = [(ch, {"item_changes": []}) for ch in range(1, 10)]
        assert [f for f in mod.scan_item_chain_ledger(root, recs9)
                if f["code"] == "ITEM_ABANDONED"] == []
        # 10 章无变更 → 触发
        recs10 = [(ch, {"item_changes": []}) for ch in range(1, 11)]
        ab = [f for f in mod.scan_item_chain_ledger(root, recs10)
              if f["code"] == "ITEM_ABANDONED"]
        assert len(ab) == 1
        assert ab[0]["item_id"] == "古剑"
        assert ab[0]["severity"] == "advisory"


def test_item_ledger_no_props_file_returns_empty():
    """道具.json 不存在 → 返回空（不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)  # 不建 道具.json
        recs = [(1, {"item_changes": [{"id": "x", "holder": "a"}]})]
        assert mod.scan_item_chain_ledger(root, recs) == []


# ============================================================
# C. scan_location_distribution_ledger（入参 recs + 读 地图.json 单例）
# ============================================================

def test_location_ledger_overfreq_and_never_visited():
    """某地点 ≥ 60% 章出现 → OVERFREQ；地图里 0 次访问的地点 → NEVER_VISITED。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "地图.json", {"locations": [
            {"name": "青云城"}, {"name": "落霞谷"}, {"name": "幽冥窟"},
            {"name": "天机阁"}, {"name": "枯骨原"},  # 5 个地点（满足 NEVER_VISITED 门槛 ≥5）
        ]})
        # 10 章里青云城出现 8 章（80% ≥ 60%），其余地点全 0
        recs = []
        for ch in range(1, 11):
            mentioned = ["青云城"] if ch <= 8 else []
            recs.append((ch, {"locations_mentioned": mentioned}))
        findings = mod.scan_location_distribution_ledger(root, recs)
        over = [f for f in findings if f["code"] == "LOCATION_OVERFREQ"]
        never = [f for f in findings if f["code"] == "LOCATION_NEVER_VISITED"]
        assert len(over) == 1
        assert over[0]["location"] == "青云城"
        assert over[0]["appearance_chs"] == 8
        assert over[0]["pct"] == 0.8
        assert len(never) == 1
        assert never[0]["total_locations"] == 5
        assert "落霞谷" in never[0]["locations"]


def test_location_ledger_rhythm_broken_streak():
    """非 hub 地点连续 ≥ 5 章（streak>=4 触发）只含该单地点 → RHYTHM_BROKEN。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "地图.json", {"locations": [{"name": "破庙"}]})
        recs = [(ch, {"locations_mentioned": ["破庙"]}) for ch in range(1, 7)]  # 6 章连
        findings = mod.scan_location_distribution_ledger(root, recs)
        rhythm = [f for f in findings if f["code"] == "LOCATION_RHYTHM_BROKEN"]
        assert len(rhythm) == 1
        assert rhythm[0]["location"] == "破庙"
        assert rhythm[0]["consecutive_chs"] >= 5


def test_location_ledger_no_map_returns_empty():
    """地图.json 缺失或无 locations → 返回空。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)  # 无 地图.json
        assert mod.scan_location_distribution_ledger(root, [(1, {})]) == []
        # 有文件但 locations 空
        _mk_db(root, "地图.json", {"locations": []})
        assert mod.scan_location_distribution_ledger(root, [(1, {})]) == []


# ============================================================
# 磁盘变体：scan_item_chain（读 道具.json + 每章 _changes.json）
# ============================================================

def test_disk_item_chain_duplicate_holder():
    """磁盘变体：从 _changes.factual.items 重建持有链，同章双持有 → DUPLICATE。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "道具.json", {"items": [{"name": "符箓"}]})
        _mk_chapter(root, 5, changes={"factual": {"items": [
            {"name": "符箓", "holder": "甲"},
            {"name": "符箓", "new_holder": "乙"},  # 用 new_holder 别名
        ]}})
        findings = mod.scan_item_chain(root, [5])
        dups = [f for f in findings if f["code"] == "ITEM_DUPLICATE_HOLDER"]
        assert len(dups) == 1
        assert dups[0]["ch"] == 5
        assert set(dups[0]["holders"]) == {"甲", "乙"}


# ============================================================
# 磁盘变体：scan_location_distribution（读 地图.json + 每章 .txt 正文）
# ============================================================

def test_disk_location_overfreq_from_text():
    """磁盘变体：扫每章正文文本统计地点频次，≥ 60% → OVERFREQ。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_db(root, "地图.json", {"places": [{"id": "雾隐镇"}]})  # 用 places + id 别名
        chapters = list(range(1, 6))  # 5 章
        for ch in chapters:
            txt = "雾隐镇的雨下了一夜。" if ch <= 4 else "他离开了。"  # 4/5 = 80%
            _mk_chapter(root, ch, text=txt)
        findings = mod.scan_location_distribution(root, chapters)
        over = [f for f in findings if f["code"] == "LOCATION_OVERFREQ"]
        assert len(over) == 1
        assert over[0]["location"] == "雾隐镇"
        assert over[0]["appearance_chs"] == 4


# ============================================================
# 辅助函数
# ============================================================

def test_load_json_missing_and_malformed():
    """load_json：缺文件 → default；坏 JSON → default；好 JSON → 解析。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert mod.load_json(root / "nope.json", default={"x": 1}) == {"x": 1}
        bad = root / "bad.json"
        bad.write_text("{ not valid json ", encoding="utf-8")
        assert mod.load_json(bad, default=[]) == []
        good = root / "good.json"
        good.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        assert mod.load_json(good) == {"k": "v"}


def test_get_chapters_sorted_and_last_n():
    """get_chapters：从 章节/第NNN章 目录名解析章号，排序后取末 last_n。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for ch in [3, 1, 12, 7, 25]:
            (root / "章节" / f"第{ch:03d}章").mkdir(parents=True)
        # 也建一个不符合命名的目录，应被忽略
        (root / "章节" / "草稿目录").mkdir(parents=True)
        assert mod.get_chapters(root, 10) == [1, 3, 7, 12, 25]
        assert mod.get_chapters(root, 3) == [7, 12, 25]
        # 无章节目录 → 空列表
        assert mod.get_chapters(Path(d) / "empty", 5) == []


def test_read_text_and_read_changes():
    """read_text / read_changes：存在则返回内容，缺失则空串 / 空 dict。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _mk_chapter(root, 2, text="第二章正文", changes={"factual": {"items": []}})
        assert mod.read_text(root, 2) == "第二章正文"
        assert mod.read_changes(root, 2) == {"factual": {"items": []}}
        # 缺失章
        assert mod.read_text(root, 99) == ""
        assert mod.read_changes(root, 99) == {}


# ============================================================
# 报告写盘 + 退出码：_emit_report
# ============================================================

def test_emit_report_writes_file_and_exit_code():
    """_emit_report：写 .cross_chapter_scan 报告；warning → exit 2 / advisory → exit 1 / 干净 → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)

        # 含一条 warning（应 exit 2）
        findings = [
            {"severity": "warning", "code": "ITEM_DUPLICATE_HOLDER", "suggestion": "双持有"},
            {"severity": "advisory", "code": "TIME_FROZEN", "suggestion": "时间停滞"},
        ]
        try:
            mod._emit_report(root, [1, 2, 3], findings)
            raise AssertionError("_emit_report 应 sys.exit，未退出")
        except SystemExit as e:
            assert e.code == 2  # warning 优先

        # 报告文件应已写盘且内容正确
        out_dir = root / "_数据库" / ".cross_chapter_scan"
        reports = list(out_dir.glob("timeline_item_location_*.json"))
        assert len(reports) == 1
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        assert report["scan_type"] == "timeline_item_location"
        assert report["chapters_scanned"] == [1, 2, 3]
        assert report["summary"] == {"warning": 1, "advisory": 1}
        assert len(report["findings"]) == 2


def test_emit_report_advisory_only_exit_1():
    """只有 advisory → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        try:
            mod._emit_report(root, [1], [{"severity": "advisory", "code": "X", "suggestion": "s"}])
            raise AssertionError("应 sys.exit")
        except SystemExit as e:
            assert e.code == 1


def test_emit_report_clean_exit_0():
    """无 findings → exit 0 + summary 全零。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        try:
            mod._emit_report(root, [1, 2], [])
            raise AssertionError("应 sys.exit")
        except SystemExit as e:
            assert e.code == 0
        out_dir = root / "_数据库" / ".cross_chapter_scan"
        report = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
        assert report["summary"] == {"warning": 0, "advisory": 0}


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
    print(f"[cross_cluster_timeline_item_location_aggregate] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
