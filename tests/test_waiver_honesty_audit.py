"""C09 WAIVER-HONESTY-AUDIT 回归测试（2026-06-27）。

【根因】advisory 豁免由 writer 自己在 cluster_changes.json self_eval.waivers 写——运动员当
裁判。旧 _load_waivers 唯一校验是「理由非空 + <300 字」：无数量上限、无去重、不校验被豁免
code 真存在（orphan）。本件加三道纯卫生防线 + 一个 META-only blanket 审计信号：

  1. _load_waivers 按 code 去重（保最长 reason · 冲突 warn）——行为中性。
  2. _apply_waivers orphan 豁免（code 不在本次 issue）→ 从 by_code 排除 + log（非静默 no-op）。
  3. _compute_waiver_audit 算 advisory_total/advisory_waived/waive_rate/blanket/orphan（纯函数）。
  4. learning_loop._track_waivers 把 waiver_audit 落 per-cluster ledger（喂自学习）。

【北极星护栏 = 本件最强回归锁】
  · blanket_suspected 只是 META flag —— 【绝不翻 verdict】（全 advisory 被合理豁免仍判 waived，
    不 block / cap-reject / downgrade-fail）。风格与通用爽文基线合法冲突的 cluster 应能全豁免。
  · hard_gate 豁免 → 【仍 force-ignore】（不可豁免，这条不动）。

只测确定性纯函数 + 假 scanner 子进程聚合，不碰 LLM / agent / 真项目。
"""
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import audit_hub as ah   # noqa: E402
import learning_loop as ll  # noqa: E402


# ═══════════════════════ 1. _load_waivers 去重 ═══════════════════════

def _write_waivers(tmp: Path, payload) -> str:
    p = tmp / "waivers.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_waivers_dedup_keeps_longest_reason():
    """同 code 重复声明 → 去重保一条·留最长 reason（更可能是具体到本 cluster 的真理由）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        wp = _write_waivers(tmp, {"waivers": [
            {"code": "NARRATIVE_repetition", "reason": "短"},
            {"code": "NARRATIVE_repetition", "reason": "更长的具体理由说明本场景刻意复沓排比"},
        ]})
        out = ah._load_waivers(wp)
        assert len(out) == 1
        assert out[0]["code"] == "NARRATIVE_repetition"
        assert out[0]["reason"] == "更长的具体理由说明本场景刻意复沓排比"


def test_load_waivers_dedup_distinct_codes_preserved():
    """去重只压同 code · 不同 code 全保留（不误删合法多豁免）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        wp = _write_waivers(tmp, {"waivers": [
            {"code": "NARRATIVE_gmc", "reason": "理由甲足够长字数"},
            {"code": "NARRATIVE_pov", "reason": "理由乙足够长字数"},
        ]})
        out = ah._load_waivers(wp)
        assert {w["code"] for w in out} == {"NARRATIVE_gmc", "NARRATIVE_pov"}


# ═══════════════════════ 2. _apply_waivers orphan + hard_gate ═══════════════════════

def test_apply_waivers_orphan_excluded_no_crash():
    """orphan 豁免（code 本次不存在）→ 不崩 · 只豁免真实 advisory · 不误匹配。"""
    issues = [{"code": "NARRATIVE_repetition", "gate_level": "advisory",
               "waived": False, "waive_reason": ""}]
    waivers = [{"code": "NARRATIVE_repetition", "reason": "刻意复沓"},
               {"code": "GHOST_CODE_NOT_PRESENT", "reason": "豁免一个不存在的 code"}]
    waived = ah._apply_waivers(issues, waivers)
    assert [i["code"] for i in waived] == ["NARRATIVE_repetition"]
    assert issues[0]["waived"] is True


def test_apply_waivers_hard_gate_force_ignore_regression():
    """🔴 回归锁：hard_gate 豁免仍 force-ignore（即便传了理由）· advisory 才 waived。"""
    hard = sorted(ah.HARD_GATE_CODES)[0]
    issues = [
        {"code": hard, "gate_level": "hard_gate", "waived": False, "waive_reason": ""},
        {"code": "NARRATIVE_repetition", "gate_level": "advisory",
         "waived": False, "waive_reason": ""},
    ]
    waivers = [{"code": hard, "reason": "我想豁免硬闸"},
               {"code": "NARRATIVE_repetition", "reason": "本场景口语刻意为之"}]
    waived = ah._apply_waivers(issues, waivers)
    assert issues[0]["waived"] is False, "hard_gate 项绝不可被豁免"
    assert issues[1]["waived"] is True
    assert all(i.get("gate_level") != "hard_gate" for i in waived)


