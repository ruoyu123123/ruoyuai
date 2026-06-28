"""writer_truth_check.py — Writer 自评真实性检测 + 回写故事块摘要（v17.5 / B2.1+B2.3+B0.3）

功能：
1. 读章节 txt 末尾的 ---CHANGES_SELF_EVAL--- 段，提取 writer 申报的 applied_style
2. 独立从正文识别 opening_type / ending_line / anchors 等
3. 对比申报 vs 独立提取 → 生成 truth_report
4. 把 applied_style 回写到 故事块摘要[ch].applied_style（B2.1）
5. 把 truth_report 写入 故事块摘要[ch].truth_check

用法：
    python writer_truth_check.py <项目路径> <章节号> [--write-back]
    python writer_truth_check.py <项目路径> --all-history [--write-back]

退出码：
    0  通过
    1  撒谎检测命中（writer 自评与正文不符）
    2  致命错误
"""

import sys
import json
import re
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


def load_json(p: Path, default=None):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def find_chapter_file(project_root: Path, ch: int) -> Path | None:
    """定位章节正文文件。v18：委托 chapter_io.find_body_file。"""
    return cio.find_body_file(project_root, ch)


# 独立识别开头/结尾 type 的简化规则（不依赖 writer 自评）

OPENING_TYPE_PATTERNS = [
    # tuple: (type_name, predicate_fn)
    ("拟声定格", lambda first_lines: bool(re.match(r"^[^\n]{1,12}——", first_lines.split("\n")[0]))
                                    or bool(re.search(r"^(咯|啪|嗒|哒|轰|咚|哗|砰|咳|噗|滋|嘎|吱|咔)——", first_lines))),
    ("纯对话开场", lambda first_lines: first_lines.lstrip().startswith(("\"", "“", "「"))),
    ("时间地点锚点", lambda first_lines: bool(re.match(r"^[^\n]{1,30}(?:点|时|刻|早晨|清晨|夜里|凌晨|下午|傍晚|黄昏)", first_lines))),
    ("人物内心吐槽", lambda first_lines: any(s in first_lines[:200] for s in ["他想", "他笑", "他骂", "她想", "呃……", "他妈"])),
    ("心理铺陈", lambda first_lines: any(s in first_lines[:200] for s in ["他记得", "他在想", "他做梦", "他不知"])),
    ("钩子回音式", lambda first_lines: bool(re.match(r"^[^\n]{1,25}(?:仍然|还在|依旧|又|再)", first_lines))),
    ("动作承接", lambda first_lines: bool(re.match(r"^[^\n]{1,30}(?:推|拉|按|抬|放|拿|走|跑|站|坐|蹲|睁|闭|握|抓|举|挥|甩|扔|扔|跳)", first_lines))),
    ("感官切入", lambda first_lines: any(s in first_lines[:100] for s in ["闻到", "听见", "感到", "触到", "看见"])),
    ("场景型", lambda first_lines: True),  # 兜底
]


def _strip_chapter_title(body: str) -> str:
    """去掉首行 '第NNN章 ...' 标题，返回正文。"""
    lines = body.split("\n")
    out = []
    skipped = False
    for ln in lines:
        if not skipped and re.match(r"^第\d+章", ln.strip()):
            skipped = True
            continue
        out.append(ln)
    return "\n".join(out).lstrip()


def identify_opening_type(body: str) -> str:
    cleaned = _strip_chapter_title(body)
    first_lines = cleaned[:300].strip()
    for type_name, pred in OPENING_TYPE_PATTERNS:
        try:
            if pred(first_lines):
                return type_name
        except Exception:
            continue
    return "场景型"


