#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anachronism_scanner.py — 时代错位/穿帮检测（advisory · cluster · 2026-06-20）

【缺口】R6 联网调研：时代错位/穿帮（anachronism）在 historical/古言/仙侠/古风最大题材群
里【全系统零覆盖】。H3「穿越感防控」此前只在 judge / writer 层教（prompt 提醒），**无 scanner**
做客观检测闭环。本 scanner 补检测端：古代/架空古风背景正文里混进现代词/超时代器物/现代制度观念。

【做法 · 确定性纯规则正则（不依赖 LLM）】：
  1. era 门控：从 <project>/_数据库 读 setting_era（世界观.json 的 era / setting_era 字段，再退作者档）。
     **无 setting_era 字段 → skip 返回骨架（北极星②：系统是顾问，作者没标背景就别擅自判）**。
     **era 存在但非古代/古风 → skip（现代/科幻背景不该被此 scanner 检）**。
  2. 仅当显式标【古代 / 架空古风 / 仙侠 / 玄幻 / 武侠 / 修真】背景才检。
  3. MODERN_TERMS 分类词库（精选高确定性穿帮词·宁可漏报）：网络数码 / 现代器物 / 现代制度观念 /
     现代量词 / 现代俚语。**刻意剔除多义词**（如『系统/数据/经济/相机/民主/博士/大学/医院』在
     古风/仙侠可能合法 → 不入库，避免误报）。
  4. 命中任一现代词即 advisory 提示（per_1k > 0）。

【genre 门控提示】穿越 / 搞笑古风 / 架空轻松题材天然会出现现代词（穿越者内心独白难区分）→
  本 scanner 只在 setting_era 存在时检，且【只给密度不 hard_gate】。

【北极星② / ⑤ 顾问非法官】时代背景由作者档决定（北极星②：作者档第一权威）·穿帮是 advisory
  待裁决（北极星⑤）·code ANACHRONISM_DETECTED **绝不进 audit_hub.HARD_GATE_CODES**。
  env ANACHRONISM_MODE: off / shadow(默认·只记不判·零回归) / active。

用法：python anachronism_scanner.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "ANACHRONISM_DETECTED"   # ⚠️ advisory 专用 · 绝不进 HARD_GATE_CODES

# 现代词分类词库（高确定性穿帮词·精选·宁可漏报）
# 刻意剔除多义/古今两栖词：系统/数据/程序/经济/相机/民主/博士/大学/医院/经理 等。
MODERN_TERM_CATEGORIES = {
    "网络数码": (r"互联网|网络|手机|电话|短信|视频通话|在线|点赞|微信|二维码|"
                 r"电脑|电子邮件|邮箱|网址|网站|下载|上传|刷屏|直播间"),
    "现代器物": (r"电灯|电视|冰箱|空调|洗衣机|微波炉|汽车|火车|飞机|地铁|高铁|"
                 r"摩托车|塑料|不锈钢|水泥|混凝土|玻璃杯|照相机|电梯|电池|雷达|导弹"),
    "现代制度观念": (r"公司|KPI|派出所|警察|护士|公民|人权|经济学|股票|"
                     r"信用卡|银行卡|身份证|户口本"),
    "现代量词": r"公里|千米|厘米|毫米|秒钟",
    "现代俚语": r"OK|拜拜|搞定|靠谱|给力|内卷|躺平",
}
_MODERN_COMPILED = {cat: re.compile(pat) for cat, pat in MODERN_TERM_CATEGORIES.items()}

# 古代/架空古风背景标志（era 字段命中才启用检测·仅显式古代/古风/仙侠/玄幻/武侠/修真）
ANCIENT_ERA = re.compile(
    r"(古代|古风|古言|古装|架空古|仙侠|玄幻|武侠|修真|修仙|洪荒|上古|远古|"
    r"江湖|王朝|皇朝|朝代|宫廷|春秋|战国|魏晋|三国|大周|大唐|大宋|大明|大清|"
    r"秦朝|汉朝|唐朝|宋朝|明朝|清朝|神话|玄幻修真|奇幻修仙)")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")


def _mode() -> str:
    m = (os.environ.get("ANACHRONISM_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _read_setting_era(project_root):
    """读 setting_era / era 字段（世界观.json 优先 · 再退作者档）。无 → None。"""
    if not project_root:
        return None
    db = Path(project_root) / "_数据库"
    candidates = [
        (db / "世界观.json", ("setting_era", "era")),
        (db / "作者风格.json", ("setting_era", "era")),
    ]
    for path, keys in candidates:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _is_ancient_era(era) -> bool:
    return bool(era) and bool(ANCIENT_ERA.search(era))


def detect_anachronisms(text: str) -> list:
    """检测现代词命中（分类）。返回 [{term, category, pos}, ...]（按位置排序）。"""
    text = _strip_changes(text)
    hits = []
    for cat, rx in _MODERN_COMPILED.items():
        for m in rx.finditer(text):
            hits.append({"term": m.group(0), "category": cat, "pos": m.start()})
    hits.sort(key=lambda h: h["pos"])
    return hits


def scan(draft_path, project_root=None) -> dict:
    """时代错位/穿帮检测。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "anachronism", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败：{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    era = _read_setting_era(project_root)
    out["setting_era"] = era
    if not era:
        out["note"] = "无 setting_era 字段·跳过（北极星②：作者未标背景不擅判）"
        return out
    if not _is_ancient_era(era):
        out["note"] = f"非古代/古风背景（era={era}）·跳过"
        return out

    hits = detect_anachronisms(draft)
    per_1k = round(len(hits) / (cjk / 1000.0), 2)
    out["anachronism_count"] = len(hits)
    out["per_1k"] = per_1k
    out["sample_terms"] = hits[:8]
    cat_counts = {}
    for h in hits:
        cat_counts[h["category"]] = cat_counts.get(h["category"], 0) + 1
    out["category_counts"] = cat_counts

    msg = None
    if len(hits) > 0:  # 命中任一现代词即提示（per_1k > 0）
        sample = "、".join(sorted({h["term"] for h in hits})[:6])
        msg = (f"疑似时代错位/穿帮：古代/古风背景（era={era}）正文出现现代词 "
               f"{len(hits)} 处（{per_1k}/千字·{sample} 等）·建议核对是否符合时代设定")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "anachronism", "severity": "minor",
                "message": msg, "per_1k": per_1k, "setting_era": era,
                "sample_terms": hits[:8], "category_counts": cat_counts,
                "_doc": "时代背景由作者档决定·穿帮 advisory 待裁决·穿越/搞笑古风可豁免·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:  # shadow：只记不判（零回归）
            print(f"[SHADOW] anachronism: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="时代错位/穿帮检测(advisory · 古代/古风背景)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 setting_era 门控")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