def test_apply_waivers_empty_returns_list():
    """空豁免清单 → 返回 list（保持 _apply_waivers 历史契约 · 不改返回类型）。"""
    assert ah._apply_waivers([{"code": "A", "gate_level": "advisory"}], []) == []


# ═══════════════════════ 3. _compute_waiver_audit 纯函数 ═══════════════════════

def _adv(code):
    return {"code": code, "gate_level": "advisory"}


def _hg(code):
    return {"code": code, "gate_level": "hard_gate"}


def _waived(code, reason):
    return {"code": code, "gate_level": "advisory", "waive_reason": reason}


def test_compute_waiver_audit_orphan_codes_and_counts():
    all_issues = [_adv("A")]
    waivers = [{"code": "A", "reason": "r"}, {"code": "ORPHAN", "reason": "x"}]
    meta = ah._compute_waiver_audit(all_issues, waivers, [_waived("A", "r")])
    assert meta["orphan_codes"] == ["ORPHAN"]
    assert meta["advisory_total"] == 1
    assert meta["advisory_waived"] == 1


def test_compute_waiver_audit_blanket_by_rate():
    """5 advisory · 豁免 4 → rate 0.8 > 0.7 → blanket_suspected。"""
    all_issues = [_adv(f"C{i}") for i in range(5)]
    waived = [_waived(f"C{i}", f"各不相同的理由{i}") for i in range(4)]
    waivers = [{"code": f"C{i}", "reason": f"各不相同的理由{i}"} for i in range(4)]
    meta = ah._compute_waiver_audit(all_issues, waivers, waived)
    assert meta["waive_rate"] == 0.8
    assert meta["blanket_suspected"] is True
    assert meta["repeated_reason_codes"] == {}  # 理由各异 → 非「同理由刷多 code」


def test_compute_waiver_audit_blanket_by_repeated_reason():
    """rate 低（0.3）但单一 reason 映射 >=3 distinct code → 仍 blanket_suspected。"""
    all_issues = [_adv(f"C{i}") for i in range(10)]
    same = "统统都是刻意为之的作者签名"
    waived = [_waived(f"C{i}", same) for i in range(3)]
    waivers = [{"code": f"C{i}", "reason": same} for i in range(3)]
    meta = ah._compute_waiver_audit(all_issues, waivers, waived)
    assert meta["waive_rate"] == 0.3
    assert meta["blanket_suspected"] is True
    assert list(meta["repeated_reason_codes"].values())[0] == ["C0", "C1", "C2"]


def test_compute_waiver_audit_clean_not_blanket():
    """低豁免率 + 理由不重复 → blanket_suspected=False（合法少量豁免不被误判）。"""
    all_issues = [_adv(f"C{i}") for i in range(10)]
    meta = ah._compute_waiver_audit(
        all_issues, [{"code": "C0", "reason": "唯一且具体的理由"}],
        [_waived("C0", "唯一且具体的理由")])
    assert meta["blanket_suspected"] is False
    assert meta["waive_rate"] == 0.1
    assert meta["orphan_codes"] == []


def test_compute_waiver_audit_hard_gate_not_in_denominator():
    """hard_gate 不计入 advisory_total（豁免分母只含 advisory）。"""
    all_issues = [_hg("LOCKED_FACT_CONFLICT"), _adv("A")]
    meta = ah._compute_waiver_audit(
        all_issues, [{"code": "A", "reason": "r"}], [_waived("A", "r")])
    assert meta["advisory_total"] == 1
    assert meta["waive_rate"] == 1.0


def test_compute_waiver_audit_empty_no_div_zero():
    """无 issue / 无豁免 → waive_rate 0.0 · 不除零崩。"""
    meta = ah._compute_waiver_audit([], [], [])
    assert meta["waive_rate"] == 0.0
    assert meta["blanket_suspected"] is False
    assert meta["orphan_codes"] == []


# ═══════════════════════ 4. audit_chapter 集成（假 scanner） ═══════════════════════

