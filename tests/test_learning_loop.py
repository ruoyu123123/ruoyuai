"""learning_loop.py 专属回归测试（聚焦尚未被间接覆盖的核心确定性逻辑）。

learning_loop 已被 4 个测试间接覆盖各自切面：
  · test_loop_efficacy.py        —— efficacy 闭环（baseline/effective/ineffective/monitoring）
  · test_reflect_attrib.py       —— reflect 归因（skill 段落级 credit-assignment + drift）
  · test_l2_1_pid.py             —— _quantized_delta_hint / accumulate_pid_state_from_calibration
  · test_experience_atomic_write.py —— save/load_experience 原子写往返 + _empty_experience

本件**聚焦上述 4 件没测到的核心算法/分支/边界/退出码**，互不重复：
  1. merge_reflection / _route_entry —— entry 按 category 分流、同 id 去重、legacy entries、FATAL exit
  2. _issue_key —— dimension::code 键 + check/desc 回退
  3. _is_consecutive / _recur_rate —— 复发连续段判定 + 复发率纯函数
  4. ingest_audit —— 复发计数 + 升级阈值 + info 级别不学 + 豁免不进复发链 + cluster:: 前缀 + FATAL exit
  5. 豁免校准链：_collect_waived_issues / _track_waivers / _build_calibration_suggestions
     （add_scene_adaptation vs adjust_threshold 分流 + _waivers_from_changes 兜底源）
  6. scan_recurring —— meta_problem 高置信 vs 候选 + DATA_GAP 排除 + 陈旧 recur_* 剪除 + 空报告
  7. _prune_and_decay —— 过期清理 / 衰减 / 无时间戳迁移 / 损坏时间戳重置
  8. main() CLI 退出码（0 正常 / 1 复发已升级 / 2 致命错误）

约定：仅标准库·无参数 test_*·tempfile·encoding=utf-8·真 import 真调用锁真实行为。
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import learning_loop as ll  # noqa: E402

_TARGET = _SCRIPTS / "learning_loop.py"


# ═══════════════════════ 公共脚手架 ═══════════════════════

def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_audit(tmp: Path, name: str, audit: dict) -> Path:
    adir = tmp / "_数据库" / ".audit"
    adir.mkdir(parents=True, exist_ok=True)
    p = adir / name
    p.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
    return p


def _audit(ch, code, dim="结构", severity="error", waived=False, **extra):
    issue = {"code": code, "dimension": dim, "severity": severity,
             "desc": f"{code} 样本第{ch}章", "waived": waived}
    a = {"chapter": ch, "issues": [issue]}
    a.update(extra)
    return a


def _ingest(tmp: Path, audit: dict, name: str) -> dict:
    p = _write_audit(tmp, name, audit)
    return ll.ingest_audit(tmp, p)


def _run_cli(tmp: Path, *args):
    """跑真 CLI（含 sys.exit）。子进程 stdout 走控制台编码 → errors='replace' 容错。
    断言只锚 returncode（确定性权威）。"""
    p = subprocess.run([sys.executable, str(_TARGET), str(tmp), *args],
                       capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


# ═══════════════════════ 1. merge_reflection / _route_entry ═══════════════════════

def test_route_entry_buckets_by_category():
    """_route_entry 按 category 分流 success/failure，未知 category → skip。"""
    exp = ll._empty_experience()
    assert ll._route_entry(exp, {"category": "success", "id": "s1"}) == "success"
    assert ll._route_entry(exp, {"category": "FAILURE", "id": "f1"}) == "failure"  # 大小写归一
    assert ll._route_entry(exp, {"category": "musing", "id": "m1"}) == "skip"
    assert ll._route_entry(exp, "not-a-dict") == "skip"
    assert len(exp["success_patterns"]) == 1
    assert len(exp["failure_patterns"]) == 1
    # _stamp_updated 给入库条目盖了时间戳
    assert "updated_at" in exp["success_patterns"][0]


def test_route_entry_dedup_same_id():
    """同 id 后写覆盖（reflector 重跑同章不产生重复条目）。"""
    exp = ll._empty_experience()
    ll._route_entry(exp, {"category": "failure", "id": "f1", "trigger": "v1"})
    ll._route_entry(exp, {"category": "failure", "id": "f1", "trigger": "v2"})
    fp = exp["failure_patterns"]
    assert len(fp) == 1, f"同 id 未去重: {fp}"
    assert fp[0]["trigger"] == "v2"  # 后写覆盖


def test_merge_reflection_entries_layout():
    """reflector 产 {ch, entries:[...]} → 按 category 分流进权威结构并落盘。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        refl = {"ch": 7, "note": "第7章复盘",
                "entries": [{"category": "success", "id": "s1", "trigger": "钩子强"},
                            {"category": "failure", "id": "f1", "trigger": "节奏拖"},
                            {"category": "neutral", "id": "n1"}]}
        rp = tmp / "refl.json"
        rp.write_text(json.dumps(refl, ensure_ascii=False), encoding="utf-8")
        res = ll.merge_reflection(tmp, rp)
        assert res["routed"] == {"success": 1, "failure": 1, "skip": 1}
        exp = ll.load_experience(tmp)
        assert len(exp["success_patterns"]) == 1
        assert len(exp["failure_patterns"]) == 1


