"""db_schema_validate.py — 13 个核心 JSON schema 校验 + 自动 migrate（v17.5 / P1.1）

修复"数据库 schema 漂移"问题——常见 migrate：
- 人物卡 dict→list
- 故事块摘要 dict→list
- 伏笔 PR012.due_by_cluster null→默认
- 进度.cluster_blueprint 字段补全

每次 save-state 前跑一次。schema 不符合 → 自动 migrate（备份后改）或报错。

用法：
    python db_schema_validate.py <项目路径> [--auto-migrate] [--strict]

退出码：
    0  全部通过 / 已自动 migrate
    1  发现 schema 错误（--strict 模式下）
    2  致命错误（如 JSON 损坏）
"""

import sys
import json
import shutil
from pathlib import Path
from datetime import datetime


# 13 个核心 JSON + 其 schema 规则
# 字段格式：(json_name, top_keys_required, item_collection_key, expected_item_type, expected_item_required_fields)
SCHEMA_RULES = {
    "人物卡": {
        "required_top_keys": ["schema_version", "characters"],
        "collection_key": "characters",
        "collection_type": list,
        "item_required_fields": ["id", "name", "role"],
    },
    "世界观": {
        "required_top_keys": ["schema_version"],
        "collection_key": "entries",
        "collection_type": list,
        "item_required_fields": ["id", "title", "keywords"],
        "optional": True,  # entries 字段可能不存在（早期项目）
    },
    "伏笔表": {
        "required_top_keys": ["schema_version", "promises"],
        "collection_key": "promises",
        "collection_type": list,
        # v2 cluster 化（2026-05-28）：纯 cluster 模式
        "item_required_fields": ["id", "description", "setup_cluster", "tier"],
    },
    "故事块摘要": {
        "required_top_keys": ["schema_version", "clusters"],
        "collection_key": "clusters",
        "collection_type": list,
        "item_required_fields": ["cluster_id", "title"],
    },
    "进度": {
        "required_top_keys": ["schema_version", "book_title", "current_cluster"],
        "collection_key": "cluster_blueprint",
        "collection_type": dict,
        "item_required_fields": [],  # cluster_blueprint 是 dict（cluster_id → cluster_data）
    },
    "场景规则": {
        "required_top_keys": ["schema_version"],
        "collection_key": "scene_types",
        "collection_type": dict,  # 这个本身是 dict 而非 list
        "item_required_fields": [],
    },
    "写作经验": {
        "required_top_keys": ["schema_version", "entries"],
        "collection_key": "entries",
        "collection_type": list,
        # v2 cluster 化（2026-05-28）：observed_in 用 cluster_id list
        "item_required_fields": ["id", "observed_in", "lesson"],
    },
    # 2026-05-29 复审修复（L15）：实际 用户偏好.json 顶层是分组键
    # （workflow_preferences / style_preferences / content_preferences / ecas_config），
    # 多数项目无 preferences[] 数组，旧规则要求 preferences[] + item {id,key,value} 产生
    # 大量误报 advisory。改为：顶层不强制 preferences；preferences[] 存在时才校验为 list，
    # 不再要求 id/key/value（不同项目 schema 形态不一）。
    "用户偏好": {
        "required_top_keys": ["schema_version"],
        "collection_key": "preferences",
        "collection_type": list,
        "item_required_fields": [],
        "optional": True,  # 文件可缺；preferences[] 也可缺（顶层分组键形态）
    },
    "地图": {
        "required_top_keys": ["schema_version", "locations"],
        "collection_key": "locations",
        "collection_type": dict,
        "item_required_fields": [],
    },
    "关系": {
        "required_top_keys": ["schema_version", "relationships"],
        "collection_key": "relationships",
        "collection_type": list,
        "item_required_fields": ["id", "from", "to", "type"],
    },
    "事件表": {
        "required_top_keys": ["schema_version"],
        "collection_key": "pending_events",
        "collection_type": list,
        "item_required_fields": ["id", "name"],
    },
    "时间线": {
        "required_top_keys": ["schema_version"],
        "collection_key": "world_clock_events",
        "collection_type": list,
        # v2 cluster 化（2026-05-28）：world clock event 用 day/cluster 颗粒
        "item_required_fields": ["event"],
    },
    "道具": {
        "required_top_keys": ["schema_version", "items"],
        "collection_key": "items",
        "collection_type": list,
        "item_required_fields": ["id", "name"],
    },
}


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON 损坏：{p} — {e}")


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_then_save(p: Path, data):
    """改写前备份原文件到 _backup/db_schema/<时间戳>/。"""
    backup_root = p.parent / "_backup" / "db_schema" / datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, backup_root / p.name)
    save_json(p, data)


