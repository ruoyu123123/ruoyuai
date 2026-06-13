#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
litrpg_structure_scanner.py — 游戏向/LitRPG/系统流题材专属结构检测（cluster 视野 · advisory · 阶段3）

题材层(内容工艺)·仅在 genre=horror_game/litrpg 时激活。游戏文核心：面板/数值呈现 +
系统提示双语域 + 升级节拍 + 副本结构。调研(LitRPG Reads / 蓝框 / 马良)：系统提示用蓝框/
方括号把机制文本与散文切开是 LitRPG 最独特维度·爽文完全没有。

探针（全 advisory·宽松·暂无金标准样本故只抓「明显缺游戏元素」）：
  1. no_system_panel  全 cluster 无系统提示/面板标记(【】/方括号/「系统」/属性数值)·游戏文应有
  2. stat_sparse      数值/属性密度过低(游戏文靠面板数值驱动·非纯散文)

⚠️ 暂无 LitRPG 金标准样本校准·阈值保守(宁可漏报不误伤)·待真游戏向书产出后校准。
用法：python litrpg_structure_scanner.py <cluster草稿> [--project <root>] [--style <作者风格.json>]
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

# 系统提示/面板标记
PANEL_MARKERS = ['【', '[系统', '系统提示', '系统：', '叮', '属性', '等级', '经验值',
                 'HP', 'MP', 'LV', '技能栏', '状态栏', '面板', '任务：', '副本']
STAT_FLOOR = 0.5    # 系统/面板标记每千字下限(保守)


def cjk(s: str) -> int:
    return sum(1 for c in s if '一' <= c <= '鿿')


def scan(text: str, project: Path | None = None, style_path: Path | None = None) -> dict:
    total = cjk(text)
    if total < 500:
        return {"scanner": "litrpg_structure", "violations_count": 0, "violations": [],
                "verdict": "PASS", "gate_level": "advisory", "metrics": {}, "_doc": "文本过短"}
    panel_hits = sum(text.count(m) for m in PANEL_MARKERS)
    # 数值密度（连续数字·面板数值代理）
    digit_runs = len(re.findall(r'\d{1,}', text))
    panel_per_1k = round(panel_hits / total * 1000, 2)

    violations = []
    if panel_hits == 0:
        violations.append({'kind': 'no_system_panel', 'severity': 'minor',
                           'panel_hits': 0,
                           'hint': '全 cluster 无系统提示/面板标记(【】/属性/等级/副本/系统：)·'
                                   '游戏文应有面板呈现·系统提示用蓝框方括号与散文切开'})
    elif panel_per_1k < STAT_FLOOR:
        violations.append({'kind': 'stat_sparse', 'severity': 'minor',
                           'panel_per_1k': panel_per_1k, 'floor': STAT_FLOOR,
                           'hint': f'系统/面板标记 {panel_per_1k}/千字偏低·游戏文靠面板数值驱动·'
                                   f'面板卡章末·升级锚在真实风险/牺牲'})

    verdict = 'PASS' if not violations else 'FAIL_MINOR'
    return {
        'scanner': 'litrpg_structure', 'violations_count': len(violations),
        'violations': violations, 'verdict': verdict, 'gate_level': 'advisory',
        'metrics': {'panel_hits': panel_hits, 'panel_per_1k': panel_per_1k,
                    'digit_runs': digit_runs},
        '_doc': '游戏向/LitRPG 题材结构·面板数值+系统提示双语域·advisory·阈值保守待金标准校准',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft")
    ap.add_argument("--project", default=None)
    ap.add_argument("--style", default=None)
    args = ap.parse_args()
    p = Path(args.draft)
    if not p.exists():
        print(f"路径不存在: {p}")
        sys.exit(2)
    r = scan(p.read_text(encoding='utf-8'),
             project=Path(args.project) if args.project else None,
             style_path=Path(args.style) if args.style else None)
    r['file'] = str(p)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r['verdict'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