def test_merge_reflection_authoritative_layout():
    """reflector 直出权威结构 {success_patterns, failure_patterns} → 也能合并（情况 A）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        refl = {"success_patterns": [{"id": "s1", "trigger": "a"}],
                "failure_patterns": [{"id": "f1", "trigger": "b"}]}
        rp = tmp / "refl.json"
        rp.write_text(json.dumps(refl, ensure_ascii=False), encoding="utf-8")
        res = ll.merge_reflection(tmp, rp)
        assert res["routed"]["success"] == 1
        assert res["routed"]["failure"] == 1
        exp = ll.load_experience(tmp)
        # setdefault 的 category 已补齐
        assert exp["success_patterns"][0]["category"] == "success"


def test_load_experience_migrates_legacy_entries():
    """历史裸 entries 结构 → load_experience 自动分流进权威结构。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        (db / "写作经验.json").write_text(json.dumps({
            "entries": [{"category": "success", "id": "s1"},
                        {"category": "failure", "id": "f1"}]
        }, ensure_ascii=False), encoding="utf-8")
        exp = ll.load_experience(tmp)
        assert "entries" not in exp  # 旧裸字段已被 pop
        assert len(exp["success_patterns"]) == 1
        assert len(exp["failure_patterns"]) == 1


# ═══════════════════════ 2. _issue_key ═══════════════════════

def test_issue_key_dimension_code():
    """_issue_key = dimension::code。"""
    assert ll._issue_key({"dimension": "结构", "code": "PLOT_X"}) == "结构::PLOT_X"


def test_issue_key_fallbacks():
    """无 code → 退回 check；无 check → 退回 desc[:20]；无 dimension → 'unknown'。"""
    assert ll._issue_key({"check": "validate_style"}) == "unknown::validate_style"
    k = ll._issue_key({"desc": "这是一段很长很长很长很长很长很长的描述文本超过二十字"})
    assert k.startswith("unknown::")
    # desc 被截断到 20 字
    assert len(k.split("::", 1)[1]) <= 20


# ═══════════════════════ 3. _is_consecutive / _recur_rate ═══════════════════════

def test_is_consecutive():
    """连续段判定：存在长度 >= n 的连续整数段才 True。"""
    assert ll._is_consecutive([1, 2], 2) is True
    assert ll._is_consecutive([1, 3, 5], 2) is False     # 间隔无连续
    assert ll._is_consecutive([2, 3, 4], 3) is True
    assert ll._is_consecutive([1, 2, 4, 5, 6], 3) is True  # 后段 4,5,6 连续
    assert ll._is_consecutive([7], 2) is False           # 不足 n
    assert ll._is_consecutive([3, 3, 4], 2) is True       # 去重后 3,4 连续


def test_recur_rate():
    """复发率 = count / 去重章数；无章 → 0.0。"""
    assert ll._recur_rate(3, [1, 2, 3]) == 1.0
    assert ll._recur_rate(2, [1, 1, 2]) == 1.0       # 去重后 2 章
    assert ll._recur_rate(0, []) == 0.0
    assert ll._recur_rate(1, [5]) == 1.0


# ═══════════════════════ 4. ingest_audit 复发追踪 / 升级 / 过滤 ═══════════════════════

def test_ingest_escalates_at_threshold():
    """同 code 连续 3 章命中 RECUR_THRESHOLD=3 → 升级为 failure_pattern（连续 → confidence 0.95）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2):
            res = _ingest(tmp, _audit(ch, "PLOT_X"), f"ch_{ch:03d}_audit.json")
            assert res["escalated"] == []  # 不足阈值
        res = _ingest(tmp, _audit(3, "PLOT_X"), "ch_003_audit.json")
        assert len(res["escalated"]) == 1
        e = res["escalated"][0]
        assert e["id"] == "recur_结构_PLOT_X"
        assert e["confidence"] == 0.95  # 1,2,3 连续 → 约束升级级别
        assert e["recurrence"] == 3


def test_ingest_non_consecutive_lower_confidence():
    """累计命中但不连续 → confidence=0.8（高频警示·非约束升级）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 3, 5):  # 不连续
            res = _ingest(tmp, _audit(ch, "PLOT_X"), f"ch_{ch:03d}_audit.json")
        assert len(res["escalated"]) == 1
        assert res["escalated"][0]["confidence"] == 0.8
        assert res["escalated"][0]["severity"] == "高频警示"


