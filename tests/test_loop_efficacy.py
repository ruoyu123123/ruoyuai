"""learning_loop efficacy 闭环测试（2026-05-31）。

测的是 learning_loop 的【约束注入有效性追踪 + 无效约束自动停注】——对照 self_heal_engine
的 regression 范式，闭合「复盘→改进」开环：原 learning_loop 把复发问题升级成 recur_*
failure_pattern 注入下章 writer，但从不验证注入后该问题的后续误报是否真降。本层验证：

  1. 约束升级 → 记 efficacy 基线（baseline_rate + baseline_chapters）；
  2. 注入后复发率真降 → effective（继续注入）；
  3. 注入后复发率没降 / 反升 → ineffective + active=False（停注·advisory 软停·不硬删）；
  4. build_manifest.experience_entries 跳过 active=False 的约束（停注真生效）；
  5. 证据不足（注入后跑的章数 < 阈值）→ 维持 monitoring（不冤判）；
  6. 终态不抖动（effective 不回退·ineffective 维持停注·重升级不复活停注的约束）。

只测确定性纯函数层（不碰 LLM / agent）·零依赖 stdlib·复用 learning_loop 框架。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import learning_loop as ll  # noqa: E402
import build_manifest as bm  # noqa: E402


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _mk_audit(ch, code, dim="结构", severity="error", cluster_key=None,
              waived=False, extra_issues=None):
    """造一份最小 audit 报告（learning_loop ingest/scan 吃的结构）。"""
    issue = {"code": code, "dimension": dim, "severity": severity,
             "desc": f"{code} 样本", "waived": waived}
    a = {"chapter": ch, "issues": [issue] + (extra_issues or [])}
    if cluster_key:
        a["_cluster_mode"] = True
        a["_cluster_key"] = cluster_key
    return a


def _project_with_audits(tmp: Path, audits: list) -> Path:
    """把多份 audit 报告写进 _数据库/.audit/，供 --scan-recurring 全量扫描。"""
    db = tmp / "_数据库"
    adir = db / ".audit"
    adir.mkdir(parents=True, exist_ok=True)
    for i, a in enumerate(audits):
        if a.get("_cluster_mode"):
            name = f"cluster_{a.get('_cluster_key', i)}_audit.json"
        else:
            name = f"ch_{a.get('chapter', i):03d}_audit.json"
        (adir / name).write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    return tmp


def _ingest(tmp: Path, audit: dict, name: str) -> dict:
    """单份 audit 走 ingest_audit。"""
    p = tmp / "_数据库" / ".audit" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return ll.ingest_audit(tmp, p)


def _load_exp(tmp: Path) -> dict:
    return ll.load_experience(tmp)


def _fp_by_id(exp: dict, pid: str):
    return next((x for x in exp["failure_patterns"] if x.get("id") == pid), None)


# ═══════════════════════ 基线记录 ═══════════════════════

def test_baseline_recorded_on_escalation():
    """约束首次升级 → _efficacy_tracker 记基线（baseline_rate + baseline_chapters）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        # 同 code 连续 3 章 → 命中 RECUR_THRESHOLD=3 → 升级
        for ch in (1, 2, 3):
            _ingest(tmp, _mk_audit(ch, "PLOT_X"), f"ch_{ch:03d}_audit.json")
        exp = _load_exp(tmp)
        et = exp["_efficacy_tracker"]
        pid = "recur_结构_PLOT_X"
        assert pid in et, f"基线未记录: {list(et)}"
        base = et[pid]
        assert base["status"] == "monitoring"
        assert base["baseline_chapters"] == [1, 2, 3]
        # 3 次复发 / 3 章 = 1.0
        assert base["baseline_rate"] == 1.0
        assert _fp_by_id(exp, pid) is not None  # 已升级成 failure_pattern


