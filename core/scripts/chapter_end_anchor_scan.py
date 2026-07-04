#!/usr/bin/env python3
"""chapter_end_anchor_scan.py — L2 章末 cliffhanger 锚定扫描

防御目标（cluster_001 ch4 三次翻车 sediment）:
1. 章末是钩子（cliffhanger），不是收束（closure）
2. 章末 cliffhanger 必须锚到「已存在的下一段剧情」——禁止纯氛围装神弄鬼
3. 章末禁用任何过渡标记（剧本体 / 文学过渡分隔符 / 听觉视觉淡出 / 收束句）

权威清单：memory/feedback_no_screenplay_stage_directions_in_novels.md

检测逻辑:
- 取章末 5 段
- A. banned_patterns 扫（剧本体 + 章末过渡）→ hard_gate
- B. 锚定扫：章末 5 段提取实词关键词，在以下数据库 grep：
    · _数据库/事件簇.json 的 cluster_blueprint / clusters[i] scope_summary / foreshadowing_to_plant
    · _数据库/伏笔表.json 的 promises / secrets descriptions
    · _数据库/进度.json 的 cluster_blueprint 各 cluster
  0 命中 → advisory 「章末未锚定任何已存在剧情」
- C. POV scan：章末段是否切换到全知镜头（无具体人物 + 全是物件描述）→ advisory

退出码:
  0 = 全章末通过
  1 = 部分 advisory（可豁免）
  2 = 命中 hard_gate banned_patterns（必修）

用法:
  python chapter_end_anchor_scan.py <项目路径> --chapters 1-4 [--strict]
"""
import argparse
import json
import os  # 🔴 2026-06-27 P1-07: 读 CHAPTER_END_WEAK_ANCHOR_RATIO env
import re
import sys
from pathlib import Path

# 🔴 2026-06-27 P1-07: 弱锚阈值外提（让 PID 控制器 + audit_hub env 注入可调）。
# 默认 0.15（沿用历史值·零回归）·env / CLI 任一传值即覆盖（CLI 优先）。
_WEAK_ANCHOR_RATIO = float(os.environ.get("CHAPTER_END_WEAK_ANCHOR_RATIO", "0.15"))

# 2026-05-29 复审复修 SC-1：cluster_blueprint 可能是 list（城南实测），裸 .items() 会崩。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_lookup  # 章号→cluster_id + blueprint list 归一守卫
except Exception:  # 防御：缺模块退回原 dict 守卫
    cluster_lookup = None


