"""chapter_splitter.py — DCAS 章节自动截断（v18 接入 chapter_io）

【设计修正】v17.8 原把 splitter 设计成 LLM agent 是过度设计——
7 维度评分全是确定性规则，不需要 LLM 语言理解。改为纯 Python 脚本：
  - 不依赖 agent 加载
  - 确定性、可单元测试
  - 速度快（毫秒级）

读 writer 生成的 6000+ 字双章草稿，自动评分候选截断点，切成 ch + ch+1 pre_opening。

【v18 改动】正文/数据已分离：
  - 草稿正文从 cio.read_body() 读（v18 纯正文稿 / 旧混合稿通吃）
  - 截断后只重写正文 txt（cio.write_body）——CHANGES 全部归属 ch，splitter 不碰
  - pre_opening 仍写到 第(N+1)章/.pre_opening.txt

用法（DCAS 双章 · 默认）:
    python chapter_splitter.py <项目路径> <章节号> [--target 3000] [--tolerance 500] [--dry-run]

用法（v27 ecas_freestyle · 按字数硬范围切 + 末章 pending_tail 补料）:
    python chapter_splitter.py <项目路径> --mode ecas_freestyle \
        --cluster-id cluster_006 --cluster-start-ch 26 --draft <cluster_draft.txt> \
        [--rhythm 标准|紧凑|厚重|混合] [--narrative-mode linear|in_medias_res] \
        [--climax-hint <scene 下标>] [--previous-pending-tail <上 cluster pending_tail.txt>] [--dry-run]

2026-05-29 复审修复（M5）：ecas_freestyle 增 --narrative-mode / --climax-hint。
in_medias_res（cluster_001 黄金三章倒叙默认）时先把 climax 段提前再切。

退出码: 0 成功 / 1 草稿不足 / 2 致命错误
"""

import sys
import re
import json
import math
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


def strip_title(body: str):
    """分离章节标题行和正文。返回 (title_line, content)。"""
    lines = body.split("\n")
    title = ""
    rest_start = 0
    for i, ln in enumerate(lines):
        if re.match(r"^第\d+章", ln.strip()):
            title = ln
            rest_start = i + 1
            break
    content = "\n".join(lines[rest_start:]).lstrip("\n")
    return title, content


def split_paragraphs_with_offset(content: str):
    """切段，返回 [(para_text, char_offset_at_end, cumulative_word_count)]."""
    paras = []
    cumulative = 0
    offset = 0
    for chunk in re.split(r"(\n\s*\n)", content):
        if chunk.strip() == "" and "\n" in chunk:
            offset += len(chunk)
            continue
        if not chunk.strip():
            offset += len(chunk)
            continue
        wc = len(chunk.replace(" ", "").replace("\n", ""))
        cumulative += wc
        offset += len(chunk)
        paras.append({
            "text": chunk.strip(),
            "char_offset_end": offset,
            "cumulative_word_count": cumulative,
        })
    return paras


# ============ 7 维度评分 ============

ONOMATOPOEIA = re.compile(r"(咯|啪|嗒|哒|轰|咚|哗|砰|咳|噗|滋|嘎|吱|咔)——")
DASH_END = re.compile(r"[—…]\s*$")
SCENE_BREAK_START = re.compile(r"^(——|\*\s*\*|\* \* \*)")
CONTINUE_ACTION = re.compile(r"^[^\n]{0,8}(回头|抬头|睁开|醒来|想起|站起|转身)")
CHAR_NAMES = ["克莱", "莫顿", "艾尔莎", "伊森", "老亨利", "七夜伯爵"]
DIALOGUE_OPEN = re.compile(r'["「]')
DIALOGUE_CLOSE = re.compile(r'["」]')
PSYCH_KW = re.compile(r"(他想|她想|他记得|他觉得|他不知)")
REVEAL_KW = re.compile(r"(原来|真相是|其实是|竟然是)")


