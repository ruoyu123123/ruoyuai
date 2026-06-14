#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adaptation_kit.py — 改编资料包生成器（一人公司·喂 IP 后端·纯确定性投影·2026-06-15）

【缺口】若渝AI 产物链 export_book.py L193 止于「拼接全文.txt」·全套结构化设定只喂 writer 自己
消费·零对外投影出口（一人公司调研 IP 端最高价值 P0·distill-character L272 已写「为续集/番外
准备角色档案」use-case 却无实现）。本模块补这个投影层：把现有子系统 JSON 投影成影视/有声/漫改
方要的资料·把若渝AI 从「写正文工具」变「喂 IP 后端工具」。

【做法 · 纯确定性零 LLM 零封号风险】读子系统 JSON → 拼 markdown·产 4 份：
  1. 人物小传（人物卡 → name/role/外貌/性格/声纹/弧线/锁定事实）
  2. 世界设定集（世界观 → era/location/规则/派系/词条）
  3. 故事梗概（进度.volumes 卷主题 → core_conflict/volume_arc/ending_state）
  4. 高潮伏笔清单（伏笔表 → tier/desc 关键转折）
落 workspace/novels/<书>/改编资料包/·缺的子系统跳过（投影现有·不编造）。

【北极星】纯确定性投影（零 gen-model 零封号）·只投影现有数据·不碰写作判断·是「一人公司 BYOK
  作者保留全部改编权」的后端拼图（机器产草稿·人做授权决策）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(project_root, name, default=None):
    p = Path(project_root) / "_数据库" / f"{name}.json"
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def build_character_bios(project_root) -> str:
    """人物卡 → 人物小传 markdown（影视/漫改方要的角色档案）。"""
    pc = _load(project_root, "人物卡", {})
    chars = (pc or {}).get("characters", []) if isinstance(pc, dict) else []
    if not chars:
        return ""
    lines = ["# 人物小传\n", "> 自动从人物卡投影·供影视/有声/漫改方参考·非授权文件\n"]
    for c in chars:
        if not isinstance(c, dict):
            continue
        lines.append(f"## {c.get('name', c.get('id', '?'))}（{c.get('role', '')}）\n")
        if c.get("appearance"):
            lines.append(f"**外貌**：{c['appearance']}\n")
        if c.get("personality"):
            lines.append(f"**性格**：{c['personality']}\n")
        vp = c.get("voice_pack") or {}
        if isinstance(vp, dict) and (vp.get("style") or vp.get("style_samples")):
            lines.append(f"**声纹**：{vp.get('style', '')}")
            samples = vp.get("style_samples") or []
            if samples:
                lines.append(f"  - 台词样本：{samples[0]}")
            lines.append("")
        if c.get("arc"):
            lines.append(f"**角色弧线**：{c['arc']}\n")
        lf = c.get("locked_facts") or []
        if lf:
            lines.append("**关键设定**：" + "；".join(str(x) for x in lf) + "\n")
    return "\n".join(lines)


def build_worldbuilding(project_root) -> str:
    """世界观 → 世界设定集 markdown。"""
    w = _load(project_root, "世界观", {})
    if not isinstance(w, dict) or not w:
        return ""
    lines = ["# 世界设定集\n", "> 自动从世界观投影·供改编方参考\n"]
    if w.get("era"):
        lines.append(f"**纪元**：{w['era']}\n")
    if w.get("location"):
        lines.append(f"**主要场域**：{w['location']}\n")
    rules = w.get("rules") or []
    if rules:
        lines.append("**核心规则**：")
        lines.extend(f"  - {r}" for r in rules)
        lines.append("")
    factions = w.get("factions") or []
    if factions:
        lines.append("**势力**：" + "、".join(str(f) for f in factions) + "\n")
    entries = w.get("entries") or []
    if entries:
        lines.append("## 设定词条\n")
        for e in entries:
            if isinstance(e, dict) and e.get("title"):
                lines.append(f"### {e['title']}\n{e.get('content', '')}\n")
    return "\n".join(lines)


def build_synopsis(project_root) -> str:
    """进度.volumes 卷主题 → 故事梗概 markdown。"""
    prog = _load(project_root, "进度", {})
    vols = (prog or {}).get("volumes", []) if isinstance(prog, dict) else []
    if not vols:
        return ""
    lines = ["# 故事梗概\n", "> 自动从进度卷大纲投影·供改编方参考\n"]
    for v in vols:
        if not isinstance(v, dict):
            continue
        lines.append(f"## {v.get('title', '')}\n")
        if v.get("core_conflict"):
            lines.append(f"**核心冲突**：{v['core_conflict']}\n")
        if v.get("volume_arc"):
            lines.append(f"**卷弧**：{v['volume_arc']}\n")
        if v.get("ending_state"):
            lines.append(f"**收束**：{v['ending_state']}\n")
    return "\n".join(lines)


def build_climax_hooks(project_root) -> str:
    """伏笔表 → 高潮伏笔清单 markdown。"""
    fs = _load(project_root, "伏笔表", {})
    if not isinstance(fs, dict):
        return ""
    lines = ["# 高潮·伏笔清单\n", "> 自动从伏笔表投影·供改编方把握关键转折\n"]
    has = False
    for cat in ("promises", "secrets", "deadlines", "pledges"):
        items = fs.get(cat) or []
        if not items:
            continue
        has = True
        lines.append(f"## {cat}\n")
        for it in items:
            if isinstance(it, dict):
                desc = it.get("desc") or it.get("description") or ""
                tier = it.get("tier", "")
                tier_str = f"[tier{tier}] " if tier != "" else ""
                lines.append(f"  - {tier_str}{desc}")
        lines.append("")
    return "\n".join(lines) if has else ""


def generate_kit(project_root) -> dict:
    """产 4 份改编资料 + 落盘 改编资料包/。返回 {out_dir, written:{文件:字节}}。"""
    project_root = Path(project_root)
    out_dir = project_root / "改编资料包"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = {
        "人物小传.md": build_character_bios(project_root),
        "世界设定集.md": build_worldbuilding(project_root),
        "故事梗概.md": build_synopsis(project_root),
        "高潮伏笔清单.md": build_climax_hooks(project_root),
    }
    written = {}
    for fname, content in parts.items():
        if content and content.strip():
            (out_dir / fname).write_text(content, encoding="utf-8")
            written[fname] = len(content)
    return {"out_dir": str(out_dir), "written": written}


def main():
    ap = argparse.ArgumentParser(description="改编资料包生成器(纯确定性投影·喂IP后端·零LLM)")
    ap.add_argument("project_root")
    args = ap.parse_args()
    result = generate_kit(args.project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
