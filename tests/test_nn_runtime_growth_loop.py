# 🔴 2026-06-30 可成长 NN 闭环接入
"""test_nn_runtime_growth_loop.py — 创作入口默认开启可成长底座 + save_state 数据飞轮接线。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_ROOT / "core" / "ml" / "flywheel"))

import nn_runtime_defaults  # noqa: E402
import save_state  # noqa: E402


def test_creative_defaults_include_growth_loop_gates(monkeypatch):
    for gate in nn_runtime_defaults.creative_nn_gates():
        monkeypatch.delenv(gate, raising=False)
    newly = nn_runtime_defaults.enable_creative_nn_defaults()
    expected = {
        "RUOYU_NN_SURPRISAL", "RUOYU_NN_COHERENCE", "RUOYU_NN_VAD",
        "RUOYU_NN_COREF", "RUOYU_CHARACTER_NETWORK",
        "RUOYU_FEATURE_STORE", "RUOYU_DATA_FLYWHEEL", "RUOYU_MODEL_REGISTRY",
        "RUOYU_PREF_RANKER",   # 2026-07-03 W3：pairwise 偏好观察捕获（纯 python 零延迟）
    }
    assert set(nn_runtime_defaults.creative_nn_gates()) == expected
    assert set(newly) == expected
    assert all(__import__("os").environ[g] == "1" for g in expected)


def test_creative_defaults_do_not_override_explicit_zero(monkeypatch):
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "0")
    nn_runtime_defaults.enable_creative_nn_defaults()
    assert __import__("os").environ["RUOYU_DATA_FLYWHEEL"] == "0"


def test_auto_post_reflect_cluster_collects_data_flywheel(monkeypatch, tmp_path):
    monkeypatch.setenv("RUOYU_DATA_FLYWHEEL", "1")
    import data_collector as dc
    monkeypatch.setattr(dc, "_POOL_DIR", tmp_path / "pool")
    monkeypatch.setattr(dc, "_MANIFEST", tmp_path / "pool" / "data_manifest.json")

    project = tmp_path / "novel"
    draft_dir = project / "章节" / "cluster_001_draft"
    audit_dir = project / "_数据库" / ".audit"
    judge_dir = project / "_数据库" / ".judge_reports"
    reading_dir = project / "_数据库" / ".reading_reflection"
    brief_dir = project / "_数据库" / ".checker_briefs"
    quality_dir = project / "章节" / "_quality"
    wal_dir = project / "_数据库" / ".wal"
    draft_dir.mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    judge_dir.mkdir(parents=True)
    reading_dir.mkdir(parents=True)
    brief_dir.mkdir(parents=True)
    quality_dir.mkdir(parents=True)
    wal_dir.mkdir(parents=True)
    (draft_dir / "cluster_001_draft.txt").write_text(
        "这是一个足够长的正文段落，用来验证数据飞轮会在保存状态后收集训练样本。\n",
        encoding="utf-8",
    )
    (audit_dir / "cluster_001_audit.json").write_text(json.dumps({
        "issues": [{"code": "COHERENCE_BREAK", "desc": "这是一段连贯性断裂的训练样本文本，需要进入连贯性模型训练池"}],
        "scanner_status": [{"scanner": "coherence", "exit_code": 0, "ok": True}],
        "waiver_audit": {"waive_rate": 0.0, "advisory_total": 1, "advisory_waived": 0},
    }, ensure_ascii=False), encoding="utf-8")
    (judge_dir / "cluster_001_voice-checker.json").write_text(json.dumps({
        "judge_id": "voice-checker",
        "overall_grade": "B",
        "confidence": 0.8,
        "evidence_quotes": ["声纹证据一", "声纹证据二"],
    }, ensure_ascii=False), encoding="utf-8")
    (reading_dir / "cluster_001_round_1.json").write_text(json.dumps({
        "round": 1,
        "verdict": "pass",
        "consecutive_clean_rounds": 3,
        "total_issues": 0,
        "new_issues_this_round": [],
    }, ensure_ascii=False), encoding="utf-8")
    (brief_dir / "cluster_001_validator.json").write_text(json.dumps({
        "version": 1,
        "chapter_path": "章节/cluster_001_draft/cluster_001_draft.txt",
        "checker": "novel-validator-checker",
        "violations": [{
            "line_start": 1,
            "line_end": 1,
            "original": "他顿时感到一种难以言喻的震撼。",
            "issue": "BANNED_WORD",
            "fix_hint": "删掉顿时。",
            "code": "BANNED_WORD",
            "gate_level": "advisory",
        }],
        "judge_report": {"judge_id": "novel-validator-checker", "overall_grade": "B", "confidence": 0.8},
    }, ensure_ascii=False), encoding="utf-8")
    draft_abs = str(draft_dir / "cluster_001_draft.txt")
    (quality_dir / "fixer_report_voice-fix_20260701_120000.json").write_text(json.dumps({
        "mode": "voice-fix",
        "used_profile": "active-profile",
        "used_model": "model-x",
        "files_written": [{"path": draft_abs, "cjk": 3000}],
        "rejected_blocks": [],
        "gen_model_summary": {
            "voice_changes_per_violation": {
                "1": {"character": "陆衍", "before": "让我们来分析一下情况。", "after": "坑在这儿。"}
            }
        },
        "scanner_results": {draft_abs: {"narrative_short_sentence_scanner.py": {"verdict": "PASS", "violations_count": 0}}},
        "timestamp": "2026-07-01T12:00:00",
    }, ensure_ascii=False), encoding="utf-8")

    fake_learning_loop = tmp_path / "learning_loop.py"
    fake_learning_loop.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    monkeypatch.setattr(save_state, "scripts_dir", lambda: tmp_path)
    monkeypatch.setattr(save_state, "child_python", lambda: sys.executable)

    assert save_state.cmd_auto_post_reflect_cluster(project, "001") == 0
    manifest = json.loads(dc._MANIFEST.read_text(encoding="utf-8"))
    assert manifest["total_records"] >= 11
    assert manifest["models"]["general"]["records"] == 1
    assert manifest["models"]["coherence"]["records"] == 1
    assert manifest["models"]["judge_reliability"]["records"] == 2
    assert manifest["models"]["reader_experience"]["records"] == 1
    assert manifest["models"]["scanner_reliability"]["records"] == 2
    assert manifest["models"]["waiver_calibration"]["records"] == 1
    assert manifest["models"]["ai_tone"]["records"] == 1
    assert manifest["models"]["voice_drift"]["records"] == 1
    assert manifest["models"]["fix_routing"]["records"] == 1
