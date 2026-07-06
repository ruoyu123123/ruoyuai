"""audit_hub 聚合主流程回归测试（2026-06-13 · 补「聚合中枢零测试」缺口）。

守护四件事（北极星⑤顾问制的执行中枢）：
  1. scanner_registry 加载：缺失/损坏 → {}（fallback 硬编码集合），合法 JSON 原样返回；
  2. 聚合主流程 audit_chapter：假 scanner 脚本（monkeypatch _SCRIPT_DIR 指向 tmpdir）
     的 advisory / hard_gate issue 都被收进报告、维度/gate_level 归类正确；
  3. 豁免协议 _load_waivers + _apply_waivers：waiver 文件同时豁免 advisory + hard_gate
     两 code → advisory 转 waived、hard_gate 强制忽略豁免仍进 pending_agent
     （hard_gate 不可豁免 = 唯一不可协商边界）；
  4. _gate_level_for 条件降级：STYLE_单段超长 warn→advisory / fatal·error→hard_gate；
     info severity（低置信旁注）对任何 code 永不 hard_gate（2026-06-02 修）。

假 scanner 输出格式从真解析器反推：
  · validate_chapter --json → {"summary": {...}, "errors": [{code, severity, msg, fix_hint}]}
    （_parse_validate_chapter 消费）；
  · narrative_scanner --all → {"<check>": {"warning": ..., "severity": ...}}
    （_parse_scanner_json + NARRATIVE_DIM 消费 → code=NARRATIVE_<check>）。
其余 7 个 chapter-mode scanner 写成空报告 stub（print "{}"），保证 scanner_status 全 ok
且零 issue 噪声。只测确定性聚合逻辑，不碰 LLM / agent / 真项目。
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import audit_hub as ah  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 沙箱工具：假项目 + 假 scanner 脚本（输出全 ASCII，杜绝子进程编码歧义）
# ════════════════════════════════════════════════════════════════

# chapter mode（非 cluster）下 audit_chapter 调度的 9 个 scanner 文件名
_ALL_SCANNER_NAMES = [
    "validate_chapter.py", "validate_style.py", "narrative_scanner.py",
    "plot_structure_scanner.py", "hook_strength_scanner.py",
    "golden_three_scanner.py", "semantic_slop_scanner.py",
    "narrative_short_sentence_scanner.py", "repeat_noun_density_scanner.py",
]

# 空报告 stub：所有解析器对 "{}" 都归零 issue（validate_style 解析按行扫 [FAIL]/[WARN]，
# "{}" 行不命中；JSON 类解析器 loads 后无 errors/check 块）
_STUB_EMPTY = "print('{}')\n"

# 假 validate_chapter（hard_gate 版）：emit LOCKED_FACT_CONFLICT（HARD_GATE_CODES 成员）
_FAKE_VC_HARD = """\
import json
report = {
    "summary": {"fatal": 0, "error": 1, "warning": 0, "info": 0, "total": 1},
    "errors": [{"code": "LOCKED_FACT_CONFLICT", "severity": "error",
                "msg": "test: body conflicts with locked fact",
                "fix_hint": "rewrite the conflicting paragraph"}],
}
print(json.dumps(report))
"""

# 假 validate_chapter（干净版）：零 issue（给「全 advisory 被豁免 → verdict=waived」用）
_FAKE_VC_CLEAN = """\
import json
print(json.dumps({"summary": {"fatal": 0, "error": 0, "warning": 0,
                              "info": 0, "total": 0}, "errors": []}))
"""

# 假 narrative_scanner（advisory 版）：repetition 检测块带 warning
# → _parse_scanner_json 合成 code=NARRATIVE_repetition（不在 HARD_GATE_CODES → advisory）
_FAKE_NS_ADVISORY = """\
import json
report = {"repetition": {"warning": "test: repeated phrase overuse",
                         "severity": "warning", "fix_hint": ""}}
