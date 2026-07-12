"""检测已闭合卷并确定性维护卷级 cluster 摘要。"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import cluster_lookup
from cluster_summary_reader import load_summary
from cluster_summary_store import upsert_volume_summary
from log_util import get_logger
from save_state_common import (
    load_json,
    require_cluster_id,
    save_json,
    wal_dir,
)


logger = get_logger(__name__)
_OPTIONAL_KEYS = (("emotional_peak", str), ("key_turning_points", list))


def me_volume_of(me: dict):
    """读取 ME 的显式卷号，或从 canonical ME id 中解析卷号。"""
    volume = me.get("volume")
    if isinstance(volume, int) and not isinstance(volume, bool):
        return volume
    if isinstance(volume, str) and volume.strip().isdigit():
        return int(volume.strip())
    match = re.search(r"[Vv](\d+)", str(me.get("id") or ""))
    return int(match.group(1)) if match else None


def _load_major_events(root: Path) -> list:
    document = load_json(root / "_数据库" / "大势卡.json", None)
    if not isinstance(document, dict):
        raise RuntimeError("大势卡.json 缺失或损坏")
    events = document.get("major_events")
    if not isinstance(events, list):
        raise RuntimeError("大势卡.json.major_events 必须是 list")
    return events


def closed_volumes(root: Path) -> dict[int, list[str]]:
    """返回 ME 全部完成且 source cluster 齐全的卷。"""
    by_volume: dict[int, list[dict]] = {}
    for event in _load_major_events(root):
        if not isinstance(event, dict):
            raise RuntimeError("大势卡.major_events 条目必须是 object")
        volume = me_volume_of(event)
        if volume is None:
            raise RuntimeError(f"ME 缺卷号: {event.get('id')!r}")
        by_volume.setdefault(volume, []).append(event)

    closed = {}
    for volume, events in sorted(by_volume.items()):
        if any(event.get("status") != "completed" for event in events):
            continue
        cluster_ids = []
        for event in events:
            raw = event.get("completed_at_cluster")
            cid = cluster_lookup.normalize_cluster_id(raw)
            if cid is None or cid != raw:
                raise RuntimeError(
                    f"卷{volume} ME {event.get('id')} completed_at_cluster 非规范"
                )
            if cid not in cluster_ids:
                cluster_ids.append(cid)
        cluster_ids.sort(key=lambda value: cluster_lookup.cluster_num(value))
        closed[volume] = cluster_ids
    return closed


def cmd_detect_volume_boundary(root: Path, cluster_key) -> int:
    """写出尚未汇总的已闭合卷；没有边界也产显式结果。"""
    try:
        cid = require_cluster_id(cluster_key)
        summary = load_summary(root)
        volume_summaries = summary["volume_summaries"]
        summarized = {
            row["volume"]
            for row in volume_summaries
            if isinstance(row, dict) and isinstance(row.get("volume"), int)
        }
        closed = closed_volumes(root)
        pending = [
            {"volume": volume, "cluster_ids": cluster_ids}
            for volume, cluster_ids in sorted(closed.items())
            if volume not in summarized
        ]
        payload = {
            "schema_version": 1,
            "cluster_id": cid,
            "boundary": bool(pending),
            "volumes_pending": pending,
            "closed_volumes": sorted(closed),
            "volumes_summarized": sorted(summarized),
            "detected_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_json(wal_dir(root) / f"{cid}_volume_boundary.json", payload)
        logger.info(f"[volume-boundary] {cid} pending={len(pending)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL save_state] detect-volume-boundary: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


def cmd_apply_volume_summary(root: Path, volume_n) -> int:
    """校验卷级摘要 source 完整性并按卷号幂等回库。"""
    try:
        volume = int(volume_n)
        db = root / "_数据库"
        product_path = db / ".wal" / f"volume_{volume}_summary.json"
        product = load_json(product_path, None)
        if not isinstance(product, dict):
            raise RuntimeError(f"{product_path.name} 不存在或损坏")
        try:
            product_volume = int(product.get("volume"))
        except (TypeError, ValueError):
            raise RuntimeError("卷级摘要 volume 必须是整数") from None
        if product_volume != volume:
            raise RuntimeError("卷级摘要 volume 与命令参数不一致")
        text = product.get("summary")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("卷级摘要 summary 不能为空")
        raw_source = product.get("source")
        if not isinstance(raw_source, list) or not raw_source:
            raise RuntimeError("卷级摘要 source 必须是非空数组")
        generated_at = product.get("generated_at_cluster")
        if cluster_lookup.normalize_cluster_id(generated_at) != generated_at:
            raise RuntimeError("generated_at_cluster 必须是规范 cluster_id")
        source = []
        for raw in raw_source:
            if cluster_lookup.normalize_cluster_id(raw) != raw:
                raise RuntimeError(f"source 含非规范 cluster_id: {raw!r}")
            if raw not in source:
                source.append(raw)
        expected = closed_volumes(root).get(volume)
        if not expected:
            raise RuntimeError(f"卷{volume} 尚未闭合")
        missing = [cid for cid in expected if cid not in source]
        extra = [cid for cid in source if cid not in expected]
        if missing or extra:
            raise RuntimeError(f"source 不完整: missing={missing} extra={extra}")

        cjk = sum(1 for char in text if "一" <= char <= "鿿" or "㐀" <= char <= "䶿")
        if not 300 <= cjk <= 500:
            logger.warning(f"[volume-summary] 卷{volume} CJK={cjk}，建议范围 300-500")
        entry = {
            "volume": volume,
            "summary": text.strip(),
            "source": sorted(source, key=lambda value: cluster_lookup.cluster_num(value)),
            "generated_at_cluster": generated_at,
        }
        for key, expected_type in _OPTIONAL_KEYS:
            value = product.get(key)
            if isinstance(value, expected_type) and value:
                entry[key] = value

        document = load_summary(root)
        rows = document["volume_summaries"]
        index = next((i for i, row in enumerate(rows)
                      if isinstance(row, dict) and row.get("volume") == volume), None)
        if index is not None:
            current = {key: value for key, value in rows[index].items() if key != "applied_at"}
            if current == entry:
                return 0
        entry["applied_at"] = datetime.now().isoformat(timespec="seconds")
        upsert_volume_summary(root, volume, entry)
        logger.info(f"[volume-summary] 卷{volume} 回库，source={len(source)} CJK={cjk}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL save_state] apply-volume-summary: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