_STUB_EMPTY = "print('{}')\n"
_ALL_SCANNER_NAMES = [
    "validate_chapter.py", "validate_style.py", "narrative_scanner.py",
    "plot_structure_scanner.py", "hook_strength_scanner.py",
    "golden_three_scanner.py", "semantic_slop_scanner.py",
    "narrative_short_sentence_scanner.py", "repeat_noun_density_scanner.py",
]
_FAKE_VC_CLEAN = (
    "import json\n"
    'print(json.dumps({"summary": {"fatal":0,"error":0,"warning":0,"info":0,"total":0},'
    ' "errors": []}))\n'
)
_FAKE_VC_HARD = (
    "import json\n"
    'print(json.dumps({"summary": {"fatal":0,"error":1,"warning":0,"info":0,"total":1},'
    ' "errors":[{"code":"LOCKED_FACT_CONFLICT","severity":"error","msg":"conflict",'
    '"fix_hint":"fix"}]}))\n'
)
# 4 个 advisory 检测块（NARRATIVE_DIM 合法 check：gmc/microten/repetition/pov · 均非 HARD_GATE）
_FAKE_NS_MULTI = (
    "import json\n"
    'print(json.dumps({'
    '"gmc": {"warning":"w","severity":"warning"},'
    '"microten": {"warning":"w","severity":"warning"},'
    '"repetition": {"warning":"w","severity":"warning"},'
    '"pov": {"warning":"w","severity":"warning"}'
    '}))\n'
)


def _make_sandbox(tmp: Path, with_hard_gate: bool = False):
    proj = tmp / "proj"
    ch_dir = proj / "章节" / "第001章"
    ch_dir.mkdir(parents=True)
    (ch_dir / "第001章.txt").write_text("他推门进来。\n\n「你来了。」\n", encoding="utf-8")
    scan = tmp / "fake_scanners"
    scan.mkdir()
    for name in _ALL_SCANNER_NAMES:
        (scan / name).write_text(_STUB_EMPTY, encoding="utf-8")
    (scan / "validate_chapter.py").write_text(
        _FAKE_VC_HARD if with_hard_gate else _FAKE_VC_CLEAN, encoding="utf-8")
    (scan / "narrative_scanner.py").write_text(_FAKE_NS_MULTI, encoding="utf-8")
    return proj, scan


@contextmanager
def _patched_script_dir(scan_dir: Path):
    old = ah._SCRIPT_DIR
    ah._SCRIPT_DIR = scan_dir
    try:
        yield
    finally:
        ah._SCRIPT_DIR = old


def test_integration_blanket_waiver_meta_only_verdict_not_flipped():
    """🔴 核心回归锁：全 4 advisory 被豁免 → waive_rate 1.0 触发 blanket_suspected，
    但 verdict 仍判 waived（META-only · 绝不 block / cap-reject / downgrade-fail）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, scan = _make_sandbox(tmp, with_hard_gate=False)
        waivers = [
            {"code": "NARRATIVE_gmc", "reason": "本场景刻意压缩目标动机"},
            {"code": "NARRATIVE_microten", "reason": "节奏留白是作者签名"},
            {"code": "NARRATIVE_repetition", "reason": "仪式吟唱排比复沓"},
            {"code": "NARRATIVE_pov", "reason": "限知视角切换是设计"},
        ]
        with _patched_script_dir(scan):
            report = ah.audit_chapter(proj, 1, False, waivers=waivers)
        assert "_fatal" not in report, report.get("_fatal")
        wa = report["waiver_audit"]
        assert wa["advisory_total"] == 4
        assert wa["advisory_waived"] == 4
        assert wa["waive_rate"] == 1.0
        assert wa["blanket_suspected"] is True
        # META-ONLY 回归锁：blanket 绝不翻 verdict
        assert report["verdict"] == "waived"
        assert report["summary"]["waived"] == 4
        assert report["summary"]["fatal"] == 0 and report["summary"]["error"] == 0
        assert report["pending_agent"] == []


def test_integration_hard_gate_waiver_force_ignored_with_meta():
    """🔴 回归锁：hard_gate 豁免 force-ignore → verdict needs_agent（绝不 waived）；
    orphan 豁免落 meta；hard_gate 不计入 advisory_total。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, scan = _make_sandbox(tmp, with_hard_gate=True)
        waivers = [
            {"code": "LOCKED_FACT_CONFLICT", "reason": "想豁免硬闸（契约必须强制忽略）"},
            {"code": "NARRATIVE_gmc", "reason": "刻意压缩动机"},
            {"code": "NARRATIVE_microten", "reason": "节奏留白"},
            {"code": "NARRATIVE_repetition", "reason": "复沓排比"},
            {"code": "NARRATIVE_pov", "reason": "限知视角"},
            {"code": "GHOST_NOT_PRESENT", "reason": "豁免一个不存在的 code"},
        ]
        with _patched_script_dir(scan):
            report = ah.audit_chapter(proj, 1, False, waivers=waivers)
        by_code = {i["code"]: i for i in report["issues"]}
        assert by_code["LOCKED_FACT_CONFLICT"]["waived"] is False
        assert "LOCKED_FACT_CONFLICT" in {p["code"] for p in report["pending_agent"]}
        assert report["verdict"] == "needs_agent"
        wa = report["waiver_audit"]
        assert "GHOST_NOT_PRESENT" in wa["orphan_codes"]
        assert wa["advisory_total"] == 4   # hard_gate 不进分母
        assert wa["advisory_waived"] == 4


