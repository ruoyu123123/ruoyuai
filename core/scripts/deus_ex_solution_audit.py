#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deus_ex_solution_audit.py — Deus Ex Solution Audit（advisory · cluster）

【缺口】Aristotle《Poetics》deus ex machina 概念 + Narrative Debt Ledger 对偶：
  · cross_cluster_narrative_debt_ledger 查【present-debt → future-payoff】方向
    （伏笔埋了未还=债·哨兵 BOOK_MORTGAGE_ABSENT/VOLUME_TAIL_RUNAWAY/VOLUME_OVERSHOOT）
  · 【present-payoff → past-anchors 方向】——卷末/cluster finale 的解决方案
    是否有前置铺垫？还是天上掉下来的 deus ex machina？本 scanner 补这一端的检测。
  本 scanner：finale cluster 触发·抽 resolution（角色/道具/能力/外力）·
  回查前置 anchors_count < 2 → advisory DEUS_EX_SOLUTION·走向卡反馈下卷 seeding 修复。

【做法 · 确定性纯规则正则（不依赖 LLM·后续可挂 resolution_agent LLM 占位）】：
  1. 触发门控：仅 is_volume_finale=True（manifest 或 cluster_index）才跑·非 finale → skip。
  2. 抽 resolution：草稿末段 30% 范围内·检测解决方案模式
     · 角色解决（"X 出手/X 挺身/X 救了"）
     · 道具解决（"取出 X/X 显神威"·配 cluster 道具.json 已知道具名）
     · 能力解决（"突破/觉醒/X 之力暴涨"）
     · 外力解决（"突然 X/不料 X/没想到 X 出现"·deus ex 高风险锚词）
  3. 回查前置 anchors：扫整 cluster + 历史 cluster_summary 中 resolution 关键元素出现次数。
     anchors_count < 2 → 没铺垫 → advisory。
  4. 外力锚词（突然/不料/没想到/天降+解决动作）单独高权重：本身就是 deus ex 候选信号。

【与 Narrative Debt Ledger 严格正交】Narrative Debt Ledger = 前向（promise→payoff·伏笔账本）·
  本 = 反向（resolution→setup·解决方案审计）·两者完全对偶。

【北极星② / ⑤ 顾问非法官】爆款修真/无脑爽流主角后期觉醒突破收尾是合理风格选择·
  永远 advisory，code DEUS_EX_SOLUTION **绝不进 HARD_GATE_CODES**。
  env DEUS_EX_AUDIT_MODE: off / shadow(默认) / active。

