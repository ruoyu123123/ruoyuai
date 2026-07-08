#!/usr/bin/env python3
"""reference_pattern_extract.py — 参考语料结构模式抽取（P3·2026-07-07）

借鉴 Ex3-NovelWriter 的 Extracting 阶段思想（research/open_source_writing_systems.md：
"conditional required substep when reference material exists: entity/pacing/pattern
extraction · no raw copyrighted text copied into prompts"）。Ex3 用微调模型抽取，
本实现**确定性零 LLM**——只借「从参考小说抽结构」的思想，用纯统计/正则落地。

输入（与蒸馏链同一路径口径·distill-style plan 的 原文/第N章.txt 惯例）：
    workspace/styles/<风格名>/原文/*.txt
    风格名解析：项目 _数据库/.wal/style_choice.json(answer.name) → 兜底
    _数据库/作者风格.json(source)。风格库路径复用 init_project._styles_dir 唯一口径。

抽取维度（纯统计·全部逐章后聚合）：
    1. 章均 CJK 分布（mean/std/分位数）
    2. 对话占比曲线（style_analyzer.calc_dialogue_ratio 逐章 + 前中后三段）
    3. 场景切换密度（分隔线 + 转场标志词 / 千字）
    4. 冲突节奏（冲突标志词 / 千字·章序列曲线）
    5. 实体引入速率（新专名首现/章·style_analyzer._build_name_registry 人名启发式）
    6. 卷级 pacing 形状（前/中/后三段各指标均值）
    另：作者风格.json 已有 quantitative 量化指纹 → **复用不重算**（仅拷数值·丢弃一切字符串）。

输出 artifact：workspace/styles/<风格名>/genre_storyline_patterns.json
    🔴 版权纪律：只含数字和短标签（每个字符串值 <50 字符）+ source_ids（章文件名）+
    每维 provenance——**绝不包含任何原文句子**。

条件性：风格库不存在 / 无 原文/*.txt → 优雅 skip（exit 0·不产物）。幂等：语料签名
（文件名+字节数）未变且 artifact 已存在 → 直接复用不重算（--force 强制重算）。

消费端：gen_creative_volume_arc.build_volume_arc_skeleton_prompt 在 artifact 存在时
注入「参考作品结构基线（advisory·可偏离）」——北极星⑤：数字化结构参照非硬约束，
大势卡内容仍由模型按灵感卡自由创作。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_analyzer as sa  # noqa: E402  复用 count_chinese/calc_dialogue_ratio/人名启发式/统计工具
from init_project import _styles_dir  # noqa: E402  风格库路径唯一口径（workspace/styles）

ARTIFACT_NAME = "genre_storyline_patterns.json"
CORPUS_DIR_NAME = "原文"
CURVE_MAX_POINTS = 48   # 曲线降采样上限（长书 1000 章也只落 ≤48 个桶均值）

# 章号提取（第001章.txt / 001.txt / chapter_12.txt 均可·排序锚）
_CH_NO_RE = re.compile(r"(\d+)")

# 分隔线独立行（场景硬切标记：※ / *** / ——— / === 等·只由符号构成的行）
_SEP_LINE_RE = re.compile(r"^\s*[※×÷#*=—·…、.~_-]{3,}\s*$", re.MULTILINE)

# 转场标志词（时间跳跃/视角切换的高频标记·检测用途非生成禁用词口径）
_SCENE_SHIFT_MARKERS = (
    "翌日", "次日", "第二天", "三天后", "数日后", "几天后", "半个月后", "一个月后",
    "与此同时", "同一时刻", "同一时间", "另一边", "另一头", "另一处",
    "片刻后", "半晌后", "一炷香后", "入夜", "天亮", "黎明", "黄昏", "傍晚",
    "清晨", "当天夜里", "翌晨", "转眼", "回到",
)

# 冲突标志词（单字为主避免互相包含双计·2 字词均不含表内单字）
_CONFLICT_MARKERS = (
    "杀", "血", "死", "吼", "怒", "撞", "爆", "惨", "刀", "剑", "拳", "轰",
    "嘶", "逃", "追", "砸", "撕", "斩", "劈", "咬", "搏", "袭",
    "咆哮", "尖叫", "断裂", "碎裂", "威胁", "危险",
)


# ============ 路径解析（与蒸馏/init_project 同口径）============
def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def resolve_style_name(project_root: Path) -> str:
    """项目选定的风格库名：.wal/style_choice.json(answer.name) → 作者风格.json(source)。"""
    db = project_root / "_数据库"
    choice = _read_json(db / ".wal" / "style_choice.json")
    if isinstance(choice, dict):
        name = (choice.get("answer") or {}).get("name") if isinstance(choice.get("answer"), dict) else None
        if name:
            return str(name)
    prof = _read_json(db / "作者风格.json")
    if isinstance(prof, dict) and prof.get("source"):
        return str(prof["source"])
    return ""


def resolve_style_dir(project_root: Path) -> Path | None:
    """选定风格库目录（不存在 → None）。路径口径 = init_project._styles_dir。"""
    name = resolve_style_name(project_root)
    if not name:
        return None
    cand = _styles_dir(project_root) / name
    return cand if cand.is_dir() else None


def list_corpus_files(style_dir: Path) -> list[Path]:
    """风格库参考原文章节（原文/*.txt·按章号数值排序·同 distill 链目录口径）。"""
    corpus_dir = style_dir / CORPUS_DIR_NAME
    if not corpus_dir.is_dir():
        return []

    def _key(p: Path):
        m = _CH_NO_RE.search(p.stem)
        return (int(m.group(1)) if m else 10 ** 9, p.name)

    return sorted((p for p in corpus_dir.glob("*.txt") if p.is_file()), key=_key)


def corpus_signature(files: list[Path]) -> str:
    """语料签名（文件名+字节数·幂等复用锚·不读全文所以极快）。"""
    h = hashlib.sha1()
    for f in files:
        try:
            h.update(f"{f.name}:{f.stat().st_size}\n".encode("utf-8"))
        except OSError:
            h.update(f"{f.name}:?\n".encode("utf-8"))
    return h.hexdigest()


def locate_patterns_artifact(project_root: Path) -> Path | None:
    """已存在的 genre_storyline_patterns.json（消费端定位入口·缺 → None）。"""
    sd = resolve_style_dir(Path(project_root))
    if sd is None:
        return None
    art = sd / ARTIFACT_NAME
    return art if art.is_file() else None


# ============ 纯统计抽取 ============
def _count_markers(text: str, markers: tuple) -> int:
    return sum(text.count(m) for m in markers)


def _chapter_metrics(text: str, seen_names: set) -> dict | None:
    """单章确定性指标（cjk==0 → None 跳过）。seen_names 被 in-place 累积（首现检测）。"""
    cjk = sa.count_chinese(text)
    if cjk <= 0:
        return None
    per_1k = 1000.0 / cjk
    shift_hits = len(_SEP_LINE_RE.findall(text)) + _count_markers(text, _SCENE_SHIFT_MARKERS)
    conflict_hits = _count_markers(text, _CONFLICT_MARKERS)
    names = sa._build_name_registry(text)
    new_names = {n for n in names if n not in seen_names}
    seen_names.update(new_names)
    return {
        "cjk": cjk,
        "dialogue_ratio": round(sa.calc_dialogue_ratio(text), 4),
        "scene_shift_per_1k": round(shift_hits * per_1k, 4),
        "conflict_per_1k": round(conflict_hits * per_1k, 4),
        "new_entities": len(new_names),   # 只记数量·绝不落人名字符串（版权纪律）
    }


def _front_mid_back(vals: list) -> list:
    """前/中/后三段均值（卷级 pacing 形状·n<3 时兜底非空段）。"""
    n = len(vals)
    if n == 0:
        return [0.0, 0.0, 0.0]
    front = vals[: n // 3] or vals[:1]
    mid = vals[n // 3: 2 * n // 3] or vals[:1]
    back = vals[2 * n // 3:] or vals[-1:]
    return [round(sum(seg) / len(seg), 4) for seg in (front, mid, back)]


def _downsample(vals: list, max_points: int = CURVE_MAX_POINTS) -> list:
    """章序列曲线降采样成 ≤max_points 个桶均值（确定性）。"""
    n = len(vals)
    if n <= max_points:
        return [round(float(v), 4) for v in vals]
    out = []
    for b in range(max_points):
        lo = b * n // max_points
        hi = max((b + 1) * n // max_points, lo + 1)
        seg = vals[lo:hi]
        out.append(round(sum(seg) / len(seg), 4))
    return out


def _numeric_only(obj):
    """递归剥掉一切字符串值·只留数值（复用作者档量化指纹时的版权/泄漏守卫）。
    bool 也丢（JSON true/false 非结构基线数值）；_ 前缀键（_doc 等）整体丢。"""
    if isinstance(obj, bool):
        return None
    if isinstance(obj, (int, float)):
        return round(float(obj), 4)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).startswith("_"):
                continue
            fv = _numeric_only(v)
            if fv is not None and fv != {} and fv != []:
                out[k] = fv
        return out or None
    if isinstance(obj, list):
        vals = [_numeric_only(v) for v in obj]
        vals = [v for v in vals if v is not None and v != {} and v != []]
        return vals or None
    return None   # str 及其他类型一律丢弃


def _dim(provenance: str, values: list, *, curve: bool = False, extra: dict | None = None) -> dict:
    """单维聚合（provenance 短标签 <50 字符·版权纪律）。"""
    d = {
        "provenance": provenance,
        "stats": sa.calc_stats([float(v) for v in values]),
        "front_mid_back": _front_mid_back([float(v) for v in values]),
    }
    q = sa.calc_quantiles([float(v) for v in values])
    if q:
        d["quantiles"] = {k: round(float(v), 4) for k, v in q.items()}
    if curve:
        d["curve"] = _downsample([float(v) for v in values])
    if extra:
        d.update(extra)
    return d


def extract_patterns(style_dir: Path, files: list[Path]) -> dict:
    """全语料 → genre_storyline_patterns dict（确定性·同语料同输出·不含任何原文句子）。"""
    per: list[dict] = []
    source_ids: list[str] = []
    seen_names: set = set()
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _chapter_metrics(text, seen_names)
        if m is None:
            continue
        per.append(m)
        source_ids.append(f.name)

    cjks = [m["cjk"] for m in per]
    dials = [m["dialogue_ratio"] for m in per]
    shifts = [m["scene_shift_per_1k"] for m in per]
    conflicts = [m["conflict_per_1k"] for m in per]
    ents = [m["new_entities"] for m in per]

    seg_names = ("front", "mid", "back")
    pacing_segments = {
        seg: {
            "chapter_cjk": _front_mid_back(cjks)[i],
            "dialogue_ratio": _front_mid_back(dials)[i],
            "scene_shift_per_1k": _front_mid_back(shifts)[i],
            "conflict_per_1k": _front_mid_back(conflicts)[i],
            "new_entities_per_chapter": _front_mid_back(ents)[i],
        }
        for i, seg in enumerate(seg_names)
    }

    data = {
        "_schema": "genre_storyline_patterns",
        "schema_version": "v1",
        "_doc": "参考作品结构基线·纯统计零LLM·advisory可偏离",
        "_copyright": "只含数字与短标签·绝不含原文句子",
        "source": {
            "style_name": style_dir.name,
            "corpus_dir": CORPUS_DIR_NAME,
            "chapter_count": len(per),
            "total_cjk": sum(cjks),
            "corpus_signature": corpus_signature(files),
        },
        "source_ids": source_ids,
        "dimensions": {
            "chapter_cjk": _dim("逐章CJK计数(原文/*.txt)", cjks),
            "dialogue_ratio": _dim("calc_dialogue_ratio逐章·引号内字数占比", dials, curve=True),
            "scene_shift_per_1k": _dim("分隔线+转场标志词/千字·逐章", shifts),
            "conflict_per_1k": _dim("冲突标志词/千字·逐章", conflicts, curve=True),
            "new_entities_per_chapter": _dim(
                "新专名首现/章·人名启发式(只记数量)", ents,
                extra={"total_entities": len(seen_names)}),
            "volume_pacing_shape": {
                "provenance": "前/中/后三段各指标均值",
                "segments": pacing_segments,
            },
        },
    }

    # 作者档量化指纹复用（不重算·仅数值·丢一切字符串）
    prof = _read_json(style_dir / "作者风格.json") or _read_json(style_dir / "作者风格_FINAL.json")
    if isinstance(prof, dict) and isinstance(prof.get("quantitative"), dict):
        reused = _numeric_only(prof["quantitative"])
        if reused:
            data["author_profile_fingerprint"] = {
                "provenance": "作者风格.json quantitative复用·仅数值",
                "quantitative": reused,
            }
    return data


# ============ 消费端注入块（gen_creative_volume_arc 调用）============
def _fmt(v) -> str:
    return f"{float(v):g}"


def build_reference_patterns_block(project_root) -> str:
    """artifact → 骨架 prompt 注入文本（缺 artifact / 结构破损 → ""·调用方据此不注入）。
    全部为数字化描述——advisory 结构参照，绝不是硬约束（北极星⑤）。"""
    art = locate_patterns_artifact(Path(project_root))
    if art is None:
        return ""
    data = _read_json(art)
    if not isinstance(data, dict) or data.get("_schema") != "genre_storyline_patterns":
        return ""
    dims = data.get("dimensions") or {}
    src = data.get("source") or {}
    lines = [f"（统计自选定风格参考作品 {src.get('chapter_count', '?')} 章·纯数字结构指纹·无任何原文）"]

    def _q(dim: dict, key: str) -> str:
        q = dim.get("quantiles") or {}
        return _fmt(q[key]) if key in q else "?"

    cc = dims.get("chapter_cjk") or {}
    if cc.get("quantiles"):
        lines.append(f"- 章均CJK：p50={_q(cc, 'p50')}（p25-p75：{_q(cc, 'p25')}-{_q(cc, 'p75')}）")
    dr = dims.get("dialogue_ratio") or {}
    if dr.get("stats"):
        f, m, b = (dr.get("front_mid_back") or [0, 0, 0])[:3]
        lines.append(f"- 对话占比：均值 {_fmt(dr['stats'].get('mean', 0))}"
                     f"·前/中/后 {_fmt(f)}/{_fmt(m)}/{_fmt(b)}")
    ss = dims.get("scene_shift_per_1k") or {}
    if ss.get("stats"):
        lines.append(f"- 场景切换密度：{_fmt(ss['stats'].get('mean', 0))}/千字")
    cf = dims.get("conflict_per_1k") or {}
    if cf.get("stats"):
        f, m, b = (cf.get("front_mid_back") or [0, 0, 0])[:3]
        lines.append(f"- 冲突节奏：{_fmt(cf['stats'].get('mean', 0))}/千字"
                     f"·前/中/后 {_fmt(f)}/{_fmt(m)}/{_fmt(b)}")
    ne = dims.get("new_entities_per_chapter") or {}
    if ne.get("stats"):
        f, m, b = (ne.get("front_mid_back") or [0, 0, 0])[:3]
        lines.append(f"- 新专名引入速率：{_fmt(ne['stats'].get('mean', 0))}/章"
                     f"·前/中/后 {_fmt(f)}/{_fmt(m)}/{_fmt(b)}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ============ CLI ============
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="参考语料结构模式抽取（P3·确定性零LLM·条件产物）")
    ap.add_argument("project", help="小说项目根（workspace/novels/<书名>）")
    ap.add_argument("--force", action="store_true", help="忽略语料签名幂等缓存·强制重算")
    args = ap.parse_args(argv)

    project_root = Path(args.project)
    style_dir = resolve_style_dir(project_root)
    if style_dir is None:
        print("[reference_pattern_extract][SKIP] 项目未选定风格库或风格库目录不存在 → 条件不成立·跳过")
        return 0
    files = list_corpus_files(style_dir)
    if not files:
        print(f"[reference_pattern_extract][SKIP] 风格库无参考原文（{style_dir.name}/{CORPUS_DIR_NAME}/*.txt）→ 跳过")
        return 0

    artifact = style_dir / ARTIFACT_NAME
    sig = corpus_signature(files)
    if artifact.is_file() and not args.force:
        old = _read_json(artifact)
        if (isinstance(old, dict)
                and (old.get("source") or {}).get("corpus_signature") == sig):
            print(f"[reference_pattern_extract] 语料签名未变·复用已有 artifact: {artifact}")
            return 0

    data = extract_patterns(style_dir, files)
    artifact.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n = (data.get("source") or {}).get("chapter_count", 0)
    print(f"[reference_pattern_extract] {n} 章 → {artifact}（纯统计·只有数字和标签·无原文句子）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
