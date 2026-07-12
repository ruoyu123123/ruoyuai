"""Audit character emotion distributions across story clusters."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cluster_summary_reader as csr  # noqa: E402

EMOTION_KEYWORDS = {
    "calm": ["平静", "镇定", "冷静", "压抑", "克制"],
    "anxious": ["紧张", "担心", "惶恐", "心跳", "屏住呼吸"],
    "angry": ["愤怒", "怒", "咬牙", "跺脚", "暴怒"],
    "sad": ["悲伤", "难过", "心痛", "泪", "默然", "哭泣"],
    "joyful": ["高兴", "笑", "兴奋", "欣喜", "畅快"],
    "curious": ["好奇", "疑惑", "怎么", "为什么", "想知道"],
    "fearful": ["恐惧", "害怕", "战栗", "毛骨悚然", "颤抖"],
}


def _nearest_emotion_by_valence(valence: float) -> str:
    anchors = {"joyful": 1.0, "calm": 0.6, "curious": 0.55, "anxious": 0.35,
               "angry": 0.28, "fearful": 0.2, "sad": 0.12}
    return min(anchors, key=lambda emotion: abs(anchors[emotion] - valence))


def _model_window_valences(windows: list[str]) -> list[float | None]:
    if not windows or os.environ.get("RUOYU_NN_VAD") != "1":
        return [None] * len(windows)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
        from feature_cache import FeatureStore, enabled
        predictions = FeatureStore.get().compute_vad_batch(windows) if enabled() else None
        if predictions is None:
            import nn_vad_bridge
            predictions = nn_vad_bridge.predict_batch(windows)
    except Exception:
        return [None] * len(windows)
    values: list[float | None] = []
    for prediction in predictions or []:
        try:
            values.append(float(prediction["valence"]) if prediction and prediction.get("valence") is not None else None)
        except (TypeError, ValueError):
            values.append(None)
    return values if len(values) == len(windows) else [None] * len(windows)


def detect_emotions_for_char_detail(text: str, char_name: str) -> dict:
    if not text or not char_name:
        return {"counts": Counter(), "source": "none", "model_window_count": 0, "lexicon_window_count": 0}
    positions = [match.start() for match in re.finditer(re.escape(char_name), text)]
    if not positions:
        return {"counts": Counter(), "source": "none", "model_window_count": 0, "lexicon_window_count": 0}
    windows = [text[max(0, pos - 150):pos + 150] for pos in positions]
    values = _model_window_valences(windows)
    counts = Counter()
    model_hits = 0
    for window, value in zip(windows, values):
        if value is not None:
            counts[_nearest_emotion_by_valence(value)] += 1
            model_hits += 1
            continue
        for emotion, keywords in EMOTION_KEYWORDS.items():
            if any(keyword in window for keyword in keywords):
                counts[emotion] += 1
                break
    source = "model_vad" if model_hits == len(windows) and windows else "lexicon_fallback" if not model_hits else "mixed"
    return {"counts": counts, "source": source, "model_window_count": model_hits,
            "lexicon_window_count": len(windows) - model_hits}


def detect_emotions_for_char(text: str, char_name: str) -> Counter:
    return detect_emotions_for_char_detail(text, char_name)["counts"]


def _cluster_emotions(cluster: dict) -> dict[str, dict[str, int]]:
    result: dict[str, Counter] = defaultdict(Counter)
    direct = cluster.get("char_emotion_counts") or {}
    if isinstance(direct, dict):
        for character, counts in direct.items():
            if isinstance(counts, dict):
                result[character].update({emotion: int(value) for emotion, value in counts.items()
                                          if emotion in EMOTION_KEYWORDS and isinstance(value, (int, float))})
    nested = cluster.get("chapters") or {}
    if isinstance(nested, dict):
        for record in nested.values():
            if not isinstance(record, dict):
                continue
            for character, counts in (record.get("char_emotion_counts") or {}).items():
                if isinstance(counts, dict):
                    result[character].update({emotion: int(value) for emotion, value in counts.items()
                                              if emotion in EMOTION_KEYWORDS and isinstance(value, (int, float))})
    return {character: dict(counts) for character, counts in result.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--last-n", type=int, default=10)
    parser.add_argument("--characters", default=None)
    args = parser.parse_args()
    project_root = Path(args.project)
    if not project_root.is_dir():
        print(f"[FATAL] project directory not found: {project_root}", file=sys.stderr)
        raise SystemExit(2)
    clusters = csr.get_clusters(project_root)
    if args.last_n > 0:
        clusters = clusters[-args.last_n:]
    if not clusters:
        print("[SKIP] no completed cluster records")
        raise SystemExit(0)
    requested = [item.strip() for item in args.characters.split(",") if item.strip()] if args.characters else None
    merged: dict[str, list[tuple[str, dict[str, int]]]] = defaultdict(list)
    for cluster in clusters:
        cid = str(cluster.get("cluster_id"))
        values = _cluster_emotions(cluster)
        names = requested or sorted(values)
        for character in names:
            merged[character].append((cid, values.get(character, {})))

    findings: list[dict] = []
    for character, entries in merged.items():
        total_counts = Counter()
        for _cid, counts in entries:
            total_counts.update(counts)
        if not total_counts:
            continue
        total = sum(total_counts.values())
        distribution = {emotion: round(count / total, 2) for emotion, count in total_counts.items()}
        top_emotion, top_count = total_counts.most_common(1)[0]
        if len(entries) >= 5 and top_count / total > 0.7:
            findings.append({"severity": "advisory", "code": "EMOTION_FLATLINE", "character": character,
                             "top_emotion": top_emotion, "top_pct": round(top_count / total, 2),
                             "distribution": distribution})
        dominant_flags = [counts and counts.get(top_emotion, 0) >= max(counts.values()) for _cid, counts in entries]
        longest: list[str] = []
        current: list[str] = []
        for (cluster_id, _counts), is_dominant in zip(entries, dominant_flags):
            if is_dominant:
                current.append(cluster_id)
                if len(current) > len(longest):
                    longest = list(current)
            else:
                current = []
        if len(longest) >= 6:
            findings.append({"severity": "advisory", "code": "EMOTION_RUN_TOO_LONG", "character": character,
                             "emotion": top_emotion, "consecutive_clusters": longest})

    summary = {"warning": sum(f["severity"] == "warning" for f in findings),
               "advisory": sum(f["severity"] == "advisory" for f in findings)}
    out_dir = project_root / "_数据库" / ".cross_cluster_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {"scan_type": "emotion_pattern", "scan_ts": ts,
              "clusters_scanned": [str(c.get("cluster_id")) for c in clusters],
              "characters_scanned": sorted(merged), "findings": findings, "summary": summary}
    out_path = out_dir / f"emotion_pattern_{ts}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[emotion_pattern] {summary['warning']} warning / {summary['advisory']} advisory")
    print(f"report: {out_path}")
    raise SystemExit(2 if summary["warning"] else 1 if summary["advisory"] else 0)


if __name__ == "__main__":
    main()
