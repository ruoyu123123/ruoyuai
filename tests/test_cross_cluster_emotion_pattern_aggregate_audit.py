"""cross_cluster_emotion_pattern_aggregate 审计回归测试（2026-06-16·确定性·零依赖）。

守护本次修复点（triage L184 · EMOTION_RUN_TOO_LONG「连续段」语义/算法错配）：
  旧 bug：chs_with_top 是全窗口平铺过滤（line 184），len(chs_with_top) >= 6 统计的是
          「被 top_emotion 主导的章总数」而非「按章号排序后相邻 run 的最长连续段」，
          却产出 code=EMOTION_RUN_TOO_LONG + consecutive_chs + suggestion『连续 ≥ 6 章』。
          → 散点/交替主导（如某情绪在 1/3/5/7/9/11 章交替出现·实际最长连续段=1）被误报成
            『连续 ≥6 章情绪僵化』，把健康的情绪波动误判为固化（虚报 advisory）。
  修复后：真·连续段检测——排序 + 相邻 run 累加 + 取最长段·max_run>=6 才报·
          consecutive_chs=真实连续段·suggestion 文案与实际连续长度对齐。

测试经 cluster 账本路径（CLUSTER_MODE=1 + 故事块摘要.json.char_emotion_counts）确定性注入
逐章情绪计数，完全绕开 detect_emotions_for_char 的 ±150 字模糊文本扫描，直接钉死 run 检测逻辑。

北极星⑤护栏：EMOTION_RUN_TOO_LONG 恒 advisory·绝不进 audit_hub.HARD_GATE_CODES。
"""
import json
import os
import sys
import tempfile
import types
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_emotion_pattern_aggregate as epa  # noqa: E402


def _set_cluster_mode(on):
    if on:
        os.environ["CLUSTER_MODE"] = "1"
    else:
        os.environ.pop("CLUSTER_MODE", None)


def _run_with_ledger(chapters: dict, char: str, last_n: int = 50):
    """构造仅含一个 cluster 的账本 → 跑 main() → 返回 (findings list, report dict)。

    chapters: {ch_int: {emotion: count}}  直接喂 char_emotion_counts（绕开文本扫描）。
    """
    td = tempfile.mkdtemp()
    proj = Path(td)
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    ch_records = {
        str(ch): {"char_emotion_counts": {char: counts}}
        for ch, counts in chapters.items()
    }
    lo = min(chapters); hi = max(chapters)
    summary = {
        "schema_version": "v2.cluster",
        "clusters": [{
            "cluster_id": "cluster_001",
            "title": "t",
            "chapter_range": [lo, hi],
            "cluster_end_ch": hi,
            "chapters": ch_records,
        }],
    }
    (db / "故事块摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    bak_argv = sys.argv[:]
    bak_mode = os.environ.get("CLUSTER_MODE")
    try:
        _set_cluster_mode(True)
        sys.argv = ["epa", str(proj), "--characters", char, "--last-n", str(last_n)]
        try:
            epa.main()
        except SystemExit:
            pass  # main() 末尾 sys.exit(1 if findings else 0) — 预期
    finally:
        sys.argv = bak_argv
        if bak_mode is None:
            os.environ.pop("CLUSTER_MODE", None)
        else:
            os.environ["CLUSTER_MODE"] = bak_mode

    # 读最新报告
    scan_dir = db / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("emotion_pattern_*.json"))
    assert reports, "未产出 emotion_pattern 报告"
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    return report.get("findings", []), report


def _run_findings(chapters, char, code):
    findings, _ = _run_with_ledger(chapters, char)
    return [f for f in findings if f.get("code") == code]


# ── 修复核心：散点/交替主导不再误报连续 ──────────────────────────────

def test_scattered_dominance_does_not_report_run():
    """回归：top_emotion 在 7 个不相邻章主导（最长连续段=1）→ 旧 bug 误报 EMOTION_RUN_TOO_LONG，
    修复后不报。用第二情绪填补偶数章·让 calm 既是全局 top 又永不连续 ≥6。"""
    chapters = {}
    # 奇数章 calm 独占，偶数章 anxious 独占 → calm 出现在 1,3,5,7,9,11,13（7 章·全不相邻）
    for ch in range(1, 14):
        if ch % 2 == 1:
            chapters[ch] = {"calm": 3}
        else:
            chapters[ch] = {"anxious": 3}
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert runs == [], f"散点主导（最长连续段=1）不应报连续告警，实得 {runs}"