ENDING_TYPE_PATTERNS = [
    ("拟声硬收", lambda last_lines: bool(re.search(r"(咯|啪|嗒|哒|轰|咚|哗|砰|咳)——\s*$", last_lines.strip()))),
    ("动作留白", lambda last_lines: bool(re.search(r"(放|推|拉|按|举|抬|蹲|站|走|坐|看|闭|睁|握|垂)[^\n]{0,15}[。\.]\s*$", last_lines.strip()))),
    ("对话悬念", lambda last_lines: last_lines.rstrip().endswith(("\"", "”", "」", "？", "?"))),
    ("独立短句", lambda last_lines: len(last_lines.strip().split("\n")[-1]) <= 12),
    ("信息悬念", lambda last_lines: any(s in last_lines[-200:] for s in ["也许", "可能", "或许", "不知道", "不确定"])),
    ("信息炸弹", lambda last_lines: any(s in last_lines[-200:] for s in ["——", "："])),
    ("场景硬收", lambda last_lines: True),
]


def identify_ending_type(body: str) -> str:
    last_lines = body[-300:]
    for type_name, pred in ENDING_TYPE_PATTERNS:
        try:
            if pred(last_lines):
                return type_name
        except Exception:
            continue
    return "场景硬收"


def extract_first_line(body: str) -> str:
    cleaned = _strip_chapter_title(body)
    for line in cleaned.split("\n"):
        line = line.strip()
        if line:
            return line
    return ""


def extract_last_line(body: str) -> str:
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    return lines[-1] if lines else ""


# ============ 🔴 2026-06-27 SYS-3/C10：声明-vs-正文 字面证据匹配（宽松匹配器·全 advisory）============
#
# SYS-3：truth_check 原只比对 self_eval.applied_style 5 个风格维度，从不读 factual.foreshadowing_paid
# 校验申报兑现的伏笔是否正文留痕 → 声明 paid 而正文 0 落字时 lie=0（漏检）。新增字面 trace 维度。
# C10：扩 factual 段（与 applied_style 平行）——遍历 factual 四类（伏笔兑现/角色死亡/道具转移/秘密揭示）
# 跑宽松正文证据匹配器，写回 truth_check.factual_corroboration。
# 北极星护栏：匹配器宽松（多 token + 同义/指代容忍，复用 validate_chapter core_tokens 思路 + cluster
# 整草稿视野避免切章误判跨章兑现）；措辞不同字面不判违，只有完全无任何痕迹才算硬矛盾；不确定走 advisory。

_QUOTED_ANCHOR_RE = re.compile(r"[『「“]([^』」”\n]{2,20})[』」”]|【([^】\n]{2,20})】")


def _extract_anchors(text: str) -> tuple[list[str], list[str]]:
    """从声明描述抽锚词。返回 (strong, weak)：
      strong = 『』「」“”【】 括起的具体短语（高置信信物/专名/系统标记）
      weak   = CJK 片段 + 2/3 字滑窗（弱信号兜底·宽松）
    锚词只是『字面留痕探针』，措辞不同不强求；weak 用滑窗 n-gram 保证『护腕』能从『碎裂护腕从天而降』
    里被抽出（北极星：宁可宽松少判违·只有锚词全 0 命中才算无痕迹·shadow 阶段偏向避免假阳）。"""
    if not text:
        return [], []
    strong = []
    for m in _QUOTED_ANCHOR_RE.finditer(text):
        ph = (m.group(1) or m.group(2) or "").strip()
        if ph:
            strong.append(ph)
    weak = []
    for run in re.findall(r"[一-鿿]{2,}", text):
        weak.append(run[:6])  # 整段（截断防超长）
        for n in (3, 2):      # 3 字优先（更具体）后补 2 字滑窗
            for i in range(len(run) - n + 1):
                weak.append(run[i:i + n])

    def _dedup(xs):
        seen, out = set(), []
        for x in xs:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return _dedup(strong), _dedup(weak)[:24]