def test_baseline_not_reset_on_re_escalation():
    """重升级（同 key 再命中）不重置基线 —— 基线必须锚最早注入点。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        for ch in (1, 2, 3):
            _ingest(tmp, _mk_audit(ch, "PLOT_X"), f"ch_{ch:03d}_audit.json")
        exp1 = _load_exp(tmp)
        base1 = dict(exp1["_efficacy_tracker"]["recur_结构_PLOT_X"])
        # 再来一章复发 → 重升级
        _ingest(tmp, _mk_audit(4, "PLOT_X"), "ch_004_audit.json")
        exp2 = _load_exp(tmp)
        base2 = exp2["_efficacy_tracker"]["recur_结构_PLOT_X"]
        assert base2["baseline_chapters"] == base1["baseline_chapters"]  # 没被章 4 污染
        assert base2["baseline_rate"] == base1["baseline_rate"]


# ═══════════════════════ 无效约束自动停注 ═══════════════════════

def test_ineffective_constraint_auto_stopped():
    """注入后复发率没降（持续复发）→ ineffective + failure_pattern active=False（停注）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 全量扫描：ch1-3 注入前复发（基线），ch4-8 注入后仍每章复发（没降）
        audits = [_mk_audit(ch, "PLOT_X") for ch in range(1, 9)]
        _project_with_audits(tmp, audits)
        res = ll.scan_recurring(tmp)
        pid = "recur_结构_PLOT_X"
        ineff_ids = {x["pattern_id"] for x in res["ineffective"]}
        assert pid in ineff_ids, f"无效约束未被标停注: {res['ineffective']}"
        exp = _load_exp(tmp)
        pat = _fp_by_id(exp, pid)
        assert pat is not None
        assert pat["active"] is False  # 停注
        assert pat["efficacy"]["verdict"] == "ineffective"
        assert exp["_efficacy_tracker"][pid]["status"] == "ineffective"


def test_effective_constraint_keeps_injecting():
    """注入后复发停止（误报真降到 0）→ effective + 约束保持注入（active 不置 False）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # ch1-3 注入前复发（基线 rate=1.0），ch4-8 注入后跑了 5 章但**0 复发**（约束有效）
        audits = [_mk_audit(ch, "PLOT_X") for ch in (1, 2, 3)]
        audits += [_mk_audit(ch, "OTHER_OK") for ch in (4, 5, 6, 7, 8)]  # 别的 code·PLOT_X 不再复发
        _project_with_audits(tmp, audits)
        res = ll.scan_recurring(tmp)
        pid = "recur_结构_PLOT_X"
        ineff_ids = {x["pattern_id"] for x in res["ineffective"]}
        assert pid not in ineff_ids  # 没被停注
        exp = _load_exp(tmp)
        eff = exp["_efficacy_tracker"][pid]
        assert eff["status"] == "effective", f"应判 effective，实为 {eff}"
        pat = _fp_by_id(exp, pid)
        # effective 约束不置 active=False（保持注入）
        assert pat.get("active") is not False


def test_insufficient_evidence_stays_monitoring():
    """注入后跑的章数 < EFFICACY_MIN_POST_CHAPTERS → 维持 monitoring（不冤判）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # ch1-3 基线，注入后只跑了 ch4 一章（< MIN=2）→ 证据不足
        audits = [_mk_audit(ch, "PLOT_X") for ch in (1, 2, 3)]
        audits += [_mk_audit(4, "OTHER_OK")]
        _project_with_audits(tmp, audits)
        ll.scan_recurring(tmp)
        exp = _load_exp(tmp)
        eff = exp["_efficacy_tracker"]["recur_结构_PLOT_X"]
        assert eff["status"] == "monitoring", f"证据不足应维持 monitoring，实为 {eff['status']}"