def test_alternating_blocks_below_threshold_no_report():
    """回归补强：连续段长度恰好 < 6（如最长 5 连）→ 不报。"""
    chapters = {}
    # 1-5 calm 连续（run=5），6 anxious 断开，7-9 calm（run=3）→ 最长 run=5 < 6
    for ch in [1, 2, 3, 4, 5, 7, 8, 9]:
        chapters[ch] = {"calm": 3}
    chapters[6] = {"anxious": 5}
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert runs == [], f"最长连续段=5（<6）不应报，实得 {runs}"


# ── 修复核心：真连续段 >=6 仍正确触发且字段准确 ──────────────────────

def test_true_consecutive_run_reports_with_accurate_fields():
    """真·连续 6 章主导 → 必报·consecutive_chs=真实连续段·suggestion 文案数字对齐。"""
    chapters = {ch: {"calm": 3} for ch in range(10, 16)}  # ch 10..15 连续 6 章 calm
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert len(runs) == 1, f"连续 6 章应报恰 1 条，实得 {runs}"
    f = runs[0]
    assert f["consecutive_chs"] == [10, 11, 12, 13, 14, 15], f["consecutive_chs"]
    assert f["emotion"] == "calm"
    # suggestion 文案应说『连续 6 章』（与真实连续长度对齐·不再是写死的『≥ 6』）
    assert "连续 6 章" in f["suggestion"], f["suggestion"]
    assert "≥" not in f["suggestion"], "文案不应再用写死的 ≥ 6"


def test_longest_run_picked_not_total_count():
    """混合：散点多 + 一段真连续 7 → consecutive_chs 必须是那 7 连段（不是全部主导章拼接）。"""
    chapters = {}
    # 散点：ch 1,3 calm 主导
    chapters[1] = {"calm": 3}
    chapters[3] = {"calm": 3}
    chapters[2] = {"anxious": 5}
    # 真连续段：ch 20..26（7 连）calm
    for ch in range(20, 27):
        chapters[ch] = {"calm": 3}
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert len(runs) == 1, runs
    f = runs[0]
    assert f["consecutive_chs"] == [20, 21, 22, 23, 24, 25, 26], f["consecutive_chs"]
    assert "连续 7 章" in f["suggestion"], f["suggestion"]


def test_longer_run_wins_over_earlier_shorter_run():
    """两段连续：先 6 连后 8 连 → 取更长的 8 连段（验证 longest_run 取最大而非首个）。"""
    chapters = {}
    for ch in range(1, 7):       # run A = 6 连（ch 1..6）
        chapters[ch] = {"calm": 3}
    chapters[7] = {"anxious": 9}  # 断开
    for ch in range(10, 18):     # run B = 8 连（ch 10..17）
        chapters[ch] = {"calm": 3}
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert len(runs) == 1, runs
    assert runs[0]["consecutive_chs"] == list(range(10, 18)), runs[0]["consecutive_chs"]
    assert "连续 8 章" in runs[0]["suggestion"], runs[0]["suggestion"]


# ── 护栏与既有行为 ─────────────────────────────────────────────────

def test_run_too_long_never_hard_gate():
    """北极星⑤：EMOTION_RUN_TOO_LONG 恒 advisory·绝不在 audit_hub.HARD_GATE_CODES。"""
    chapters = {ch: {"calm": 3} for ch in range(1, 7)}
    runs = _run_findings(chapters, "陆衍", "EMOTION_RUN_TOO_LONG")
    assert runs and all(f["severity"] == "advisory" for f in runs)
    import audit_hub
    assert "EMOTION_RUN_TOO_LONG" not in audit_hub.HARD_GATE_CODES


def test_ledger_path_active_no_crash_empty_findings_possible():
    """烟雾：账本路径正常跑通（情绪均衡分散·不必然有 finding）·import + 执行不崩。"""
    chapters = {
        1: {"calm": 2, "anxious": 2},
        2: {"joyful": 2, "angry": 2},
        3: {"curious": 2, "sad": 2},
    }
    findings, report = _run_with_ledger(chapters, "陆衍")
    assert report["scan_type"] == "emotion_pattern"
    assert isinstance(findings, list)


# ============================================================
# 🔴 2026-07-01 emotion_vad 模型集成测试(source=model_vad|lexicon_fallback|mixed·零回归)
# ============================================================