# 🔴 2026-07-04: 内容语义 embedding 后端接线（W6-C 迁移：风格模型→bge 内容模型·
# 本仓约定：每个消费 embedding 的脚本自带一份门控副本）。
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available·
    替代旧的按 EMBED_BACKEND/GEN_EMBED__ 环境变量猜测的 _has_real_embedding_backend）。

    import 失败 → False（调用方保留字面法判定的 issue）。"""
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


# 章末文本 vs anchor 描述池的余弦相似度 ≥ 此值 → 判定语义锚定（抓字面 0 重叠但同一剧情点的
# 改写，如伏笔写「铭文」章末写「刻在石壁上的古老符文」）。env 可覆盖。
# 金标准校准 2026-07-04：content_embed_separability_20260704 报告 neg_p95=0.5165/Youden=0.4904
DEFAULT_SEMANTIC_ANCHOR_FLOOR = 0.49


def _semantic_anchor_floor() -> float:
    raw = os.environ.get("CHAPTER_END_SEMANTIC_ANCHOR_FLOOR")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_SEMANTIC_ANCHOR_FLOOR


def _semantic_anchor_match(tail_text: str, anchor_texts: list[str]) -> "dict | None":
    """章末文本 vs anchor_texts 池（伏笔/scope_summary 原始描述）逐条算余弦相似度。
    最高分 >= floor → 返回 {anchor_text, similarity}（语义锚定命中）；内容后端不可用 /
    池空 / 编码失败 / 维度不一致 → None（调用方保留字面法判定的 issue）。"""
    if not anchor_texts:
        return None
    try:
        from embedding_store import compute_content_embedding, cosine_similarity, prefetch_content_embeddings
        # 2026-07-03 Wave-4：章末文本 + 全部 anchor 池一次性预热缓存，其后逐条
        # compute_content_embedding 命中缓存（否则真后端下每条 anchor 各起一次子进程暖机不可用）。
        prefetch_content_embeddings([tail_text] + [t for t in dict.fromkeys(anchor_texts) if t])
        tail_emb = compute_content_embedding(tail_text)
    except Exception:
        return None
    if not tail_emb:
        return None
    floor = _semantic_anchor_floor()
    best_text, best_sim = None, 0.0
    for t in dict.fromkeys(anchor_texts):  # 去重·保序
        if not t:
            continue
        try:
            emb = compute_content_embedding(t)
        except Exception:
            continue
        if not emb or len(emb) != len(tail_emb):
            continue
        sim = cosine_similarity(tail_emb, emb)
        if sim > best_sim:
            best_text, best_sim = t, sim
    if best_text is None or best_sim < floor:
        return None
    return {"anchor_text": best_text[:120], "similarity": round(best_sim, 4)}

# ============ banned patterns（与 pretooluse_chapter_edit_gate.py 同源）============

SCREENPLAY_PATTERNS = [
    (r"（镜头[^）]{0,15}）", "剧本体镜头指令"),
    (r"（切镜[^）]{0,10}）", "剧本体切镜"),
    (r"（旁白[^）]{0,15}）", "剧本体旁白"),
    (r"（画外音[^）]{0,15}）", "剧本体画外音"),
    (r"（音效[^）]{0,15}）", "剧本体音效"),
    (r"（[^）]{0,15}的视角[^）]{0,5}）", "剧本体 POV 指令"),
    (r"（[^）]{0,10}离开[^）]{0,10}视角[^）]{0,5}）", "剧本体 POV 切换"),
]

# 2026-05-29 北极星 P4 [H1-dont]：拆两类（守原则5「不干涉模型判断」）——
# ① 物理分隔符（剧本/排版污染，任何风格都不该有）→ hard_gate 保留；
# ② 语义收束句（一切安静下来/灯熄了/画面渐暗…）是【读者体验偏好】，不该升格为不可豁免
#    hard_gate（且正则会误杀场景中段正常句）→ 降 advisory（写作 agent 有理由可豁免）。
CHAPTER_END_SEPARATOR_PATTERNS = [
    (r"^\s*\*{1,3}\s*$", "章末单独 * 分隔符"),
    (r"^\s*[·]{3,}\s*$", "章末单独 ··· 分隔符"),
    (r"^\s*—{3,}\s*$", "章末单独 ——— 分隔符"),
]
CHAPTER_END_CLOSURE_PATTERNS = [
    (r"一切安静下来", "章末收束句"),
    (r"声.{0,3}越来越远", "章末听觉淡出"),
    (r"灯.{0,5}熄了", "章末视觉淡出"),
    (r"画面.{0,3}渐暗", "章末视觉渐暗"),
    (r"然后.{0,3}安静", "章末收束式短语"),
]
# 向后兼容别名（旧引用方仍可用全集）
CHAPTER_END_PATTERNS = CHAPTER_END_SEPARATOR_PATTERNS + CHAPTER_END_CLOSURE_PATTERNS

# ============ 章末提取 ============

def get_chapter_tail_paragraphs(chapter_text: str, n: int = 5) -> list[str]:
    """取章末最后 n 个非空段。"""
    paras = [p.strip() for p in chapter_text.split("\n\n") if p.strip()]
    return paras[-n:] if len(paras) >= n else paras


def extract_keywords(text: str) -> set[str]:
    """从段落抽实词关键词（去停用词 + 单字）。
    简化实现：抽 2-4 字中文连续词 + 已知人名地名物件。
    """
    # 找 2-4 字连续中文（不含标点）
    candidates = set(re.findall(r"[一-鿿]{2,4}", text))
    # 去常见停用词 / 代词
    stop = {
        "他", "她", "它", "们", "这个", "那个", "什么", "怎么", "为什么", "因为", "所以",
        "然后", "时候", "时间", "地方", "东西", "一个", "一种", "一些", "一直", "一边",
        "看着", "听着", "说着", "走着", "想着", "坐着", "站着", "拿着",
        "知道", "不知道", "觉得", "似乎", "好像", "应该", "可能", "也许",
        "今天", "明天", "昨天", "上午", "下午", "晚上", "夜里", "早上", "中午",
    }
    return {w for w in candidates if w not in stop and len(w) >= 2}


def collect_anchors(db_dir: Path) -> set[str]:
    """收集所有「已存在剧情」的 anchor 关键词。
    来源：事件簇.json / 伏笔表.json / 进度.json.cluster_blueprint
    """
    anchors = set()

    def add_from_text(text: str):
        anchors.update(extract_keywords(text))

    # 事件簇
    ec_path = db_dir / "事件簇.json"
    if ec_path.exists():
        try:
            ec = json.loads(ec_path.read_text(encoding="utf-8"))
            for c in ec.get("clusters", []):
                for fld in ("title", "scope_summary", "_emergence_seed"):
                    v = c.get(fld)
                    if isinstance(v, str):
                        add_from_text(v)
                for sb in c.get("scene_storyboard", []) or []:
                    add_from_text(sb.get("summary", ""))
                for fs in c.get("foreshadowing_to_plant", []) or []:
                    add_from_text(fs.get("description", ""))
                add_from_text(", ".join(c.get("characters_in_cluster", []) or []))
        except Exception:
            pass

    # 伏笔表
    fb_path = db_dir / "伏笔表.json"
    if fb_path.exists():
        try:
            fb = json.loads(fb_path.read_text(encoding="utf-8"))
            for p in fb.get("promises", []):
                add_from_text(p.get("description", ""))
                tc = p.get("trigger_condition", {})
                if isinstance(tc, dict):
                    add_from_text(tc.get("physical_evidence", ""))
            for s in fb.get("secrets", []):
                add_from_text(s.get("secret", ""))
        except Exception:
            pass

    # 进度.cluster_blueprint
    pr_path = db_dir / "进度.json"
    if pr_path.exists():
        try:
            pr = json.loads(pr_path.read_text(encoding="utf-8"))
            # 2026-05-29 复审复修 SC-1：blueprint 可能是 list，先归一成 dict 再迭代。
            if cluster_lookup is not None:
                _bp = cluster_lookup.normalize_blueprint(pr)
            else:
                _bp = pr.get("cluster_blueprint") or {}
                if not isinstance(_bp, dict):
                    _bp = {}
            for cid, c in _bp.items():
                if not isinstance(c, dict):
                    continue
                for fld in ("title", "scope_summary"):
                    v = c.get(fld)
                    if isinstance(v, str):
                        add_from_text(v)
        except Exception:
            pass

    # 人物卡
    cc_path = db_dir / "人物卡.json"
    if cc_path.exists():
        try:
            cc = json.loads(cc_path.read_text(encoding="utf-8"))
            for c in cc.get("characters", []):
                anchors.add(c.get("name", ""))
                for alias in c.get("aliases", []) or []:
                    anchors.add(alias)
        except Exception:
            pass

    # 道具 + 地图
    for fname in ("道具.json", "地图.json"):
        fp = db_dir / fname
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
                for itm in d.get("items", []) or []:
                    add_from_text(itm.get("name", ""))
                locs = d.get("locations", {})
                if isinstance(locs, dict):
                    for loc in locs.values():
                        if isinstance(loc, dict):
                            anchors.add(loc.get("name", ""))
                elif isinstance(locs, list):
                    for loc in locs:
                        if isinstance(loc, dict):
                            anchors.add(loc.get("name", ""))
            except Exception:
                pass

    return {a for a in anchors if a and len(a) >= 2}


def collect_anchor_texts(db_dir: Path) -> list[str]:
    """收集「已存在剧情」的原始描述文本池（未经 extract_keywords 切词·供语义锚定比对用）。

    来源同 collect_anchors 的事件簇.json / 伏笔表.json 描述字段——人物卡/道具/地图只是
    短名词，embedding 语义比对意义不大，不纳入此池（字面法仍覆盖它们）。"""
    texts: list[str] = []

    def add(v):
        if isinstance(v, str) and v.strip():
            texts.append(v.strip())

    ec_path = db_dir / "事件簇.json"
    if ec_path.exists():
        try:
            ec = json.loads(ec_path.read_text(encoding="utf-8"))
            for c in ec.get("clusters", []):
                for fld in ("title", "scope_summary", "_emergence_seed"):
                    add(c.get(fld))
                for sb in c.get("scene_storyboard", []) or []:
                    add(sb.get("summary", ""))
                for fs in c.get("foreshadowing_to_plant", []) or []:
                    add(fs.get("description", ""))
        except Exception:
            pass

    fb_path = db_dir / "伏笔表.json"
    if fb_path.exists():
        try:
            fb = json.loads(fb_path.read_text(encoding="utf-8"))
            for p in fb.get("promises", []):
                add(p.get("description", ""))
                tc = p.get("trigger_condition", {})
                if isinstance(tc, dict):
                    add(tc.get("physical_evidence", ""))
            for s in fb.get("secrets", []):
                add(s.get("secret", ""))
        except Exception:
            pass

    return texts


# ============ 检测主逻辑 ============

def scan_chapter_end(chapter_path: Path, anchors: set[str], hard_gate_only: bool = False,
                      anchor_texts: "list[str] | None" = None) -> dict:
    """扫一章末段，返回 issues。

    🔴 2026-06-27 C06：hard_gate_only=True（--hard-gate-only）只跑 SCREENPLAY + SEPARATOR
    两族 hard_gate（供切章后复扫·与 audit_hub step3 整段 SCREENPLAY 扫互补：此处补 SEPARATOR
    须锚定章末的位置敏感检测），跳过 closure / anchor / POV advisory（避免 advisory 噪声干扰
    复扫纯阻断语义）。北极星⑤：语义收束/弱锚永不在此升格 hard_gate。

    🔴 2026-07-02：anchor_texts（collect_anchor_texts 产出的原始描述文本池）为可选参数。
    内容语义后端就绪 + 字面锚定判定 NO_ANCHOR/WEAK_ANCHOR 时，补一次语义 rescue——
    章末与池中某条描述语义同指（哪怕零字面重叠）则不再误报。不传此参数（默认 None）时
    与升级前行为逐字节一致（仅 advisory 锚定维度·不碰 hard_gate SCREENPLAY/TRANSITION）。
    """
    text = chapter_path.read_text(encoding="utf-8")
    tail_paras = get_chapter_tail_paragraphs(text, n=5)
    tail_text = "\n\n".join(tail_paras)

    issues = []

    # A. banned_patterns
    for pat, reason in SCREENPLAY_PATTERNS:
        for m in re.finditer(pat, tail_text, re.IGNORECASE | re.MULTILINE):
            issues.append({
                "code": "CHAPTER_END_FORBIDDEN_SCREENPLAY",
                "gate_level": "hard_gate",
                "severity": "fatal",
                "matched": m.group(0)[:60],
                "reason": reason,
                "fix_hint": "删除剧本体过渡 · POV 不切换让角色全程在场",
            })
    # 物理分隔符 → hard_gate（格式污染不可豁免）
    for pat, reason in CHAPTER_END_SEPARATOR_PATTERNS:
        for m in re.finditer(pat, tail_text, re.MULTILINE):
            issues.append({
                "code": "CHAPTER_END_FORBIDDEN_TRANSITION",
                "gate_level": "hard_gate",
                "severity": "fatal",
                "matched": m.group(0)[:60],
                "reason": reason,
                "fix_hint": "删除章末物理分隔符 · 章节是格式输出不该出现排版分隔",
            })
    # 🔴 2026-06-27 C06：--hard-gate-only 模式到此为止（只 SCREENPLAY + SEPARATOR 两族 hard_gate）。
    if hard_gate_only:
        return {
            "chapter_path": str(chapter_path),
            "tail_text_preview": tail_text[:200] + ("..." if len(tail_text) > 200 else ""),
            "tail_keywords_count": 0,
            "hit_anchors": [],
            "anchor_ratio": 0.0,
            "issues": issues,
        }
    # 语义收束句 → advisory（读者体验偏好 · 写作 agent 有理由可豁免 · 不再 hard_gate 误升格）
    for pat, reason in CHAPTER_END_CLOSURE_PATTERNS:
        for m in re.finditer(pat, tail_text, re.MULTILINE):
            issues.append({
                "code": "CHAPTER_END_CLOSURE_ADVISORY",
                "gate_level": "advisory",
                "severity": "warning",
                "matched": m.group(0)[:60],
                "reason": reason,
                "fix_hint": "章末倾向钩子而非收束（建议非强制）· 末句留悬念更佳 · 若本场景确需收束可豁免",
            })

    # B. 锚定 scan（字面 token 交集为准 · 内容后端就绪时对字面判定的 NO_ANCHOR/WEAK_ANCHOR
    # 补一次语义 rescue·2026-07-02）
    tail_keywords = extract_keywords(tail_text)
    hit_anchors = tail_keywords & anchors
    anchor_ratio = len(hit_anchors) / max(len(tail_keywords), 1)

    no_anchor = not hit_anchors
    weak_anchor = bool(hit_anchors) and anchor_ratio < _WEAK_ANCHOR_RATIO
    semantic_rescue = None
    if (no_anchor or weak_anchor) and anchor_texts and _content_backend_ready():
        semantic_rescue = _semantic_anchor_match(tail_text, anchor_texts)

    if no_anchor and semantic_rescue is None:
        issues.append({
            "code": "CHAPTER_END_NO_ANCHOR",
            "gate_level": "advisory",
            "severity": "warning",
            "matched": ", ".join(list(tail_keywords)[:5]),
            "reason": "章末 5 段 0 关键词命中 cluster_blueprint / 伏笔表 / 事件簇 brief — 可能装神弄鬼无锚 cliffhanger",
            "fix_hint": "重写章末，锚定到下一 cluster brief 的具体伏笔 / 角色 / 物件 / 事件",
        })
    elif weak_anchor and semantic_rescue is None:
        issues.append({
            "code": "CHAPTER_END_WEAK_ANCHOR",
            "gate_level": "advisory",
            "severity": "warning",
            "matched": f"hit={len(hit_anchors)}/{len(tail_keywords)} keywords ({anchor_ratio:.1%})",
            "reason": f"章末锚定率偏低 (< {_WEAK_ANCHOR_RATIO:.0%}) — cliffhanger 与已存在剧情关联弱",
            "fix_hint": "增加章末与下一 cluster 伏笔/角色/物件的具体绑定",
        })

    result = {
        "chapter_path": str(chapter_path),
        "tail_text_preview": tail_text[:200] + ("..." if len(tail_text) > 200 else ""),
        "tail_keywords_count": len(tail_keywords),
        "hit_anchors": list(hit_anchors)[:10],
        "anchor_ratio": round(anchor_ratio, 3),
        "issues": issues,
    }
    if semantic_rescue is not None:
        result["semantic_anchor_rescue"] = semantic_rescue
    return result


# ============ CLI ============

def parse_chapter_range(s: str) -> list[int]:
    if "-" in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s)]


def main():
    ap = argparse.ArgumentParser(description="L2 章末 cliffhanger 锚定扫描")
    ap.add_argument("project", help="项目路径")
    ap.add_argument("--chapters", required=True, help="章节范围（如 1-4 或 5）")
    ap.add_argument("--strict", action="store_true", help="严格模式：advisory 也 exit 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    # 🔴 2026-06-27 C06：只跑 SCREENPLAY + SEPARATOR 两族 hard_gate（跳 closure/anchor/POV advisory）·
    # 供切章后复扫纯阻断（与 audit_hub step3 整段 SCREENPLAY 扫互补：补章末位置敏感的 SEPARATOR）。
    ap.add_argument("--hard-gate-only", action="store_true",
                    help="只检测 SCREENPLAY + SEPARATOR 两族 hard_gate · 跳过所有 advisory")
    # 🔴 2026-06-27 P1-07: CLI 覆盖弱锚阈值（优先级高于 env CHAPTER_END_WEAK_ANCHOR_RATIO）
    ap.add_argument("--weak-anchor-ratio", type=float, default=None,
                    help="弱锚 advisory 触发阈值（默认 0.15·env CHAPTER_END_WEAK_ANCHOR_RATIO·CLI 优先）")
    args = ap.parse_args()
    if args.weak_anchor_ratio is not None:
        global _WEAK_ANCHOR_RATIO
        _WEAK_ANCHOR_RATIO = float(args.weak_anchor_ratio)

    project = Path(args.project).resolve()
    if not project.exists():
        print(f"[FATAL] 项目路径不存在: {project}", file=sys.stderr)
        sys.exit(2)

    db = project / "_数据库"
    if not db.exists():
        print(f"[FATAL] 找不到 _数据库 目录: {db}", file=sys.stderr)
        sys.exit(2)

    anchors = collect_anchors(db)
    anchor_texts = collect_anchor_texts(db)  # 语义 rescue 池（内容后端未就绪时无副作用）
    if not anchors:
        print(f"[WARN] 没采集到 anchors（_数据库 可能为空）", file=sys.stderr)

    results = []
    total_hard = 0
    total_advisory = 0

    for ch in parse_chapter_range(args.chapters):
        ch_path = project / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"
        if not ch_path.exists():
            print(f"[SKIP] 第{ch:03d}章 正文未找到", file=sys.stderr)
            continue
        r = scan_chapter_end(ch_path, anchors, hard_gate_only=args.hard_gate_only,
                              anchor_texts=anchor_texts)
        results.append(r)
        for iss in r["issues"]:
            if iss["gate_level"] == "hard_gate":
                total_hard += 1
            elif iss["gate_level"] == "advisory":
                total_advisory += 1

    if args.json:
        print(json.dumps({"results": results, "anchors_count": len(anchors)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[chapter_end_anchor_scan] anchors_pool={len(anchors)} · chapters_scanned={len(results)}")
        print(f"  hard_gate: {total_hard} · advisory: {total_advisory}")
        for r in results:
            ch_name = Path(r["chapter_path"]).parent.name
            if r["issues"]:
                print(f"\n  📄 {ch_name}:")
                print(f"     anchor_ratio={r['anchor_ratio']} hit={r['hit_anchors'][:5]}")
                for iss in r["issues"]:
                    flag = "🔴" if iss["gate_level"] == "hard_gate" else "🟡"
                    print(f"     {flag} [{iss['code']}] {iss['reason'][:80]}")
                    if iss.get("matched"):
                        print(f"        matched: {iss['matched']}")
            else:
                print(f"  ✅ {ch_name}: anchor_ratio={r['anchor_ratio']}")

    if total_hard > 0:
        # 🔴 2026-06-26 fatal 走 stderr+flush（同 gen_writer/gen_fixer 修法）
        sys.stderr.write(f"[chapter_end_anchor_scan][HARD_GATE] {total_hard} 条 hard_gate 命中，必修\n")
        sys.stderr.flush()
        sys.exit(2)
    if total_advisory > 0 and args.strict:
        # 🔴 2026-06-26 加 banner（cluster_001 翻车 sediment）：
        # exit 1 在 shell 惯例下 = failed，调用方易误以为 scanner 崩了。
        # 实际上是 --strict 模式下 advisory 也阻断的设计。明示「advisory · 非崩溃」。
        sys.stderr.write(
            f"[chapter_end_anchor_scan][ADVISORY] {total_advisory} 条 advisory 命中。"
            f"--strict 模式下用 exit 1 标记（非 fatal · 主代理可写 waiver 到 changes.json 豁免）。\n"
            f"  取消 --strict → advisory 不阻断（exit 0）；hard_gate 任何模式都 exit 2。\n")
        sys.stderr.flush()
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
