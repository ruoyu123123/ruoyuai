#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-04 Wave-6 统计族阈值基线 harness（第二套方法论 · 非 embedding 族）
"""statistical_threshold_baseline.py — 统计族（关键词密度/比例类）阈值的作者语料分位数基线。

【背景】threshold_registry.json 的 37 条 other 族是**正则命中密度(per_1k)/占比(ratio)**类
统计阈值。历史校准是一次性手工测量（散落在各 scanner 注释里的「5/6 真作者实测 X-Y」），
本 harness 把它变成**可复跑的程序**：对真作者语料逐章调用各 scanner 自己的 scan() 入口
（**不复制任何指标逻辑**——strip_changes/CJK 计数/正则/守卫全部走 scanner 原代码），
汇总每书 + 三书合并分位数带，对照现行阈值给建议。

【每章两层测量】
  metric  — scan() 输出的核心指标值（分位数分布用·仅统计 eligible 章：过了该 scanner
            自身样本量守卫、指标非 None 的章——防非群戏小对话 ratio=1.0 之类污染 p95）
  flagged — scan() 在 active mode 下是否产 warning（= 现行阈值 + 全部守卫的**生产口径**
            误伤率；>30% 真作者章被判 = 过严，<1% = 过松/无信息量·任务判据）

【北极星⑤】产出是「作者基线参考」advisory 数据，不改任何 scanner/阈值/registry。
这批 scanner 是单向 anti-pattern 哨兵（设计意图=真作者零触发·宁可漏报），「过松/无信息量」
verdict 应读作「对真作者语料无区分度=安全余量充足」，p90/p95/max 是想收紧时的候选参考。

（分位数函数与 semantic_threshold_calibrator.percentile 同算法但独立实现：import 那边会
拖进 embedding_store/nn_daemon_client 导入链，本 harness 必须纯 stdlib 零模型依赖。）

用法：
  py core/ml/calibration/statistical_threshold_baseline.py \\
      --books 主神大道,诡秘之主,轮回乐园 --chapters 60 --seed 20260704
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_SCRIPTS = _REPO_ROOT / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_REGISTRY_PATH = _HERE / "threshold_registry.json"
CHAPTER_NUM_RE = re.compile(r"第0*(\d+)章")

# ════════════════ 受测 scanner 适配表（首批 5 + 1 附赠·全部 relation_family=other） ════════════════
# 选择标准：registry note 带「真作者实测」历史数据（一次性手工测量→本 harness 变可复跑）；
# scan(draft_path) 只吃纯文本文件、无 manifest/数据库上下文依赖。
# eligible_guard=(scan输出key, scanner常量名)：指标值需 out[key] >= getattr(mod, 常量) 才计入分布。
SCANNERS: dict = {
    "dramatic_irony": dict(
        module="dramatic_irony_scanner", constant="IRONY_TELL_FLOOR",
        mode_env="DRAMATIC_IRONY_MODE", metric_key="per_1k",
        registry_id="dramatic_irony_scanner.py::IRONY_TELL_FLOOR",
        desc="dramatic irony 显式标志词密度/千字（殊不知/浑然不知等·tell 过多）"),
    "group_dialogue_balance": dict(
        module="group_dialogue_balance_scanner", constant="RATIO_FLOOR",
        mode_env="GROUP_DIALOGUE_BALANCE_MODE", metric_key="explicit_name_attrib_ratio",
        registry_id="group_dialogue_balance_scanner.py::RATIO_FLOOR",
        eligible_guard=("dialogue_lines", "MIN_DIALOGUE_LINES"),
        desc="群戏显式点名归属占比（专名+说/道 行 / 对话行·仅群戏规模章计入分布）"),
    "physio_cue_diversity": dict(
        module="physio_cue_diversity_scanner", constant="FACIAL_RATIO_FLOOR",
        mode_env="PHYSIO_CUE_DIVERSITY_MODE", metric_key="facial_ratio",
        registry_id="physio_cue_diversity_scanner.py::FACIAL_RATIO_FLOOR",
        desc="生理情绪线索面部占比 facial/(facial+nonfacial)（样本<8 的章指标为 None 不计入）"),
    "interiority_mode_balance": dict(
        module="interiority_mode_balance_scanner", constant="MARKED_MONOLOGUE_FLOOR",
        mode_env="INTERIORITY_MODE_BALANCE_MODE", metric_key="marked_monologue_per_1k",
        registry_id="interiority_mode_balance_scanner.py::MARKED_MONOLOGUE_FLOOR",
        desc="带标记直接独白密度/千字（他想/心中暗道等·三态坍缩检测）"),
    "synesthesia_density": dict(
        module="synesthesia_density_scanner", constant="SYN_PER_1K_FLOOR",
        mode_env="SYNESTHESIA_MODE", metric_key="per_1k",
        registry_id="synesthesia_density_scanner.py::SYN_PER_1K_FLOOR",
        desc="明显跨感官通感短语密度/千字（响亮的光/冰冷的声音等）"),
    # 附赠第 6 个：registry note 记录两批历史实测口径分歧（0.06 vs 1.14/千字·差 19 倍），
    # 本次可复跑测量直接仲裁哪个数字是当前代码口径的真值。
    "subtext_rescan": dict(
        module="subtext_rescan_scanner", constant="ON_THE_NOSE_PER_1K_FLOOR",
        mode_env="SUBTEXT_RESCAN_MODE", metric_key="on_the_nose_per_1k",
        registry_id="subtext_rescan_scanner.py::ON_THE_NOSE_PER_1K_FLOOR",
        desc="on-the-nose 情绪直陈密度/千字（感到+愤怒 类引导词紧邻情绪名词）"),
}

# 任务判据：现行阈值把 >30% 真作者章判违规=过严；<1%=过松/无信息量（对单向哨兵=安全余量足）
OVERSTRICT_FLAG_RATE = 0.30
UNINFORMATIVE_FLAG_RATE = 0.01


# ════════════════ 语料采样（确定性·per-book 独立 rng 流） ════════════════

def list_chapter_files(book_dir: Path) -> list:
    """原文目录下「第N章.txt」按章号升序。目录不存在→诚实 FileNotFoundError。"""
    if not book_dir.is_dir():
        raise FileNotFoundError(f"[baseline] 原文目录不存在：{book_dir}")
    files = []
    for fp in book_dir.glob("*.txt"):
        m = CHAPTER_NUM_RE.search(fp.stem)
        if m:
            files.append((int(m.group(1)), fp))
    files.sort(key=lambda t: t[0])
    return files


def sample_chapters(book_dir: Path, n: int, seed: int, book_name: str) -> list:
    """每书随机抽 n 章（章号升序返回）。rng 用 f\"{seed}:{book_name}\" 派生——
    str seed 经 CPython sha512 处理跨平台确定，且加/删别的书不影响本书采样。"""
    files = list_chapter_files(book_dir)
    if not files:
        raise FileNotFoundError(f"[baseline] 《{book_name}》原文目录无「第N章.txt」可采样：{book_dir}")
    if len(files) <= n:
        return files
    rng = random.Random(f"{seed}:{book_name}")
    picked = rng.sample(files, n)
    return sorted(picked, key=lambda t: t[0])


# ════════════════ 分位数统计（纯 stdlib·线性插值·与姊妹 harness 同算法） ════════════════

def percentile(xs: list, p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    if n == 1:
        return s[0]
    pos = p * (n - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def distribution_stats(xs: list) -> dict:
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 4) if xs else 0.0,
        "p5": round(percentile(xs, 0.05), 4), "p25": round(percentile(xs, 0.25), 4),
        "p50": round(percentile(xs, 0.50), 4), "p75": round(percentile(xs, 0.75), 4),
        "p90": round(percentile(xs, 0.90), 4), "p95": round(percentile(xs, 0.95), 4),
        "max": round(max(xs), 4) if xs else 0.0,
    }


def ecdf_at(xs: list, v: float) -> float:
    """经验 CDF：作者分布里 <= v 的占比（现行阈值落在哪个分位）。"""
    if not xs:
        return 0.0
    return round(sum(1 for x in xs if x <= v) / len(xs), 4)


# ════════════════ 指标抽取（复用 scanner 自身 scan() 入口·零逻辑复制） ════════════════

@contextlib.contextmanager
def _force_active_modes(cfgs: list):
    """临时把受测 scanner 的 mode env 全设 active：①保证 off/shadow 默认下指标也被计算；
    ②warning 判定 = 生产 active 行为（含全部样本量守卫）。退出时恢复原 env（防测试污染）。"""
    saved = {}
    for cfg in cfgs:
        env = cfg["mode_env"]
        saved[env] = os.environ.get(env)
        os.environ[env] = "active"
    try:
        yield
    finally:
        for env, old in saved.items():
            if old is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = old


def _load_modules(cfgs: list) -> dict:
    return {name: importlib.import_module(cfg["module"])
            for name, cfg in cfgs_items(cfgs)}


def cfgs_items(cfgs: list):
    for cfg in cfgs:
        yield cfg["name"], cfg


def make_real_extract_fn(modules: dict):
    """真指标抽取器：extract(cfg, chapter_path) -> {metric, flagged, eligible[, error]}。"""
    def _extract(cfg: dict, chapter_path: Path) -> dict:
        mod = modules[cfg["name"]]
        try:
            out = mod.scan(str(chapter_path))
        except UnicodeDecodeError as e:   # 个别坏编码章不拖垮整轮·记录后继续
            return {"metric": None, "flagged": False, "eligible": False, "error": str(e)[:80]}
        metric = out.get(cfg["metric_key"])
        if not isinstance(metric, (int, float)):
            metric = None
        eligible = metric is not None
        guard = cfg.get("eligible_guard")
        if eligible and guard:
            key, const_name = guard
            eligible = out.get(key, 0) >= getattr(mod, const_name)
        return {"metric": metric, "flagged": bool(out.get("warning")), "eligible": eligible}
    return _extract


# ════════════════ 聚合 + 建议 ════════════════

def aggregate_rows(rows: list) -> dict:
    metrics = [r["metric"] for r in rows if r["eligible"] and r["metric"] is not None]
    return {
        "chapters_scanned": len(rows),
        "chapters_eligible": len(metrics),
        "chapters_flagged": sum(1 for r in rows if r["flagged"]),
        "errors": sum(1 for r in rows if r.get("error")),
        "dist": distribution_stats(metrics),
        "_metrics": metrics,   # 内部字段·merged 汇总用·出报告前剥离
    }


def build_advice(cfg: dict, floor: float, merged: dict) -> dict:
    metrics = merged["_metrics"]
    scanned, flagged = merged["chapters_scanned"], merged["chapters_flagged"]
    eligible = merged["chapters_eligible"]
    flag_rate = round(flagged / scanned, 4) if scanned else 0.0
    flag_rate_eligible = round(flagged / eligible, 4) if eligible else 0.0
    floor_pct = ecdf_at(metrics, floor)
    d = merged["dist"]
    if flag_rate > OVERSTRICT_FLAG_RATE:
        verdict = "过严"
        text = (f"现行 {cfg['constant']}={floor} 会把 {flag_rate:.1%} 真作者章判违规（>30%）——"
                f"建议抬到作者分布 p95={d['p95']} 以上（max={d['max']}）再留余量。")
    elif flag_rate < UNINFORMATIVE_FLAG_RATE:
        verdict = "过松/无信息量"
        text = (f"现行 {cfg['constant']}={floor} 落在作者分布 p{floor_pct * 100:.0f}"
                f"（真作者章误伤率 {flag_rate:.1%}<1%）。对单向 anti-pattern 哨兵这是设计预期"
                f"（真作者零触发=安全）；若想收紧到贴作者分布上沿，候选值 p90={d['p90']} / "
                f"p95={d['p95']}（观测 max={d['max']}）。")
    else:
        verdict = "适中"
        text = (f"现行 {cfg['constant']}={floor} 误伤率 {flag_rate:.1%}（1%-30% 之间），"
                f"落在作者分布 p{floor_pct * 100:.0f}；参考带 p90={d['p90']} p95={d['p95']} "
                f"max={d['max']}。")
    return {
        "verdict": verdict,
        "flag_rate_scanned": flag_rate,
        "flag_rate_eligible": flag_rate_eligible,
        "floor_percentile_in_author_dist": floor_pct,
        "candidate_p90": d["p90"], "candidate_p95": d["p95"], "observed_max": d["max"],
        "text": text,
    }


def _registry_entry(registry: dict, registry_id: str):
    for e in registry.get("entries", []):
        if e.get("id") == registry_id:
            return e
    return None


# ════════════════ 报告 ════════════════

def build_report(cfgs: list, results: dict, floors: dict, registry: dict, meta: dict) -> dict:
    scanners_out = {}
    for name, cfg in cfgs_items(cfgs):
        per_book, merged = results[name]
        entry = _registry_entry(registry, cfg["registry_id"])
        reg_value = entry.get("current_value") if entry else None
        floor = floors[name]
        node = {
            "module": cfg["module"], "constant": cfg["constant"],
            "metric_key": cfg["metric_key"], "desc": cfg["desc"],
            "registry_id": cfg["registry_id"],
            "current_floor_runtime": floor,
            "registry_current_value": reg_value,
            # entry is None = 该 scanner 已金标准校准并从注册表 drop（注册表只追踪"待校准"·
            # 无记录则无从漂移·视为一致）；有 entry 才校验 current_value == 运行时（防文档漂移）。
            "registry_runtime_consistent": (entry is None) or (reg_value == floor),
            "registry_note": (entry or {}).get("note"),
            "per_book": {b: {k: v for k, v in agg.items() if k != "_metrics"}
                         for b, agg in per_book.items()},
            "merged": {k: v for k, v in merged.items() if k != "_metrics"},
            "advice": build_advice(cfg, floor, merged),
        }
        scanners_out[name] = node
    return {"meta": meta, "scanners": scanners_out}


def render_markdown(report: dict) -> str:
    m = report["meta"]
    lines = [
        "# 统计族阈值作者语料分位数基线报告（Wave-6 · other 族）", "",
        f"- 生成时间：{m['generated_at']}",
        f"- 书目：{', '.join(m['books'])} · 每书采样 {m['chapters_per_book']} 章 · seed={m['seed']}",
        f"- 方法：逐章调用各 scanner 自身 scan()（active mode·含原生守卫），"
        f"metric 进分位数分布（仅 eligible 章）、warning 进生产口径误伤率",
        f"- 判据：误伤率 >30%=过严 · <1%=过松/无信息量（单向哨兵设计预期=安全）· 其间=适中", "",
    ]
    for name, s in report["scanners"].items():
        a = s["advice"]
        lines += [
            f"## {name}（`{s['constant']}` = {s['current_floor_runtime']}）", "",
            s["desc"], "",
            f"| 语料 | 章 | eligible | p5 | p25 | p50 | p75 | p90 | p95 | max | mean | flagged |",
            f"|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for book, agg in s["per_book"].items():
            d = agg["dist"]
            lines.append(
                f"| {book} | {agg['chapters_scanned']} | {agg['chapters_eligible']} | {d['p5']} | "
                f"{d['p25']} | {d['p50']} | {d['p75']} | {d['p90']} | {d['p95']} | {d['max']} | "
                f"{d['mean']} | {agg['chapters_flagged']} |")
        mg, dm = s["merged"], s["merged"]["dist"]
        lines.append(
            f"| **三书合并** | {mg['chapters_scanned']} | {mg['chapters_eligible']} | {dm['p5']} | "
            f"{dm['p25']} | {dm['p50']} | {dm['p75']} | {dm['p90']} | {dm['p95']} | {dm['max']} | "
            f"{dm['mean']} | {mg['chapters_flagged']} |")
        lines += [
            "",
            f"- 现行阈值 vs 作者分布：floor={s['current_floor_runtime']} 落在合并分布 "
            f"p{a['floor_percentile_in_author_dist'] * 100:.0f} · 生产口径误伤率 "
            f"{a['flag_rate_scanned']:.2%}（eligible 口径 {a['flag_rate_eligible']:.2%}）",
            f"- **判定：{a['verdict']}** — {a['text']}",
        ]
        if s.get("registry_note"):
            lines.append(f"- registry 历史注记：{s['registry_note']}")
        if not s["registry_runtime_consistent"]:
            lines.append(f"- ⚠️ registry current_value={s['registry_current_value']} 与运行时常量"
                         f"={s['current_floor_runtime']} 不一致，registry 可能过时")
        lines.append("")
    return "\n".join(lines)


# ════════════════ 编排 ════════════════

def run_baseline(books: list, chapters_per_book: int, seed: int, styles_root: Path,
                 out_dir: Path, scanner_names=None, extract_fn=None,
                 registry_path: Path = None) -> dict:
    """可测试入口：extract_fn=None → import 真 scanner 模块并用其 scan()；
    传入 mock extract_fn(cfg, path)->{metric,flagged,eligible} → 零 core/scripts 接触。"""
    names = list(scanner_names) if scanner_names else list(SCANNERS)
    unknown = [n for n in names if n not in SCANNERS]
    if unknown:
        raise KeyError(f"[baseline] 未注册的 scanner：{unknown}（可选：{list(SCANNERS)}）")
    cfgs = [dict(SCANNERS[n], name=n) for n in names]

    reg_p = registry_path or _REGISTRY_PATH
    registry = json.loads(reg_p.read_text(encoding="utf-8")) if reg_p.exists() else {}

    if extract_fn is None:
        modules = _load_modules(cfgs)
        extract_fn = make_real_extract_fn(modules)
        floors = {name: getattr(modules[name], cfg["constant"]) for name, cfg in cfgs_items(cfgs)}
    else:   # mock 路径：阈值取 registry 值·不 import scanner
        floors = {name: (_registry_entry(registry, cfg["registry_id"]) or {}).get("current_value")
                  for name, cfg in cfgs_items(cfgs)}

    sampled = {b: sample_chapters(styles_root / b / "原文", chapters_per_book, seed, b)
               for b in books}

    results = {}
    with _force_active_modes(cfgs):
        for name, cfg in cfgs_items(cfgs):
            per_book = {}
            all_rows = []
            for book in books:
                rows = [extract_fn(cfg, fp) for _, fp in sampled[book]]
                per_book[book] = aggregate_rows(rows)
                all_rows.extend(rows)
            results[name] = (per_book, aggregate_rows(all_rows))

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books": books,
        "chapters_per_book": chapters_per_book,
        "chapters_sampled": {b: [num for num, _ in sampled[b]] for b in books},
        "seed": seed,
        "scanners": names,
        "registry_path": str(reg_p),
        "extractor": "real_scanner_scan" if extract_fn.__qualname__.startswith(
            "make_real_extract_fn") else "injected_mock",
    }
    report = build_report(cfgs, results, floors, registry, meta)

    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"statistical_baseline_{date_str}.json"
    md_path = out_dir / f"statistical_baseline_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    report["meta"]["output_json"] = str(json_path)
    report["meta"]["output_md"] = str(md_path)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="统计族阈值作者语料分位数基线 harness（Wave-6·other 族）")
    ap.add_argument("--books", required=True, help="逗号分隔书名（对应 workspace/styles/<书名>/原文）")
    ap.add_argument("--chapters", type=int, default=60, help="每书随机采样章数（默认 60）")
    ap.add_argument("--seed", type=int, default=20260704)
    ap.add_argument("--styles-root", default=None, help="默认 workspace/styles")
    ap.add_argument("--output-dir", default=None, help="默认 core/ml/calibration/reports")
    ap.add_argument("--scanners", default=None, help=f"逗号分隔子集·默认全部：{','.join(SCANNERS)}")
    args = ap.parse_args()

    books = [b.strip() for b in args.books.split(",") if b.strip()]
    names = [s.strip() for s in args.scanners.split(",") if s.strip()] if args.scanners else None
    styles_root = Path(args.styles_root) if args.styles_root else _REPO_ROOT / "workspace" / "styles"
    out_dir = Path(args.output_dir) if args.output_dir else _HERE / "reports"

    report = run_baseline(books, args.chapters, args.seed, styles_root, out_dir,
                          scanner_names=names)
    print(f"[baseline] json → {report['meta']['output_json']}")
    print(f"[baseline] md   → {report['meta']['output_md']}")
    for name, s in report["scanners"].items():
        a = s["advice"]
        print(f"[baseline] {name}: floor={s['current_floor_runtime']} "
              f"p50={s['merged']['dist']['p50']} p95={s['merged']['dist']['p95']} "
              f"max={s['merged']['dist']['max']} 误伤率={a['flag_rate_scanned']:.2%} → {a['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
