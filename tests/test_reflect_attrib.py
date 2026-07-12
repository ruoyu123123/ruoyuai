"""Cluster-level style-failure attribution to author-skill sections."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import learning_loop as loop  # noqa: E402

SKILL = """---
name: test-style
---

# 测试作者风格

## 场景识别

### 对话占比

- 对话场景 35-50%
- cluster 平均不低于 26%

### 拟声词独段

- 战斗场景 3-5 处

## 段落硬约束

- 平均段长 15-35 字
- 单段不超过 120 字
- 单句独行占比 40-65%

## 禁用词严控

- 禁用 AI 套话
"""

DRIFT = {
    "code": "LONGRANGE_STYLE_DRIFT",
    "gate_level": "advisory",
    "severity": "advisory",
    "metric": {"n_points": 5, "last": 0.42},
    "message": "跨 cluster 作者文风相似度持续下行",
}


def _project(tmp_path: Path, with_style: bool = True) -> Path:
    database = tmp_path / "_数据库"
    database.mkdir(parents=True, exist_ok=True)
    if with_style:
        skill_path = tmp_path / "skill_v1.md"
        skill_path.write_text(SKILL, encoding="utf-8")
        (database / "作者风格.json").write_text(
            json.dumps({"style_source": str(skill_path)}, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


def _audit(cluster_id: str, code: str, dimension: str = "风格", severity: str = "warning") -> dict:
    return {
        "cluster_id": cluster_id,
        "issues": [{
            "code": code,
            "dimension": dimension,
            "severity": severity,
            "desc": f"{code} in {cluster_id}",
            "waived": False,
        }],
        "pending_agent": [],
        "waived_issues": [],
        "waiver_audit": {
            "waive_rate": 0.0,
            "advisory_total": 1,
            "advisory_waived": 0,
            "blanket_suspected": False,
            "orphan_codes": [],
            "repeated_reason_codes": {},
        },
    }


def _scan(project: Path, audits: list[dict]) -> dict:
    audit_dir = project / "_数据库" / ".audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for audit in audits:
        (audit_dir / f"{audit['cluster_id']}_audit.json").write_text(
            json.dumps(audit, ensure_ascii=False), encoding="utf-8"
        )
    return loop.scan_recurring(project)


def _suggestion(project: Path, code: str):
    suggestions = loop.load_experience(project)["skill_rewrite_suggestions"]
    return next((item for item in suggestions if item.get("style_code") == code), None)


def test_persistent_style_failure_is_attributed_to_skill_section(tmp_path):
    project = _project(tmp_path)
    _scan(project, [
        _audit("cluster_001", "STYLE_对话占比"),
        _audit("cluster_002", "STYLE_对话占比"),
    ])
    suggestion = _suggestion(project, "STYLE_对话占比")
    assert suggestion["suggestion_type"] == "tighten_clause"
    assert "对话占比" in suggestion["attributed_section"]["heading"]
    assert suggestion["source_clusters"] == ["cluster_001", "cluster_002"]
    assert suggestion["gate_level"] == "advisory"


def test_paragraph_and_banned_word_codes_hit_matching_sections(tmp_path):
    project = _project(tmp_path)
    _scan(project, [
        _audit("cluster_001", "STYLE_单段超长"),
        _audit("cluster_002", "STYLE_单段超长"),
        _audit("cluster_003", "STYLE_禁用词"),
        _audit("cluster_004", "STYLE_禁用词"),
    ])
    assert _suggestion(project, "STYLE_单段超长")["attributed_section"]["heading"] == "段落硬约束"
    assert "禁用词" in _suggestion(project, "STYLE_禁用词")["attributed_section"]["heading"]


def test_unlocated_style_code_produces_add_clause(tmp_path):
    project = _project(tmp_path)
    _scan(project, [
        _audit("cluster_001", "STYLE_逗句比"),
        _audit("cluster_002", "STYLE_逗句比"),
    ])
    suggestion = _suggestion(project, "STYLE_逗句比")
    assert suggestion["suggestion_type"] == "add_clause"
    assert suggestion["attributed_section"] is None


def test_non_style_code_is_not_attributed(tmp_path):
    project = _project(tmp_path)
    _scan(project, [
        _audit("cluster_001", "LOCKED_FACT_CONFLICT", "结构", "error"),
        _audit("cluster_002", "LOCKED_FACT_CONFLICT", "结构", "error"),
    ])
    assert _suggestion(project, "LOCKED_FACT_CONFLICT") is None


def test_single_cluster_is_not_persistent(tmp_path):
    project = _project(tmp_path)
    _scan(project, [_audit("cluster_001", "STYLE_对话占比")])
    assert _suggestion(project, "STYLE_对话占比") is None


def test_env_off_skips_attribution(tmp_path, monkeypatch):
    project = _project(tmp_path)
    experience = loop._empty_experience()
    experience["_recurrence_tracker"] = {
        "风格::STYLE_对话占比": {
            "count": 2,
            "clusters": ["cluster_001", "cluster_002"],
            "dimension": "风格",
            "sample_desc": "x",
        }
    }
    loop.save_experience(project, experience)
    monkeypatch.setenv("LL_REFLECT_ATTRIB", "off")
    assert loop.reflect_attribution(project) == []
    assert loop.load_experience(project)["skill_rewrite_suggestions"] == []


def test_longrange_drift_is_a_separate_advisory_source(tmp_path):
    project = _project(tmp_path)
    produced = loop.reflect_attribution(project, drift_findings=[dict(DRIFT)])
    suggestion = next(item for item in produced if item["style_code"] == "LONGRANGE_STYLE_DRIFT")
    assert suggestion["evidence_source"] == "longrange_drift"
    assert suggestion["source_clusters"] == []
    assert suggestion["gate_level"] == "advisory"


def _patch_drift_scanner(monkeypatch, report):
    import cross_cluster_style_drift_scanner as scanner
    monkeypatch.setattr(scanner, "scan", lambda project, **kwargs: report)


def test_default_drift_collection_reads_shadow_and_active_findings(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _patch_drift_scanner(monkeypatch, {
        "issues": [dict(DRIFT)],
        "shadow_findings": [dict(DRIFT)],
    })
    produced = loop.reflect_attribution(project)
    assert any(item["style_code"] == "LONGRANGE_STYLE_DRIFT" for item in produced)


def test_explicit_empty_drift_list_does_not_query_scanner(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _patch_drift_scanner(monkeypatch, {"issues": [dict(DRIFT)], "shadow_findings": []})
    assert loop.reflect_attribution(project, drift_findings=[]) == []


def test_drift_scanner_failure_does_not_block_recurrence_attribution(tmp_path, monkeypatch):
    project = _project(tmp_path)
    import cross_cluster_style_drift_scanner as scanner
    monkeypatch.setattr(scanner, "scan", lambda project, **kwargs: (_ for _ in ()).throw(RuntimeError("x")))
    assert loop.reflect_attribution(project) == []


def test_missing_style_file_keeps_advisory_unlocated(tmp_path):
    project = _project(tmp_path, with_style=False)
    _scan(project, [
        _audit("cluster_001", "STYLE_对话占比"),
        _audit("cluster_002", "STYLE_对话占比"),
    ])
    suggestion = _suggestion(project, "STYLE_对话占比")
    assert suggestion["suggestion_type"] == "add_clause"
    assert suggestion["skill_path"] is None


def test_attribution_is_idempotent(tmp_path):
    project = _project(tmp_path)
    _scan(project, [
        _audit("cluster_001", "STYLE_对话占比"),
        _audit("cluster_002", "STYLE_对话占比"),
    ])
    loop.reflect_attribution(project, drift_findings=[])
    suggestions = loop.load_experience(project)["skill_rewrite_suggestions"]
    assert sum(item["style_code"] == "STYLE_对话占比" for item in suggestions) == 1


def test_attribution_clears_suggestion_when_evidence_disappears(tmp_path):
    project = _project(tmp_path)
    experience = loop._empty_experience()
    experience["skill_rewrite_suggestions"] = [{
        "style_code": "STYLE_对话占比",
        "source_clusters": ["cluster_001", "cluster_002"],
    }]
    loop.save_experience(project, experience)
    assert loop.reflect_attribution(project, drift_findings=[]) == []
    assert loop.load_experience(project)["skill_rewrite_suggestions"] == []


def test_skill_section_parser_and_keyword_scoring():
    sections = loop._parse_skill_sections(SKILL)
    headings = [section["heading"] for section in sections]
    assert "段落硬约束" in headings
    hit = loop._attribute_to_skill_section(sections, ("拟声", "拟声词独段"))
    assert "拟声" in hit["heading"]
    assert loop._attribute_to_skill_section(sections, ("不存在_xyz",)) is None


def test_embedding_backend_is_off_without_configuration(monkeypatch):
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    for key in list(os.environ):
        if key.startswith("GEN_EMBED__"):
            monkeypatch.delenv(key, raising=False)
    assert loop._has_real_embedding_backend() is False


def test_semantic_attribution_prefetches_all_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    sections = loop._parse_skill_sections(SKILL)
    calls = []
    monkeypatch.setattr(embedding_store, "prefetch_embeddings", lambda texts: calls.append(list(texts)))
    monkeypatch.setattr(
        embedding_store,
        "compute_embedding",
        lambda text: [1.0, 0.0] if "对话" in text or "台词" in text else [0.0, 1.0],
    )
    hit = loop._attribute_to_skill_section(sections, ("台词比例",))
    assert hit is not None
    assert hit["method"] == "semantic"
    assert len(calls) == 1
    assert len(calls[0]) == len(sections) + 1


def test_embedding_error_uses_deterministic_keyword_scoring(monkeypatch):
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    monkeypatch.setattr(
        embedding_store,
        "compute_embedding",
        lambda text: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    hit = loop._attribute_to_skill_section(
        loop._parse_skill_sections(SKILL), ("拟声", "拟声词独段")
    )
    assert hit is not None
    assert "method" not in hit