def score_split_point(paras, idx, target, tolerance):
    """对"在 paras[idx] 之后切割"评分。idx 是切断点前最后一段的下标。"""
    if idx < 0 or idx >= len(paras):
        return None
    before = paras[idx]
    after = paras[idx + 1] if idx + 1 < len(paras) else None
    wc = before["cumulative_word_count"]

    # 字数接近度
    score = 0
    reasons = []
    if target - 300 <= wc <= target + 300:
        score += 5
        reasons.append(f"字数 {wc} 在 ±300 (+5)")
    elif target - tolerance <= wc <= target + tolerance:
        score += 3
        reasons.append(f"字数 {wc} 在 ±{tolerance} (+3)")
    else:
        return None  # 字数太偏，不进候选

    before_text = before["text"]
    # 钩子张力
    last_sentence = before_text.split("。")[-1] or before_text.split("。")[-2] if "。" in before_text else before_text
    if ONOMATOPOEIA.search(before_text[-30:]):
        score += 3
        reasons.append("末尾拟声 (+3)")
    if DASH_END.search(before_text):
        score += 3
        reasons.append("末尾破折号/省略号 (+3)")
    if len(before_text.split("\n")[-1]) <= 12:
        score += 2
        reasons.append("末段独立短句 (+2)")

    # 场景边界 + 接续
    if after:
        after_text = after["text"]
        if SCENE_BREAK_START.search(after_text):
            score += 5
            reasons.append("后段场景分隔符 (+5)")
        if CONTINUE_ACTION.search(after_text):
            score += 3
            reasons.append("后段接续动作 (+3)")
        for name in CHAR_NAMES:
            if after_text.startswith(name):
                score += 2
                reasons.append(f"后段角色名'{name}'开头 (+2)")
                break

    # avoid 项
    # 对话引号未闭（before 段引号开闭不平衡）
    opens = len(DIALOGUE_OPEN.findall(before_text))
    closes = len(DIALOGUE_CLOSE.findall(before_text))
    if opens != closes:
        score -= 50
        reasons.append("⚠️ 对话引号未闭 (-50)")
    # 破折号中间
    if before_text.rstrip().endswith("——") and after and not after["text"].startswith(("他", "她", "克莱")):
        score -= 30
        reasons.append("⚠️ 破折号悬空 (-30)")
    # 心理戏中间（before 末段是心理 + after 首段也是心理）
    if PSYCH_KW.search(before_text[-50:]) and after and PSYCH_KW.search(after["text"][:50]):
        score -= 10
        reasons.append("⚠️ 心理戏中间 (-10)")
    # 后段直接揭谜底
    if after and REVEAL_KW.search(after["text"][:60]):
        score -= 5
        reasons.append("⚠️ 后段直接揭谜底 (-5)")

    return {
        "para_idx": idx,
        "char_offset": before["char_offset_end"],
        "word_count_before": wc,
        "score": score,
        "reasons": reasons,
    }


# ============ v27 ecas_freestyle 模式 ============
#
# 与 DCAS 双章模式的差异（详见 novel-chapter-splitter.md §v27）：
#   - 章数不由外部传 TARGET_CHAPTERS，splitter 按整 cluster 草稿字数自动算
#     N = round(draft_cjk / 3500)，钳到 [ceil(draft_cjk/4500), floor(draft_cjk/3000)]
#   - 每章硬范围 3000-4500 CJK（rhythm_profile 微调）
#   - 末章 < 下限 → 不强切，末段退回 cluster_<key>_pending_tail.txt，本 cluster 只切 N-1 章
#   - 接 --previous-pending-tail <path> 时把上 cluster 的 pending_tail prepend 到草稿头部联合切
#   - 沿用 score_split_point 的最佳切点评分（每个等距锚点附近 ±tolerance 选最佳段落边界）
#
# 不破坏 DCAS：仅当 --mode ecas_freestyle 时走本分支，默认仍走原 main() 双章逻辑。

# rhythm_profile → (每章下限, 每章上限, 目标)
RHYTHM_RANGES = {
    "紧凑": (3000, 4000, 3500),
    "compact": (3000, 4000, 3500),
    "标准": (3000, 4500, 3500),
    "standard": (3000, 4500, 3500),
    "厚重": (3500, 5000, 4200),
    "heavy": (3500, 5000, 4200),
    "混合": (3000, 4500, 3500),
    "mixed": (3000, 4500, 3500),
}


