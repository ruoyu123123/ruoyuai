#!/usr/bin/env python3
"""scaffold_subsystems.py — 新书 34 子系统 JSON 的【确定性脚手架 + 验证】

根治「34 子系统无确定性框架→每本书手搓/agent 自由生成→契约债 + 弱模型翻车」。
单一真理源 = core/claude-home/templates/subsystem_skeletons.json（schema 正确的空骨架）。
弱模型只需往骨架里填内容，不碰 schema（照顾弱模型）。

用法：
  python scaffold_subsystems.py --list                       # 列出 34 个 canonical 文件
  python scaffold_subsystems.py emit  <项目名|--db-dir 路径>  # 生成缺失骨架（已存在不覆盖，--force 覆盖）
  python scaffold_subsystems.py verify <项目名|--db-dir 路径>  # 校验 34 件存在 + json 合法（流程缺步补全）

退出码：
  0  成功 / 全部齐全合法
  2  verify 发现缺失或损坏 / 致命错误
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # 仓库根
SKELETON_FILE = ROOT / "core" / "claude-home" / "templates" / "subsystem_skeletons.json"


def _load_skeletons():
    data = json.loads(SKELETON_FILE.read_text(encoding="utf-8"))
    return data["_canonical_34"], data["skeletons"]


def _resolve_db_dir(args) -> Path:
    """从 args 解析 _数据库 目录。支持 --db-dir <路径> 或 位置参数=项目名。"""
    if "--db-dir" in args:
        i = args.index("--db-dir")
        return Path(args[i + 1])
    # 位置参数 = 项目名
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("[FATAL] 需要 项目名 或 --db-dir 路径", file=sys.stderr)
        sys.exit(2)
    name = positional[0]
    for base in ("novels", "styles"):
        cand = ROOT / "workspace" / base / name / "_数据库"
        if cand.parent.exists():
            return cand
    # 默认按 novels
    return ROOT / "workspace" / "novels" / name / "_数据库"


def _atomic_write(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def cmd_list():
    canonical, _ = _load_skeletons()
    print(f"canonical 34 子系统（{len(canonical)} 个）:")
    for i, name in enumerate(canonical, 1):
        print(f"  {i:2d}. {name}.json")
    return 0


def cmd_emit(args):
    canonical, skeletons = _load_skeletons()
    db = _resolve_db_dir(args)
    force = "--force" in args
    db.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for name in canonical:
        path = db / f"{name}.json"
        if path.exists() and not force:
            skipped.append(name)
            continue
        skel = skeletons.get(name)
        if skel is None:
            print(f"[WARN] skeletons.json 缺 {name} 骨架，写空对象兜底", file=sys.stderr)
            skel = {"schema_version": "v27", "_doc": f"{name} 骨架占位（skeletons.json 未定义）"}
        _atomic_write(path, skel)
        created.append(name)
    print(f"[scaffold emit] 目标: {db}")
    print(f"  新建 {len(created)} · 跳过(已存在) {len(skipped)} · 共 {len(canonical)}")
    if created:
        print(f"  新建: {created}")
    if skipped and force:
        print("  (--force 模式应全部覆盖，无跳过)")
    return 0


def cmd_verify(args):
    canonical, _ = _load_skeletons()
    db = _resolve_db_dir(args)
    if not db.exists():
        print(f"[FATAL] _数据库 目录不存在: {db}", file=sys.stderr)
        return 2
    missing, bad, no_schema_ver, ok = [], [], [], 0
    for name in canonical:
        path = db / f"{name}.json"
        if not path.exists():
            missing.append(name)
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append((name, str(e)[:80]))
            continue
        ok += 1
        if isinstance(obj, dict) and "schema_version" not in obj and "_schema" not in obj:
            no_schema_ver.append(name)
    print(f"[scaffold verify] 目标: {db}")
    print(f"  合法 {ok}/{len(canonical)} · 缺失 {len(missing)} · 损坏 {len(bad)} · 无 schema_version {len(no_schema_ver)}")
    if missing:
        print(f"  ❌ 缺失: {missing}", file=sys.stderr)
    if bad:
        print(f"  ❌ 损坏: {bad}", file=sys.stderr)
    if no_schema_ver:
        print(f"  ⚠ 无 schema_version(建议补): {no_schema_ver}")
    if missing or bad:
        return 2
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if "--list" in args or args[0] == "list":
        return cmd_list()
    op = args[0]
    rest = args[1:]
    if op == "emit":
        return cmd_emit(rest)
    if op == "verify":
        return cmd_verify(rest)
    print(f"[FATAL] 未知操作: {op}（用 emit / verify / list）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