print(json.dumps(report))
"""


def _make_sandbox(tmp: Path, with_hard_gate: bool = True):
    """建假项目（章节/第001章/第001章.txt）+ 假 scanner 目录。返回 (proj, scan_dir)。"""
    proj = tmp / "proj"
    ch_dir = proj / "章节" / "第001章"
    ch_dir.mkdir(parents=True)
    (ch_dir / "第001章.txt").write_text(
        "他推门进来。\n\n「你来了。」\n", encoding="utf-8")
    scan = tmp / "fake_scanners"
    scan.mkdir()
    for name in _ALL_SCANNER_NAMES:
        (scan / name).write_text(_STUB_EMPTY, encoding="utf-8")
    (scan / "validate_chapter.py").write_text(
        _FAKE_VC_HARD if with_hard_gate else _FAKE_VC_CLEAN, encoding="utf-8")
    (scan / "narrative_scanner.py").write_text(_FAKE_NS_ADVISORY, encoding="utf-8")
    return proj, scan


@contextmanager
def _patched_script_dir(scan_dir: Path):
    """monkeypatch audit_hub._SCRIPT_DIR → 假 scanner 目录（函数体运行时读模块全局）。"""
    old = ah._SCRIPT_DIR
    ah._SCRIPT_DIR = scan_dir
    try:
        yield
    finally:
        ah._SCRIPT_DIR = old


# ════════════════════════════════════════════════════════════════
# 1. _gate_level_for 条件降级（顾问制权力判定的最小单元）
# ════════════════════════════════════════════════════════════════

def test_gate_level_style_overlong_conditional_downgrade():
    """STYLE_单段超长：fatal/error → hard_gate（超例外 FAIL），warning → advisory（警告区可豁免）。"""
    assert ah._gate_level_for("STYLE_单段超长", "fatal") == "hard_gate"
    assert ah._gate_level_for("STYLE_单段超长", "error") == "hard_gate"
    assert ah._gate_level_for("STYLE_单段超长", "warning") == "advisory"


def test_gate_level_info_severity_never_hard_gate():
    """info = 低置信旁注（如 UNKNOWN_CHARACTER_DETECTED 的 NER 碎片），对任何 code 永不 hard_gate。

    2026-06-02 修：否则 NER 垃圾碎片会硬毙整 cluster（北极星⑤顾问非法官）。
    """
    assert ah._gate_level_for("UNKNOWN_CHARACTER_DETECTED", "info") == "advisory"
    # info 降级对 HARD_GATE_CODES 全体成员生效（含条件 code）
    assert ah._gate_level_for("LOCKED_FACT_CONFLICT", "info") == "advisory"
    assert ah._gate_level_for("STYLE_单段超长", "info") == "advisory"


def test_gate_level_default_mapping():
    """非 info 时：HARD_GATE_CODES 成员 → hard_gate，清单外 code 一律 advisory。"""
    assert ah._gate_level_for("LOCKED_FACT_CONFLICT", "error") == "hard_gate"
    assert ah._gate_level_for("UNKNOWN_CHARACTER_DETECTED", "error") == "hard_gate"
    assert ah._gate_level_for("NARRATIVE_repetition", "warning") == "advisory"
    assert ah._gate_level_for("SEMANTIC_aphorism", "error") == "advisory"


# ════════════════════════════════════════════════════════════════
# 2. scanner_registry 加载
# ════════════════════════════════════════════════════════════════

def test_load_scanner_registry_missing_or_corrupt_returns_empty():
    """registry 缺失 / JSON 损坏 → {}（不崩；registry 是元数据/一致性契约源，调度由 audit_hub tasks 硬编码）。"""
    with tempfile.TemporaryDirectory() as td:
        scan = Path(td)
        with _patched_script_dir(scan):
            assert ah.load_scanner_registry() == {}
            (scan / "scanner_registry.json").write_text("{broken json", encoding="utf-8")
            assert ah.load_scanner_registry() == {}


def test_load_scanner_registry_valid_roundtrip_and_real_file():
    """合法 registry 原样返回；真仓库 scanner_registry.json 也必须可加载（契约对账）。"""
    reg = {"_schema_version": 1,
           "scanners": {"validate_chapter": {"layer": "cluster",
                                             "script": "validate_chapter.py"}}}
    with tempfile.TemporaryDirectory() as td:
        scan = Path(td)
        (scan / "scanner_registry.json").write_text(
            json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        with _patched_script_dir(scan):
            assert ah.load_scanner_registry() == reg
    # 真仓库 registry（未 monkeypatch）：结构契约存在且含核心 scanner
    real = ah.load_scanner_registry()
    assert isinstance(real, dict) and "validate_chapter" in real.get("scanners", {})


# ════════════════════════════════════════════════════════════════
# 3. 聚合主流程（假 scanner 子进程 → 归一化 issue → 分类 → verdict）
# ════════════════════════════════════════════════════════════════

def test_aggregation_collects_fake_scanner_issues():
    """advisory + hard_gate 两路假 scanner 的 issue 都进报告，归类/路由正确。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, scan = _make_sandbox(tmp, with_hard_gate=True)
        with _patched_script_dir(scan):
            report = ah.audit_chapter(proj, 1, False, waivers=[])
        assert "_fatal" not in report, report.get("_fatal")
        by_code = {i["code"]: i for i in report["issues"]}
        # hard_gate issue：来自假 validate_chapter
        hard = by_code.get("LOCKED_FACT_CONFLICT")
        assert hard is not None, f"hard issue 丢失: {sorted(by_code)}"
        assert hard["gate_level"] == "hard_gate"
        assert hard["dimension"] == "剧情"  # LOCKED_ 前缀映射
        assert hard["source"] == "validate_chapter"
        # advisory issue：来自假 narrative_scanner（合成 code=NARRATIVE_<check>）
        adv = by_code.get("NARRATIVE_repetition")
        assert adv is not None, f"advisory issue 丢失: {sorted(by_code)}"
        assert adv["gate_level"] == "advisory"
        assert adv["severity"] == "warning"
        # verdict：hard_gate error 残留 → needs_agent + 进 pending_agent（带路由 agent）
        assert report["verdict"] == "needs_agent"
        pend = {p["code"]: p for p in report["pending_agent"]}
        assert "LOCKED_FACT_CONFLICT" in pend
        assert pend["LOCKED_FACT_CONFLICT"]["suggested_agent"] == "novel-validator-checker"
        # 两个假 scanner 都被记为 ok（exit 0 在各自 ok_set 内）
        st = {s["scanner"]: s for s in report["scanner_status"]}
        assert st["validate_chapter"]["ok"] and st["narrative_scanner"]["ok"]