def _resolve_rhythm(profile: str):
    """返回 (lo, hi, target) 每章字数硬范围。未知 profile 回退标准 3000-4500。"""
    return RHYTHM_RANGES.get((profile or "").strip(), (3000, 4500, 3500))


# 2026-05-29 复审修复（M5）：in_medias_res 倒叙 climax 段检测关键词
# （CLAUDE.md「黄金三章倒叙」：cliffhanger 关键词 / 强冲突信号）。
CLIMAX_KW = re.compile(
    r"(尖叫|惨叫|怒吼|爆炸|轰鸣|鲜血|血泊|尸体|崩塌|断裂|坠落|嘶吼|枪声|刀光|"
    r"死了|杀了|来不及|轰然|炸开|裂开|喷涌|刺穿|断气|窒息|绝望|崩溃|失控)"
)


def _find_climax_para_index(paras, climax_hint):
    """2026-05-29 复审修复（M5）：在段落组里定位 climax 段下标（in_medias_res 重组用）。

    优先级：
      1. climax_hint（outline-planner 标的 scene_storyboard 下标）映射到段落位置 —
         scene 数无法精确对齐段落，按 hint 占总段落比例估算锚点位置，在附近取
         CLIMAX_KW 命中最密集的段落。
      2. 无 hint：全局扫 CLIMAX_KW 命中数最高的段落（窗口 3 段滑动累计）。
    找不到任何信号返回 None（调用方退化为不重组）。
    """
    n = len(paras)
    if n == 0:
        return None

    def _kw_score(text):
        return len(CLIMAX_KW.findall(text or ""))

    # 滑窗（前后各 1 段）累计 climax 关键词密度
    densities = []
    for i in range(n):
        s = _kw_score(paras[i]["text"])
        if i > 0:
            s += _kw_score(paras[i - 1]["text"])
        if i < n - 1:
            s += _kw_score(paras[i + 1]["text"])
        densities.append(s)

    # 1) climax_hint 锚点：把 scene 下标按比例映射到段落位置，附近 ±15% 段落窗口取最高密度
    if isinstance(climax_hint, int) and climax_hint >= 0:
        # hint 是 scene 下标；scene_storyboard 总场景数未知，保守按 hint/(hint+1) 估占比，
        # 但更稳的做法：把 hint 当「越靠后越接近 climax」的相对信号，落到 [0.4, 0.9] 区间。
        approx_frac = min(0.9, 0.4 + 0.1 * climax_hint)
        anchor = int(n * approx_frac)
        lo_w = max(0, anchor - max(1, int(n * 0.15)))
        hi_w = min(n - 1, anchor + max(1, int(n * 0.15)))
        window = range(lo_w, hi_w + 1)
        best_i = max(window, key=lambda i: (densities[i], -abs(i - anchor)))
        if densities[best_i] > 0:
            return best_i
        # hint 窗口无关键词命中 → 直接用 anchor 作为 climax 锚点（信任 outline 标注）
        return anchor

    # 2) 无 hint：全局最高密度段
    best_i = max(range(n), key=lambda i: densities[i])
    if densities[best_i] > 0:
        return best_i
    return None


