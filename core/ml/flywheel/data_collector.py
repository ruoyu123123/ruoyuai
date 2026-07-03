# 🔴 2026-06-29 可成长NN架构 · 数据飞轮
"""data_collector.py — 每 cluster 写完自动收集训练数据到 JSONL 池。

业界参考: Tesla FSD 数据飞轮 + Snorkel 弱监督 + LLM-as-judge 校准
集成点: cluster-save-state step 9 末尾调用
env 门控: RUOYU_DATA_FLYWHEEL=1（默认 off）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_POOL_DIR = _SCRIPT_DIR / "training_pool"
_MANIFEST = _POOL_DIR / "data_manifest.json"

_CHANGES_SEP = re.compile(r"---CHANGES(?:_FACTUAL)?---")

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
# 🔴 2026-07-02 CODE_TO_MODEL/CODE_PREFIX_TO_MODEL/EXCLUDED_FROM_FLYWHEEL 桶表拆到
# code_to_model_table.py（本文件 + 桶表会超 1000 行，用户全局规则要求单文件拆分）。
from code_to_model_table import (  # noqa: E402
    CODE_TO_MODEL,
    CODE_PREFIX_TO_MODEL,
    EXCLUDED_FROM_FLYWHEEL,
    resolve_model_for_code,
)


READING_DIMENSION_TO_MODEL: dict[str, str] = {
    "结构层anti-slop": "ai_tone",
    "anti-slop": "ai_tone",
    "塑料感": "ai_tone",
    "voice漂移": "voice_drift",
    "voice": "voice_drift",
    "POV": "coherence",
    "pov": "coherence",
    "信息密度": "surprisal",
    "节奏感": "tension_trajectory",
    "对话工艺": "dialogue_pragmatics",
    "互动质感": "character_trajectory",
}


def enabled() -> bool:
    return os.environ.get("RUOYU_DATA_FLYWHEEL") == "1"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _strip_changes(text: str) -> str:
    parts = _CHANGES_SEP.split(text, maxsplit=1)
    return parts[0].rstrip() if parts else text


def _split_paragraphs(text: str, min_cjk: int = 6) -> list[str]:
    cjk_re = re.compile(r"[一-鿿]")
    paras = []
    for line in text.split("\n"):
        s = line.strip()
        if s and len(cjk_re.findall(s)) >= min_cjk:
            paras.append(s)
    return paras


def _grade_to_num(grade) -> int | None:
    if not isinstance(grade, str):
        return None
    return {"A": 4, "B": 3, "C": 2, "D": 1}.get(grade.strip().upper())


def _safe_len_list(v) -> int:
    return len(v) if isinstance(v, list) else 0


def _judge_text(report: dict) -> str:
    parts: list[str] = []
    for q in report.get("evidence_quotes", []) if isinstance(report.get("evidence_quotes"), list) else []:
        if isinstance(q, str) and q.strip():
            parts.append(q.strip())
        elif isinstance(q, dict):
            text = q.get("text") or q.get("quote") or q.get("excerpt")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    for flag in report.get("uncertainty_flags", []) if isinstance(report.get("uncertainty_flags"), list) else []:
        if isinstance(flag, str) and flag.strip():
            parts.append(flag.strip())
    sf = report.get("specific_findings")
    if isinstance(sf, dict):
        for key, val in list(sf.items())[:12]:
            if isinstance(val, (str, int, float, bool)):
                parts.append(f"{key}: {val}")
            elif isinstance(val, list) and val:
                parts.append(f"{key}: {json.dumps(val[:5], ensure_ascii=False)}")
    if not parts:
        parts.append(json.dumps({
            "judge_id": report.get("judge_id"),
            "overall_grade": report.get("overall_grade"),
            "confidence": report.get("confidence"),
        }, ensure_ascii=False))
    return "\n".join(parts)[:4000]


def _judge_calibration_features(report: dict) -> dict:
    grade = report.get("overall_grade")
    grade_num = _grade_to_num(grade)
    confidence = report.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    evidence_count = _safe_len_list(report.get("evidence_quotes"))
    uncertainty_count = _safe_len_list(report.get("uncertainty_flags"))
    waiver_count = _safe_len_list(report.get("waivers"))
    return {
        "feature_schema": "judge_report_reliability_v1",
        "judge_id": report.get("judge_id"),
        "persona": report.get("persona") or "default",
        "overall_grade": grade,
        "grade_num": grade_num,
        "confidence": round(confidence, 4),
        "evidence_quote_count": evidence_count,
        "uncertainty_flag_count": uncertainty_count,
        "waiver_count": waiver_count,
        "has_specific_findings": isinstance(report.get("specific_findings"), dict),
        "schema_version": report.get("schema_version"),
        "chapter": report.get("chapter"),
        "gate_level": "advisory",
        "model_status": "training_sample",
    }


def _append_jsonl(path: Path, records: list[dict]) -> int:
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def _load_manifest() -> dict:
    if _MANIFEST.exists():
        try:
            return json.loads(_MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"total_records": 0, "models": {}, "last_update": None}


def _save_manifest(m: dict) -> None:
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    _MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _cluster_key(cluster_id: str) -> str:
    return cluster_id.replace("cluster_", "")


def _chapter_nums_for_cluster(project: Path, cluster_id: str) -> list[int]:
    event_path = project / "_数据库" / "事件簇.json"
    if not event_path.exists():
        return []
    key = _cluster_key(cluster_id)
    try:
        data = json.loads(event_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    for c in data.get("clusters", []) if isinstance(data, dict) else []:
        cid = str(c.get("cluster_id", "")).replace("cluster_", "")
        if cid != key:
            continue
        cr = c.get("chapter_range") or []
        if isinstance(cr, list) and len(cr) == 2:
            try:
                return list(range(int(cr[0]), int(cr[1]) + 1))
            except (TypeError, ValueError):
                return []
    return []


def _brief_model(checker: str, violation: dict) -> str | None:
    code = str(violation.get("code") or violation.get("issue") or "").strip().upper()
    model = resolve_model_for_code(code)
    if model:
        return model
    if checker == "novel-voice-checker":
        issue = code.lower()
        if issue in {"voice_drift", "catchphrase_overuse"}:
            return "voice_drift"
        if issue == "tone_inconsistency":
            return "dialogue_pragmatics"
        if issue == "pov_violation":
            return "coherence"
    return None


def _text_from_evidence_quotes(quotes) -> str:
    parts: list[str] = []
    if not isinstance(quotes, list):
        return ""
    for q in quotes:
        if isinstance(q, str):
            parts.append(q)
        elif isinstance(q, dict):
            text = q.get("quote") or q.get("text") or q.get("excerpt")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(p.strip() for p in parts if p and p.strip())


def _path_mentions_cluster(path_text: str, cluster_id: str, chapter_nums: list[int]) -> bool:
    norm = path_text.replace("\\", "/")
    key = _cluster_key(cluster_id)
    if f"cluster_{key}_draft" in norm or f"/{cluster_id}_draft/" in norm:
        return True
    for ch in chapter_nums:
        if f"第{ch:03d}章" in norm or f"ch_{ch:03d}" in norm:
            return True
    return False


class ClusterDataCollector:
    """每 cluster 写完后自动收集 9 类训练数据。"""

    def __init__(self, project_dir: str, cluster_id: str):
        self.project = Path(project_dir)
        self.cluster_id = cluster_id
        self.project_name = self.project.name
        self._ts = _ts()
        self._seen: set[str] = set()
        self._model_counts: dict[str, int] = {}

    def collect(self) -> dict:
        if not enabled():
            return {"skipped": True, "reason": "RUOYU_DATA_FLYWHEEL != 1"}

        stats = {
            "paragraphs": self._collect_paragraphs(),
            "fix_pairs": self._collect_fix_pairs(),
            "weak_labels": self._collect_weak_labels(),
            "strong_labels": self._collect_strong_labels(),
            "judge_reports": self._collect_judge_reports(),
            "checker_briefs": self._collect_checker_briefs(),
            "fixer_reports": self._collect_fixer_reports(),
            "repair_reports": self._collect_repair_reports(),
            "reading_reflections": self._collect_reading_reflections(),
            "audit_metadata": self._collect_audit_metadata(),
        }
        total = sum(stats.values())
        stats["total"] = total

        manifest = _load_manifest()
        manifest["total_records"] = manifest.get("total_records", 0) + total
        models = manifest.setdefault("models", {})
        for model, count in self._model_counts.items():
            entry = models.setdefault(model, {"records": 0, "last_update": None})
            entry["records"] = entry.get("records", 0) + count
            entry["last_update"] = self._ts
        manifest["last_update"] = self._ts
        _save_manifest(manifest)

        return stats

    def _make_record(self, text: str, label: str, source: str,
                     model: str = "general", **extra) -> dict | None:
        h = _text_hash(text)
        seen_key = f"{source}|{label}|{model}|{h}"
        if seen_key in self._seen:
            return None
        self._seen.add(seen_key)
        rec = {
            "text": text, "label": label, "source": source,
            "model": model,
            "cluster": self.cluster_id, "project": self.project_name,
            "ts": self._ts, "text_hash": h,
        }
        rec.update(extra)
        self._model_counts[model] = self._model_counts.get(model, 0) + 1
        return rec

    def _collect_paragraphs(self) -> int:
        draft_dir = self.project / "章节" / f"{self.cluster_id}_draft"
        draft_path = draft_dir / f"{self.cluster_id}_draft.txt"
        if not draft_path.exists():
            return 0
        text = _strip_changes(draft_path.read_text(encoding="utf-8"))
        paras = _split_paragraphs(text)
        records = []
        for p in paras:
            r = self._make_record(p, "positive", "draft_paragraph", model="general")
            if r:
                records.append(r)
        return _append_jsonl(_POOL_DIR / "general" / "paragraphs.jsonl", records)

    def _collect_fix_pairs(self) -> int:
        fixer_log = (self.project / "_数据库" /
                     f"{self.cluster_id}_fixer_log.json")
        records_by_model: dict[str, list[dict]] = {}
        if fixer_log.exists():
            try:
                data = json.loads(fixer_log.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
            fixes = data if isinstance(data, list) else data.get("fixes", [])
            for fix in fixes:
                before = fix.get("before", "")
                after = fix.get("after", "")
                if before and after and before != after:
                    r = self._make_record(
                        before, "negative", "fixer_before",
                        model="ai_tone", after=after,
                        report_path=_rel_path(fixer_log, self.project),
                    )
                    if r:
                        records_by_model.setdefault("ai_tone", []).append(r)

        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(_POOL_DIR / model / "fix_pairs.jsonl", recs)
        return total

    def _collect_weak_labels(self) -> int:
        audit_dir = self.project / "_数据库" / ".audit"
        cluster_key = self.cluster_id.replace("cluster_", "")
        candidates = [
            audit_dir / f"cluster_{cluster_key}_audit.json",
            audit_dir / f"{self.cluster_id}_audit.json",
        ]
        audit_path = next((p for p in candidates if p.exists()), None)
        if audit_path is None:
            return 0
        try:
            report = json.loads(audit_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        issues = report.get("issues", [])
        if isinstance(report, list):
            issues = report
        records_by_model: dict[str, list[dict]] = {}
        for iss in issues:
            code = iss.get("code", "")
            model = resolve_model_for_code(code)
            if not model:
                continue
            text = (iss.get("paragraph", "") or iss.get("text", "") or
                    iss.get("anchor_text", "") or iss.get("desc", ""))
            if not text or len(text) < 20:
                continue
            r = self._make_record(text, code, "weak_scanner", model=model)
            if r:
                records_by_model.setdefault(model, []).append(r)
        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(
                _POOL_DIR / model / "weak_labels.jsonl", recs
            )
        return total

    def _collect_strong_labels(self) -> int:
        ch_dir = self.project / "章节"
        changes_glob = []
        cluster_changes = ch_dir / f"{self.cluster_id}_draft" / f"{self.cluster_id}_changes.json"
        if cluster_changes.exists():
            changes_glob.append(cluster_changes)
        for p in ch_dir.glob("第*章/第*_changes.json"):
            changes_glob.append(p)
        records = []
        for cp in changes_glob:
            try:
                changes = json.loads(cp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            waivers = (changes.get("self_eval", {}).get("waivers", [])
                       if isinstance(changes, dict) else [])
            for w in waivers:
                code = w.get("code", "")
                reason = w.get("reason", "")
                model = resolve_model_for_code(code)
                if not model:
                    continue
                text = (w.get("paragraph", "") or w.get("text", "") or
                        w.get("anchor_text", "") or w.get("reason", ""))
                if not text:
                    continue
                r = self._make_record(
                    text, f"WAIVED_{code}", "strong_waiver",
                    model=model, waiver_reason=reason,
                )
                if r:
                    records.append(r)
        if not records:
            return 0
        by_model: dict[str, list[dict]] = {}
        for r in records:
            by_model.setdefault(r.get("model", "general"), []).append(r)
        total = 0
        for model, recs in by_model.items():
            total += _append_jsonl(
                _POOL_DIR / model / "strong_labels.jsonl", recs
            )
        return total

    def _collect_judge_reports(self) -> int:
        judge_dir = self.project / "_数据库" / ".judge_reports"
        if not judge_dir.exists():
            return 0
        cluster_key = self.cluster_id.replace("cluster_", "")
        candidates = list(judge_dir.glob(f"{self.cluster_id}_*.json"))
        candidates += list(judge_dir.glob(f"cluster_{cluster_key}_*.json"))

        # judge_reports_archive 仍会产 ch_NNN_*；cluster-save-state 传入 cluster 后可用
        # 事件簇 chapter_range 把本 cluster 的逐章 judge 信号收进来。
        event_path = self.project / "_数据库" / "事件簇.json"
        chapter_nums = _chapter_nums_for_cluster(self.project, self.cluster_id)
        for ch in chapter_nums:
            candidates += list(judge_dir.glob(f"ch_{ch:03d}_*.json"))

        # 无 chapter_range 时保守收 cluster 级报告；不扫全目录，避免跨 cluster 污染。
        unique = sorted({p.resolve(): p for p in candidates}.values(), key=lambda p: p.name)
        records = []
        for p in unique:
            try:
                report = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(report, dict):
                continue
            features = _judge_calibration_features(report)
            text = _judge_text(report)
            label = f"JUDGE_{features.get('overall_grade') or 'NA'}"
            r = self._make_record(
                text,
                label,
                "judge_report",
                model="judge_reliability",
                report_path=str(p.relative_to(self.project)) if p.is_relative_to(self.project) else str(p),
                calibration_features=features,
            )
            if r:
                records.append(r)
        return _append_jsonl(
            _POOL_DIR / "judge_reliability" / "judge_reports.jsonl", records
        )

    def _collect_checker_briefs(self) -> int:
        brief_dir = self.project / "_数据库" / ".checker_briefs"
        if not brief_dir.exists():
            return 0
        cluster_key = _cluster_key(self.cluster_id)
        candidates = list(brief_dir.glob(f"cluster_{cluster_key}_*.json"))
        for ch in _chapter_nums_for_cluster(self.project, self.cluster_id):
            candidates += list(brief_dir.glob(f"ch_{ch:03d}_*.json"))
        records_by_model: dict[str, list[dict]] = {}
        judge_records: list[dict] = []
        for p in sorted({x.resolve(): x for x in candidates}.values(), key=lambda x: x.name):
            try:
                brief = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(brief, dict) or brief.get("version") != 1:
                continue
            checker = str(brief.get("checker") or "")
            rel_path = _rel_path(p, self.project)
            chapter_path = brief.get("chapter_path")
            judge_report = brief.get("judge_report") if isinstance(brief.get("judge_report"), dict) else None
            judge_grade = judge_report.get("overall_grade") if judge_report else None
            judge_confidence = judge_report.get("confidence") if judge_report else None
            for v in brief.get("violations", []) if isinstance(brief.get("violations"), list) else []:
                if not isinstance(v, dict):
                    continue
                model = _brief_model(checker, v)
                if not model:
                    continue
                text = str(v.get("original") or v.get("text") or "").strip()
                if len(text) < 4:
                    continue
                code = str(v.get("code") or v.get("issue") or "ISSUE")
                source = "checker_brief_voice" if checker == "novel-voice-checker" else "checker_brief_validator"
                r = self._make_record(
                    text,
                    f"CHECKER_{code}",
                    source,
                    model=model,
                    brief_path=rel_path,
                    chapter_path=chapter_path,
                    checker=checker,
                    line_start=v.get("line_start"),
                    line_end=v.get("line_end"),
                    fix_hint=v.get("fix_hint"),
                    gate_level=v.get("gate_level"),
                    character=v.get("character"),
                    voice_pack_violated_field=v.get("voice_pack_violated_field"),
                    judge_grade=judge_grade,
                    judge_confidence=judge_confidence,
                )
                if r:
                    records_by_model.setdefault(model, []).append(r)
            if judge_report:
                features = _judge_calibration_features(judge_report)
                jr_text = _judge_text(judge_report)
                r = self._make_record(
                    jr_text,
                    f"JUDGE_{features.get('overall_grade') or 'NA'}",
                    "checker_brief_embedded_judge_report",
                    model="judge_reliability",
                    brief_path=rel_path,
                    checker=checker,
                    calibration_features=features,
                )
                if r:
                    judge_records.append(r)
            if checker == "novel-voice-checker" and not brief.get("violations") and judge_report:
                grade = str(judge_report.get("overall_grade") or "").upper()
                text = _text_from_evidence_quotes(judge_report.get("evidence_quotes"))
                if grade == "A" and text:
                    r = self._make_record(
                        text,
                        "VOICE_CLEAN_A",
                        "checker_brief_voice_clean",
                        model="voice_drift",
                        brief_path=rel_path,
                        checker=checker,
                        judge_grade=grade,
                        judge_confidence=judge_report.get("confidence"),
                    )
                    if r:
                        records_by_model.setdefault("voice_drift", []).append(r)
        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(_POOL_DIR / model / "checker_briefs.jsonl", recs)
        total += _append_jsonl(
            _POOL_DIR / "judge_reliability" / "embedded_judge_reports.jsonl", judge_records
        )
        return total

    def _collect_fixer_reports(self) -> int:
        quality_dir = self.project / "章节" / "_quality"
        if not quality_dir.exists():
            return 0
        chapter_nums = _chapter_nums_for_cluster(self.project, self.cluster_id)
        records_by_model: dict[str, list[dict]] = {}
        for p in sorted(quality_dir.glob("fixer_report_*.json"), key=lambda x: x.name):
            try:
                report = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(report, dict):
                continue
            touched_paths = [str(x.get("path") or "") for x in report.get("files_written", []) if isinstance(x, dict)]
            touched_paths += [str(x.get("path") or "") for x in report.get("rejected_blocks", []) if isinstance(x, dict)]
            if touched_paths and not any(_path_mentions_cluster(x, self.cluster_id, chapter_nums) for x in touched_paths):
                continue
            rel_path = _rel_path(p, self.project)
            mode = report.get("mode") or "unknown"
            summary = report.get("gen_model_summary") if isinstance(report.get("gen_model_summary"), dict) else {}
            voice_changes = summary.get("voice_changes_per_violation") if isinstance(summary, dict) else None
            if isinstance(voice_changes, dict):
                for key, change in voice_changes.items():
                    if not isinstance(change, dict):
                        continue
                    before = str(change.get("before") or "").strip()
                    after = str(change.get("after") or "").strip()
                    if not before or not after or before == after:
                        continue
                    r = self._make_record(
                        before,
                        f"FIX_BEFORE_{mode}",
                        "gen_fixer_voice_pair",
                        model="voice_drift",
                        after=after,
                        mode=mode,
                        violation_id=key,
                        character=change.get("character"),
                        used_profile=report.get("used_profile"),
                        used_model=report.get("used_model"),
                        fixer_report_path=rel_path,
                    )
                    if r:
                        records_by_model.setdefault("voice_drift", []).append(r)
            addressed = summary.get("violations_addressed", []) if isinstance(summary, dict) else []
            skipped = summary.get("violations_skipped", []) if isinstance(summary, dict) else []
            status_payload = {
                "mode": mode,
                "used_profile": report.get("used_profile"),
                "used_model": report.get("used_model"),
                "files_written": report.get("files_written", []),
                "violations_addressed": addressed,
                "violations_skipped": skipped,
                "timestamp": report.get("timestamp"),
            }
            label = f"FIX_SUCCESS_{mode}" if report.get("files_written") else f"FIX_NOOP_{mode}"
            r = self._make_record(
                json.dumps(status_payload, ensure_ascii=False),
                label,
                "gen_fixer_report",
                model="fix_routing",
                fixer_report_path=rel_path,
            )
            if r:
                records_by_model.setdefault("fix_routing", []).append(r)
            for block in report.get("rejected_blocks", []) if isinstance(report.get("rejected_blocks"), list) else []:
                if not isinstance(block, dict):
                    continue
                reason = block.get("reason") or "unknown"
                r = self._make_record(
                    json.dumps(block, ensure_ascii=False),
                    f"FIX_REJECTED_{reason}",
                    "gen_fixer_rejected_block",
                    model="fix_routing",
                    mode=mode,
                    used_model=report.get("used_model"),
                    fixer_report_path=rel_path,
                )
                if r:
                    records_by_model.setdefault("fix_routing", []).append(r)
            scanner_results = report.get("scanner_results") if isinstance(report.get("scanner_results"), dict) else {}
            for file_path, scanners in scanner_results.items():
                if not isinstance(scanners, dict):
                    continue
                for scanner, result in scanners.items():
                    if not isinstance(result, dict):
                        continue
                    verdict = result.get("verdict") or "NA"
                    payload = {"file_path": file_path, "scanner": scanner, **result}
                    r = self._make_record(
                        json.dumps(payload, ensure_ascii=False),
                        f"POST_FIX_SCANNER_{verdict}",
                        "gen_fixer_scanner_result",
                        model="scanner_reliability",
                        mode=mode,
                        fixer_report_path=rel_path,
                    )
                    if r:
                        records_by_model.setdefault("scanner_reliability", []).append(r)
        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(_POOL_DIR / model / "fixer_reports.jsonl", recs)
        return total

    def _collect_repair_reports(self) -> int:
        chapter_nums = _chapter_nums_for_cluster(self.project, self.cluster_id)
        if not chapter_nums:
            return 0
        records_by_model: dict[str, list[dict]] = {}
        for ch in chapter_nums:
            report_path = self.project / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.repair.json"
            if not report_path.exists():
                continue
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(report, dict):
                continue
            rel_path = _rel_path(report_path, self.project)
            for pp in report.get("per_pass", []) if isinstance(report.get("per_pass"), list) else []:
                if not isinstance(pp, dict):
                    continue
                for fix in pp.get("banned_fixes", []) if isinstance(pp.get("banned_fixes"), list) else []:
                    if not isinstance(fix, dict):
                        continue
                    text = json.dumps(fix, ensure_ascii=False)
                    r = self._make_record(text, "REPAIR_BANNED_WORD", "style_repair_banned_fix",
                                          model="ai_tone", repair_report_path=rel_path)
                    if r:
                        records_by_model.setdefault("ai_tone", []).append(r)
                for fix in pp.get("tag_fixes", []) if isinstance(pp.get("tag_fixes"), list) else []:
                    if not isinstance(fix, dict):
                        continue
                    text = json.dumps(fix, ensure_ascii=False)
                    r = self._make_record(text, "REPAIR_TAG", "style_repair_tag_fix",
                                          model="dialogue_pragmatics", repair_report_path=rel_path)
                    if r:
                        records_by_model.setdefault("dialogue_pragmatics", []).append(r)
            if isinstance(report.get("before"), dict) and isinstance(report.get("after"), dict):
                rhythm_payload = {
                    "before": report.get("before"),
                    "after": report.get("after"),
                    "actual_passes": report.get("actual_passes"),
                    "converged_at_pass": report.get("converged_at_pass"),
                }
                r = self._make_record(json.dumps(rhythm_payload, ensure_ascii=False),
                                      "REPAIR_STYLE_METRICS", "style_repair_metrics",
                                      model="style_fidelity", repair_report_path=rel_path)
                if r:
                    records_by_model.setdefault("style_fidelity", []).append(r)
            routing_payload = {
                "iterative": report.get("iterative"),
                "max_passes": report.get("max_passes"),
                "actual_passes": report.get("actual_passes"),
                "converged_at_pass": report.get("converged_at_pass"),
            }
            r = self._make_record(json.dumps(routing_payload, ensure_ascii=False),
                                  "REPAIR_CONVERGED" if report.get("converged_at_pass") else "REPAIR_NOT_CONVERGED",
                                  "style_repair_routing", model="fix_routing", repair_report_path=rel_path)
            if r:
                records_by_model.setdefault("fix_routing", []).append(r)
        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(_POOL_DIR / model / "repair_reports.jsonl", recs)
        return total

    def _collect_reading_reflections(self) -> int:
        rr_dir = self.project / "_数据库" / ".reading_reflection"
        if not rr_dir.exists():
            return 0
        cluster_key = self.cluster_id.replace("cluster_", "")
        candidates = list(rr_dir.glob(f"{self.cluster_id}_round_*.json"))
        candidates += list(rr_dir.glob(f"cluster_{cluster_key}_round_*.json"))
        records_by_model: dict[str, list[dict]] = {}
        verdict_records: list[dict] = []
        for p in sorted({x.resolve(): x for x in candidates}.values(), key=lambda x: x.name):
            try:
                report = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(report, dict):
                continue
            round_no = report.get("round") or report.get("round_no")
            issues = report.get("new_issues_this_round") or []
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    dim = str(issue.get("dimension") or "").strip()
                    model = READING_DIMENSION_TO_MODEL.get(dim, "reader_experience")
                    text = (issue.get("evidence") or issue.get("description") or
                            issue.get("location") or issue.get("suggested_fix") or "")
                    if not isinstance(text, str) or len(text.strip()) < 8:
                        continue
                    label = f"READING_{dim or 'ISSUE'}"
                    r = self._make_record(
                        text.strip(), label, "reading_reflection_issue", model=model,
                        dimension=dim, severity=issue.get("severity"),
                        round=round_no, applies_to_future_clusters=bool(issue.get("applies_to_future_clusters")),
                        report_path=str(p.relative_to(self.project)) if p.is_relative_to(self.project) else str(p),
                    )
                    if r:
                        records_by_model.setdefault(model, []).append(r)
            verdict = report.get("verdict")
            if verdict:
                text = json.dumps({
                    "verdict": verdict,
                    "consecutive_clean_rounds": report.get("consecutive_clean_rounds"),
                    "total_issues": report.get("total_issues"),
                    "fixed_from_previous_round": report.get("fixed_from_previous_round"),
                }, ensure_ascii=False)
                r = self._make_record(
                    text, f"READING_VERDICT_{verdict}", "reading_reflection_verdict",
                    model="reader_experience", round=round_no,
                    report_path=str(p.relative_to(self.project)) if p.is_relative_to(self.project) else str(p),
                )
                if r:
                    verdict_records.append(r)
        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(
                _POOL_DIR / model / "reading_reflections.jsonl", recs
            )
        total += _append_jsonl(
            _POOL_DIR / "reader_experience" / "reading_verdicts.jsonl", verdict_records
        )
        return total

    def _collect_audit_metadata(self) -> int:
        audit_dir = self.project / "_数据库" / ".audit"
        cluster_key = self.cluster_id.replace("cluster_", "")
        candidates = [
            audit_dir / f"cluster_{cluster_key}_audit.json",
            audit_dir / f"{self.cluster_id}_audit.json",
        ]
        audit_path = next((p for p in candidates if p.exists()), None)
        if audit_path is None:
            return 0
        try:
            report = json.loads(audit_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        if not isinstance(report, dict):
            return 0
        records_by_model: dict[str, list[dict]] = {}
        rel_path = str(audit_path.relative_to(self.project)) if audit_path.is_relative_to(self.project) else str(audit_path)

        scanner_status = report.get("scanner_status") or []
        if isinstance(scanner_status, list):
            for st in scanner_status:
                if not isinstance(st, dict):
                    continue
                text = json.dumps(st, ensure_ascii=False)
                label = "SCANNER_OK" if st.get("ok") else "SCANNER_FAIL"
                r = self._make_record(text, label, "audit_scanner_status", model="scanner_reliability",
                                      report_path=rel_path)
                if r:
                    records_by_model.setdefault("scanner_reliability", []).append(r)

        waiver_audit = report.get("waiver_audit")
        if isinstance(waiver_audit, dict):
            r = self._make_record(
                json.dumps(waiver_audit, ensure_ascii=False),
                "WAIVER_AUDIT",
                "audit_waiver_metadata",
                model="waiver_calibration",
                report_path=rel_path,
            )
            if r:
                records_by_model.setdefault("waiver_calibration", []).append(r)

        for key, model, source in (
            ("auto_fixed", "fix_routing", "audit_auto_fixed"),
            ("pending_agent", "fix_routing", "audit_pending_agent"),
        ):
            vals = report.get(key) or []
            if not isinstance(vals, list):
                continue
            for item in vals:
                if not isinstance(item, dict):
                    continue
                code = item.get("code") or item.get("dimension") or key
                text = json.dumps(item, ensure_ascii=False)
                r = self._make_record(text, f"{key.upper()}_{code}", source, model=model,
                                      report_path=rel_path)
                if r:
                    records_by_model.setdefault(model, []).append(r)

        total = 0
        for model, recs in records_by_model.items():
            total += _append_jsonl(
                _POOL_DIR / model / "audit_metadata.jsonl", recs
            )
        return total


def main():
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="数据飞轮 · cluster 级数据收集")
    ap.add_argument("project_dir", help="小说项目目录")
    ap.add_argument("cluster_id", help="cluster key（如 cluster_001）")
    args = ap.parse_args()
    result = ClusterDataCollector(args.project_dir, args.cluster_id).collect()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