# ═══════════════════════ 5. learning_loop per-cluster ledger ═══════════════════════

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _ingest(tmp: Path, audit: dict, name: str) -> dict:
    adir = tmp / "_数据库" / ".audit"
    adir.mkdir(parents=True, exist_ok=True)
    p = adir / name
    p.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return ll.ingest_audit(tmp, p)


def test_ledger_records_waiver_audit_chapter_key():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        audit = {
            "chapter": 7, "issues": [],
            "waived_issues": [{"code": "NARRATIVE_repetition", "dimension": "风格",
                               "waive_reason": "刻意复沓"}],
            "waiver_audit": {"waive_rate": 0.83, "advisory_total": 6, "advisory_waived": 5,
                             "blanket_suspected": True, "orphan_codes": ["GHOST"],
                             "repeated_reason_codes": {}},
        }
        _ingest(tmp, audit, "ch_007_audit.json")
        rec = ll.load_experience(tmp)["_waiver_audit_ledger"]["ch::7"]
        assert rec["blanket_suspected"] is True
        assert rec["waive_rate"] == 0.83
        assert rec["orphan_codes"] == ["GHOST"]
        assert "ts" in rec


def test_ledger_records_waiver_audit_cluster_key():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        audit = {
            "chapter": 9000, "_cluster_mode": True, "_cluster_key": "001",
            "issues": [], "waived_issues": [{"code": "NARRATIVE_pov", "waive_reason": "设计"}],
            "waiver_audit": {"waive_rate": 0.2, "advisory_total": 5, "advisory_waived": 1,
                             "blanket_suspected": False, "orphan_codes": [],
                             "repeated_reason_codes": {}},
        }
        _ingest(tmp, audit, "cluster_001_audit.json")
        ledger = ll.load_experience(tmp)["_waiver_audit_ledger"]
        assert "cluster::001" in ledger
        assert ledger["cluster::001"]["blanket_suspected"] is False


def test_ledger_blanket_does_not_escalate_or_block():
    """🔴 北极星护栏：blanket ledger 喂自学习 · 绝不制造 failure_pattern / 升级（META-only）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        audit = {
            "chapter": 3, "issues": [],
            "waived_issues": [{"code": f"C{i}", "waive_reason": "统一理由"} for i in range(4)],
            "waiver_audit": {"waive_rate": 1.0, "advisory_total": 4, "advisory_waived": 4,
                             "blanket_suspected": True, "orphan_codes": [],
                             "repeated_reason_codes": {"统一理由": ["C0", "C1", "C2", "C3"]}},
        }
        res = _ingest(tmp, audit, "ch_003_audit.json")
        assert res["escalated"] == []   # 豁免不进复发升级链
        exp = ll.load_experience(tmp)
        assert exp["failure_patterns"] == []  # blanket 不制造 failure_pattern
        assert exp["_waiver_audit_ledger"]["ch::3"]["blanket_suspected"] is True


def test_ledger_absent_when_no_waiver_audit():
    """audit 无 waiver_audit 段（旧报告）→ ledger 不写该键 · 不崩（向后兼容）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        audit = {"chapter": 1, "issues": [],
                 "waived_issues": [{"code": "NARRATIVE_pov", "waive_reason": "r"}]}
        _ingest(tmp, audit, "ch_001_audit.json")
        assert ll.load_experience(tmp)["_waiver_audit_ledger"] == {}