def _reorder_for_in_medias_res(paras, climax_idx):
    """2026-05-29 复审修复（M5）：in_medias_res 倒叙重排段落组。

    把 climax 段（及紧邻的强冲突前后文，取 climax 段所在的「场景块」近似）提到最前，
    其后接 cluster 原始时间序的开头→climax 之前→climax 之后剩余。

    近似实现（纯 Python 段落级，不做语义场景切分）：
      新顺序 = [climax 段] + [0 .. climax-1 原序] + [climax+1 .. 末尾 原序]
    即把单个 climax 段抽到最前作 in_medias_res 开场钩子，其余保持时间序，
    回到开头逐步推进到 climax 之后。返回重排后的新 paras（重算 offset/cumulative）。
    """
    if climax_idx is None or not (0 <= climax_idx < len(paras)):
        return paras
    reordered = [paras[climax_idx]] + paras[:climax_idx] + paras[climax_idx + 1:]
    # 重算 cumulative_word_count / char_offset_end（切点评分与字数依赖累计值）
    rebuilt = []
    cumulative = 0
    offset = 0
    for p in reordered:
        wc = len(p["text"].replace(" ", "").replace("\n", ""))
        cumulative += wc
        offset += len(p["text"]) + 2  # +2 近似段间 \n\n
        rebuilt.append({
            "text": p["text"],
            "char_offset_end": offset,
            "cumulative_word_count": cumulative,
        })
    return rebuilt


def compute_freestyle_chapter_count(draft_cjk: int, lo: int, hi: int, target: int) -> int:
    """按字数算 N（每章硬范围 [lo, hi]）。极端短篇返回 0（全段退 pending_tail）。"""
    if draft_cjk < lo:
        return 0  # 不切 · 整段写 pending_tail（等下 cluster 拼）
    n_min = math.ceil(draft_cjk / hi)   # 每章不超 hi → 最少章数
    n_max = math.floor(draft_cjk / lo)  # 每章不少 lo → 最多章数
    if n_max < 1:
        n_max = 1
    if n_min < 1:
        n_min = 1
    if n_max < n_min:
        n_max = n_min
    n_recommend = max(1, round(draft_cjk / target))
    return max(n_min, min(n_max, n_recommend))


def _best_anchor_split(paras, anchor_word, tolerance, lo, hi, mid_target):
    """在累计字数 ≈ anchor_word 的位置附近，按 score_split_point 找最佳段落边界。
    返回选中的 para_idx（在该段之后切），找不到合法点返回 None。"""
    best = None
    for idx in range(len(paras) - 1):
        wc = paras[idx]["cumulative_word_count"]
        if abs(wc - anchor_word) > tolerance:
            continue
        r = score_split_point(paras, idx, mid_target, tolerance)
        if r is None:
            # 字数虽近锚点但 score_split_point 因 mid_target 判定出界 → 仍纳入（freestyle 用锚点距离兜底）
            r = {"para_idx": idx, "char_offset": paras[idx]["char_offset_end"],
                 "word_count_before": wc, "score": 0, "reasons": ["锚点兜底候选"]}
        # freestyle 综合分：评分 - 偏离锚点惩罚
        combined = r["score"] - abs(wc - anchor_word) / 100.0
        if best is None or combined > best[0]:
            best = (combined, r)
    return best[1] if best else None


