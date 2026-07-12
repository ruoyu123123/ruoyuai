"""chapter_splitter.py — cluster 章节自动截断（ecas_freestyle 模式 · 经 chapter_io 读写章节）

纯 Python 脚本（非 LLM agent）：7 维度评分全是确定性规则，不需要 LLM 语言理解。
  - 不依赖 agent 加载
  - 确定性、可单元测试
  - 速度快（毫秒级）

读 writer 生成的整 cluster 草稿，按字数硬范围算 N，自动评分候选切点逐章切。

【正文/数据分离】：
  - 草稿为纯正文，直接读入并裁剪尾部空白
  - 截断后只重写正文 txt（cio.write_body）——CHANGES 全部归属 ch，splitter 不碰

用法（ecas_freestyle · 按字数硬范围切 + 末章 pending_tail 补料）:
    python chapter_splitter.py <项目路径> --mode ecas_freestyle \
        --cluster-id cluster_006 --cluster-start-ch 26 --draft <cluster_draft.txt> \
        [--rhythm 标准|紧凑|厚重|混合] [--narrative-mode linear|in_medias_res] \
        [--climax-hint <scene 下标>] [--previous-pending-tail <上 cluster pending_tail.txt>] [--dry-run]

splitter 不做倒叙重排——倒叙由 outline 设计 scene_storyboard 顺序（scene0=倒叙开场）+ writer
按序写负责，splitter 是纯格式层（北极星④：只按字数 linear 切）。--narrative-mode / --climax-hint
仅作痕迹保留（不触发 climax 段提前）：若 splitter 也做一次 climax 前置重排，会与 writer 已经
按倒叙排好的正文顺序叠加成双重倒叙，破坏开篇结构。

退出码: 0 成功 / 1 草稿不足 / 2 致命错误
"""

import sys
import re
import json
import math
from pathlib import Path

# 统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio
# pending_tail 是跨 cluster 补料产物（下个 cluster 拼接消费），半截文件会被下次联合切割拼进
# 残缺正文，故原子落盘；WAL/.pre_opening 属日志/临时写，无需原子写。
from atomic_json import atomic_write_text


# splitter 字数守恒 hard_gate（北极星④纯格式层：防丢字/重复静默落盘）。
class SplitterIntegrityError(Exception):
    """splitter 字数守恒被破坏（丢字 / 重复 / 空块落盘 / 切片计数失配）= 北极星④纯格式层契约破损。

    code=SPLIT_WORD_NOT_CONSERVED（与 audit_hub.HARD_GATE_CODES / STRUCTURE§12.2 / CLAUDE.md / scanner_registry 四方一致）·
    携 delta(accounted - draft_cjk)。main / _main_freestyle 捕获 → stderr [FATAL] → sys.exit(2)，
    使 cluster-write step6 fail-fast（坏章节零落盘）。
    """
    code = "SPLIT_WORD_NOT_CONSERVED"

    def __init__(self, message, delta=None):
        super().__init__(message)
        self.delta = delta