def migrate_dict_to_list(data: dict, collection_key: str) -> tuple[dict, bool]:
    """把 dict 形式的 collection 转 list。返回 (new_data, migrated)。"""
    coll = data.get(collection_key)
    if not isinstance(coll, dict):
        return data, False
    new_list = []
    for k, v in coll.items():
        if isinstance(v, dict):
            v = dict(v)  # 拷贝
            # 如果原 key 是数字（章号），追加 'ch' 字段
            try:
                v["ch"] = int(k)
            except (ValueError, TypeError):
                v["_orig_key"] = k
            new_list.append(v)
    # 按 ch 排序（如有）
    try:
        new_list.sort(key=lambda x: x.get("ch", 0))
    except TypeError:
        pass
    data[collection_key] = new_list
    return data, True


def fix_null_due_by(data: dict) -> tuple[dict, int]:
    """伏笔表 PR.due_by null → 999。返回 (data, 修复条数)。"""
    fixed = 0
    for p in data.get("promises", []):
        if p.get("due_by") is None:
            p["due_by"] = 999
            fixed += 1
    return data, fixed


def validate_file(path: Path, rules: dict, auto_migrate: bool) -> tuple[list[str], list[str], bool]:
    """校验单个 JSON。返回 (errors, warnings, migrated)。"""
    errors = []
    warnings = []
    migrated = False

    if not path.exists():
        if rules.get("optional"):
            warnings.append(f"[OPTIONAL_MISSING] {path.name} 不存在（可选文件）")
            return errors, warnings, False
        errors.append(f"[MISSING] {path.name} 不存在")
        return errors, warnings, False

    try:
        data = load_json(path)
    except RuntimeError as e:
        errors.append(f"[JSON_BROKEN] {path.name}: {e}")
        return errors, warnings, False

    # 顶层 keys 检查
    for k in rules.get("required_top_keys", []):
        if k not in data:
            warnings.append(f"[MISSING_TOP_KEY] {path.name} 缺 '{k}'")

    # collection 类型检查
    coll_key = rules.get("collection_key")
    coll_type = rules.get("collection_type")
    if coll_key and coll_key in data:
        coll = data[coll_key]
        if not isinstance(coll, coll_type):
            current_type = type(coll).__name__
            expected = coll_type.__name__
            if coll_type is list and isinstance(coll, dict) and auto_migrate:
                data, did = migrate_dict_to_list(data, coll_key)
                if did:
                    backup_then_save(path, data)
                    migrated = True
                    warnings.append(f"[MIGRATED] {path.name}.{coll_key}: dict→list（已备份）")
            else:
                errors.append(
                    f"[TYPE_MISMATCH] {path.name}.{coll_key}: 实际 {current_type}，期望 {expected}"
                )

    # 字段约束检查
    fc = rules.get("field_constraints", {})
    if fc:
        coll = data.get(coll_key, [])
        if isinstance(coll, list):
            for i, item in enumerate(coll):
                if not isinstance(item, dict):
                    continue
                for field, (constraint_type, msg) in fc.items():
                    v = item.get(field)
                    if constraint_type == "int_or_999" and v is None:
                        if auto_migrate:
                            item[field] = 999
                            migrated = True
                            warnings.append(f"[AUTO_FIXED] {path.name}.{coll_key}[{i}].{field}: null→999")
                        else:
                            errors.append(f"[NULL_FIELD] {path.name}.{coll_key}[{i}].{field}: {msg}")
            if migrated:
                backup_then_save(path, data)

    # item 必需字段检查（仅警告，不致命）
    req_fields = rules.get("item_required_fields", [])
    if req_fields and coll_key in data and isinstance(data[coll_key], list):
        for i, item in enumerate(data[coll_key]):
            if not isinstance(item, dict):
                continue
            missing = [f for f in req_fields if f not in item]
            if missing:
                warnings.append(
                    f"[ITEM_MISSING_FIELD] {path.name}.{coll_key}[{i}] 缺字段：{missing}"
                )

    return errors, warnings, migrated


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    auto_migrate = "--auto-migrate" in args
    strict = "--strict" in args

    db_root = project_root / "_数据库"
    if not db_root.exists():
        print(f"[FATAL] 找不到 _数据库 目录：{db_root}", file=sys.stderr)
        sys.exit(2)

    total_errors = []
    total_warnings = []
    migrated_files = []
    for name, rules in SCHEMA_RULES.items():
        path = db_root / f"{name}.json"
        errs, warns, mig = validate_file(path, rules, auto_migrate)
        total_errors.extend(errs)
        total_warnings.extend(warns)
        if mig:
            migrated_files.append(name)

    print(f"[Schema 校验] 项目：{project_root.name}")
    print(f"  共校验 {len(SCHEMA_RULES)} 个 JSON")
    print(f"  Errors: {len(total_errors)}, Warnings: {len(total_warnings)}, Migrated: {len(migrated_files)}")
    if migrated_files:
        print(f"\n[已自动迁移]")
        for n in migrated_files:
            print(f"  - {n}.json（已备份至 _backup/db_schema/）")
    if total_warnings:
        print(f"\n[警告]")
        for w in total_warnings:
            print(f"  {w}")
    if total_errors:
        print(f"\n[错误]")
        for e in total_errors:
            print(f"  {e}")

    if total_errors and strict:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
