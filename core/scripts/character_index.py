"""character_index.py — 角色出现历史快速索引（v17.6 D5 / Story Bible 启发）

业界 Sudowrite/NovelCrafter 标配：搜"克莱上次出现在哪一章说了什么"。
本脚本扫描所有章节，对每个登记角色维护出现历史，写到
_数据库/character_index.json。

用法：
    python character_index.py <项目路径> [--write]
    python character_index.py <项目路径> --query 克莱

索引结构：
{
  "snapshot_at_ch": N,
  "characters": {
    "克莱": {
      "first_appearance_ch": 1,
      "last_appearance_ch": 3,
      "appearances": [
        {"ch": 1, "dialogue_count": 8, "first_line_in_chapter": "...", "actions_summary": "..."},
        ...
      ]
    }
  }
}
"""

import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chapter_io as cio  # noqa: E402  v18：统一正文/数据分离读写


def load_json(p, default=None):
    if not Path(p).exists():
        return default
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def find_chapter_files(project_root: Path) -> list:
    """返回 [(章节号, 章节号)] 列表 —— 第二项保留为章节号占位，
    实际正文读取统一走 cio.read_body()，不再依赖具体文件路径。"""
    chs = set()
    for d in project_root.glob("章节/第*章"):
        m = re.match(r"第(\d+)章", d.name)
        if m:
            chs.add(int(m.group(1)))
    # 兼容平铺旧布局
    for f in project_root.glob("第*章*.txt"):
        m = re.match(r"第(\d+)章", f.name)
        if m and not f.name.endswith("_changes.json"):
            chs.add(int(m.group(1)))
    return [(ch, ch) for ch in sorted(chs)]


def scan_chapter_for_character(body_only: str, name: str) -> dict:
    """对单角色单章扫描。body_only 须为纯正文（由 cio.read_body() 提供）。"""
    out = {"appears": False}
    if name not in body_only:
        return out
    out["appears"] = True
    # 统计提及次数
    out["mention_count"] = body_only.count(name)
    # 简单提取对话：name 后跟引号的引号内容
    dialogues = re.findall(
        rf'{re.escape(name)}[^"「\n]{{0,10}}["「]([^"」\n]{{1,80}})["」]',
        body_only
    )
    if not dialogues:
        # 退一步：找 name 出现后最近的引号内容
        for m in re.finditer(rf'{re.escape(name)}', body_only):
            nearby = body_only[m.end():m.end() + 200]
            dq = re.search(r'["「]([^"」\n]{1,80})["」]', nearby)
            if dq:
                dialogues.append(dq.group(1))
                if len(dialogues) >= 3:
                    break
    out["dialogue_samples"] = dialogues[:3]
    out["dialogue_count_estimate"] = len(dialogues)
    # 首次出现的句子
    first_idx = body_only.find(name)
    if first_idx >= 0:
        # 找该句完整内容（前后到。或换行）
        start = max(0, body_only.rfind("。", 0, first_idx) + 1)
        end = body_only.find("。", first_idx)
        if end < 0:
            end = first_idx + 100
        out["first_line_context"] = body_only[start:end+1].strip()[:120]
    return out


def build_index(project_root: Path) -> dict:
    """扫描所有章节，为每个登记角色构建出现历史。"""
    cards = load_json(project_root / "_数据库" / "人物卡.json", {}).get("characters", [])
    # 角色名 + full_name 两种身份
    names_to_track = {}
    for c in cards:
        n = c.get("name", "")
        fn = c.get("full_name", "")
        if n:
            names_to_track[n] = c
    chapter_files = find_chapter_files(project_root)
    # v18：一次性读全部章节纯正文（cio.read_body 自动处理 v18 分离 / 旧混合 txt）
    bodies = {}
    for ch, _ in chapter_files:
        try:
            bodies[ch] = cio.read_body(project_root, ch)
        except FileNotFoundError:
            continue
    index = {"snapshot_at_ch": chapter_files[-1][0] if chapter_files else 0,
             "characters": {}}
    for name, card in names_to_track.items():
        index["characters"][name] = {
            "card_id": card.get("id"),
            "role": card.get("role"),
            "first_appearance_ch_declared": card.get("first_appearance_ch"),
            "first_appearance_ch_actual": None,
            "last_appearance_ch_actual": None,
            "total_chapters_appeared": 0,
            "appearances": [],
        }
        for ch, _ in chapter_files:
            body = bodies.get(ch)
            if body is None:
                continue
            r = scan_chapter_for_character(body, name)
            if r.get("appears"):
                index["characters"][name]["appearances"].append({
                    "ch": ch,
                    "mention_count": r.get("mention_count", 0),
                    "dialogue_count_estimate": r.get("dialogue_count_estimate", 0),
                    "dialogue_samples": r.get("dialogue_samples", []),
                    "first_line_context": r.get("first_line_context", ""),
                })
                index["characters"][name]["last_appearance_ch_actual"] = ch
                if index["characters"][name]["first_appearance_ch_actual"] is None:
                    index["characters"][name]["first_appearance_ch_actual"] = ch
                index["characters"][name]["total_chapters_appeared"] += 1
        # 检测申报 vs 实际首次出现差异
        declared = index["characters"][name]["first_appearance_ch_declared"]
        actual = index["characters"][name]["first_appearance_ch_actual"]
        if declared and actual and declared != actual:
            index["characters"][name]["declaration_warning"] = (
                f"人物卡申报 first_appearance_ch={declared}，但实际首次出现在 ch{actual}"
            )
    return index


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    if "--query" in args:
        # 查询模式
        idx = args.index("--query")
        name = args[idx + 1]
        index_path = project_root / "_数据库" / "character_index.json"
        if not index_path.exists():
            print(f"[FATAL] 先跑一次 python {__file__} {project_root} --write")
            sys.exit(1)
        index = load_json(index_path, {})
        char = index.get("characters", {}).get(name)
        if not char:
            print(f"未找到角色「{name}」")
            sys.exit(1)
        print(f"[角色查询] {name}")
        print(f"  申报首章: ch{char['first_appearance_ch_declared']}")
        print(f"  实际首章: ch{char['first_appearance_ch_actual']}")
        print(f"  最后章: ch{char['last_appearance_ch_actual']}")
        print(f"  出场总数: {char['total_chapters_appeared']}")
        print(f"  最近 3 次出场：")
        for app in char.get("appearances", [])[-3:]:
            print(f"    ch{app['ch']}: 提及 {app['mention_count']} 次 / 对话 {app['dialogue_count_estimate']} 句")
            if app.get("first_line_context"):
                print(f"      首句：{app['first_line_context']}")
            for d in app.get("dialogue_samples", []):
                print(f"      对话样本：「{d[:50]}」")
        sys.exit(0)

    index = build_index(project_root)
    write = "--write" in args
    if write:
        out = project_root / "_数据库" / "character_index.json"
        out.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] character_index 已写入: {out}")
    print(f"[Character Index Snapshot]")
    print(f"  扫描章节范围: 1-{index['snapshot_at_ch']}")
    print(f"  角色数: {len(index['characters'])}")
    for name, info in index["characters"].items():
        print(f"  {name}: 出现 {info['total_chapters_appeared']} 章，最后 ch{info['last_appearance_ch_actual']}")
        if info.get("declaration_warning"):
            print(f"    ⚠️  {info['declaration_warning']}")


if __name__ == "__main__":
    main()