def _assert_word_conservation(report, draft_cjk, per_chapter_cjk, pending_tail_cjk,
                              chunks_for_empty_check, chapters_split):
    """落盘前字数守恒确定性自检。守护边界严限三条恒等（北极星⑤边界：
    绝不越界断言章数 N / 切点质量 / 叙事顺序 / 任何内容判断·只查 CJK 守恒 + 无空块 + 计数同步）：

      ① sum(per_chapter_cjk) + pending_tail_cjk == draft_cjk（CJK 精确整数·基线取 strip-title/
         prepend 后 draft_cjk·别把故意剥离的伪标题算丢字）
      ② 无空 chunk 落盘
      ③ len(chunks) == chapters_split == len(per_chapter_cjk)（切片与计数同步）

    任一破 → report['integrity'].conserved=False 后 raise SplitterIntegrityError。守恒则写
    integrity 段返回（report 增 integrity:{conserved, draft_cjk, accounted, pending_tail_cjk, delta}）。
    """
    accounted = sum(per_chapter_cjk) + pending_tail_cjk
    empty_chunk = any(not c.strip() for c in chunks_for_empty_check)
    chunk_count = len(chunks_for_empty_check)
    count_mismatch = (chunk_count != chapters_split) or (len(per_chapter_cjk) != chapters_split)
    conserved = (accounted == draft_cjk) and (not empty_chunk) and (not count_mismatch)
    report["integrity"] = {
        "conserved": conserved,
        "draft_cjk": draft_cjk,
        "accounted": accounted,
        "pending_tail_cjk": pending_tail_cjk,
        "delta": accounted - draft_cjk,
    }
    if not conserved:
        raise SplitterIntegrityError(
            f"SPLIT_WORD_NOT_CONSERVED · draft_cjk={draft_cjk} accounted={accounted} "
            f"delta={accounted - draft_cjk} empty_chunk={empty_chunk} count_mismatch={count_mismatch} "
            f"(chunks={chunk_count} chapters_split={chapters_split} per_chapter_cjk={len(per_chapter_cjk)})",
            delta=accounted - draft_cjk)
    return conserved


def _strip_pseudo_title(draft_text: str):
    """剥离 freestyle 草稿前几行的伪章标题（writer 锁字数路径吐出）。

    匹配「第N章」「第N章 标题」「第N章 <短标题>」等独行伪头·只扫前 3 个非空行(防误伤正文中段)。
    freestyle 草稿不该有章标题·splitter step 6 自己 gen_chapter_titles·任何前导伪头都是噪声。
    """
    lines = draft_text.split("\n")
    out = []
    scanned_nonblank = 0
    for ln in lines:
        s = ln.strip()
        if scanned_nonblank < 3:
            if s:
                scanned_nonblank += 1
                # 伪头：第N章 单独 / 第N章 标题 / 第N章 后接 ≤12 字短串
                if re.match(r"^第\d+章(\s*标题)?\s*$", s) or re.match(r"^第\d+章\s+\S{1,12}\s*$", s):
                    continue  # 丢弃此行
        out.append(ln)
    return "\n".join(out).lstrip("\n")


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
        # 纯 CJK 口径（与 anchor=draft_cjk*i/N、target、lo-hi 量纲对齐）——若混用 len(去空白)
        # 这种含标点/ascii 的口径，会与纯 CJK 锚点量纲不一致，导致切点系统性偏前（标点密集
        # 尤甚）。
        wc = cio.count_cjk(chunk)
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
# 角色名从 人物卡.json 动态加载（切点评分跨书通用，不依赖硬编码书名角色）。
# 默认空表；run_freestyle / main 开头调 _load_char_names(project_root) 覆盖此 module global。
# 加载失败/无项目 → 保持空表（score_split_point 角色名加分项为 0，退化到其他切点信号，不崩）。
CHAR_NAMES: list = []
DIALOGUE_OPEN = re.compile(r'["“「『]')   # 含弯引号 U+201C（splitter 切点不切对话中段）
DIALOGUE_CLOSE = re.compile(r'["”」』]')  # 补弯引号 U+201D
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


# ============ ecas_freestyle 模式 ============
#
# 切割规则（详见 novel-chapter-splitter.md）：
#   - splitter 按整 cluster 草稿字数自动计算章数
#     N = round(draft_cjk / 3500)，钳到 [ceil(draft_cjk/4500), floor(draft_cjk/3000)]
#   - 每章硬范围 3000-4500 CJK（rhythm_profile 微调）
#   - 末章 < 下限 → 不强切，末段退回 cluster_<key>_pending_tail.txt，本 cluster 只切 N-1 章
#   - 接 --previous-pending-tail <path> 时把上 cluster 的 pending_tail prepend 到草稿头部联合切
#   - 沿用 score_split_point 的最佳切点评分（每个等距锚点附近 ±tolerance 选最佳段落边界）

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


