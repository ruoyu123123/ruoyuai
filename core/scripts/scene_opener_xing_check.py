#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scene_opener_xing_check.py — 起兴 scene-opener 检测(advisory · cluster · 2026-06-20 R8 W4 Batch-I)

【缺口】L26 联网调研(Project MUSE Mimesis and 興 + 朱熹比兴 + SCIRP 2017 武侠仙侠玄幻新书前 3 章
ratio 0.68 vs 失败 0.31): 中文叙事尤其武侠/仙侠/玄幻题材的传统场景开篇技法『起兴(xing)』
此前**全系统零检测**。先借外部环境/景物意象起兴 → 自然过渡到角色心理/动作,
而非劈头直接「他很愤怒」式情绪命名(tagged_opener) 或 直接进入对话动作的 bare_opener。

【做法 · 确定性纯规则正则】:
  1. 按段落空行+场景切换标志(『另一边』『与此同时』『片刻后』『次日』等时空切换词)切分场景。
  2. 取每场景前 60-150 字判定三档:
     - xing_ok: 含外部环境/感官意象(月/风/雨/雪/山/水/烟/灯/钟/铃/鸟/虫等)
       且**前 60 字内不出现情绪命名**(愤怒/喜悦/恐惧/紧张/心一沉/心中一凛)
     - bare_opener: 既无外部意象也无情绪命名,直接动作/对话
     - tagged_opener: 前 60 字出现情绪命名词(『他很愤怒』『心中一凛』)
  3. 计算 xing_ratio = xing_ok / 总场景数
  4. 门控:作者档/genre pack `scene_opener_profile.xing_ratio` baseline
     - 现代都市 / 职场 / slice_of_life / workplace_drama 题材天然低 xing → skip
     - 其他题材 actual_xing_ratio < baseline - 0.2 → advisory

【北极星② / ⑤ 顾问非法官】题材声明由作者档决定 · xing 是中文叙事工艺 advisory ·
  code SCENE_OPENER_XING_THIN 绝不进 audit_hub.HARD_GATE_CODES。
  env SCENE_OPENER_XING_MODE: off / shadow(默认) / active。

