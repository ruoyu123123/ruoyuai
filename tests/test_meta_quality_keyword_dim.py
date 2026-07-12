"""摘要与正文关键词指纹的一致性测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from cluster_summary_fixtures import cluster_record

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import cross_cluster_meta_quality_aggregate as scanner


def _mismatches(records: list[dict]) -> list[dict]:
    return [
        item for item in scanner.scan_summary_consistency(records)
        if item["code"] == "SUMMARY_KEYWORD_MISMATCH"
    ]


def test_faithful_summary_matches_text_fingerprint() -> None:
    summary = "重黎挥剑斩断天柱，天柱崩塌后大地震动，重黎护住同伴并继续追查天柱来历。" * 2
    record = cluster_record(
        summary=summary,
        text_keyword_set=["重黎", "挥剑", "斩断", "天柱", "大地", "追查"],
    )
    assert _mismatches([record]) == []


def test_fabricated_summary_is_detected() -> None:
    summary = "飞船穿越宇宙后引擎过载，舰长指挥船员躲开辐射并降落陌生星球。" * 3
    record = cluster_record(
        summary=summary,
        text_keyword_set=["重黎", "挥剑", "斩断", "天柱", "大地", "追查"],
    )
    finding = _mismatches([record])[0]
    assert finding["cluster_id"] == "cluster_001"
    assert finding["miss_ratio"] == 1.0


def test_small_fingerprint_skips_mismatch_check() -> None:
    record = cluster_record(
        summary="重黎挥剑斩断天柱，随后护住同伴并追查幕后真相。" * 3,
        text_keyword_set=["重黎", "挥剑"],
    )
    assert _mismatches([record]) == []