def run_freestyle(project_root, cluster_id, cluster_start_ch, draft_text,
                  rhythm_profile, previous_pending_tail, dry_run,
                  narrative_mode="linear", climax_hint=None):
    """v27 ecas_freestyle 切割。返回 report dict。

    2026-05-29 复审修复（M5）：新增 narrative_mode + climax_hint。
    narrative_mode == "in_medias_res"（cluster_001 黄金三章倒叙默认）时，切章前先把
    climax 段提前到草稿头部（in_medias_res 开场钩子），再按字数硬范围切。linear 不动。
    """
    cluster_key = str(cluster_id).replace("cluster_", "")
    lo, hi, target = _resolve_rhythm(rhythm_profile)
    tolerance = 600  # freestyle 锚点搜索半径（比 DCAS 略宽，给最佳切点更多空间）

    # 1. prepend 上 cluster pending_tail（跨 cluster 补料）
    prepend_cjk = 0
    content = draft_text
    if previous_pending_tail:
        prepend_text = previous_pending_tail.rstrip()
        prepend_cjk = cio.count_cjk(prepend_text)
        content = prepend_text + "\n\n" + content.lstrip("\n")

    draft_cjk = cio.count_cjk(content)

    # 2. 算 N
    N = compute_freestyle_chapter_count(draft_cjk, lo, hi, target)

    paras = split_paragraphs_with_offset(content)

    # 2.5 in_medias_res 倒叙重组（M5）：climax 段提前作开场钩子，再切。
    in_medias_res_reordered = False
    climax_idx_detected = None
    if (narrative_mode or "linear").strip() == "in_medias_res" and len(paras) >= 2:
        climax_idx_detected = _find_climax_para_index(paras, climax_hint)
        if climax_idx_detected is not None and climax_idx_detected > 0:
            paras = _reorder_for_in_medias_res(paras, climax_idx_detected)
            in_medias_res_reordered = True

    decision_log = {
        "rhythm_profile": rhythm_profile or "标准",
        "per_chapter_range": [lo, hi],
        "N_min": math.ceil(draft_cjk / hi) if draft_cjk >= lo else 0,
        "N_max": math.floor(draft_cjk / lo) if draft_cjk >= lo else 0,
        "N_recommend": max(1, round(draft_cjk / target)) if draft_cjk >= lo else 0,
        "N_final": N,
        # M5：倒叙重组决策痕迹
        "narrative_mode": (narrative_mode or "linear").strip(),
        "climax_hint": climax_hint,
        "climax_para_idx_detected": climax_idx_detected,
        "in_medias_res_reordered": in_medias_res_reordered,
    }

    report = {
        "schema_version": "1.0",
        "scanner": "chapter_splitter",
        "mode": "ecas_freestyle",
        "cluster_id": f"cluster_{cluster_key}",
        "cluster_start_ch": cluster_start_ch,
        "draft_cjk_total": draft_cjk,
        "previous_pending_tail_consumed_cjk": prepend_cjk,
        "narrative_mode": (narrative_mode or "linear").strip(),
        "in_medias_res_reordered": in_medias_res_reordered,
        "dry_run": dry_run,
        "_freestyle_decision_log": decision_log,
    }

    # 极端短篇 / 草稿不足一章下限 → 整段退 pending_tail，0 切
    if N == 0:
        report.update({
            "chapters_split": 0,
            "chapter_range": [],
            "per_chapter_cjk": [],
            "pending_tail": {
                "exists": True,
                "cjk": draft_cjk,
                "path": f"章节/cluster_{cluster_key}_draft/cluster_{cluster_key}_pending_tail.txt",
                "_doc": f"整段 {draft_cjk} CJK < 单章下限 {lo} · 不切 · 退 pending_tail 等下 cluster 拼接",
            },
        })
        if not dry_run:
            _write_pending_tail(project_root, cluster_key, content)
            _write_splitter_wal(project_root, cluster_key, report)
        report["files_written"] = []
        return report

    # 3. 等距锚点 + 最佳切点评分（找 N-1 个切点）
    split_indices = []
    used_idx = set()
    for i in range(1, N):
        anchor_word = draft_cjk * i / N
        chosen = _best_anchor_split(paras, anchor_word, tolerance, lo, hi, target)
        if chosen is None:
            # 锚点附近无候选 → 退化：取累计字数最接近锚点的段落边界
            chosen = _nearest_para(paras, anchor_word)
        if chosen and chosen["para_idx"] not in used_idx:
            split_indices.append(chosen["para_idx"])
            used_idx.add(chosen["para_idx"])
    split_indices.sort()

    # 4. 按切点切成 chunk
    chunks = _slice_by_indices(paras, split_indices)
    per_chapter_cjk = [cio.count_cjk(c) for c in chunks]

    # 5. 末章字数补料（Step F_v27）
    pending_tail_text = None
    pending_tail_path_rel = None
    last_cjk = per_chapter_cjk[-1] if per_chapter_cjk else 0
    if len(chunks) >= 2 and last_cjk < lo:
        # 末章不达下限 · 末章退回 pending_tail · 实切 N-1 章
        pending_tail_text = chunks.pop()
        per_chapter_cjk.pop()
        pending_tail_path_rel = f"章节/cluster_{cluster_key}_draft/cluster_{cluster_key}_pending_tail.txt"

    chapters_split = len(chunks)
    ch_start = int(cluster_start_ch)
    ch_end = ch_start + chapters_split - 1

    report.update({
        "chapters_split": chapters_split,
        "chapter_range": [ch_start, ch_end],
        "per_chapter_cjk": per_chapter_cjk,
        "split_para_indices": split_indices[:chapters_split - 1] if chapters_split >= 1 else [],
        "pending_tail": {
            "exists": pending_tail_text is not None,
            "cjk": cio.count_cjk(pending_tail_text) if pending_tail_text else 0,
            "path": pending_tail_path_rel,
            "_doc": (f"末段 {cio.count_cjk(pending_tail_text)} CJK 不足 {lo} · 退回 pending_tail · 下 cluster 拼接后切"
                     if pending_tail_text else None),
        },
    })

    files_written = []
    if not dry_run:
        for offset, chunk in enumerate(chunks):
            n = ch_start + offset
            written = cio.write_body(project_root, n, chunk)
            files_written.append(str(written.relative_to(project_root)))
        if pending_tail_text is not None:
            _write_pending_tail(project_root, cluster_key, pending_tail_text)
            files_written.append(pending_tail_path_rel)
        _write_splitter_wal(project_root, cluster_key, report)
    report["files_written"] = files_written
    return report