用法: python scene_opener_xing_check.py <draft_path> [--project <root>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ISSUE_CODE = "SCENE_OPENER_XING_THIN"

# 外部环境/自然/感官意象词(起兴典型素材)
IMAGERY_WORDS = re.compile(
    r"(月|月色|月光|星|星辰|夜空|天色|天光|晨光|霞光|霞色|"
    r"风|微风|清风|寒风|秋风|春风|"
    r"雨|细雨|夜雨|烟雨|"
    r"雪|落雪|飞雪|风雪|"
    r"山|青山|远山|山影|山色|"
    r"水|江水|河水|溪水|溪流|"
    r"云|乌云|流云|云雾|薄雾|"
    r"灯|灯火|油灯|烛火|烛光|"
    r"钟|钟声|铃|铃声|"
    r"鸟|燕|雀|鸦|蝉|虫|草|花|柳|松|竹|梅|"
    r"街|长街|青石|石板|长廊|庭院|院落|窗|帘)"
)
# 情绪命名词(tagged_opener 判定)
EMOTION_TAG = re.compile(
    r"(他|她|我|主角|林[^字]?|张[^字]?|王[^字]?|李[^字]?)?\s*"
    r"(很|非常|十分|无比|极为)?"
    r"(愤怒|生气|恼怒|怒火|喜悦|高兴|开心|快乐|兴奋|"
    r"恐惧|害怕|惊恐|惊吓|惊慌|紧张|忐忑|不安|"
    r"悲伤|哀伤|难过|沮丧|绝望|"
    r"心中一凛|心一沉|心头一震|眼中闪过一丝|脸色一沉|顿时|"
    r"心情很|情绪很|感到[^,，。]{1,6}(地|的)?)"
)
# 场景切换标志
SCENE_BREAKS = re.compile(
    r"(另一边|与此同时|同一时间|片刻后|不多时|片刻之后|半晌后|"
    r"次日|翌日|第二天|清晨|傍晚|入夜|深夜|当晚|当夜|"
    r"三日后|数日后|一日后|一晃|许久之后|多年后)"
)

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
# 通用阈值兜底(作者档无 baseline 时)·武侠/仙侠/玄幻历史等典型 0.5-0.7
DEFAULT_BASELINE = 0.4
# 现代都市/职场天然低 xing(豁免)
LOW_XING_GENRES = {"urban_supernatural", "scifi_meta", "slice_of_life",
                   "workplace_drama", "scheming_politics", "heist_caper",
                   "espionage", "regression"}


def _mode() -> str:
    m = (os.environ.get("SCENE_OPENER_XING_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


from text_metrics import count_cjk as _cjk_count  # noqa: E402 字数口径单一真理源


def _resolve_profile(project_root):
    """读 scene_opener_profile + genre。返回 (baseline_or_None, genre_or_None)。"""
    if not project_root:
        return None, None
    db = Path(project_root) / "_数据库"
    baseline = None
    genre = None
    for path in [db / "作者风格.json", db / "用户偏好.json"]:
        if not path.exists():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        prof = obj.get("scene_opener_profile")
        if isinstance(prof, dict):
            r = prof.get("xing_ratio")
            if isinstance(r, (int, float)) and baseline is None:
                baseline = float(r)
        if genre is None:
            v = obj.get("genre_tags") or obj.get("genre")
            if isinstance(v, list) and v:
                genre = str(v[0]).strip().lower()
            elif isinstance(v, str) and v.strip():
                genre = v.strip().lower()
    return baseline, genre


def split_scenes(text: str) -> list:
    """按段落空行 + 场景切换标志切场景。返回每场景的前 150 字 (用于头部判定)。"""
    # 先按双换行切大段
    blocks = re.split(r"\n\s*\n", text)
    scenes = []
    for blk in blocks:
        blk = blk.strip()
        if not blk:
            continue
        # 块内查场景切换关键词,如果出现在前 80 字之外则进一步拆
        m = SCENE_BREAKS.search(blk)
        if m and m.start() > 80:
            scenes.append(blk[:m.start()].strip())
            scenes.append(blk[m.start():].strip())
        else:
            scenes.append(blk)
    return [s for s in scenes if _cjk_count(s) >= 30]


def classify_opener(scene_head: str) -> str:
    """三档判定 xing_ok / bare_opener / tagged_opener。"""
    head60 = scene_head[:60]
    head150 = scene_head[:150]
    if EMOTION_TAG.search(head60):
        return "tagged_opener"
    # 前 60-150 字含意象 → xing_ok
    if IMAGERY_WORDS.search(head150):
        return "xing_ok"
    return "bare_opener"


def scan(draft_path, project_root=None) -> dict:
    mode = _mode()
    out = {"scanner": "scene_opener_xing", "schema_version": "1.0", "mode": mode,
           "code": ISSUE_CODE, "gate_level": "advisory", "warning": None,
           "violations": [], "verdict": "PASS"}
    if mode == "off":
        return out
    try:
        draft = Path(draft_path).read_text(encoding="utf-8")
    except OSError as e:
        out["note"] = f"草稿读取失败:{str(e)[:120]}"
        return out
    draft = _strip_changes(draft)
    cjk = _cjk_count(draft)
    if cjk < 500:
        out["note"] = "草稿太短·跳过"
        return out

    baseline, genre = _resolve_profile(project_root)
    out["baseline"] = baseline
    out["genre"] = genre

    # 现代都市/职场题材天然低 xing → 若 baseline 未给则 skip
    if genre in LOW_XING_GENRES and baseline is None:
        out["note"] = f"现代/职场类题材(genre={genre})天然低 xing·skip"
        return out

    scenes = split_scenes(draft)
    out["scene_count"] = len(scenes)
    if len(scenes) < 2:
        out["note"] = "场景数 <2·跳过(单场景 cluster 不评 xing 工艺)"
        return out

    classes = [classify_opener(s) for s in scenes]
    xing_ok = classes.count("xing_ok")
    bare = classes.count("bare_opener")
    tagged = classes.count("tagged_opener")
    ratio = round(xing_ok / len(scenes), 3)
    out["xing_ok"] = xing_ok
    out["bare_opener"] = bare
    out["tagged_opener"] = tagged
    out["xing_ratio_actual"] = ratio
    out["classes"] = classes

    effective_baseline = baseline if baseline is not None else DEFAULT_BASELINE
    out["effective_baseline"] = effective_baseline

    msg = None
    if ratio < effective_baseline - 0.2:
        msg = (f"起兴 scene-opener 偏薄: xing_ratio={ratio} < baseline {effective_baseline} - 0.2 "
               f"(共 {len(scenes)} 场景·xing_ok {xing_ok} / bare {bare} / tagged {tagged})·"
               f"建议新场景前 60-150 字加外部环境意象再过渡到情绪/动作")
    if msg:
        if mode == "active":
            out["violations"].append({
                "kind": "scene_opener_xing", "severity": "minor",
                "message": msg,
                "xing_ratio_actual": ratio, "effective_baseline": effective_baseline,
                "scene_count": len(scenes),
                "_doc": "中文叙事场景开篇起兴工艺·作者档第一权威·advisory 可豁免·绝不 hard_gate"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] scene_opener_xing: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="起兴 scene-opener 检测(advisory)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None, help="读 scene_opener_profile + genre")
    ap.add_argument("--manifest", default=None, help="兼容 audit_hub 传参")
    ap.add_argument("--style", default=None, help="兼容 audit_hub 传参")
    args, _ = ap.parse_known_args()
    report = scan(args.draft_path, args.project)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