def test_waiver_file_advisory_waived_hard_gate_immune():
    """waiver 文件同时豁免两 code：advisory 转 waived，hard_gate 强制忽略豁免仍 hard。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, scan = _make_sandbox(tmp, with_hard_gate=True)
        # 独立 waivers.json（顶层 waivers 布局 · _load_waivers 兼容路径之一）
        wpath = tmp / "waivers.json"
        wpath.write_text(json.dumps({"waivers": [
            {"code": "NARRATIVE_repetition",
             "reason": "本章重复短语是刻意的仪式吟唱排比，场景需要节奏复沓"},
            {"code": "LOCKED_FACT_CONFLICT",
             "reason": "尝试豁免硬闸（契约上必须被强制忽略）"},
        ]}, ensure_ascii=False), encoding="utf-8")
        waivers = ah._load_waivers(str(wpath))
        assert {w["code"] for w in waivers} == {"NARRATIVE_repetition",
                                               "LOCKED_FACT_CONFLICT"}
        with _patched_script_dir(scan):
            report = ah.audit_chapter(proj, 1, False, waivers=waivers)
        by_code = {i["code"]: i for i in report["issues"]}
        # advisory → waived=True + 理由落档
        adv = by_code["NARRATIVE_repetition"]
        assert adv["waived"] is True
        assert "排比" in adv["waive_reason"]
        # hard_gate → 豁免被强制忽略：waived 仍 False、仍 hard、仍进 pending_agent
        hard = by_code["LOCKED_FACT_CONFLICT"]
        assert hard["waived"] is False
        assert hard["gate_level"] == "hard_gate"
        pend_codes = {p["code"] for p in report["pending_agent"]}
        assert "LOCKED_FACT_CONFLICT" in pend_codes
        assert "NARRATIVE_repetition" not in pend_codes  # 被豁免的不修不派
        # waived_issues 段只含 advisory；计数对账
        assert {w["code"] for w in report["waived_issues"]} == {"NARRATIVE_repetition"}
        assert report["summary"]["waived"] == 1
        # hard_gate 残留 → 绝不判 waived/pass
        assert report["verdict"] == "needs_agent"


def test_all_advisory_waived_verdict_waived():
    """全部 advisory 都被合理豁免、无 hard_gate 残留 → verdict=waived（区别于 pass）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        proj, scan = _make_sandbox(tmp, with_hard_gate=False)  # validate_chapter 干净版
        waivers = [{"code": "NARRATIVE_repetition",
                    "reason": "刻意排比复沓，本场景为仪式吟唱"}]
        with _patched_script_dir(scan):
            report = ah.audit_chapter(proj, 1, False, waivers=waivers)
        assert report["summary"]["fatal"] == 0
        assert report["summary"]["error"] == 0
        assert report["summary"]["waived"] == 1
        assert report["pending_agent"] == []
        assert report["verdict"] == "waived"