def test_post_rate_drop_below_baseline_is_effective():
    """注入后复发率虽 >0 但低于基线（真降）→ effective。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 基线：ch1-3 每章复发 → rate=1.0
        # 注入后：跑 ch4-9 共 6 章，只 ch4 复发一次 → post_rate=1/6≈0.167 < 1.0 → 真降
        audits = [_mk_audit(ch, "PLOT_X") for ch in (1, 2, 3, 4)]
        audits += [_mk_audit(ch, "OTHER_OK") for ch in (5, 6, 7, 8, 9)]
        _project_with_audits(tmp, audits)
        ll.scan_recurring(tmp)
        exp = _load_exp(tmp)
        eff = exp["_efficacy_tracker"]["recur_结构_PLOT_X"]
        assert eff["status"] == "effective"
        assert eff["post_rate"] < eff["baseline_rate"]


# ═══════════════════════ 终态不抖动 / 停注不复活 ═══════════════════════

def test_ineffective_not_reactivated_on_re_escalation():
    """已判 ineffective 的约束·后续重升级覆盖刷新时**不**复活（维持 active=False）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        audits = [_mk_audit(ch, "PLOT_X") for ch in range(1, 9)]
        _project_with_audits(tmp, audits)
        ll.scan_recurring(tmp)
        pid = "recur_结构_PLOT_X"
        exp = _load_exp(tmp)
        assert _fp_by_id(exp, pid)["active"] is False
        # 再来一章复发 → ingest 触发 _escalate_recurring 覆盖刷新该 pattern
        _ingest(tmp, _mk_audit(9, "PLOT_X"), "ch_009_audit.json")
        exp2 = _load_exp(tmp)
        pat = _fp_by_id(exp2, pid)
        assert pat is not None
        assert pat["active"] is False, "停注的无效约束被重升级复活了（前功尽弃）"


def test_effective_does_not_flip_back():
    """effective 终态不抖动回退 —— 再扫一次仍 effective。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        audits = [_mk_audit(ch, "PLOT_X") for ch in (1, 2, 3)]
        audits += [_mk_audit(ch, "OTHER_OK") for ch in (4, 5, 6, 7, 8)]
        _project_with_audits(tmp, audits)
        ll.scan_recurring(tmp)
        exp = _load_exp(tmp)
        assert exp["_efficacy_tracker"]["recur_结构_PLOT_X"]["status"] == "effective"
        ll.scan_recurring(tmp)  # 再扫
        exp2 = _load_exp(tmp)
        assert exp2["_efficacy_tracker"]["recur_结构_PLOT_X"]["status"] == "effective"


# ═══════════════════════ build_manifest 消费侧（停注真生效）═══════════════════════

def test_build_manifest_skips_inactive_failure_pattern():
    """experience_entries 跳过 active=False 的 failure_pattern（停注真切断注入链）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = tmp / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "写作经验.json").write_text(json.dumps({
            "success_patterns": [],
            "failure_patterns": [
                {"id": "recur_active", "category": "failure", "confidence": 0.95,
                 "trigger": "活跃约束", "scene_types": []},
                {"id": "recur_stopped", "category": "failure", "confidence": 0.95,
                 "trigger": "已停注约束", "scene_types": [], "active": False},
            ],
            "preferences": [],
        }, ensure_ascii=False), encoding="utf-8")
        # DatabaseScanner 暴露 experience_entries（消费 写作经验 failure_patterns）
        scanner = bm.DatabaseScanner(tmp, 1)
        entries = scanner.experience_entries()
        ids = {e.get("id") for e in entries}
        assert "recur_active" in ids       # 活跃约束仍注入
        assert "recur_stopped" not in ids  # 停注约束被跳过


# ═══════════════════════ 退化路径（ingest 无全章集）═══════════════════════

def test_ingest_no_observed_set_is_conservative():
    """--ingest 拿不到全章集（observed=None）→ 持续复发时仍能判 ineffective（分母=复发章）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        # ch1-3 升级（基线 rate=1.0），ch4-6 继续每章复发 → 注入后 post_recur=3 章·post_rate=1.0 没降
        for ch in (1, 2, 3, 4, 5, 6):
            _ingest(tmp, _mk_audit(ch, "PLOT_X"), f"ch_{ch:03d}_audit.json")
        exp = _load_exp(tmp)
        eff = exp["_efficacy_tracker"]["recur_结构_PLOT_X"]
        # 注入后 3 章均复发 → 没降 → ineffective + 停注
        assert eff["status"] == "ineffective"
        assert _fp_by_id(exp, "recur_结构_PLOT_X")["active"] is False