def _nearest_para(paras, anchor_word):
    """累计字数最接近 anchor_word 的段落边界（退化兜底）。"""
    best = None
    for idx in range(len(paras) - 1):
        wc = paras[idx]["cumulative_word_count"]
        d = abs(wc - anchor_word)
        if best is None or d < best[0]:
            best = (d, idx, wc)
    if best is None:
        return None
    return {"para_idx": best[1], "char_offset": paras[best[1]]["char_offset_end"],
            "word_count_before": best[2], "score": 0, "reasons": ["nearest_para 退化兜底"]}


def _slice_by_indices(paras, split_indices):
    """按 split_indices（在这些 para_idx 之后切）把段落组切成 chunk 文本列表。"""
    chunks = []
    start = 0
    bounds = list(split_indices) + [len(paras) - 1]
    for b in bounds:
        seg = paras[start:b + 1]
        if seg:
            chunks.append("\n\n".join(p["text"] for p in seg))
        start = b + 1
    return [c for c in chunks if c.strip()]


def _write_pending_tail(project_root, cluster_key, text):
    """末段退回 章节/cluster_<key>_draft/cluster_<key>_pending_tail.txt。"""
    d = Path(project_root) / "章节" / f"cluster_{cluster_key}_draft"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"cluster_{cluster_key}_pending_tail.txt"
    p.write_text(text.rstrip() + "\n", encoding="utf-8")
    return p