def test_ingest_info_severity_not_learned():
    """info 级别问题不进复发链（只学 fatal/error/warning）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2, 3):
            _ingest(tmp, _audit(ch, "TRIVIAL", severity="info"), f"ch_{ch:03d}_audit.json")
        exp = ll.load_experience(tmp)
        assert exp["_recurrence_tracker"] == {}  # info 完全没进追踪
        assert exp["failure_patterns"] == []


def test_ingest_waived_issue_not_in_recurrence():
    """被 AI 豁免的 issue 不进复发链（顾问制：正当豁免不该反向升级成硬约束）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2, 3):
            _ingest(tmp, _audit(ch, "STYLE_X", waived=True), f"ch_{ch:03d}_audit.json")
        exp = ll.load_experience(tmp)
        # 复发追踪空（豁免不进），但豁免追踪有计数
        assert exp["_recurrence_tracker"] == {}
        assert exp["failure_patterns"] == []
        assert "STYLE_X" in exp["_waiver_tracker"]
        assert exp["_waiver_tracker"]["STYLE_X"]["count"] == 3


def test_ingest_cluster_mode_key_prefix():
    """cluster 视野 audit → 复发 key 加 cluster:: 前缀（与章节视野隔离不混算）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        a = _audit(1, "PLOT_X", _cluster_mode=True, _cluster_key="cluster_001")
        _ingest(tmp, a, "cluster_001_audit.json")
        exp = ll.load_experience(tmp)
        keys = list(exp["_recurrence_tracker"])
        assert keys == ["cluster::结构::PLOT_X"]
        rec = exp["_recurrence_tracker"]["cluster::结构::PLOT_X"]
        assert rec["_view_mode"] == "cluster"
        assert "cluster_001" in rec["_cluster_keys"]


def test_ingest_same_chapter_same_code_counts_once():
    """同章重复同 code → 只计一次（seen_keys 去重）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        a = {"chapter": 1, "issues": [
            {"code": "PLOT_X", "dimension": "结构", "severity": "error", "desc": "a"},
            {"code": "PLOT_X", "dimension": "结构", "severity": "error", "desc": "b"},
        ]}
        _ingest(tmp, a, "ch_001_audit.json")
        exp = ll.load_experience(tmp)
        assert exp["_recurrence_tracker"]["结构::PLOT_X"]["count"] == 1


# ═══════════════════════ 5. 豁免校准链 ═══════════════════════

def test_collect_waived_issues_prefers_waived_block():
    """优先用 audit 的 waived_issues 段；退回扫 issues 里 waived==True。"""
    a = {"waived_issues": [{"code": "STYLE_A", "dimension": "风格", "waive_reason": "r"}],
         "issues": [{"code": "STYLE_B", "waived": True}]}
    out = ll._collect_waived_issues(a)
    codes = [w["code"] for w in out]
    assert codes == ["STYLE_A"]  # waived_issues 段优先·不再扫 issues


def test_collect_waived_issues_fallback_to_issues():
    """无 waived_issues 段 → 退回扫 issues 里 waived==True 的项。"""
    a = {"issues": [{"code": "STYLE_B", "dimension": "风格", "waived": True},
                    {"code": "STYLE_C", "waived": False}]}
    out = ll._collect_waived_issues(a)
    assert [w["code"] for w in out] == ["STYLE_B"]


def test_waiver_calibration_adjust_threshold():
    """同 code 跨 3 章被豁免（不集中单一场景类型）→ adjust_threshold 校准建议。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2, 3):
            a = {"chapter": ch, "issues": [],
                 "waived_issues": [{"code": "STYLE_对话占比", "dimension": "风格",
                                    "waive_reason": f"第{ch}章合理豁免"}]}
            res = _ingest(tmp, a, f"ch_{ch:03d}_audit.json")
        calib = res["calibration"]
        assert len(calib) == 1
        c = calib[0]
        assert c["code"] == "STYLE_对话占比"
        assert c["waived_count"] == 3
        assert c["suggestion_type"] == "adjust_threshold"  # 无 scene_type 集中
        # L2-1：被控 4 键之一 → 附量化幅度提示
        assert "quantized_delta" in c
        assert c["quantized_delta"]["controlled_key"] == "dialogue_ratio"


def test_waiver_calibration_below_threshold_no_suggestion():
    """豁免次数 < WAIVER_CALIBRATION_THRESHOLD=3 → 不产校准建议。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2):  # 只 2 次
            a = {"chapter": ch, "issues": [],
                 "waived_issues": [{"code": "STYLE_X", "waive_reason": "r"}]}
            res = _ingest(tmp, a, f"ch_{ch:03d}_audit.json")
        assert res["calibration"] == []


