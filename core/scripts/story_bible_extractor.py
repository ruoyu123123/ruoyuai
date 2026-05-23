"""story_bible_extractor.py — 自动 NER 提取 story bible（v17.6 D6 / NovelCrafter 启发）

业界标准：扫描章节正文中的人物/地点/物件 → 与 CHANGES 申报对比 → 警告漏报或误报。

工作模式：
- 仅警告模式（默认）：扫描差异，输出报告，不修改任何文件
- diff 模式：与人物卡/地图/道具.json 对比，找出新发现实体

用法：
    python story_bible_extractor.py <项目路径> <章节号>
    python story_bible_extractor.py <项目路径> --diff <章节号>  # 与现有数据库 diff
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


def find_chapter_file(project_root: Path, ch: int):
    """v18：统一走 cio.find_body_file（兼容嵌套/平铺多布局）。"""
    return cio.find_body_file(project_root, ch)


def extract_known_entities_from_db(project_root: Path) -> dict:
    """从 13 JSON 提取已登记的实体集合。"""
    db = project_root / "_数据库"
    entities = {
        "characters": set(),
        "locations": set(),
        "items": set(),
    }
    cards = load_json(db / "人物卡.json", {}).get("characters", [])
    for c in cards:
        if c.get("name"): entities["characters"].add(c["name"])
        if c.get("full_name"): entities["characters"].add(c["full_name"])
    locations = load_json(db / "地图.json", {}).get("locations", {})
    if isinstance(locations, dict):
        for loc_name in locations.keys():
            entities["locations"].add(loc_name)
    items = load_json(db / "道具.json", {}).get("items", [])
    for it in items:
        if it.get("name"): entities["items"].add(it["name"])
    return entities


def extract_chapter_active_entities(body: str, known: dict) -> dict:
    """从章节正文识别哪些已登记实体被提及。"""
    active = {"characters": [], "locations": [], "items": []}
    for kind, names in known.items():
        for n in names:
            if n and n in body:
                cnt = body.count(n)
                active[kind].append({"name": n, "mention_count": cnt})
    # 按提及次数排序
    for k in active:
        active[k].sort(key=lambda x: -x["mention_count"])
    return active


def scan_potential_new_entities(body: str, known: dict) -> dict:
    """启发式发现可能的"未登记实体"：
    - 反复出现的"X 章"、"X 镇"、"X 教堂"等地点
    - 反复出现的人名（2 字 + 不在 known 里）
    """
    found = {"new_locations_maybe": [], "new_characters_maybe": []}
    # 地点 heuristic：(\w+)(镇|城|港|村|塔|教堂|岛|河|海|街|码头|宅|店|铺)
    loc_pattern = re.compile(r"([一-鿿]{1,4})(镇|城|港|村|塔|教堂|岛|河|海|街|码头|宅|店|铺|府|楼|院|区|湾|崖)")
    loc_counts = {}
    for m in loc_pattern.finditer(body):
        full = m.group(0)
        if full in known["locations"]:
            continue
        loc_counts[full] = loc_counts.get(full, 0) + 1
    for name, cnt in sorted(loc_counts.items(), key=lambda x: -x[1])[:5]:
        if cnt >= 2:
            found["new_locations_maybe"].append({"name": name, "count": cnt})
    # 人名 heuristic 较难，跳过（容易误报）
    return found


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    if "--diff" in args:
        ch = int(args[args.index("--diff") + 1])
    else:
        ch = int(args[1])
    f = find_chapter_file(project_root, ch)
    if not f:
        print(f"[FATAL] 找不到 ch{ch}", file=sys.stderr)
        sys.exit(2)
    # v18：正文走 cio.read_body（v18 分离直读 / 旧混合 txt 自动剥离 CHANGES）
    body = cio.read_body(project_root, ch)
    known = extract_known_entities_from_db(project_root)
    active = extract_chapter_active_entities(body, known)
    potential = scan_potential_new_entities(body, known)

    # 读 CHANGES 申报对比 —— v18 走 cio.read_changes（优先 _changes.json）
    factual = cio.read_changes(project_root, ch).get("factual", {})
    declared_chars = set()
    declared_locs = set()
    if factual:
        for fld in ["active_characters", "characters_present"]:
            for x in factual.get(fld, []) or []:
                if isinstance(x, str): declared_chars.add(x)
                elif isinstance(x, dict): declared_chars.add(x.get("name", ""))
        for cm in factual.get("character_movements", []) or []:
            name = cm.get("character") or cm.get("name")
            if name: declared_chars.add(name)
        for lc in factual.get("location_changes", []) or []:
            n = lc.get("name") or lc.get("location_id")
            if n: declared_locs.add(n)

    actual_chars = {c["name"] for c in active["characters"]}
    actual_locs = {l["name"] for l in active["locations"]}

    print(f"[Story Bible Extract] ch{ch}")
    print(f"\n[角色]")
    print(f"  正文实际提及: {sorted(actual_chars)}")
    print(f"  CHANGES 申报: {sorted(declared_chars)}")
    missing_in_changes = actual_chars - declared_chars
    extra_in_changes = declared_chars - actual_chars
    if missing_in_changes:
        print(f"  ⚠️  漏报（正文有但 CHANGES 未列）: {sorted(missing_in_changes)}")
    if extra_in_changes:
        print(f"  ⚠️  误报（CHANGES 列但正文无）: {sorted(extra_in_changes)}")
    print(f"\n[地点]")
    print(f"  正文实际提及: {sorted(actual_locs)}")
    print(f"  CHANGES 申报: {sorted(declared_locs)}")
    if potential["new_locations_maybe"]:
        print(f"\n[潜在新地点（≥2 次提及，未登记）]")
        for p in potential["new_locations_maybe"]:
            print(f"  - 「{p['name']}」 出现 {p['count']} 次")
    print(f"\n[物件提及]")
    for it in active["items"][:5]:
        print(f"  「{it['name']}」 出现 {it['mention_count']} 次")
    sys.exit(0)


if __name__ == "__main__":
    main()