def _write_splitter_wal(project_root, cluster_key, report):
    """写 splitter WAL（split_cluster_changes.py 消费 chapter_range / pending_tail）。"""
    d = Path(project_root) / "_数据库" / ".wal"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"splitter_cluster_{cluster_key}_decisions.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(0)

    # ---- v27 ecas_freestyle 分发（在 DCAS 位置参数解析之前拦截）----
    if "--mode" in args and args[args.index("--mode") + 1:args.index("--mode") + 2] == ["ecas_freestyle"]:
        return _main_freestyle(args)

    # ⚠️ DEPRECATED（2026-05-29 北极星 P3 [F4]）：以下 DCAS 双章切割路径已废弃。
    # v27 cluster 主轨一律走 --mode ecas_freestyle（按字数硬范围切 + pending_tail 补料），
    # 不再有流水线触达本位置参路径。保留仅为向后兼容旧手动调用；下方 helper（score_split_point/
    # split_paragraphs_with_offset/strip_title）被 _main_freestyle 共用，故不删整文件。
    project_root = Path(args[0])
    ch = int(args[1])
    target = 3000
    tolerance = 500
    dry_run = "--dry-run" in args
    # v24 黄金三章倒叙模式参数
    narrative_mode = "linear"
    for i, a in enumerate(args):
        if a == "--target" and i + 1 < len(args):
            target = int(args[i + 1])
        if a == "--tolerance" and i + 1 < len(args):
            tolerance = int(args[i + 1])
        if a == "--narrative-mode" and i + 1 < len(args):
            narrative_mode = args[i + 1]  # linear | in_medias_res

    # v24 黄金三章倒叙模式说明（实际重组在 novel-chapter-splitter agent ECAS 模式实现，
    # 本 DCAS 脚本仅暴露参数接口，DCAS 双章模式不做倒叙重组）
    if narrative_mode == "in_medias_res":
        print(f"[INFO] narrative_mode=in_medias_res 已识别 ·"
              f" DCAS 双章模式不实现倒叙重组，建议改用 ECAS 模式 + novel-chapter-splitter agent",
              file=sys.stderr)

    # 找草稿正文文件（v18：cio 兼容 4 布局 + 旧平铺）
    draft_path = cio.find_body_file(project_root, ch)
    if not draft_path:
        print(f"[FATAL] 找不到 ch{ch} 草稿", file=sys.stderr)
        sys.exit(2)

    # v18：正文经 cio 读取（纯正文稿直接读，旧混合稿自动剥离 CHANGES 段）
    body = cio.read_body(project_root, ch)
    title, content = strip_title(body)
    total_wc = cio.count_words(content)

    if total_wc < target + tolerance:
        print(f"[FATAL] 草稿仅 {total_wc} 字 < {target}+{tolerance}，不足双章长度。"
              f"不切割——可能 writer 没按 DCAS 模式生成。", file=sys.stderr)
        sys.exit(1)

    paras = split_paragraphs_with_offset(content)
    candidates = []
    for idx in range(len(paras) - 1):
        r = score_split_point(paras, idx, target, tolerance)
        if r:
            candidates.append(r)

    if not candidates:
        print(f"[FATAL] 无合格候选截断点（字数都偏离 {target}±{tolerance}）", file=sys.stderr)
        sys.exit(1)

    # v17.8 修正：score 降序 + 字数接近 target 做 tie-break（避免并列）
    candidates.sort(key=lambda x: (-x["score"], abs(x["word_count_before"] - target)))
    top1 = candidates[0]
    top2 = candidates[1] if len(candidates) > 1 else None
    # uncertainty 改判：score 相同但已用字数 tie-break → 不算 uncertain
    uncertainty = bool(top2 and top1["score"] == top2["score"]
                       and abs(top1["word_count_before"] - target) == abs(top2["word_count_before"] - target))

    # 切割
    split_idx = top1["para_idx"]
    before_paras = paras[:split_idx + 1]
    after_paras = paras[split_idx + 1:]
    before_content = "\n\n".join(p["text"] for p in before_paras)
    after_content = "\n\n".join(p["text"] for p in after_paras)

    # v18：正文文件只放纯正文（title + 截断前内容）；CHANGES 全部归属 ch，
    # 留在 第NNN章_changes.json，splitter 不切、不动它。
    ch_body = f"{title}\n\n{before_content}" if title else before_content
    pre_opening = after_content

    report = {
        "schema_version": "1.0",
        "scanner": "chapter_splitter",
        "current_chapter": ch,
        "next_chapter": ch + 1,
        "draft_total_words": total_wc,
        "candidates_count": len(candidates),
        "chosen_split_point": {
            "para_idx": top1["para_idx"],
            "word_count_before": top1["word_count_before"],
            "word_count_after": total_wc - top1["word_count_before"],
            "score": top1["score"],
            "reasons": top1["reasons"],
        },
        "top3_candidates": [
            {"para_idx": c["para_idx"], "wc_before": c["word_count_before"], "score": c["score"]}
            for c in candidates[:3]
        ],
        "uncertainty_flag": bool(uncertainty),
        "dry_run": dry_run,
    }
    # v17.8 修正：草稿远超 2×target 时，pre_opening 会偏长
    pre_opening_wc = total_wc - top1["word_count_before"]
    if pre_opening_wc > target + tolerance:
        report["pre_opening_oversized"] = True
        report["pre_opening_word_count"] = pre_opening_wc
        report["note"] = (
            f"pre_opening {pre_opening_wc} 字 > {target}+{tolerance}。"
            f"writer 在 DCAS 模式写超了（草稿 {total_wc} 字 vs target ~6500）。"
            f"ch{ch+1} 启动时可直接用 pre_opening 作为完整章节，或再次 DCAS 切割"
        )

    if not dry_run:
        # 写 ch 正文（截断前 · 纯正文）——cio.write_body 落到标准嵌套路径
        written_body = cio.write_body(project_root, ch, ch_body)
        # 写 ch+1 pre_opening
        next_dir = project_root / "章节" / f"第{ch+1:03d}章"
        next_dir.mkdir(parents=True, exist_ok=True)
        pre_path = next_dir / ".pre_opening.txt"
        pre_path.write_text(pre_opening, encoding="utf-8")
        report["files_written"] = [
            str(written_body.relative_to(project_root)),
            str(pre_path.relative_to(project_root)),
        ]

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)