# ════════════════════════════════════════════════════════════════
# C06 整段草稿剧本体 SCREENPLAY 扫（2026-06-27 · step3 真阻断点）
# ════════════════════════════════════════════════════════════════

def test_c06_screenplay_emits_hard_gate():
    """含（镜头）的草稿 → _scan_cluster_draft_screenplay emit hard_gate · severity error。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cluster_001_draft.txt"
        p.write_text("他抬起头，望向门外。\n\n（镜头拉远）\n\n风继续吹。", encoding="utf-8")
        issues = ah._scan_cluster_draft_screenplay(p)
        assert issues, "（镜头）应命中 SCREENPLAY"
        iss = issues[0]
        assert iss["code"] == "CHAPTER_END_FORBIDDEN_SCREENPLAY"
        assert iss["gate_level"] == "hard_gate"
        assert iss["severity"] == "error"
        # 经权威裁决口复核：error 严重度落 hard_gate（→ verdict needs_agent → exit 2）
        assert ah._gate_level_for(iss["code"], iss["severity"]) == "hard_gate"


def test_c06_screenplay_position_independent():
    """位置无关：（旁白）出现在草稿【中段】（非章末）也命中（整段硬扫）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cluster_001_draft.txt"
        p.write_text("开场第一段。\n\n（旁白：多年以后）\n\n" + "正文继续。\n\n" * 50, encoding="utf-8")
        issues = ah._scan_cluster_draft_screenplay(p)
        assert any(i["code"] == "CHAPTER_END_FORBIDDEN_SCREENPLAY" for i in issues)


def test_c06_closure_not_flagged_no_upgrade_regression():
    """🔴 防升格回归：语义收束句『灯熄了』绝不被整段 SCREENPLAY 扫升 hard_gate（保持 advisory 边界）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cluster_001_draft.txt"
        p.write_text("他关上门。\n\n灯熄了。\n\n一切安静下来。", encoding="utf-8")
        issues = ah._scan_cluster_draft_screenplay(p)
        assert issues == [], "收束句不是剧本体污染 · 整段层零 hard_gate（语义类保持 advisory）"


def test_c06_separator_not_flagged_at_draft_layer():
    """北极星⑤边界：物理分隔符 *** 须锚定章末 → 整段草稿层【不】硬扫（中段场景分隔某些作者合法）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cluster_001_draft.txt"
        p.write_text("场景一结束。\n\n***\n\n场景二开始。", encoding="utf-8")
        issues = ah._scan_cluster_draft_screenplay(p)
        assert issues == [], "*** 在整段层不硬扫（由 chapter_end_anchor_scan --hard-gate-only 章末位置敏感复扫）"


def test_c06_clean_draft_no_issue():
    """干净草稿（无剧本体标记）→ 零 issue。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cluster_001_draft.txt"
        p.write_text("他握紧刀柄，迈步走入祭坛深处。\n\n黑刀在掌心微微发烫。", encoding="utf-8")
        assert ah._scan_cluster_draft_screenplay(p) == []


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