def _corroborate(claim_text: str, body: str, extra_text: str = "") -> dict:
    """宽松正文证据匹配。返回 {corroborated: True|False|"uncertain", anchors, evidence_span}。
      True       = 任一锚词命中正文（有字面痕迹·措辞不同不判违）
      False      = 有 strong 锚词（具体专名/信物）但全部 0 命中（完全无痕迹·硬矛盾域）
      "uncertain"= 只有弱锚词且全 0 命中 / 无可抽锚词（弱信号·走 advisory）"""
    strong, weak = _extract_anchors((claim_text or "") + " " + (extra_text or ""))
    anchors = strong + [w for w in weak if w not in strong]
    if not anchors:
        return {"corroborated": "uncertain", "anchors": [], "evidence_span": ""}
    hit = next((a for a in anchors if a in body), None)
    if hit is not None:
        idx = body.find(hit)
        span = body[max(0, idx - 12): idx + len(hit) + 12].replace("\n", " ")
        return {"corroborated": True, "anchors": anchors, "evidence_span": span}
    if strong:
        return {"corroborated": False, "anchors": anchors, "evidence_span": ""}
    return {"corroborated": "uncertain", "anchors": anchors, "evidence_span": ""}


_DEATH_WORDS = ("死", "亡", "牺牲", "陨落", "殒", "丧命", "毙", "dead", "died", "deceased")


def corroborate_factual(project_root: Path, changes: dict, body: str) -> dict:
    """C10 声明-vs-正文校验 + SYS-3 伏笔字面 trace。

    遍历 changes['factual'] 四类（伏笔兑现/角色死亡/道具转移/秘密揭示）跑宽松匹配器（对齐 cluster
    整草稿 body 视野，避免切章误判跨章兑现）。全 advisory：uncertain → FACTUAL_CLAIM_UNCORROBORATED
    （放本 report·可豁免）；零痕迹 False 归现有 hard_gate 域（STRUCTURE§11 既有码·不新增）。"""
    factual = (changes or {}).get("factual") or {}
    fs_tbl = load_json(Path(project_root) / "_数据库" / "伏笔表.json", {}) or {}
    fs_by_id: dict = {}
    for p in fs_tbl.get("promises", []) or []:
        if isinstance(p, dict) and p.get("id"):
            fs_by_id.setdefault(p["id"], p)
    for s in fs_tbl.get("secrets", []) or []:
        if isinstance(s, dict) and s.get("id"):
            fs_by_id.setdefault(s["id"], s)

    claims: list = []
    advisories: list = []
    foreshadowing_trace: list = []

    def _setup_desc(fid):
        sp = fs_by_id.get(fid) or {}
        return sp.get("description") or sp.get("desc") or sp.get("secret") or ""

    def _record(category, claim, res, fs_id=None):
        rec = {"category": category, "claim": (claim or "")[:80],
               "corroborated": res["corroborated"], "anchors": res["anchors"][:6],
               "evidence_span": res["evidence_span"]}
        if fs_id:
            rec["fs_id"] = fs_id
        claims.append(rec)
        if res["corroborated"] == "uncertain":
            advisories.append({"code": "FACTUAL_CLAIM_UNCORROBORATED", "category": category,
                               "fs_id": fs_id, "claim": (claim or "")[:80],
                               "anchors": res["anchors"][:6]})
        return rec

    # 1. 伏笔兑现（foreshadowing_paid）—— anchors 取 伏笔表 setup/planted desc + paid desc
    for it in factual.get("foreshadowing_paid", []) or []:
        if isinstance(it, dict):
            fid = it.get("id")
            desc = it.get("desc") or it.get("description") or ""
        else:
            fid, desc = None, str(it)
        res = _corroborate(desc, body, extra_text=_setup_desc(fid))
        _record("伏笔兑现", desc or (fid or ""), res, fs_id=fid)
        # SYS-3：锚词集非空且全部在 body 0 命中（对齐 foreshadower 零-grep 判据）→ no_trace（advisory）
        if res["anchors"] and not res["evidence_span"]:
            foreshadowing_trace.append({"field": "foreshadowing_paid_no_trace",
                                        "fs_id": fid, "anchors": res["anchors"][:6]})

    # 2. 角色死亡（character_deaths / deaths / character_changes 含死亡语义）
    _death_items = []
    for key in ("character_deaths", "deaths"):
        for it in factual.get(key, []) or []:
            _death_items.append(it)
    for cc in factual.get("character_changes", []) or []:
        if isinstance(cc, dict):
            blob = f"{cc.get('field','')}{cc.get('to','')}{cc.get('key_change','')}"
            if any(w in blob for w in _DEATH_WORDS):
                _death_items.append(cc)
    for it in _death_items:
        if isinstance(it, dict):
            name = it.get("name") or it.get("character") or ""
            descr = it.get("desc") or it.get("description") or it.get("key_change") or ""
        else:
            name, descr = str(it), ""
        res = _corroborate(name, body, extra_text=descr)
        _record("角色死亡", f"{name} {descr}".strip(), res)

    # 3. 道具转移（item_transfers）—— 检物件名 + 新持有者是否在正文留痕
    for t in factual.get("item_transfers", []) or []:
        if not isinstance(t, dict):
            continue
        item = t.get("item") or ""
        to = t.get("to") or ""
        res = _corroborate(item, body, extra_text=to)
        _record("道具转移", f"{item} → {to}".strip(" →"), res)

    # 4. 秘密揭示（foreshadowing_actions secret.reveal）—— anchors 取 伏笔表 secret desc + how
    for a in factual.get("foreshadowing_actions", []) or []:
        if not isinstance(a, dict):
            continue
        if a.get("category") == "secret" and a.get("type") == "reveal":
            fid = a.get("id")
            how = a.get("how") or a.get("description") or ""
            res = _corroborate(_setup_desc(fid), body, extra_text=how)
            _record("秘密揭示", how or (fid or ""), res, fs_id=fid)

    counts = {"true": 0, "false": 0, "uncertain": 0}
    for c in claims:
        v = c["corroborated"]
        counts[("true" if v is True else "false" if v is False else "uncertain")] += 1
    return {
        "claims": claims,
        "advisories": advisories,
        "foreshadowing_trace": foreshadowing_trace,
        "counts": counts,
        "uncorroborated_count": counts["uncertain"],
        "hard_miss_count": counts["false"],
    }


