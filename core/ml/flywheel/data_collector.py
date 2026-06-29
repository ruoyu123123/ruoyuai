# 🔴 2026-06-29 可成长NN架构 · 数据飞轮
"""data_collector.py — 每 cluster 写完自动收集训练数据到 JSONL 池。

业界参考: Tesla FSD 数据飞轮 + Snorkel 弱监督
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

CODE_TO_MODEL: dict[str, str] = {
    "SEMANTIC_METAPHOR_EXPLAIN": "ai_tone",
    "SEMANTIC_APHORISM": "ai_tone",
    "SEMANTIC_NEG_PARALLEL": "ai_tone",
    "SEMANTIC_COPULA_AVOID": "ai_tone",
    "SEMANTIC_FAKE_RANGE": "ai_tone",
    "SEMANTIC_OVER_HEDGE": "ai_tone",
    "SEMANTIC_FORCED_TRIPLE": "ai_tone",
    "SEMANTIC_TAG_SYNONYM_CYCLE": "ai_tone",
    "REVEAL_TELL_OVERUSE": "hook_strength",
    "HOOK_WEAK": "hook_strength",
    "COHERENCE_BREAK": "coherence",
    "COHERENCE_LOW_OVERALL": "coherence",
    "SURPRISAL_TOO_FLAT": "surprisal",
    "SURPRISAL_CLIFF": "surprisal",
    "VOICE_DRIFT_CROSS_SCENE": "voice_drift",
    "EMOTION_ARC_FLAT": "emotion_arc",
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


def _split_paragraphs(text: str, min_cjk: int = 20) -> list[str]:
    cjk_re = re.compile(r"[一-鿿]")
    paras = []
    for line in text.split("\n"):
        s = line.strip()
        if s and len(cjk_re.findall(s)) >= min_cjk:
            paras.append(s)
    return paras


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


class ClusterDataCollector:
    """每 cluster 写完后自动收集 4 类训练数据。"""

    def __init__(self, project_dir: str, cluster_id: str):
        self.project = Path(project_dir)
        self.cluster_id = cluster_id
        self.project_name = self.project.name
        self._ts = _ts()
        self._seen: set[str] = set()

    def collect(self) -> dict:
        if not enabled():
            return {"skipped": True, "reason": "RUOYU_DATA_FLYWHEEL != 1"}

        stats = {
            "paragraphs": self._collect_paragraphs(),
            "fix_pairs": self._collect_fix_pairs(),
            "weak_labels": self._collect_weak_labels(),
            "strong_labels": self._collect_strong_labels(),
        }
        total = sum(stats.values())
        stats["total"] = total

        manifest = _load_manifest()
        manifest["total_records"] = manifest.get("total_records", 0) + total
        manifest["last_update"] = self._ts
        _save_manifest(manifest)

        return stats

    def _make_record(self, text: str, label: str, source: str,
                     model: str = "general", **extra) -> dict | None:
        h = _text_hash(text)
        if h in self._seen:
            return None
        self._seen.add(h)
        rec = {
            "text": text, "label": label, "source": source,
            "cluster": self.cluster_id, "project": self.project_name,
            "ts": self._ts, "text_hash": h,
        }
        rec.update(extra)
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
        if not fixer_log.exists():
            return 0
        try:
            data = json.loads(fixer_log.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        fixes = data if isinstance(data, list) else data.get("fixes", [])
        records = []
        for fix in fixes:
            before = fix.get("before", "")
            after = fix.get("after", "")
            if before and after and before != after:
                r = self._make_record(
                    before, "negative", "fixer_before",
                    model="ai_tone", after=after,
                )
                if r:
                    records.append(r)
        return _append_jsonl(_POOL_DIR / "ai_tone" / "fix_pairs.jsonl", records)

    def _collect_weak_labels(self) -> int:
        audit_path = (self.project / "_数据库" /
                      f"{self.cluster_id}_audit_report.json")
        if not audit_path.exists():
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
            model = CODE_TO_MODEL.get(code)
            if not model:
                continue
            text = iss.get("paragraph", "") or iss.get("text", "")
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
        changes_glob = list(
            (self.project / "章节").glob(f"{self.cluster_id}_draft/第*_changes.json")
        )
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
                model = CODE_TO_MODEL.get(code)
                if not model:
                    continue
                text = w.get("paragraph", "") or w.get("text", "")
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
