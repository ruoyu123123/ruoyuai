"""构建角色在各 cluster 中的出现历史索引。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_json import atomic_write_text  # noqa: E402
import cluster_lookup  # noqa: E402


_QUOTED = r'(?:"([^"\n]{1,80})"|“([^”\n]{1,80})”|「([^」\n]{1,80})」)'


def load_json(path: Path, default=None):
    """读取 UTF-8 JSON；缺文件返回显式 default，坏文件直接报错。"""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON 无法读取: {path}: {exc}") from exc


def find_cluster_files(project_root: Path) -> list[tuple[str, Path]]:
    """返回按 cluster 序号排序的 canonical 完整草稿。"""
    found: dict[str, Path] = {}
    for draft in project_root.glob("章节/cluster_*_draft/cluster_*_draft.txt"):
        parent_id = draft.parent.name.removesuffix("_draft")
        file_id = draft.stem.removesuffix("_draft")
        if parent_id != file_id:
            raise ValueError(f"cluster 草稿目录与文件名不一致: {draft}")
        cid = cluster_lookup.normalize_cluster_id(parent_id)
        if cid is None or parent_id != cid:
            raise ValueError(f"cluster 草稿路径使用非规范 id: {draft}")
        if cid in found:
            raise ValueError(f"cluster 草稿重复: {cid}")
        found[cid] = draft
    return sorted(found.items(), key=lambda item: cluster_lookup.cluster_num(item[0]))


def scan_cluster_for_character(body: str, name: str) -> dict:
    """统计一个角色在完整 cluster 草稿中的提及、对白与首次上下文。"""
    if name not in body:
        return {"appears": False}
    escaped = re.escape(name)
    dialogues = []
    for match in re.finditer(rf'{escaped}[^"“「\n]{{0,10}}{_QUOTED}', body):
        dialogues.append(next(group for group in match.groups() if group is not None))
    if not dialogues:
        for match in re.finditer(escaped, body):
            nearby = body[match.end():match.end() + 200]
            quote = re.search(_QUOTED, nearby)
            if quote:
                dialogues.append(next(group for group in quote.groups() if group is not None))
                if len(dialogues) >= 3:
                    break

    first = body.find(name)
    start = max(0, body.rfind("。", 0, first) + 1, body.rfind("\n", 0, first) + 1)
    end = body.find("。", first)
    if end < 0:
        end = min(len(body) - 1, first + 99)
    return {
        "appears": True,
        "mention_count": body.count(name),
        "dialogue_samples": dialogues[:3],
        "dialogue_count_estimate": len(dialogues),
        "first_context": body[start:end + 1].strip()[:120],
    }


def build_index(project_root: Path) -> dict:
    """扫描人物卡和全部 canonical cluster 草稿。"""
    cards_doc = load_json(project_root / "_数据库" / "人物卡.json")
    if not isinstance(cards_doc, dict) or not isinstance(cards_doc.get("characters"), list):
        raise ValueError("人物卡.json.characters 必须是数组")
    cluster_files = find_cluster_files(project_root)
    bodies = {
        cid: path.read_text(encoding="utf-8")
        for cid, path in cluster_files
    }
    index = {
        "schema_version": 1,
        "snapshot_at_cluster": cluster_files[-1][0] if cluster_files else None,
        "characters": {},
    }
    seen_names: set[str] = set()
    for card_index, card in enumerate(cards_doc["characters"]):
        if not isinstance(card, dict):
            raise ValueError(f"人物卡 characters[{card_index}] 必须是 object")
        name = card.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"人物卡 characters[{card_index}].name 不能为空")
        if name in seen_names:
            raise ValueError(f"人物卡角色名重复: {name}")
        seen_names.add(name)
        declared = card.get("first_appearance_cluster")
        if declared is not None:
            normalized = cluster_lookup.normalize_cluster_id(declared)
            if normalized is None or normalized != declared:
                raise ValueError(f"角色 {name}.first_appearance_cluster 非规范: {declared!r}")
        record = {
            "card_id": card.get("id"),
            "role": card.get("role"),
            "first_appearance_cluster_declared": declared,
            "first_appearance_cluster_actual": None,
            "last_appearance_cluster_actual": None,
            "total_clusters_appeared": 0,
            "appearances": [],
        }
        for cid, _ in cluster_files:
            result = scan_cluster_for_character(bodies[cid], name)
            if not result["appears"]:
                continue
            record["appearances"].append({
                "cluster_id": cid,
                "mention_count": result["mention_count"],
                "dialogue_count_estimate": result["dialogue_count_estimate"],
                "dialogue_samples": result["dialogue_samples"],
                "first_context": result["first_context"],
            })
            record["last_appearance_cluster_actual"] = cid
            if record["first_appearance_cluster_actual"] is None:
                record["first_appearance_cluster_actual"] = cid
            record["total_clusters_appeared"] += 1
        actual = record["first_appearance_cluster_actual"]
        if declared is not None and actual is not None and declared != actual:
            record["declaration_warning"] = (
                f"人物卡申报 first_appearance_cluster={declared}，"
                f"正文首次出现于 {actual}"
            )
        index["characters"][name] = record
    return index


def _print_query(name: str, record: dict) -> None:
    print(f"[角色查询] {name}")
    print(f"  申报首次: {record['first_appearance_cluster_declared']}")
    print(f"  正文首次: {record['first_appearance_cluster_actual']}")
    print(f"  最近出现: {record['last_appearance_cluster_actual']}")
    print(f"  出场块数: {record['total_clusters_appeared']}")
    for appearance in record.get("appearances", [])[-3:]:
        print(
            f"  {appearance['cluster_id']}: 提及 {appearance['mention_count']} 次 / "
            f"对白 {appearance['dialogue_count_estimate']} 句"
        )
        if appearance.get("first_context"):
            print(f"    首次上下文：{appearance['first_context']}")
        for dialogue in appearance.get("dialogue_samples", []):
            print(f"    对话样本：「{dialogue[:50]}」")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="构建或查询 cluster 角色出现索引")
    parser.add_argument("project")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--query")
    args = parser.parse_args(argv)
    project_root = Path(args.project)
    try:
        if args.query:
            index = load_json(project_root / "_数据库" / "character_index.json")
            if not isinstance(index, dict):
                raise ValueError("character_index.json 不存在或顶层无效")
            record = index.get("characters", {}).get(args.query)
            if not isinstance(record, dict):
                raise ValueError(f"未找到角色: {args.query}")
            _print_query(args.query, record)
            return 0
        index = build_index(project_root)
        if args.write:
            out = project_root / "_数据库" / "character_index.json"
            atomic_write_text(out, json.dumps(index, ensure_ascii=False, indent=2))
            print(f"[OK] character_index 已写入: {out}")
        print(f"[Character Index] 扫描到 {index['snapshot_at_cluster']}，"
              f"角色数 {len(index['characters'])}")
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