def truth_check_chapter(project_root: Path, ch: int) -> dict:
    """单章撒谎检测。v18：正文走 cio.read_body()，自评走 cio.read_changes()。"""
    if not find_chapter_file(project_root, ch) and not cio.changes_path(project_root, ch).is_file():
        return {"ch": ch, "error": f"找不到章节 txt"}

    body = cio.read_body(project_root, ch)
    changes = cio.read_changes(project_root, ch)
    factual = changes.get("factual") or {}
    self_eval = changes.get("self_eval") or {}

    # writer 自评（self_eval 段优先，回退 factual 根字段——兼容旧稿）
    applied_writer = self_eval.get("applied_style") or factual.get("applied_style") or {}
    declared_open = applied_writer.get("opening_type", "")
    declared_open_line = applied_writer.get("opening_line", "")
    declared_end = applied_writer.get("ending_type", "")
    declared_end_line = applied_writer.get("ending_line", "")
    declared_anchors = applied_writer.get("anchors_hit", [])

    # 独立提取
    actual_first = extract_first_line(body)
    actual_last = extract_last_line(body)
    detected_open_type = identify_opening_type(body)
    detected_end_type = identify_ending_type(body)

    # 锚点验证：每个 declared_anchor 是否真在正文出现
    anchors_truth = []
    for a in declared_anchors:
        appears = a in body
        anchors_truth.append({"anchor": a, "in_body": appears})

    # opening_line 真实性
    opening_line_match = (
        declared_open_line in actual_first[:200]
        or actual_first[:30] in declared_open_line[:30]
    ) if declared_open_line else None
    # ending_line 真实性
    ending_line_match = (
        declared_end_line in body[-300:]
        or actual_last[:30] in declared_end_line[:30]
    ) if declared_end_line else None

    # type 匹配（独立提取 == 自评）
    open_type_match = declared_open == detected_open_type if declared_open else None
    end_type_match = declared_end == detected_end_type if declared_end else None

    # 撒谎指数
    lies = []
    if opening_line_match is False:
        lies.append({"field": "opening_line", "declared": declared_open_line[:40], "actual": actual_first[:40]})
    if ending_line_match is False:
        lies.append({"field": "ending_line", "declared": declared_end_line[:40], "actual": actual_last[:40]})
    missing_anchors = [a["anchor"] for a in anchors_truth if not a["in_body"]]
    if missing_anchors:
        lies.append({"field": "anchors_hit", "missing": missing_anchors})

    # 🔴 2026-06-27 SYS-3/C10：声明-vs-正文 factual 段（advisory·不进 lies/lie_count）
    factual_corroboration = corroborate_factual(project_root, changes, body)

    return {
        "ch": ch,
        "declared_opening_type": declared_open,
        "detected_opening_type": detected_open_type,
        "opening_type_match": open_type_match,
        "declared_ending_type": declared_end,
        "detected_ending_type": detected_end_type,
        "ending_type_match": end_type_match,
        "opening_line_match": opening_line_match,
        "ending_line_match": ending_line_match,
        "anchors_truth": anchors_truth,
        "lies_detected": lies,
        "lie_count": len(lies),
        "writer_applied_style_raw": applied_writer,
        "factual_corroboration": factual_corroboration,
    }


