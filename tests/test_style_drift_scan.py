"""style_drift_scan 回归测试 — 跨章风格漂移扫描纯逻辑（零 LLM / 零联网）。

钉死 5 个确定性公开函数：
  - load_json          文件读取 + default 兜底（缺文件 / 坏 JSON）
  - find_chapter_files 嵌套 + 平铺布局发现章节、按 ch 号排序
  - scan_anchor_frequency  锚点计数 + ---CHANGES 截断
  - check_strategy_violations  "每N章不超过M次" 滑窗 + severity 分级；"每章不超过N次"
  - scan_opening_types_distribution  Counter 聚合 opening_type
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import style_drift_scan as mod  # noqa: E402


# ---------- load_json ----------

def test_load_json_missing_returns_default():
    """缺文件 → 返回 default，不抛。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "nope.json"
        assert mod.load_json(p) is None
        assert mod.load_json(p, {"x": 1}) == {"x": 1}


def test_load_json_valid_and_corrupt():
    """合法 JSON 正常解析；坏 JSON → 返回 default 不崩。"""
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "good.json"
        good.write_text(json.dumps({"a": [1, 2]}, ensure_ascii=False), encoding="utf-8")
        assert mod.load_json(good) == {"a": [1, 2]}

        bad = Path(d) / "bad.json"
        bad.write_text("{not valid json,,,", encoding="utf-8")
        assert mod.load_json(bad, default={}) == {}


# ---------- find_chapter_files ----------

def test_find_chapter_files_nested_layout_sorted():
    """嵌套布局 章节/第NNN章/第NNN章*.txt → 按 ch 号排序返回。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for ch in (2, 10, 1):
            dchap = root / "章节" / f"第{ch:03d}章"
            dchap.mkdir(parents=True)
            (dchap / f"第{ch:03d}章_标题.txt").write_text("正文", encoding="utf-8")
        found = mod.find_chapter_files(root)
        nums = [n for n, _ in found]
        assert nums == [1, 2, 10]  # 数字排序，非字典序
        assert all(p.exists() for _, p in found)


def test_find_chapter_files_flat_fallback():
    """无嵌套目录时，平铺 第N章*.txt 兜底。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "第1章_开端.txt").write_text("a", encoding="utf-8")
        (root / "第3章_高潮.txt").write_text("b", encoding="utf-8")
        (root / "笔记.txt").write_text("非章节", encoding="utf-8")  # 不匹配
        found = mod.find_chapter_files(root)
        assert [n for n, _ in found] == [1, 3]


def test_find_chapter_files_empty():
    """空项目 → 空列表，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.find_chapter_files(Path(d)) == []


# ---------- scan_anchor_frequency ----------

def test_scan_anchor_frequency_counts_and_changes_truncation():
    """锚点计数；---CHANGES 之后的正文不计入。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        f1 = root / "第1章.txt"
        # body 里 "绯红月光" 出现 2 次；CHANGES 段里 1 次不应被计
        f1.write_text("绯红月光照下来，绯红月光很美。\n---CHANGES---\n绯红月光", encoding="utf-8")
        f2 = root / "第2章.txt"
        f2.write_text("没有锚点的一章", encoding="utf-8")
        chapters = [(1, f1), (2, f2)]
        freq = mod.scan_anchor_frequency(chapters, ["绯红月光", "缺席词"])
        assert freq["绯红月光"] == {1: 2, 2: 0}  # CHANGES 后那次不计
        assert freq["缺席词"] == {1: 0, 2: 0}


def test_scan_anchor_frequency_no_changes_marker():
    """无 ---CHANGES 标记 → 全文都算 body。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "第5章.txt"
        f.write_text("月光月光月光", encoding="utf-8")
        freq = mod.scan_anchor_frequency([(5, f)], ["月光"])
        assert freq["月光"] == {5: 3}


# ---------- check_strategy_violations ----------

def test_check_strategy_violations_window_rule_and_severity():
    """'每3章不超过2次' 滑窗超限触发违规；超 limit+2 → strict，否则 mild。"""
    # ch1-3 每章各 1 次 → 窗口 [1,3] 共 3 次 > 2 → mild（3 不超过 limit+2=4）
    freq_mild = {"绯红月光": {1: 1, 2: 1, 3: 1}}
    strategy = [{"元素": "绯红月光", "策略": "每3章不超过2次"}]
    v_mild = mod.check_strategy_violations(freq_mild, strategy)
    assert len(v_mild) >= 1
    assert any("🟡 mild" in x["severity"] for x in v_mild)
    assert all(x["anchor"] == "绯红月光" for x in v_mild)

    # 窗口内爆量到 5 次（> limit+2=4）→ strict
    freq_strict = {"绯红月光": {1: 5}}
    v_strict = mod.check_strategy_violations(freq_strict, strategy)
    assert any("🔴 strict" in x["severity"] for x in v_strict)


def test_check_strategy_violations_per_chapter_rule():
    """'每章不超过1次' 单章超限触发 mild 违规。"""
    freq = {"颤抖": {1: 1, 2: 3}}  # ch2 超
    strategy = [{"元素": "颤抖", "策略": "每章不超过1次"}]
    v = mod.check_strategy_violations(freq, strategy)
    assert len(v) == 1
    assert v[0]["anchor"] == "颤抖"
    assert "ch 2" in v[0]["violation"]
    assert "🟡 mild" in v[0]["severity"]


def test_check_strategy_violations_no_rule_or_unknown_anchor():
    """策略无可解析规则 / anchor 不在 freq → 无违规，不崩。"""
    freq = {"已知": {1: 99}}
    # 1) anchor 不在 freq（freq 没 "未知"）
    s1 = [{"元素": "未知", "策略": "每章不超过1次"}]
    assert mod.check_strategy_violations(freq, s1) == []
    # 2) 策略文本无可识别规则
    s2 = [{"元素": "已知", "策略": "随意使用即可"}]
    assert mod.check_strategy_violations(freq, s2) == []
    # 3) 空 anchor name
    s3 = [{"元素": "", "策略": "每章不超过1次"}]
    assert mod.check_strategy_violations(freq, s3) == []


# ---------- scan_opening_types_distribution ----------

def test_scan_opening_types_distribution():
    """聚合各章 applied_style.opening_type → counter + total。"""
    summaries = [
        {"ch": 1, "applied_style": {"opening_type": "动作"}},
        {"ch": 2, "applied_style": {"opening_type": "动作"}},
        {"ch": 3, "applied_style": {"opening_type": "对话"}},
        {"ch": 4, "applied_style": {}},          # 无 opening_type → 跳过
        {"applied_style": {"opening_type": "环境"}},  # 无 ch → 跳过
    ]
    dist = mod.scan_opening_types_distribution([], summaries)
    assert dist["total"] == 3
    assert dist["type_counts"] == {"动作": 2, "对话": 1}
    assert dist["ch_to_type"] == {1: "动作", 2: "动作", 3: "对话"}


def test_scan_opening_types_distribution_empty():
    """空摘要 → total 0、空分布，不崩。"""
    dist = mod.scan_opening_types_distribution([], [])
    assert dist == {"ch_to_type": {}, "type_counts": {}, "total": 0}


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
    print(f"[style_drift_scan] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