用法：python deus_ex_solution_audit.py <draft_path> [--project <root>] [--manifest <m>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

ISSUE_CODE = "DEUS_EX_SOLUTION"   # ⚠️ advisory · 绝不进 HARD_GATE_CODES

# Resolution 模式锚词
# 角色解决：name 段须独立词（前面是非 CJK 边界·或句首·常见标点）
_NAME_BOUNDARY = r"(?:^|[\s，。、；：！？.,;:!?「」“”'""\"\n（）()])"
CHAR_RESOLUTION = re.compile(
    _NAME_BOUNDARY + r"([一-鿿]{2,3})(?:出手|挺身|救了|斩了|打倒|击败|赶到)"
)
# 道具解决：取出/拿出 + 2-3 CJK 物名（非贪婪·允许后接任何上下文·名词偏短防贪噬全句）
ITEM_RESOLUTION = re.compile(
    r"(?:取出|拿出)([一-鿿]{2,3})|"
    r"([一-鿿]{2,4})(?:显神威|光华暴涨)"
)
POWER_RESOLUTION = re.compile(r"(突破|觉醒|顿悟|大彻大悟|血脉觉醒|境界暴涨|实力暴涨|得道)")
# 高风险 deus ex 外力锚词（无前置铺垫概率极高）
EXTERNAL_DEUS_EX = re.compile(r"(突然|不料|没想到|忽然|猛地|蓦地|天降|从天而降|及时赶到|"
                              r"恰好出现|刚好遇到|这时|就在此刻)")
RESOLUTION_TRIGGER = re.compile(r"(救|斩|破|杀|败|胜|解决|平息|压制)")

_CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")

MIN_CJK = 500
TAIL_RATIO = 0.3   # finale 草稿末段 30% 为 resolution 区
ANCHOR_FLOOR = 2   # < 2 个前置 anchors → 报
SEMANTIC_ANCHOR_SIM_THRESHOLD = 0.52   # element+tail 上下文 vs 历史段落余弦阈值
# 金标准校准依据：content_embed_separability_20260704 报告 neg_p95=0.5165/Youden=0.4904
ANCHOR_CONTEXT_WINDOW = 80   # element 在 tail 中出现位置前后各取 N 字符当语境（同 macguffin_entanglement_scanner 模式）


def _mode() -> str:
    m = (os.environ.get("DEUS_EX_AUDIT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ── 内容语义 embedding 路径（bge 内容模型，非风格模型）───────
def _content_backend_ready() -> bool:
    """内容语义后端可用性门控（委托 embedding_store.content_backend_available()）。

    import 失败 → False（调用方回退纯字面子串计数）。
    """
    try:
        from embedding_store import content_backend_available
        return content_backend_available()
    except Exception:
        return False


def _split_history_paragraphs(history_text: str) -> list:
    """history_text 按换行/句号切段（比整段拼接更细粒度·适合语义扫描）。"""
    return [p.strip() for p in re.split(r"[\n。]", history_text or "") if len(p.strip()) >= 6]


def _embed_history_once(history_text: str) -> "list[tuple[str, list]] | None":
    """内容后端就绪时把历史段落编码一次，供本次 audit_deus_ex() 内所有 resolution
    element 复用（避免 O(元素 × 段落) 重复编码）。

    内容后端不可用 / 无历史段落 / 编码异常 → None（调用方逐元素回退纯字面子串计数）。
    """
    if not _content_backend_ready():
        return None
    paras = _split_history_paragraphs(history_text)
    if not paras:
        return None
    try:
        from embedding_store import compute_content_embedding
        out = []
        for p in paras:
            pe = compute_content_embedding(p)
            if pe:
                out.append((p, pe))
        return out or None
    except Exception:
        return None


def _anchor_query_text(element: str, tail_context: str) -> str:
    """element 在 tail_context 中的语境窗口查询文本（_semantic_anchor_hit 判定用·也供
    audit_deus_ex 批量 prefetch 收集复用，避免两处重复算 window）。

    语境窗口取 element 在 tail_context 中实际出现位置前后 ANCHOR_CONTEXT_WINDOW 字符
    （而非整段 tail 头部截断——tail 可能长达数千字，resolution 短语可能出现在任意位置，
    头部截断会把它截没）；element 定位不到 → 退回 tail_context 头部截断兜底。
    """
    idx = tail_context.find(element)
    if idx >= 0:
        lo = max(0, idx - ANCHOR_CONTEXT_WINDOW)
        hi = min(len(tail_context), idx + len(element) + ANCHOR_CONTEXT_WINDOW)
        window = tail_context[lo:hi]
    else:
        window = tail_context[:200]
    return f"{element} {window}".strip()


def _semantic_anchor_hit(element: str, tail_context: str, history_embs) -> bool:
    """内容后端下：element（+tail 语境窗口）与历史段落语义扫描 —— 命中 → 视为已有语义铺垫
    （意译/概念性铺垫 · 字面子串扫不出）。

    history_embs 为 None（无内容后端/无历史）/ element 空 / 计算异常 → False
    （调用方回退字面子串计数 · _count_anchors_for_element 仍是兜底）。
    """
    if not history_embs or not element:
        return False
    try:
        from embedding_store import compute_content_embedding, cosine_similarity
        query = _anchor_query_text(element, tail_context)
        qe = compute_content_embedding(query)
        if not qe:
            return False
        for _p, pe in history_embs:
            if len(pe) == len(qe) and cosine_similarity(qe, pe) >= SEMANTIC_ANCHOR_SIM_THRESHOLD:
                return True
    except Exception:
        return False
    return False


def _strip_changes(text: str) -> str:
    for sep in _CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text


def _cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _read_is_volume_finale(manifest_path, project_root):
    """读 is_volume_finale 标志。manifest 优先·再退 cluster_index。"""
    if manifest_path:
        try:
            obj = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                v = obj.get("is_volume_finale")
                if v is not None:
                    return bool(v)
                # 兼容嵌套
                cluster = obj.get("cluster") or {}
                if isinstance(cluster, dict) and "is_volume_finale" in cluster:
                    return bool(cluster["is_volume_finale"])
        except (json.JSONDecodeError, OSError):
            pass
    return False


def _read_historical_text(project_root):
    """读历史 cluster_summary 拼接·用作前置 anchors 检索语料。"""
    if not project_root:
        return ""
    db = Path(project_root) / "_数据库"
    pieces = []
    summary_path = db / "故事块摘要.json"
    if summary_path.exists():
        try:
            obj = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                for cid, cdata in obj.items():
                    if not isinstance(cdata, dict):
                        continue
                    for k in ("summary", "scope_summary", "key_events"):
                        v = cdata.get(k)
                        if isinstance(v, str):
                            pieces.append(v)
                        elif isinstance(v, list):
                            pieces.extend(s for s in v if isinstance(s, str))
            elif isinstance(obj, list):
                for cdata in obj:
                    if isinstance(cdata, dict):
                        for k in ("summary", "scope_summary"):
                            v = cdata.get(k)
                            if isinstance(v, str):
                                pieces.append(v)
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(pieces)


def _extract_resolution_elements(tail_text):
    """从 finale 草稿末段抽 resolution 元素（角色/道具/能力/外力）。

    返回 {char_names: [...], item_names: [...], power_hits: [...],
          external_deus_hits: [...]}。
    """
    chars = [m.group(1) for m in CHAR_RESOLUTION.finditer(tail_text)
             if RESOLUTION_TRIGGER.search(tail_text[max(0, m.start() - 30):m.end() + 30])]
    items = []
    for m in ITEM_RESOLUTION.finditer(tail_text):
        for g in m.groups():
            if g:
                items.append(g)
    powers = POWER_RESOLUTION.findall(tail_text)
    externals = EXTERNAL_DEUS_EX.findall(tail_text)
    return {
        "char_names": list(dict.fromkeys(chars))[:6],
        "item_names": list(dict.fromkeys(items))[:6],
        "power_hits": list(dict.fromkeys(powers))[:6],
        "external_deus_hits": list(dict.fromkeys(externals))[:6],
    }


def _count_anchors_for_element(element: str, body_text: str, history_text: str) -> int:
    """回查前置 anchors_count：身体（cluster body 除末段）+ history 中 element 出现次数。"""
    if not element:
        return 0
    pat = re.escape(element)
    return len(re.findall(pat, body_text)) + len(re.findall(pat, history_text))


def audit_deus_ex(text: str, history_text: str = "") -> dict:
    """Deus Ex Solution Audit。

    返回 {resolution_elements, anchors_per_element, anchor_source_per_element,
          underbacked_count, deus_ex_risk, external_deus_hits_count}。

    anchors_per_element 计数：字面子串是兜底（_count_anchors_for_element）。
    内容后端就绪时叠加语义扫描历史摘要（tail resolution 段 vs 历史 cluster 摘要余弦）——
    字面不足 ANCHOR_FLOOR 但语义命中 → 视为已达标铺垫（意译/概念性铺垫漏检）。
    anchor_source_per_element 标注每个 element 最终判定用的是 "literal_substring" 还是
    "embedding_cosine"。
    """
    text = _strip_changes(text)
    n = len(text)
    if n < 200:
        return {"note": "草稿太短·跳过"}
    tail_start = int(n * (1.0 - TAIL_RATIO))
    body = text[:tail_start]
    tail = text[tail_start:]
    elements = _extract_resolution_elements(tail)
    all_elements = (elements["char_names"] + elements["item_names"] +
                    elements["power_hits"])

    # 历史段落 + 每个 resolution 元素的语境窗口查询——两侧文本
    # 集合一次性 prefetch（内容后端子进程按条调用极贵·合并成一次批调用），下面
    # _embed_history_once / _semantic_anchor_hit 内的逐条 compute_content_embedding 全部命中缓存。
    if _content_backend_ready():
        try:
            from embedding_store import prefetch_content_embeddings
            prefetch_content_embeddings(
                _split_history_paragraphs(history_text) +
                [_anchor_query_text(el, tail) for el in all_elements])
        except Exception:
            pass

    history_embs = _embed_history_once(history_text)
    anchors = {}
    anchor_source = {}
    for el in all_elements:
        lit = _count_anchors_for_element(el, body, history_text)
        if lit >= ANCHOR_FLOOR:
            anchors[el] = lit
            anchor_source[el] = "literal_substring"
            continue
        if _semantic_anchor_hit(el, tail, history_embs):
            anchors[el] = ANCHOR_FLOOR   # 语义命中 → 视为已达标铺垫（不虚报精确计数）
            anchor_source[el] = "embedding_cosine"
        else:
            anchors[el] = lit
            anchor_source[el] = "literal_substring"
    underbacked = [el for el, c in anchors.items() if c < ANCHOR_FLOOR]
    external_count = len(elements["external_deus_hits"])
    return {
        "resolution_elements": elements,
        "anchors_per_element": anchors,
        "anchor_source_per_element": anchor_source,
        "underbacked_count": len(underbacked),
        "underbacked": underbacked[:6],
        "external_deus_hits_count": external_count,
        "deus_ex_risk": (len(underbacked) > 0 and len(all_elements) > 0) or external_count >= 3,
    }


def scan(draft_path, project_root=None, manifest_path=None) -> dict:
    """Deus Ex Solution Audit。永远 advisory（北极星②/⑤）。"""
    mode = _mode()
    out = {"scanner": "deus_ex_solution_audit", "schema_version": "1.0", "mode": mode,
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
    if cjk < MIN_CJK:
        out["note"] = "草稿太短·跳过"
        return out

    is_finale = _read_is_volume_finale(manifest_path, project_root)
    out["is_volume_finale"] = is_finale
    if not is_finale:
        out["note"] = "非 finale cluster·跳过（仅 is_volume_finale 触发）"
        return out

    history = _read_historical_text(project_root)
    result = audit_deus_ex(draft, history)
    if "note" in result:
        out["note"] = result["note"]
        return out

    out["resolution_elements"] = result["resolution_elements"]
    out["anchors_per_element"] = result["anchors_per_element"]
    out["anchor_source_per_element"] = result.get("anchor_source_per_element", {})
    out["underbacked"] = result["underbacked"]
    out["external_deus_hits_count"] = result["external_deus_hits_count"]

    if result["deus_ex_risk"]:
        underbacked = result["underbacked"]
        ext = result["external_deus_hits_count"]
        msg = (f"Deus Ex Solution 风险：finale 解决方案前置 anchors 不足"
               f"（{result['underbacked_count']} 元素 anchors_count < {ANCHOR_FLOOR}"
               f"·外力锚词 {ext} 处）。建议下卷 seeding 修复·"
               f"为关键 resolution 元素（{', '.join(underbacked[:3])}...）"
               f"提前埋 ≥ 2 处铺垫·避免天降解。")
        if mode == "active":
            out["violations"].append({
                "kind": "deus_ex_solution", "severity": "minor",
                "message": msg,
                "underbacked": underbacked,
                "external_deus_hits": result["resolution_elements"]["external_deus_hits"],
                "_doc": "Aristotle Poetics deus ex machina + Narrative Debt 对偶·"
                        "present-payoff→past-anchors 方向·爆款修真后期觉醒突破可豁免→advisory"})
            out["verdict"] = "FAIL_MINOR"
            out["warning"] = msg
        else:
            print(f"[SHADOW] deus_ex_solution_audit: {msg} — 不上报", file=sys.stderr)
    out["violations_count"] = len(out["violations"])
    return out


def main():
    ap = argparse.ArgumentParser(description="Deus Ex Solution Audit (advisory · finale)")
    ap.add_argument("draft_path", help="cluster 草稿路径")
    ap.add_argument("--project", default=None)
    ap.add_argument("--manifest", default=None, help="读 is_volume_finale 触发标志")
    args = ap.parse_args()
    report = scan(args.draft_path, args.project, args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if report.get("warning") else 0)


if __name__ == "__main__":
    main()