def test_waiver_empty_reason_discarded_in_changes_source():
    """_waivers_from_changes：空 reason 的豁免被丢弃（防空理由刷掉工具校准）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        # 造 _changes.json，含一条有理由 + 一条空理由
        chdir = tmp / "章节" / "第001章"
        chdir.mkdir(parents=True, exist_ok=True)
        (chdir / "第001章_changes.json").write_text(json.dumps({
            "self_eval": {"waivers": [
                {"code": "STYLE_有理由", "reason": "本场景合理"},
                {"code": "STYLE_空理由", "reason": ""},
                {"code": "", "reason": "空code"},
            ]}
        }, ensure_ascii=False), encoding="utf-8")
        out = ll._waivers_from_changes(tmp, 1)
        codes = [w["code"] for w in out]
        if ll.cio is None:
            # chapter_io 缺失环境 → 降级空（依然不报错）
            assert out == []
        else:
            assert "STYLE_有理由" in codes
            assert "STYLE_空理由" not in codes  # 空理由丢弃
            assert "" not in codes              # 空 code 丢弃


# ═══════════════════════ 6. scan_recurring 元问题 / 剪除 / 空报告 ═══════════════════════

def test_scan_recurring_empty_no_reports():
    """无任何历史 audit 报告 → 平稳返回空结果（不报错·不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        res = ll.scan_recurring(tmp)
        assert res["escalated"] == []
        assert res["meta_problems"] == []
        assert res["calibration"] == []


def test_scan_recurring_meta_problem_high_confidence():
    """issue 带 meta_suspect → 高置信元问题（疑似审核器误判）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2):
            a = {"chapter": ch, "issues": [
                {"code": "STYLE_VAL_BUG", "dimension": "风格", "severity": "error",
                 "desc": "validate_style 把 CHANGES 当正文", "meta_suspect": True}]}
            _write_audit(tmp, f"ch_{ch:03d}_audit.json", a)
        res = ll.scan_recurring(tmp)
        highs = [m for m in res["meta_problems"] if m.get("confidence") == "high"]
        assert len(highs) >= 1
        assert any("meta_suspect" in m["reason"] for m in highs)


def test_scan_recurring_meta_candidate_and_data_gap_excluded():
    """100% 章命中非数据缺失 code → candidate 元问题；数据缺失类 code（含 MISSING）排除。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        # 3 章全命中 PACING_BUG（非数据缺失）+ FORESHADOWING_MISSING（数据缺失·应排除）
        for ch in (1, 2, 3):
            a = {"chapter": ch, "issues": [
                {"code": "PACING_BUG", "dimension": "节奏", "severity": "warning",
                 "desc": "节奏问题"},
                {"code": "FORESHADOWING_MISSING", "dimension": "伏笔", "severity": "error",
                 "desc": "伏笔未声明 MISSING"}]}
            _write_audit(tmp, f"ch_{ch:03d}_audit.json", a)
        res = ll.scan_recurring(tmp)
        cand_keys = [m["key"] for m in res["meta_problems"]
                     if m.get("confidence") == "candidate"]
        assert any("PACING_BUG" in k for k in cand_keys), f"非数据缺失 100% 命中应成候选: {cand_keys}"
        assert all("MISSING" not in k for k in cand_keys), "数据缺失类不该进元问题候选"