def _main_freestyle(args):
    """v27 ecas_freestyle CLI 入口。

    用法:
        python chapter_splitter.py <项目路径> --mode ecas_freestyle \\
            --cluster-id cluster_006 --cluster-start-ch 26 \\
            --draft <cluster_draft.txt 路径> \\
            [--rhythm 标准|紧凑|厚重|混合] \\
            [--narrative-mode linear|in_medias_res] \\
            [--climax-hint <scene_storyboard 下标>] \\
            [--previous-pending-tail <上 cluster pending_tail.txt>] \\
            [--dry-run]

    2026-05-29 复审修复（M5）：新增 --narrative-mode / --climax-hint。
    in_medias_res（cluster_001 黄金三章倒叙默认）时先把 climax 段提前再切。
    build_manifest.inject_event_cluster_context 注入的 narrative_mode +
    climax_hint_scene_index 由 novel-chapter-splitter agent 透传到这两个参数。
    """
    project_root = Path(args[0])
    cluster_id = None
    cluster_start_ch = None
    draft_path = None
    rhythm = "标准"
    prev_pending_path = None
    narrative_mode = "linear"
    climax_hint = None
    dry_run = "--dry-run" in args
    for i, a in enumerate(args):
        if a == "--cluster-id" and i + 1 < len(args):
            cluster_id = args[i + 1]
        elif a == "--cluster-start-ch" and i + 1 < len(args):
            cluster_start_ch = int(args[i + 1])
        elif a == "--draft" and i + 1 < len(args):
            draft_path = args[i + 1]
        elif a == "--rhythm" and i + 1 < len(args):
            rhythm = args[i + 1]
        elif a == "--narrative-mode" and i + 1 < len(args):
            narrative_mode = args[i + 1]  # linear | in_medias_res
        elif a == "--climax-hint" and i + 1 < len(args):
            try:
                climax_hint = int(args[i + 1])
            except (ValueError, TypeError):
                climax_hint = None
        elif a == "--previous-pending-tail" and i + 1 < len(args):
            prev_pending_path = args[i + 1]

    if cluster_id is None or cluster_start_ch is None or draft_path is None:
        print("[FATAL] ecas_freestyle 缺参数: 需 --cluster-id / --cluster-start-ch / --draft", file=sys.stderr)
        sys.exit(2)

    dp = Path(draft_path)
    if not dp.is_file():
        print(f"[FATAL] 草稿文件不存在: {draft_path}", file=sys.stderr)
        sys.exit(2)
    draft_text = cio._strip_changes(dp.read_text(encoding="utf-8"))

    previous_pending_tail = None
    if prev_pending_path:
        pp = Path(prev_pending_path)
        if pp.is_file():
            previous_pending_tail = pp.read_text(encoding="utf-8")
        else:
            print(f"[WARN] --previous-pending-tail 指定但文件不存在: {prev_pending_path}", file=sys.stderr)

    report = run_freestyle(project_root, cluster_id, cluster_start_ch, draft_text,
                           rhythm, previous_pending_tail, dry_run,
                           narrative_mode=narrative_mode, climax_hint=climax_hint)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