# splitter 不做 in_medias_res 倒叙重排。倒叙由 outline 的 scene_storyboard 顺序（scene0=倒叙
# 开场）+ writer 按序写负责，splitter 是纯格式层（北极星④：只按字数切，不理解叙事）。若在
# 这里再做一次 climax 中段提前重排，会与 writer 已经排好的倒叙叠加成「双重倒叙」，破坏
# 已经写好的开篇结构。


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


def _load_char_names(project_root) -> list:
    """从 人物卡.json（schema: {"characters":[{"name":...}]}）动态加载角色名 → 切点评分跨书通用。
    失败/缺文件 → 空表（不崩，退化到其他切点信号）。"""
    try:
        p = Path(project_root) / "_数据库" / "人物卡.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        names = []
        for c in (data.get("characters") or []):
            if isinstance(c, dict):
                nm = c.get("name") or c.get("姓名")
                if isinstance(nm, str) and 1 <= len(nm) <= 8:
                    names.append(nm)
        return names
    except Exception:
        return []


def run_freestyle(project_root, cluster_id, cluster_start_ch, draft_text,
                  rhythm_profile, previous_pending_tail, dry_run,
                  narrative_mode="linear", climax_hint=None):
    """ecas_freestyle 切割。返回 report dict。

    splitter 只按字数 linear 切（北极星④纯格式层），**不做任何倒叙重排**。倒叙由 outline 排
    scene_storyboard + writer 按序写负责。narrative_mode / climax_hint 仅写入决策日志留痕，
    不触发重排。
    """
    cluster_key = str(cluster_id).replace("cluster_", "")
    global CHAR_NAMES
    CHAR_NAMES = _load_char_names(project_root) or CHAR_NAMES   # 动态角色名（跨书通用切点评分）
    lo, hi, target = _resolve_rhythm(rhythm_profile)
    tolerance = 600  # freestyle 锚点搜索半径（给最佳切点更多空间）

    # 剥离 writer 锁字数路径吐出的伪标题头（如「第5章 标题」）。freestyle 草稿不应含章标题
    # （splitter 自己起标题）·任何前 3 行的「第N章[ 标题]」伪头都是 spurious·不剥会拼进首章
    # 正文第一屏，读者可见。
    draft_text = _strip_pseudo_title(draft_text)

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

    # 2.5 倒叙归属：倒叙由 outline 的 scene_storyboard 顺序（scene0 = 倒叙开场）+ writer 按序写
    # 共同负责——gen_writer prompt 只让它「按 scene_storyboard 自由发挥」，场景顺序即叙事顺序。
    # splitter 是纯格式层（北极星④：只按字数切、不理解叙事），**不做任何倒叙重排**：若在这里
    # 再做一次 climax 中段提前重排，会与 writer 已排好的倒叙叠加成「双重倒叙」，破坏开篇结构。
    # narrative_mode 仅留作痕迹，不触发任何重排。
    in_medias_res_reordered = False
    climax_idx_detected = None

    decision_log = {
        "rhythm_profile": rhythm_profile or "标准",
        "per_chapter_range": [lo, hi],
        "N_min": math.ceil(draft_cjk / hi) if draft_cjk >= lo else 0,
        "N_max": math.floor(draft_cjk / lo) if draft_cjk >= lo else 0,
        "N_recommend": max(1, round(draft_cjk / target)) if draft_cjk >= lo else 0,
        "N_final": N,
        # 倒叙参数仅留痕，不触发任何重排
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
        # N==0 全退 pending_tail 也守恒（accounted = pending_tail_cjk = draft_cjk）。
        _assert_word_conservation(report, draft_cjk, [], draft_cjk,
                                  chunks_for_empty_check=[], chapters_split=0)
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

    # 4.5 末章上溢再平衡：等距锚点+最佳切点会把边界压向前段，余量全堆末章。pending_tail 只防
    # 下溢，这里补上溢：末章 > hi 时把切点后移。若单个切点后移无路可退（比如倒数第二章已经
    # 贴近上限），从最后一个切点往前找第一个「后移一段不破 hi」的切点移动，空间波浪式前传，
    # 余量摊给所有有余量的前章。纯字数再平衡，不理解叙事（北极星④格式层）。
    while split_indices and per_chapter_cjk and per_chapter_cjk[-1] > hi:
        moved = False
        for j in range(len(split_indices) - 1, -1, -1):
            cand = split_indices[j] + 1
            if cand >= len(paras):
                continue
            if j + 1 < len(split_indices) and cand >= split_indices[j + 1]:
                continue               # 不可越过下一个切点
            trial = list(split_indices)
            trial[j] = cand
            tchunks = _slice_by_indices(paras, trial)
            # 后移切点 j 使第 j 章吃进一段——该章不得破上限
            if cio.count_cjk(tchunks[j]) > hi:
                continue
            split_indices = trial
            chunks = tchunks
            per_chapter_cjk = [cio.count_cjk(c) for c in chunks]
            moved = True
            break
        if not moved:
            break                      # 全部切点都挪不动·保持现状（advisory）

    # 5. 末章字数补料（末章不足下限退 pending_tail）
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

    # 末章 pending_tail 处理完、落盘前插确定性守恒自检（坏章节零落盘）。
    _pending_tail_cjk = cio.count_cjk(pending_tail_text) if pending_tail_text else 0
    _assert_word_conservation(report, draft_cjk, per_chapter_cjk, _pending_tail_cjk,
                              chunks_for_empty_check=chunks, chapters_split=chapters_split)

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
    # 产物落盘走原子写（内容口径：rstrip + 末尾单 \n）。
    atomic_write_text(p, text.rstrip() + "\n")
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

    # ---- ecas_freestyle 分发 ----
    if "--mode" in args and args[args.index("--mode") + 1:args.index("--mode") + 2] == ["ecas_freestyle"]:
        return _main_freestyle(args)

    print("[FATAL] 必须使用 --mode ecas_freestyle", file=sys.stderr)
    sys.exit(2)


def _main_freestyle(args):
    """ecas_freestyle CLI 入口。

    用法:
        python chapter_splitter.py <项目路径> --mode ecas_freestyle \\
            --cluster-id cluster_006 --cluster-start-ch 26 \\
            --draft <cluster_draft.txt 路径> \\
            [--rhythm 标准|紧凑|厚重|混合] \\
            [--narrative-mode linear|in_medias_res] \\
            [--climax-hint <scene_storyboard 下标>] \\
            [--previous-pending-tail <上 cluster pending_tail.txt>] \\
            [--dry-run]

    --narrative-mode / --climax-hint 仅作决策日志痕迹保留，**不触发任何倒叙重排**（splitter
    只按字数 linear 切 · 北极星④）。build_manifest.inject_event_cluster_context 注入的
    narrative_mode + climax_hint_scene_index 由 novel-chapter-splitter agent 透传到这两个参数。
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
    draft_text = dp.read_text(encoding="utf-8").rstrip()

    previous_pending_tail = None
    if prev_pending_path:
        pp = Path(prev_pending_path)
        if pp.is_file():
            previous_pending_tail = pp.read_text(encoding="utf-8")
        else:
            print(f"[WARN] --previous-pending-tail 指定但文件不存在: {prev_pending_path}", file=sys.stderr)

    # 守恒被破坏 → [FATAL] SPLIT_WORD_NOT_CONSERVED stderr → exit 2（step6 fail-fast·坏章节零落盘）。
    try:
        report = run_freestyle(project_root, cluster_id, cluster_start_ch, draft_text,
                               rhythm, previous_pending_tail, dry_run,
                               narrative_mode=narrative_mode, climax_hint=climax_hint)
    except SplitterIntegrityError as e:
        sys.stderr.write(f"[FATAL] {e.code}: {e}\n")
        sys.stderr.flush()
        sys.exit(2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