def test_scan_recurring_prunes_stale_recur_pattern():
    """全量重建：某 code 不再复现 → 对应陈旧 recur_* failure_pattern 被剪除（不再注入 writer）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        # 先 ingest 让 OLD_X 升级成 recur_*
        for ch in (1, 2, 3):
            _ingest(tmp, _audit(ch, "OLD_X"), f"ch_{ch:03d}_audit.json")
        exp = ll.load_experience(tmp)
        assert any(x["id"] == "recur_结构_OLD_X" for x in exp["failure_patterns"])
        # 删掉 OLD_X 的所有 audit，换成全新的报告（无 OLD_X）→ 全量重建后 OLD_X 不在 tracker
        adir = tmp / "_数据库" / ".audit"
        for p in adir.glob("*.json"):
            p.unlink()
        _write_audit(tmp, "ch_010_audit.json", _audit(10, "BRAND_NEW"))
        ll.scan_recurring(tmp)
        exp2 = ll.load_experience(tmp)
        ids = {x["id"] for x in exp2["failure_patterns"]}
        assert "recur_结构_OLD_X" not in ids, "陈旧 recur_* 未被剪除"


# ═══════════════════════ 7. _prune_and_decay 时间维度 ═══════════════════════

def test_prune_and_decay_expires_old():
    """updated_at 超 EXPIRY_DAYS → 删除（pattern 视为过期）。"""
    exp = ll._empty_experience()
    old = (datetime.now() - timedelta(days=ll.EXPIRY_DAYS + 5)).strftime("%Y-%m-%d %H:%M:%S")
    exp["failure_patterns"].append({"id": "f1", "confidence": 0.9, "updated_at": old,
                                    "trigger": "陈旧"})
    rep = ll._prune_and_decay(exp)
    assert exp["failure_patterns"] == []           # 被删
    assert len(rep["pruned"]) == 1
    assert rep["pruned"][0]["id"] == "f1"


def test_prune_and_decay_decays_mid_age():
    """DECAY_DAYS < age < EXPIRY_DAYS → confidence *= 0.8（保留但降权）。"""
    exp = ll._empty_experience()
    mid = (datetime.now() - timedelta(days=ll.DECAY_DAYS + 2)).strftime("%Y-%m-%d %H:%M:%S")
    exp["success_patterns"].append({"id": "s1", "confidence": 1.0, "updated_at": mid})
    rep = ll._prune_and_decay(exp)
    assert len(exp["success_patterns"]) == 1       # 没删
    assert exp["success_patterns"][0]["confidence"] == 0.8  # 1.0 * 0.8
    assert len(rep["decayed"]) == 1


def test_prune_and_decay_migrates_missing_and_corrupt_ts():
    """无 updated_at / 损坏时间戳的旧条目 → 本轮标 now 放行（非破坏式迁移·不丢数据）。"""
    exp = ll._empty_experience()
    exp["failure_patterns"].append({"id": "no_ts", "confidence": 0.9})            # 无时间戳
    exp["failure_patterns"].append({"id": "bad_ts", "confidence": 0.9,
                                    "updated_at": "garbage-not-a-date"})           # 损坏
    rep = ll._prune_and_decay(exp)
    assert len(exp["failure_patterns"]) == 2, "迁移不该丢任何数据"
    for p in exp["failure_patterns"]:
        assert "updated_at" in p and p["updated_at"] != "garbage-not-a-date"
    assert rep["pruned"] == [] and rep["decayed"] == []


# ═══════════════════════ 8. main() CLI 退出码 ═══════════════════════

def test_cli_exit_0_when_no_recurrence():
    """--scan-recurring 无复发/无元问题 → exit 0（健康）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        _write_audit(tmp, "ch_001_audit.json", _audit(1, "PLOT_X"))  # 仅 1 次·不升级
        r = _run_cli(tmp, "--scan-recurring")
        assert r.returncode == 0, f"stderr={r.stderr}"


def test_cli_exit_1_when_escalated():
    """--scan-recurring 命中复发升级约束 → exit 1（值得关注）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        for ch in (1, 2, 3):
            _write_audit(tmp, f"ch_{ch:03d}_audit.json", _audit(ch, "PLOT_X"))
        r = _run_cli(tmp, "--scan-recurring")
        assert r.returncode == 1, f"应 exit 1（已升级约束）·stderr={r.stderr}"


def test_cli_exit_2_bad_project_path():
    """项目路径不存在 → exit 2（致命错误）。"""
    bad = str(_ROOT / "definitely_not_a_real_dir_xyz123")
    p = subprocess.run([sys.executable, str(_TARGET), bad, "--scan-recurring"],
                       capture_output=True, cwd=str(_ROOT))
    assert p.returncode == 2


def test_cli_exit_2_ingest_missing_audit():
    """--ingest 指向不存在的 audit 报告 → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        r = _run_cli(tmp, "--ingest", "nope_audit.json")
        assert r.returncode == 2


def test_cli_exit_2_no_mode_flag():
    """未指定任何模式 flag → exit 2（提示用法）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)
        r = _run_cli(tmp)  # 只给项目路径·无 flag
        assert r.returncode == 2


def test_cli_no_args_prints_doc_exit_0():
    """完全无参数 → 打印 __doc__ + exit 0。"""
    p = subprocess.run([sys.executable, str(_TARGET)], capture_output=True, cwd=str(_ROOT))
    assert p.returncode == 0