def test_model_valence_classifies_to_nearest_emotion(monkeypatch):
    """RUOYU_NN_VAD=1 + 桥命中 valence → 最近邻分类到 EMOTION_KEYWORDS 7 类之一·source=model_vad。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts: [
            {"valence": 0.95, "arousal": 0.6, "dominance": None, "source": "model"}
            for _ in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    detail = epa.detect_emotions_for_char_detail("陆衍笑了笑，转身离开。" * 3, "陆衍")
    assert detail["source"] == "model_vad"
    assert detail["model_window_count"] > 0
    assert detail["lexicon_window_count"] == 0
    # valence=0.95 离 joyful(1.0)最近 → 应全部分到 joyful
    assert detail["counts"]["joyful"] == detail["model_window_count"]


def test_model_unavailable_matches_default_keyword_counts_exactly(monkeypatch):
    """零回归契约：RUOYU_NN_VAD=1 但桥返回全 None(模型不可用) → counts 必须与默认(不开模型)
    逐字节一致(dict 全等，不只是 top emotion 一致)。"""
    text = "陆衍紧张地咬紧牙关，手心全是汗。" * 3
    baseline = dict(epa.detect_emotions_for_char(text, "陆衍"))  # 默认(env 未设)行为

    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    detail = epa.detect_emotions_for_char_detail(text, "陆衍")
    assert dict(detail["counts"]) == baseline
    assert detail["source"] == "lexicon_fallback"
    assert baseline.get("anxious", 0) > 0  # 确认真的走了关键词命中(非空对比无意义)


def test_no_mentions_returns_none_source():
    """角色名未出现 → 无窗口可扫，source=none(既不是模型也不是词典)。"""
    detail = epa.detect_emotions_for_char_detail("平淡无奇的一段话，没有主角登场。", "陆衍")
    assert detail["source"] == "none"
    assert detail["counts"] == Counter()
    assert detail["model_window_count"] == 0
    assert detail["lexicon_window_count"] == 0


def test_detect_emotions_for_char_still_returns_counter_backward_compat():
    """向后兼容：detect_emotions_for_char 签名/返回类型不变(cluster_summary_builder.py 直接消费)。"""
    counts = epa.detect_emotions_for_char("陆衍紧张地咬紧牙关，手心全是汗。" * 3, "陆衍")
    assert isinstance(counts, Counter)
    assert counts["anxious"] > 0


def test_nearest_emotion_by_valence_boundaries():
    """最近邻分类锚点(与 emotion_curve_rescan_scanner.EMOTION_VALENCE 同源数值)边界正确。"""
    assert epa._nearest_emotion_by_valence(1.0) == "joyful"
    assert epa._nearest_emotion_by_valence(0.0) == "sad"


def _mk_raw_project(chapters: dict):
    """非 ledger 路径：写原始 第NNN章/第NNN章.txt 磁盘结构(不设 CLUSTER_MODE·驱动 main() 直扫文本)。"""
    td = tempfile.mkdtemp()
    proj = Path(td)
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    for ch, text in chapters.items():
        chdir = proj / "章节" / f"第{ch:03d}章"
        chdir.mkdir(parents=True, exist_ok=True)
        (chdir / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")
    return proj


def _run_main_raw(proj, char="陆衍", last_n=10):
    bak_argv = sys.argv[:]
    bak_mode = os.environ.get("CLUSTER_MODE")
    try:
        os.environ.pop("CLUSTER_MODE", None)  # 强制非 cluster 模式 → 走原始磁盘扫描分支
        sys.argv = ["epa", str(proj), "--characters", char, "--last-n", str(last_n)]
        try:
            epa.main()
        except SystemExit:
            pass
    finally:
        sys.argv = bak_argv
        if bak_mode is None:
            os.environ.pop("CLUSTER_MODE", None)
        else:
            os.environ["CLUSTER_MODE"] = bak_mode
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("emotion_pattern_*.json"))
    assert reports, "未产出 emotion_pattern 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def test_main_raw_disk_path_reports_vad_source_lexicon_by_default():
    """非 cluster 模式(原始磁盘扫描路径)默认(env 不开 RUOYU_NN_VAD)：
    report.vad_source=lexicon_fallback·model_window_count=0(零回归)。"""
    proj = _mk_raw_project({
        1: "陆衍紧张地咬紧牙关，手心全是汗。" * 3,
        2: "陆衍笑了笑，很高兴地转身离开。" * 3,
    })
    report = _run_main_raw(proj)
    assert report["vad_source"] == "lexicon_fallback"
    assert report["vad_model_window_count"] == 0
    assert report["vad_lexicon_window_count"] > 0


def test_main_ledger_path_reports_not_applicable_ledger_mode():
    """ledger 路径(cluster 模式)不做文本扫描 → vad_source 应标 not_applicable_ledger_mode
    (与 model/lexicon 归因无关·避免误读为「用了词典」)。"""
    chapters = {1: {"calm": 2}, 2: {"anxious": 2}}
    _, report = _run_with_ledger(chapters, "陆衍")
    assert report["vad_source"] == "not_applicable_ledger_mode"
    assert report["vad_model_window_count"] == 0
    assert report["vad_lexicon_window_count"] == 0


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
