# 🔴 2026-06-29 可成长NN架构 · 数据飞轮测试
"""test_data_collector.py — ClusterDataCollector 测试。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "ml" / "flywheel"))
from data_collector import (
    ClusterDataCollector, enabled, _text_hash, _strip_changes,
    _split_paragraphs, CODE_TO_MODEL,
)


@pytest.fixture(autouse=True)
def _env_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "1")
    import data_collector as dc
    monkeypatch.setattr(dc, "_POOL_DIR", tmp_path / "pool")
    monkeypatch.setattr(dc, "_MANIFEST", tmp_path / "pool" / "data_manifest.json")
    yield


def test_enabled_gate(monkeypatch):
    monkeypatch.delenv("RUOYU_DATA_FLYWHEEL", raising=False)
    assert not enabled()
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "1")
    assert enabled()


def test_disabled_returns_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("RUOYU_DATA_FLYWHEEL", raising=False)
    c = ClusterDataCollector(str(tmp_path), "cluster_001")
    result = c.collect()
    assert result["skipped"] is True


def test_text_hash_deterministic():
    h1 = _text_hash("你好世界")
    h2 = _text_hash("你好世界")
    assert h1 == h2
    assert len(h1) == 16


def test_strip_changes():
    text = "正文内容\n---CHANGES---\n变更数据"
    assert _strip_changes(text) == "正文内容"
    text2 = "正文内容\n---CHANGES_FACTUAL---\n变更数据"
    assert _strip_changes(text2) == "正文内容"


def test_split_paragraphs():
    text = "这是一个足够长的中文段落用来测试分段功能的哦\n短短\n另一个足够长的中文段落用来测试分段功能"
    paras = _split_paragraphs(text, min_cjk=6)
    assert len(paras) == 2


def test_collect_paragraphs(tmp_path):
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    draft = draft_dir / "cluster_001_draft.txt"
    draft.write_text(
        "这是第一个段落，有足够多的中文字来通过最小长度过滤器的检测。\n"
        "这是第二个段落，同样也有足够多的中文字来通过最小长度的过滤。\n"
        "短\n",
        encoding="utf-8",
    )
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["paragraphs"] == 2


def test_collect_weak_labels(tmp_path):
    proj = tmp_path / "novel"
    audit_dir = proj / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    report = audit_dir / "cluster_001_audit.json"
    report.write_text(json.dumps({
        "issues": [
            {"code": "SEMANTIC_APHORISM", "paragraph": "这是一段含有格言体AI腔调的文本，需要被检测出来进行分析"},
            {"code": "HOOK_WEAK", "paragraph": "这是一段钩子力度不足的章末文本，需要被检测出来进行分析"},
            {"code": "UNKNOWN_CODE", "paragraph": "不在映射表中的代码"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["weak_labels"] == 2


def test_collect_fix_pairs(tmp_path):
    proj = tmp_path / "novel"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    fixer_log = db / "cluster_001_fixer_log.json"
    fixer_log.write_text(json.dumps({
        "fixes": [
            {"before": "与此同时他感到无比的愤怒和不安的情绪", "after": "他一拳砸在桌上"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["fix_pairs"] == 1


def test_collect_strong_labels(tmp_path):
    proj = tmp_path / "novel"
    ch_dir = proj / "章节" / "cluster_001_draft"
    ch_dir.mkdir(parents=True)
    changes = ch_dir / "cluster_001_changes.json"
    changes.write_text(json.dumps({
        "self_eval": {
            "waivers": [
                {"code": "SEMANTIC_APHORISM",
                 "reason": "作者风格确实如此",
                 "paragraph": "这是一段被豁免的文本，已确认符合作者风格，不需要修改"},
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["strong_labels"] == 1


def test_dedup_same_text(tmp_path):
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    same_text = "这是完全相同的段落文本用来测试去重功能是否正常工作"
    draft = draft_dir / "cluster_001_draft.txt"
    draft.write_text(f"{same_text}\n{same_text}\n", encoding="utf-8")
    c = ClusterDataCollector(str(proj), "cluster_001")
    result = c.collect()
    assert result["paragraphs"] == 1


def test_manifest_updated(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    draft_dir = proj / "章节" / "cluster_001_draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "cluster_001_draft.txt").write_text(
        "这是一个测试段落，有足够的中文字来通过最小长度过滤器的检查。\n",
        encoding="utf-8",
    )
    c = ClusterDataCollector(str(proj), "cluster_001")
    c.collect()
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["total_records"] >= 1
    assert manifest["last_update"] is not None
    assert manifest["models"]["general"]["records"] >= 1


def test_manifest_counts_records_by_model(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    audit_dir = proj / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "cluster_001_audit.json").write_text(json.dumps({
        "issues": [
            {"code": "COHERENCE_UNSTABLE", "desc": "这是一段连贯性忽高忽低的训练样本文本，需要进入连贯性模型训练池"},
            {"code": "INFO_DENSITY_IMBALANCE", "desc": "这是一段高潮信息密度失衡的训练样本文本，需要进入信息密度模型训练池"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)
    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert result["weak_labels"] == 2
    assert manifest["models"]["coherence"]["records"] == 1
    assert manifest["models"]["surprisal"]["records"] == 1


def test_collect_judge_reports_cluster_level(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    judge_dir = proj / "_数据库" / ".judge_reports"
    judge_dir.mkdir(parents=True)
    (judge_dir / "cluster_001_voice-checker.json").write_text(json.dumps({
        "judge_id": "voice-checker",
        "schema_version": "1.0",
        "overall_grade": "B",
        "confidence": 0.72,
        "persona": "line_editor",
        "evidence_quotes": ["这句对白不像角色原本的说话方式", "第二处证据"],
        "uncertainty_flags": ["样本较少"],
        "specific_findings": {"voice_drift_count": 1},
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["judge_reports"] == 1
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["judge_reliability"]["records"] == 1
    lines = (dc._POOL_DIR / "judge_reliability" / "judge_reports.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert rec["model"] == "judge_reliability"
    assert rec["source"] == "judge_report"
    assert rec["label"] == "JUDGE_B"
    cf = rec["calibration_features"]
    assert cf["feature_schema"] == "judge_report_reliability_v1"
    assert cf["judge_id"] == "voice-checker"
    assert cf["persona"] == "line_editor"
    assert cf["grade_num"] == 3
    assert cf["evidence_quote_count"] == 2
    assert cf["uncertainty_flag_count"] == 1
    assert cf["gate_level"] == "advisory"


def test_collect_judge_reports_chapter_range_only(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    db = proj / "_数据库"
    judge_dir = db / ".judge_reports"
    judge_dir.mkdir(parents=True)
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 2]}],
    }, ensure_ascii=False), encoding="utf-8")
    for ch, grade in [(1, "A"), (2, "C"), (3, "D")]:
        (judge_dir / f"ch_{ch:03d}_audit-hub.json").write_text(json.dumps({
            "judge_id": "audit-hub",
            "overall_grade": grade,
            "confidence": 0.9,
            "evidence_quotes": [f"第{ch}章证据一", f"第{ch}章证据二"],
        }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["judge_reports"] == 2
    labels = [
        json.loads(line)["label"]
        for line in (dc._POOL_DIR / "judge_reliability" / "judge_reports.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert labels == ["JUDGE_A", "JUDGE_C"]


def test_collect_reading_reflections(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    rr_dir = proj / "_数据库" / ".reading_reflection"
    rr_dir.mkdir(parents=True)
    (rr_dir / "cluster_001_round_1.json").write_text(json.dumps({
        "round": 1,
        "verdict": "fail",
        "consecutive_clean_rounds": 0,
        "total_issues": 1,
        "fixed_from_previous_round": [{"description": "上一轮已修复"}],
        "new_issues_this_round": [{
            "dimension": "对话工艺",
            "severity": "minor",
            "location": "scene 2",
            "description": "对白太直给",
            "evidence": "你必须告诉我全部真相这句像工具人台词",
            "suggested_fix": "改成试探和打岔",
            "applies_to_future_clusters": True,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["reading_reflections"] == 2
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["dialogue_pragmatics"]["records"] == 1
    assert manifest["models"]["reader_experience"]["records"] == 1
    issue = json.loads((dc._POOL_DIR / "dialogue_pragmatics" / "reading_reflections.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert issue["source"] == "reading_reflection_issue"
    assert issue["dimension"] == "对话工艺"
    verdict = json.loads((dc._POOL_DIR / "reader_experience" / "reading_verdicts.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert verdict["label"] == "READING_VERDICT_fail"


def test_collect_audit_metadata(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    audit_dir = proj / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "cluster_001_audit.json").write_text(json.dumps({
        "scanner_status": [
            {"scanner": "coherence", "exit_code": 0, "ok": True},
            {"scanner": "voice", "exit_code": 2, "ok": False},
        ],
        "waiver_audit": {"waive_rate": 0.5, "advisory_total": 2, "advisory_waived": 1},
        "auto_fixed": [{"code": "SEMANTIC_APHORISM", "action": "rewrite"}],
        "pending_agent": [{"code": "VOICE_DRIFT_CROSS_SCENE", "suggested_agent": "novel-voice-checker"}],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["audit_metadata"] == 5
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["scanner_reliability"]["records"] == 2
    assert manifest["models"]["waiver_calibration"]["records"] == 1
    assert manifest["models"]["fix_routing"]["records"] == 2


def test_collect_checker_briefs_validator_and_embedded_judge(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    brief_dir = proj / "_数据库" / ".checker_briefs"
    brief_dir.mkdir(parents=True)
    (brief_dir / "cluster_001_validator.json").write_text(json.dumps({
        "version": 2,
        "carrier": "cluster",
        "cluster_id": "cluster_001",
        "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt",
        "checker": "novel-validator-checker",
        "mode": "cluster",
        "violations": [
            {
                "line_start": 10,
                "line_end": 10,
                "original": "他顿时感到一种难以言喻的震撼。",
                "issue": "BANNED_WORD",
                "fix_hint": "删掉顿时，改成直接动作。",
                "code": "BANNED_WORD",
                "gate_level": "hard_gate",
            },
            {
                "line_start": 20,
                "line_end": 22,
                "original": "这一章末尾没有具体悬念。",
                "issue": "HOOK_WEAK",
                "fix_hint": "补具体悬念锚点。",
                "code": "HOOK_WEAK",
                "gate_level": "advisory",
            },
        ],
        "judge_report": {
            "judge_id": "novel-validator-checker",
            "schema_version": "1.1",
            "cluster_id": "cluster_001",
            "overall_grade": "B",
            "confidence": 0.9,
            "specific_findings": {"hard_gate_count": 1},
            "waivers": [],
            "uncertainty_flags": [],
        },
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["checker_briefs"] == 3
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["ai_tone"]["records"] == 1
    assert manifest["models"]["hook_strength"]["records"] == 1
    assert manifest["models"]["judge_reliability"]["records"] == 1
    ai = json.loads((dc._POOL_DIR / "ai_tone" / "checker_briefs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert ai["source"] == "checker_brief_validator"
    assert ai["brief_path"].endswith("cluster_001_validator.json")
    assert ai["fix_hint"] == "删掉顿时，改成直接动作。"
    assert ai["gate_level"] == "hard_gate"
    judge = json.loads((dc._POOL_DIR / "judge_reliability" / "embedded_judge_reports.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert judge["source"] == "checker_brief_embedded_judge_report"
    assert judge["calibration_features"]["judge_id"] == "novel-validator-checker"


def test_collect_checker_briefs_voice_issue_and_legacy_v1_loudly_skipped(tmp_path, capsys):
    """v2 voice brief 正常收样本；v1 遗留 brief（chapter_path 载体）必须响亮跳过提示重产，
    不得无声 continue（回归锁：brief 契约统一 v2 后静默过滤 = 训练样本通道整体无声死亡）。"""
    import data_collector as dc
    proj = tmp_path / "novel"
    db = proj / "_数据库"
    brief_dir = db / ".checker_briefs"
    brief_dir.mkdir(parents=True)
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "chapter_range": [4, 4]}],
    }, ensure_ascii=False), encoding="utf-8")
    (brief_dir / "cluster_001_voice.json").write_text(json.dumps({
        "version": 2,
        "carrier": "cluster",
        "cluster_id": "cluster_001",
        "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt",
        "checker": "novel-voice-checker",
        "violations": [
            {
                "line_start": 8,
                "line_end": 8,
                "original": "「让我们来分析一下情况，这显然是个陷阱。」",
                "issue": "voice_drift",
                "fix_hint": "改成短句，不用显然。",
                "character": "陆衍",
                "voice_pack_violated_field": "rhythm + banned_phrases",
            },
            {
                "line_start": 9,
                "line_end": 9,
                "original": "「我知道未来会发生什么。」",
                "issue": "pov_violation",
                "fix_hint": "删掉未来知识。",
                "character": "陆衍",
                "voice_pack_violated_field": "knowledge.doesnt_know",
            },
        ],
        "judge_report": {"judge_id": "novel-voice-checker", "overall_grade": "B", "confidence": 0.8},
    }, ensure_ascii=False), encoding="utf-8")
    # v1 遗留 brief（chapter 载体·契约已清除）→ 响亮跳过 · 零样本
    (brief_dir / "ch_004_voice.json").write_text(json.dumps({
        "version": 1,
        "chapter_path": "章节/第004章/第004章.txt",
        "checker": "novel-voice-checker",
        "violations": [],
        "judge_report": {
            "judge_id": "novel-voice-checker",
            "overall_grade": "A",
            "confidence": 0.95,
            "evidence_quotes": [{"character": "陆衍", "quote": "「坑在这儿。」", "voice_match": "匹配短句"}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    err = capsys.readouterr().err
    assert "非 v2 checker brief" in err and "ch_004_voice.json" in err, \
        "v1 遗留 brief 必须响亮提示重产（不得无声 continue）"
    assert "重产" in err
    assert result["checker_briefs"] == 3
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["voice_drift"]["records"] == 1
    assert manifest["models"]["coherence"]["records"] == 1
    assert manifest["models"]["judge_reliability"]["records"] == 1
    voice_lines = (dc._POOL_DIR / "voice_drift" / "checker_briefs.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["label"] == "CHECKER_voice_drift" for line in voice_lines)
    # v1 brief 的 clean-A 样本不得进池
    assert not any(json.loads(line)["label"] == "VOICE_CLEAN_A" for line in voice_lines)
    # 违规样本记录 v2 draft_path（v1 chapter_path 字段已清除）
    assert all("chapter_path" not in json.loads(line) for line in voice_lines)
    assert any(json.loads(line).get("draft_path", "").endswith("cluster_001_draft.txt")
               for line in voice_lines)


def test_collect_checker_briefs_voice_clean_positive_v2(tmp_path):
    """v2 clean voice brief（violations 空 + judge A + evidence_quotes）→ VOICE_CLEAN_A 正样本。"""
    import data_collector as dc
    proj = tmp_path / "novel"
    brief_dir = proj / "_数据库" / ".checker_briefs"
    brief_dir.mkdir(parents=True)
    (brief_dir / "cluster_001_voice.json").write_text(json.dumps({
        "version": 2,
        "carrier": "cluster",
        "cluster_id": "cluster_001",
        "draft_path": "章节/cluster_001_draft/cluster_001_draft.txt",
        "checker": "novel-voice-checker",
        "violations": [],
        "judge_report": {
            "judge_id": "novel-voice-checker",
            "overall_grade": "A",
            "confidence": 0.95,
            "evidence_quotes": [{"character": "陆衍", "quote": "「坑在这儿。」", "voice_match": "匹配短句"}],
        },
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["checker_briefs"] == 2
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["voice_drift"]["records"] == 1
    assert manifest["models"]["judge_reliability"]["records"] == 1
    voice_lines = (dc._POOL_DIR / "voice_drift" / "checker_briefs.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["label"] == "VOICE_CLEAN_A" for line in voice_lines)


def test_collect_fixer_reports_voice_pairs_and_scanner_results(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    qdir = proj / "章节" / "_quality"
    qdir.mkdir(parents=True)
    draft_path = str(proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt")
    (qdir / "fixer_report_voice-fix_20260701_120000.json").write_text(json.dumps({
        "mode": "voice-fix",
        "used_profile": "active-profile",
        "used_model": "model-x",
        "files_written": [{"path": draft_path, "cjk": 3000}],
        "rejected_blocks": [],
        "gen_model_summary": {
            "violations_addressed": [1],
            "voice_changes_per_violation": {
                "1": {"character": "陆衍", "before": "让我们来分析一下情况。", "after": "坑在这儿。别急。"}
            },
        },
        "scanner_results": {draft_path: {"narrative_short_sentence_scanner.py": {"verdict": "PASS", "violations_count": 0}}},
        "timestamp": "2026-07-01T12:00:00",
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True, exist_ok=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["fixer_reports"] == 3
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["voice_drift"]["records"] == 1
    assert manifest["models"]["fix_routing"]["records"] == 1
    assert manifest["models"]["scanner_reliability"]["records"] == 1
    pair = json.loads((dc._POOL_DIR / "voice_drift" / "fixer_reports.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert pair["after"] == "坑在这儿。别急。"
    assert pair["used_model"] == "model-x"


def test_collect_fixer_reports_rejected_blocks(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    qdir = proj / "章节" / "_quality"
    qdir.mkdir(parents=True)
    (qdir / "fixer_report_validator-repair_20260701_120000.json").write_text(json.dumps({
        "mode": "validator-repair",
        "used_profile": "active-profile",
        "used_model": "model-x",
        "files_written": [],
        "rejected_blocks": [{
            "path": str(proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"),
            "before_cjk": 3000,
            "after_cjk": 900,
            "delta_pct": -70.0,
            "reason": "cjk_conservation_violation",
        }],
        "gen_model_summary": {},
        "scanner_results": {},
        "timestamp": "2026-07-01T12:00:00",
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True, exist_ok=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["fixer_reports"] == 2
    routing_lines = (dc._POOL_DIR / "fix_routing" / "fixer_reports.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["label"] == "FIX_REJECTED_cjk_conservation_violation" for line in routing_lines)
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["fix_routing"]["records"] == 2


def test_collect_repair_reports_from_chapter_range(tmp_path):
    import data_collector as dc
    proj = tmp_path / "novel"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    (db / "事件簇.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 1]}],
    }, ensure_ascii=False), encoding="utf-8")
    ch_dir = proj / "章节" / "第001章"
    ch_dir.mkdir(parents=True)
    (ch_dir / "第001章.repair.json").write_text(json.dumps({
        "before": {"comma_period_ratio": 0.6, "banned_words": 3},
        "after": {"comma_period_ratio": 1.8, "banned_words": 0},
        "iterative": True,
        "max_passes": 3,
        "actual_passes": 2,
        "converged_at_pass": 2,
        "per_pass": [{
            "steps": ["merge_short_sentences", "fix_banned_words"],
            "banned_fixes": [{"word": "顿时", "replacement": "忽然间", "count": 2}],
            "tag_fixes": [{"tag": "淡淡地说", "replacement": "说", "count": 1}],
            "pass": 1,
            "converged": False,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (proj / "章节" / "cluster_001_draft").mkdir(parents=True)

    result = ClusterDataCollector(str(proj), "cluster_001").collect()
    assert result["repair_reports"] == 4
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["models"]["ai_tone"]["records"] == 1
    assert manifest["models"]["dialogue_pragmatics"]["records"] == 1
    assert manifest["models"]["style_fidelity"]["records"] == 1
    assert manifest["models"]["fix_routing"]["records"] == 1


def test_code_to_model_coverage():
    assert "SEMANTIC_APHORISM" in CODE_TO_MODEL
    assert CODE_TO_MODEL["BANNED_WORD"] == "ai_tone"
    assert CODE_TO_MODEL["HOOK_WEAK"] == "hook_strength"
    assert CODE_TO_MODEL["POV_VIOLATION"] == "coherence"
    assert CODE_TO_MODEL["VOICE_DRIFT"] == "voice_drift"
    assert CODE_TO_MODEL["COHERENCE_BREAK"] == "coherence"
    assert CODE_TO_MODEL["COHERENCE_UNSTABLE"] == "coherence"
    assert CODE_TO_MODEL["SITUATION_MODEL_DIM_DROPOUT"] == "coherence"
    assert CODE_TO_MODEL["CENTERING_ROUGH_SHIFT_OVERLOAD"] == "coherence"
    assert CODE_TO_MODEL["INFO_DENSITY_IMBALANCE"] == "surprisal"
    assert CODE_TO_MODEL["EMOTION_CURVE_RESCAN_DRIFT"] == "emotion_arc"
    assert CODE_TO_MODEL["GROUP_DIALOGUE_NAME_CRUTCH"] == "dialogue_pragmatics"
    assert CODE_TO_MODEL["GROUP_DIALOGUE_IMBALANCE"] == "dialogue_pragmatics"
    assert CODE_TO_MODEL["DISPREFERRED_TURN_BARE"] == "dialogue_pragmatics"
    assert CODE_TO_MODEL["REVEAL_TELL_OVERUSE"] == "promise_payoff"
    assert CODE_TO_MODEL["PERSONA_DRIFT"] == "character_trajectory"
    assert CODE_TO_MODEL["ACTANT_DRIFT_NO_PIVOT"] == "character_trajectory"
    assert CODE_TO_MODEL["ANTAGONIST_VALENCE_DRIFT_UNAUTHORIZED"] == "character_trajectory"
    assert CODE_TO_MODEL["CHARACTER_IDENTITY_ANCHOR_DRIFT"] == "character_trajectory"
    assert CODE_TO_MODEL["ACTION_MENTAL_RATIO_DRIFT"] == "action_mentalizing"
    assert CODE_TO_MODEL["COGNITIVE_OVERLOAD"] == "cognitive_load"


def test_empty_project(tmp_path):
    c = ClusterDataCollector(str(tmp_path / "nonexistent"), "cluster_001")
    result = c.collect()
    assert result["total"] == 0