def truth_check_cluster(project_root: Path, chapters: list[int]) -> dict:
    """🔴 2026-06-27 C11：cluster 级撒谎检测（消除逐章重复误报）。

    根因：split_cluster_changes v1 把整 cluster 的 self_eval.applied_style（opening_line /
    ending_line / anchors_hit 是 cluster 级一次性申报）平铺进每章 _changes.json。旧 truth_check
    逐章验：
      · opening_line —— chapters[1..N] 的实际首行是各自章首（≠cluster 开篇）→ 假 opening_line_match=False
      · ending_line  —— chapters[0..N-1] 的实际末行是各自章末（≠cluster 收束）→ 假 ending_line_match=False
      · anchors_hit  —— anchor 散落在 cluster 不同章，逐章只看本章 body → 大量假 missing
    cluster 级修法（语义对齐 writer 申报口径）：
      · opening 只对 chapters[0] body 验
      · ending  只对 chapters[-1] body 验
      · anchors 对全 cluster 拼接 body 验
    declared applied_style 取 chapters[0]（平铺后各章相同）。纯确定性，无 LLM。
    """
    chs = sorted({int(c) for c in chapters})
    if not chs:
        return {"chapters": [], "error": "空 chapter 列表", "lie_count": 0, "lies_detected": []}
    first_ch, last_ch = chs[0], chs[-1]

    # declared applied_style：cluster 平铺后各章相同 → 取 chapters[0]（self_eval 优先，兼容旧稿 factual）
    changes0 = cio.read_changes(project_root, first_ch)
    applied_writer = ((changes0.get("self_eval") or {}).get("applied_style")
                      or (changes0.get("factual") or {}).get("applied_style") or {})
    declared_open = applied_writer.get("opening_type", "")
    declared_open_line = applied_writer.get("opening_line", "")
    declared_end = applied_writer.get("ending_type", "")
    declared_end_line = applied_writer.get("ending_line", "")
    declared_anchors = applied_writer.get("anchors_hit", [])

    # bodies：opening→首章 / ending→末章 / anchors→全 cluster 拼接
    try:
        first_body = cio.read_body(project_root, first_ch)
    except FileNotFoundError:
        return {"chapters": chs, "error": f"找不到首章 ch{first_ch} txt", "lie_count": 0, "lies_detected": []}
    last_body = cio.read_body(project_root, last_ch) if last_ch != first_ch else first_body
    full_parts = []
    for c in chs:
        try:
            full_parts.append(cio.read_body(project_root, c))
        except FileNotFoundError:
            continue
    full_body = "\n".join(full_parts)

    actual_first = extract_first_line(first_body)
    actual_last = extract_last_line(last_body)
    detected_open_type = identify_opening_type(first_body)
    detected_end_type = identify_ending_type(last_body)

    # 锚点：对全 cluster 拼接 body 验（散落各章不再误报）
    anchors_truth = [{"anchor": a, "in_body": (a in full_body)} for a in declared_anchors]

    opening_line_match = (
        declared_open_line in actual_first[:200]
        or actual_first[:30] in declared_open_line[:30]
    ) if declared_open_line else None
    ending_line_match = (
        declared_end_line in last_body[-300:]
        or actual_last[:30] in declared_end_line[:30]
    ) if declared_end_line else None
    open_type_match = declared_open == detected_open_type if declared_open else None
    end_type_match = declared_end == detected_end_type if declared_end else None

    lies = []
    if opening_line_match is False:
        lies.append({"field": "opening_line", "declared": declared_open_line[:40], "actual": actual_first[:40]})
    if ending_line_match is False:
        lies.append({"field": "ending_line", "declared": declared_end_line[:40], "actual": actual_last[:40]})
    missing_anchors = [a["anchor"] for a in anchors_truth if not a["in_body"]]
    if missing_anchors:
        lies.append({"field": "anchors_hit", "missing": missing_anchors})

    # 🔴 2026-06-27 SYS-3/C10：声明-vs-正文 factual 段（cluster 整草稿视野·advisory·不进 lies）
    factual_corroboration = corroborate_factual(project_root, changes0, full_body)

    return {
        "chapters": chs,
        "first_ch": first_ch,
        "last_ch": last_ch,
        "factual_corroboration": factual_corroboration,
        "declared_opening_type": declared_open,
        "detected_opening_type": detected_open_type,
        "opening_type_match": open_type_match,
        "declared_ending_type": declared_end,
        "detected_ending_type": detected_end_type,
        "ending_type_match": end_type_match,
        "opening_line_match": opening_line_match,
        "ending_line_match": ending_line_match,
        "anchors_truth": anchors_truth,
        "lies_detected": lies,
        "lie_count": len(lies),
        "writer_applied_style_raw": applied_writer,
    }


