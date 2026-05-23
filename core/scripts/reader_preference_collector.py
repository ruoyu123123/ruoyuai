"""reader_preference_collector.py — 读者偏好持久化采集（v19.6 G10 新增）

策略：
- 周期性（每周/手动）爬主流平台榜单
- 提取榜单作品的"开场套路 / 章节字数 / 爆款元素"分布
- 存 _数据库/.reader_pref/<genre>_<YYYYMMDD>.json
- build_manifest 注入到 writer manifest

简易实现（避免实际爬虫的反爬麻烦）：
- 不真爬，写一个 placeholder + 调用 novel-researcher agent 让 LLM 联网总结
- 数据保留 30 天，过期自动刷新（reader_pref_retention）

用法：
    python reader_preference_collector.py <project> --genre 都市克苏鲁 --refresh
    python reader_preference_collector.py <project> list
退出码: 0 成功
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["refresh", "list", "fetch_via_researcher_stub"])
    ap.add_argument("--genre", default=None)
    args = ap.parse_args()

    project_root = Path(args.project)
    pref_dir = project_root / "_数据库" / ".reader_pref"
    pref_dir.mkdir(parents=True, exist_ok=True)

    if args.action == "list":
        files = sorted(pref_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"reader_pref 累计 {len(files)} 个文件:")
        now = datetime.now()
        for f in files[:10]:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            age_days = (now - mtime).days
            print(f"  {f.name} (age={age_days}d, size={f.stat().st_size}B)")
        sys.exit(0)

    if args.action == "refresh":
        if not args.genre:
            print("[ERROR] refresh 需 --genre", file=sys.stderr)
            sys.exit(2)
        # 写一个 stub 文件，建议主代理后续 spawn novel-researcher 真正填充
        today = datetime.now().strftime("%Y%m%d")
        out_path = pref_dir / f"{args.genre}_{today}.json"
        stub = {
            "_schema": "reader_preference_v19.6_G10",
            "_doc": "由 novel-researcher TASK_TYPE=inspiration 真采集后填充。当前为 stub 占位符。",
            "genre": args.genre,
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "expiry": (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds"),
            "filled": False,
            "platforms_to_scan": ["起点", "番茄", "七猫", "飞卢"],
            "top_works_count": 0,
            "structure": {
                "common_opening_patterns": [],
                "avg_chapter_chars": None,
                "hot_elements": [],
                "reader_complaints_top5": [],
                "data_sources": [],
            },
            "next_refresh_hint": "spawn novel-researcher TASK_TYPE=inspiration SCOPE=[hot_topic, competition] TOPIC=" + args.genre,
        }
        out_path.write_text(json.dumps(stub, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] stub 已生成: {out_path}")
        print(f"[NEXT] 主代理可 spawn novel-researcher 填充数据；或调 fetch_via_researcher_stub action")
        sys.exit(0)

    if args.action == "fetch_via_researcher_stub":
        # 仅打印调用指令，由主代理执行
        print(f"调用主代理 spawn novel-researcher 完成真实采集：")
        print(f"  TASK_TYPE: inspiration")
        print(f"  SCOPE: [hot_topic, competition]")
        print(f"  TOPIC: {args.genre or '<请指定>'}")
        print(f"  OUTPUT_PATH: _数据库/.reader_pref/{args.genre}_{datetime.now().strftime('%Y%m%d')}.json")
        sys.exit(0)


if __name__ == "__main__":
    main()