def write_back_cluster(project_root: Path, report: dict):
    """🔴 2026-06-27 C11：把 cluster 级 truth_check 回写到 cluster 内每章记录。

    cluster 级 verdict（已消除逐章误报）写入各章 故事块摘要[ch].truth_check / applied_style，
    保持「每章记录都有 truth_check」的下游期望，同时杜绝假撒谎。复用单章 write_back 的
    cluster_summary_store.patch_chapter 落账机制（原子 + 深合并）。"""
    chs = report.get("chapters") or []
    for ch in chs:
        per_chapter = dict(report)
        per_chapter["ch"] = ch
        write_back(project_root, per_chapter)


def write_back(project_root: Path, report: dict):
    """B2.1 回写 applied_style + truth_check 到故事块摘要。

    2026-05-29 复审修复（L2）：v2 cluster 化后 故事块摘要.json = {clusters:[{chapters:{ch:rec}}]}，
    旧实现写顶层 chapters[] list 会与 v2 clusters[] 并存冲突（污染账本数据模型）。
    改走 cluster_summary_store.patch_chapter：用 cluster_lookup.ch_to_cluster_id 由章号反查
    所属 cluster，把 truth_check / applied_style 合并进 clusters[].chapters[ch] 章记录，
    原子写 + 深合并（不丢 builder 已写的其它预算字段）。

    cluster 反查不到（fluid 未回填 chapter_range）→ 不强写顶层 chapters[]（禁止污染 v2 账本），
    仅打印告警并跳过回写（truth_report 仍由 main 打印 / 退出码体现，不丢检测结论）。
    """
    from datetime import datetime as _dt

    ch = report["ch"]

    # 组装本章 truth_check patch
    chapter_patch: dict = {
        "truth_check": {
            "opening_type_match": report.get("opening_type_match"),
            "ending_type_match": report.get("ending_type_match"),
            "opening_line_match": report.get("opening_line_match"),
            "ending_line_match": report.get("ending_line_match"),
            "lie_count": report.get("lie_count"),
            "lies_detected": report.get("lies_detected"),
        },
        "_truth_check_last_by": "writer_truth_check",
        "_truth_check_at": _dt.now().isoformat(timespec="seconds"),
    }
    if report.get("writer_applied_style_raw"):
        chapter_patch["applied_style"] = report["writer_applied_style_raw"]
    # 🔴 2026-06-27 SYS-3/C10：声明-vs-正文校验结果回写（advisory·主代理据此裁决待补伏笔/脏账本）
    if report.get("factual_corroboration"):
        chapter_patch["factual_corroboration"] = report["factual_corroboration"]

    # 章号 → cluster_id（SC-5：禁止 f"cluster_{ch:03d}" 机械拼接，用 ch_to_cluster_id 反查）
    try:
        import cluster_lookup as _cl
        import cluster_summary_store as _css
    except Exception as e:  # 模块缺失 → 不污染账本，跳过回写
        print(f"[WARN] ch{ch} truth_check 回写跳过：cluster 工具不可用（{e}）", file=sys.stderr)
        return

    cluster_id = _cl.ch_to_cluster_id(project_root, ch)
    if not cluster_id:
        print(f"[WARN] ch{ch} truth_check 回写跳过：章号未落入任何 cluster 的 chapter_range"
              f"（fluid 未回填）· 不写顶层 chapters[] 以免污染 v2 账本", file=sys.stderr)
        return

    # 原子 + 深合并落账（cluster_summary_store 内部 with_file_lock 防并发丢更新）
    _css.patch_chapter(project_root, cluster_id, ch, chapter_patch)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    project_root = Path(args[0])
    write = "--write-back" in args

    # 🔴 2026-06-27 C11：cluster 级入口 `--cluster-chapters 1,2,3`（save_state._run_writer_truth_check
    # 调用）。opening 只验首章 / ending 只验末章 / anchors 验全拼接 body——消除平铺逐章误报。
    if "--cluster-chapters" in args:
        idx = args.index("--cluster-chapters")
        try:
            chs = [int(x) for x in args[idx + 1].split(",") if x.strip()]
        except (IndexError, ValueError):
            print("[FATAL] --cluster-chapters 需逗号分隔章号（如 1,2,3）", file=sys.stderr)
            sys.exit(2)
        report = truth_check_cluster(project_root, chs)
        if "error" in report and not report.get("chapters"):
            print(f"[cluster truth-check] {report['error']}")
            sys.exit(0)
        print(f"\n== cluster 撒谎检测 ch{report['first_ch']}-ch{report['last_ch']} ==")
        print(f"  opening_type: 自评={report['declared_opening_type']!r}  独立={report['detected_opening_type']!r}  match={report['opening_type_match']}（仅首章 ch{report['first_ch']}）")
        print(f"  ending_type:  自评={report['declared_ending_type']!r}  独立={report['detected_ending_type']!r}  match={report['ending_type_match']}（仅末章 ch{report['last_ch']}）")
        print(f"  opening_line match: {report['opening_line_match']}")
        print(f"  ending_line match: {report['ending_line_match']}")
        print(f"  anchors_hit（全 cluster 拼接验）: {[(a['anchor'], a['in_body']) for a in report['anchors_truth']]}")
        if report["lies_detected"]:
            print(f"  🔴 撒谎：{report['lie_count']} 条")
            for lie in report["lies_detected"]:
                print(f"    - {lie}")
        else:
            print(f"  ✅ 无撒谎")
        # 🔴 2026-06-27 SYS-3/C10：声明-vs-正文 factual 校验（advisory·不计撒谎/不改退出码）
        _fc = report.get("factual_corroboration") or {}
        _ft = _fc.get("foreshadowing_trace") or []
        _adv = _fc.get("advisories") or []
        if _ft:
            print(f"  🟡 SYS-3 申报兑现但正文 0 痕迹：{len(_ft)} 条 → {[t.get('fs_id') for t in _ft]}")
        if _adv:
            print(f"  🟡 C10 FACTUAL_CLAIM_UNCORROBORATED（弱信号·advisory）：{len(_adv)} 条")
        if write:
            write_back_cluster(project_root, report)
        print(f"\n[Total] cluster {len(report['chapters'])} 章 / 共 {report['lie_count']} 条撒谎")
        sys.exit(1 if report["lie_count"] > 0 else 0)

    if "--all-history" in args:
        # 扫描所有已写章节
        # 2026-05-29 复审修复（L2）：v2 账本 = {clusters:[{chapters:{ch:rec}}]}，
        # 顶层 chapters 已废弃。优先从 clusters[].chapters 拍平章号；旧顶层 chapters
        # （list/dict）仍兼容回退。
        summary = load_json(project_root / "_数据库" / "故事块摘要.json", {})
        chs: list[int] = []
        clusters = summary.get("clusters")
        if isinstance(clusters, list):
            for c in clusters:
                if not isinstance(c, dict):
                    continue
                for k in (c.get("chapters") or {}).keys():
                    try:
                        chs.append(int(k))
                    except (ValueError, TypeError):
                        pass
        if not chs:
            chapters = summary.get("chapters", [])
            if isinstance(chapters, dict):
                chs = [int(k) for k in chapters.keys()]
            else:
                chs = [c.get("ch") for c in chapters if isinstance(c, dict) and c.get("ch")]
        target_chs = sorted(set(c for c in chs if isinstance(c, int)))
    else:
        target_chs = [int(args[1])]

    total_lies = 0
    for ch in target_chs:
        report = truth_check_chapter(project_root, ch)
        if "error" in report:
            print(f"ch {ch}: {report['error']}")
            continue
        print(f"\n== ch {ch} 撒谎检测 ==")
        print(f"  opening_type: 自评={report['declared_opening_type']!r}  独立={report['detected_opening_type']!r}  match={report['opening_type_match']}")
        print(f"  ending_type:  自评={report['declared_ending_type']!r}  独立={report['detected_ending_type']!r}  match={report['ending_type_match']}")
        print(f"  opening_line match: {report['opening_line_match']}")
        print(f"  ending_line match: {report['ending_line_match']}")
        anchors_summary = [(a["anchor"], a["in_body"]) for a in report["anchors_truth"]]
        print(f"  anchors_hit: {anchors_summary}")
        if report["lies_detected"]:
            print(f"  🔴 撒谎：{report['lie_count']} 条")
            for lie in report["lies_detected"]:
                print(f"    - {lie}")
            total_lies += report["lie_count"]
        else:
            print(f"  ✅ 无撒谎")
        if write:
            write_back(project_root, report)

    print(f"\n[Total] {len(target_chs)} 章 / 共 {total_lies} 条撒谎")
    sys.exit(1 if total_lies > 0 else 0)


if __name__ == "__main__":
    # 🔴 2026-06-27：直接 CLI 调用时 Windows GBK 控制台编码不了 print 里的 ✅/🔴/🟡 emoji →
    # UnicodeEncodeError 崩（orchestrator 路径有 PYTHONIOENCODING=utf-8 掩盖·裸调用崩）。
    # 入口强制 UTF-8 输出（对齐 split_cluster_changes / judge_runner __main__）。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
