#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macguffin_entanglement_scanner.py — MacGuffin Entanglement Index
(advisory · cross-cluster · 2026-06-20 R9 W5 Batch-M · plot_devices_taxonomy)

【缺口】R9 联网调研 (Hitchcock MacGuffin definition + plot_devices_taxonomy):
MacGuffin = 推动情节的「核心驱动物」(可以是真实物件/抽象目标)·真正好的 MacGuffin
应该与主角 goal 紧绷·LLM 默认产「装饰性 MacGuffin」(出现但与 goal 脱钩)。此前
全系统:
  · 道具.json     声明物件
  · timeline_item_location 跟持有者
  · narrative_debt_ledger 跟伏笔
  · 【MacGuffin entanglement-with-goal 零检测】

本 scanner 补：跨 cluster MacGuffin Entanglement Index =
  entanglement_ratio = |S_m^goal| / |S_m|
其中:
  S_m       = 出现 MacGuffin 的 cluster 集合
  S_m^goal  = 既出现 MacGuffin 又出现主角 goal pursuit 信号的 cluster 集合
ratio < 0.4 → advisory MACGUFFIN_ORNAMENTAL (装饰性·与 goal 脱钩)

【做法 · 确定性抽取（默认零 LLM）· 真语义 embedding 可选】
  1. 读 _数据库/道具.json (扩 is_macguffin 声明位)
     items_list = [{"name", "is_macguffin": true/false, ...}]
     若所有 item.is_macguffin 都 False → skip (北极星②·作者未声明)
  2. 读 cluster_summary_reader 的 clusters 序列
  3. 物件出现判定 = 名字字面匹配(不变·MacGuffin 是具名实体·非模糊概念·不适合语义匹配)
  4. goal 关联判定：
     · 默认 = goal_pursuit 信号词典关键词共现(高确定性)：
       追查/夺回/护送/守护/为了/目的/任务/接近/锁定/找到/找回/查清 在 cluster 全文任意出现
     · 🔴 2026-07-01 EMBED_BACKEND 配置真后端时 = 物件出现的语境句(mention 前后窗口) 与
       goal-pursuit「原型语句」(上述信号词拼接) 的 embedding 余弦相似度 ≥ 阈值——比全文松散
       关键词共现更贴近 entanglement 本意(语境局部语义关联)·也能抓同义改写的 goal-pursuit
       表达(词典关键词抓不到的换词说法)·任一语境句命中即算该 cluster 关联
  5. 每 cluster 扫:
     - 道具命中: name 在 cluster 全文/摘要任意出现 → S_m += cluster
     - goal 命中(见上) → S_m^goal += cluster (含道具 且 含 goal 关联)
  6. ratio = |S_m^goal| / max(1, |S_m|)
  7. ratio < 0.4 且 |S_m| >= 2 → advisory MACGUFFIN_ORNAMENTAL

【依赖】embedding_store.compute_embedding() + cosine_similarity()（同 topic_drift_scanner 模式）。
  EMBED_BACKEND 未设（默认 hash·无真语义）→ 完全走关键词共现·per_macguffin.match_method="lexicon"。

【输出】
  _数据库/.cross_chapter_scan/macguffin_advisory_snapshot.json (供 build_manifest 注入)

【北极星② / ⑤ 顾问非法官】作者档第一权威 (is_macguffin 必须显式声明)·全 advisory·
code MACGUFFIN_ORNAMENTAL 绝不进 audit_hub.HARD_GATE_CODES。
env MACGUFFIN_ENTANGLEMENT_MODE: off / shadow(默认) / active。

用法：python macguffin_entanglement_scanner.py <project> [--last-n N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cluster_summary_reader as csr   # noqa: E402
except Exception:
    csr = None

ISSUE_CODE = "MACGUFFIN_ORNAMENTAL"
ENTANGLEMENT_FLOOR = 0.4
MACGUFFIN_GOAL_SIM_THRESHOLD = 0.5  # 语境句 vs goal-pursuit 原型语句余弦相似度阈值(seed·后续校准)
MACGUFFIN_MENTION_WINDOW = 60        # 语境句窗口：mention 前后各 N 字符

GOAL_PURSUIT_PATTERN = re.compile(
    r"追查|夺回|护送|守护|为了|目的|任务|接近|锁定|找到|找回|查清|"
    r"寻回|奔向|抢回|追踪|追寻|追击|阻止|抵达|获取|取得"
)


def _mode() -> str:
    m = (os.environ.get("MACGUFFIN_ENTANGLEMENT_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "active") else "shadow"


# ── 🔴 2026-07-01 真语义 embedding 可选路径（完全照抄 topic_drift_scanner 已验证的模式）───────
def _has_real_embedding_backend() -> bool:
    """跟 topic_drift_scanner._has_real_embedding_backend 判断逻辑完全一致（各文件各自留一份）。"""
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb and eb != "hash":
        return True
    for k in os.environ:
        if k.startswith("GEN_EMBED__"):
            return True
    return False


def _goal_pursuit_prototype_text() -> str:
    """goal-pursuit"原型语句"：由既有 GOAL_PURSUIT_PATTERN 关键词拼接（同源·避免两处维护漂移）。"""
    return "、".join(GOAL_PURSUIT_PATTERN.pattern.split("|"))


def _mention_context_windows(text: str, name_pat: str,
                             window: int = MACGUFFIN_MENTION_WINDOW) -> list:
    """抽取物件每次出现的语境窗口（mention 前后 window 字符的"语境句"简化实现）。"""
    windows = []
    for m in re.finditer(name_pat, text):
        windows.append(text[max(0, m.start() - window):min(len(text), m.end() + window)])
    return windows


def _semantic_goal_entangled(text: str, name_pat: str, compute_embedding, cosine_similarity,
                             goal_proto_emb: list) -> "bool | None":
    """语境句 vs goal-pursuit 原型语句余弦相似度替代关键词共现（≥1 语境句命中即算关联）。
    维度不一致/embedding 计算异常 → None（调用方兜底关键词共现·不半真半假）。"""
    windows = _mention_context_windows(text, name_pat)
    if not windows:
        return False
    try:
        for w in windows:
            w_emb = compute_embedding(w)
            if not w_emb or len(w_emb) != len(goal_proto_emb):
                return None
            if cosine_similarity(w_emb, goal_proto_emb) >= MACGUFFIN_GOAL_SIM_THRESHOLD:
                return True
    except Exception:
        return None
    return False


def _read_macguffins(project_root: Path) -> list:
    """读 道具.json items_list·过滤 is_macguffin=true·返回 [{name, ...}]"""
    p = project_root / "_数据库" / "道具.json"
    if not p.exists():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    items = obj.get("items") or []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("title") or it.get("id")
        if not isinstance(name, str) or not name.strip():
            continue
        if it.get("is_macguffin") is True:
            out.append({"name": name.strip(),
                        "id": it.get("id") or name.strip(),
                        "category": it.get("category")})
    return out


def _read_cluster_text(project_root: Path, cluster: dict) -> str:
    parts = []
    scope = cluster.get("scope_summary") or ""
    if isinstance(scope, str) and scope:
        parts.append(scope)
    chapters = cluster.get("chapters") or {}
    if isinstance(chapters, dict):
        for rec in chapters.values():
            if not isinstance(rec, dict):
                continue
            for fld in ("summary", "scene_summary", "title"):
                v = rec.get(fld)
                if isinstance(v, str) and v:
                    parts.append(v)
    if not parts:
        cluster_id = cluster.get("cluster_id") or ""
        if cluster_id:
            draft_dir = project_root / "章节" / f"{cluster_id}_draft"
            if draft_dir.exists():
                for f in sorted(draft_dir.glob("*.txt")):
                    try:
                        parts.append(f.read_text(encoding="utf-8"))
                    except OSError:
                        pass
    return "\n".join(parts)


def compute_entanglement(macguffins: list, clusters: list,
                         project_root: Path) -> dict:
    """计算每个 macguffin 的 entanglement_ratio.

    goal 关联判定：真语义后端可用 → 语境句 vs goal-pursuit 原型语句余弦相似度
    (match_method="semantic")；否则关键词共现 (match_method="lexicon"·原逻辑不变·零回归)。
    物件出现(S_m)判定不变——字面匹配(MacGuffin 是具名实体)。
    """
    per_mac = {}
    cluster_texts = []
    for c in clusters:
        cid = c.get("cluster_id") or "?"
        text = _read_cluster_text(project_root, c)
        cluster_texts.append((cid, text))

    use_semantic = False
    compute_embedding = cosine_similarity = None
    goal_proto_emb = None
    if _has_real_embedding_backend():
        try:
            from embedding_store import compute_embedding, cosine_similarity, prefetch_embeddings
        except Exception:
            compute_embedding = cosine_similarity = prefetch_embeddings = None
        if compute_embedding is not None:
            # 🔴 2026-07-03 Wave-4：先收集本次要 embed 的全部文本（goal 原型句 + 各
            # macguffin×cluster 命中的语境窗口）一次性 prefetch 灌缓存——下面
            # goal_proto_emb / _semantic_goal_entangled 的逐条 compute_embedding
            # 全部命中缓存（取代每个 mention 窗口各自触发一次后端 subprocess 调用）。
            proto_text = _goal_pursuit_prototype_text()
            prefetch_texts = [proto_text]
            for mac in macguffins:
                name_pat = re.escape(mac["name"])
                for _cid, text in cluster_texts:
                    if text and re.search(name_pat, text):
                        prefetch_texts.extend(_mention_context_windows(text, name_pat))
            try:
                prefetch_embeddings(prefetch_texts)
                goal_proto_emb = compute_embedding(proto_text)
            except Exception:
                goal_proto_emb = None
            use_semantic = bool(goal_proto_emb)

    for mac in macguffins:
        name = mac["name"]
        name_pat = re.escape(name)
        S_m = []
        S_m_goal = []
        for cid, text in cluster_texts:
            if not text:
                continue
            if re.search(name_pat, text):
                S_m.append(cid)
                goal_hit = None
                if use_semantic:
                    goal_hit = _semantic_goal_entangled(
                        text, name_pat, compute_embedding, cosine_similarity, goal_proto_emb)
                if goal_hit is None:
                    goal_hit = bool(GOAL_PURSUIT_PATTERN.search(text))
                if goal_hit:
                    S_m_goal.append(cid)
        if not S_m:
            ratio = None
        else:
            ratio = round(len(S_m_goal) / len(S_m), 3)
        per_mac[name] = {
            "name": name,
            "id": mac.get("id"),
            "S_m": S_m,
            "S_m_goal": S_m_goal,
            "entanglement_ratio": ratio,
            "appearance_clusters": len(S_m),
            "match_method": "semantic" if use_semantic else "lexicon",
        }
    return per_mac


def emit_findings(per_mac: dict) -> list:
    findings = []
    ornamental = []
    for name, info in per_mac.items():
        ratio = info["entanglement_ratio"]
        if ratio is None:
            continue
        if info["appearance_clusters"] < 2:
            continue  # 单 cluster 出现不判 (尚未循环铺线)
        if ratio < ENTANGLEMENT_FLOOR:
            ornamental.append(info)
    if ornamental:
        findings.append({
            "severity": "advisory",
            "code": ISSUE_CODE,
            "count": len(ornamental),
            "samples": [{"name": o["name"], "ratio": o["entanglement_ratio"],
                         "S_m": o["S_m"], "S_m_goal": o["S_m_goal"]}
                        for o in ornamental[:6]],
            "suggestion": (
                f"{len(ornamental)} 个 MacGuffin entanglement_ratio < {ENTANGLEMENT_FLOOR}"
                f"·与主角 goal 脱钩=装饰性·建议下个 cluster 把 MacGuffin 写入 goal pursuit "
                f"动作 (追查/夺回/守护)"),
        })
    return findings


def main():
    ap = argparse.ArgumentParser(
        description="MacGuffin Entanglement Index 跨 cluster (advisory · cross-cluster)")
    ap.add_argument("project")
    ap.add_argument("--last-n", type=int, default=None)
    args = ap.parse_args()

    mode = _mode()
    out = {
        "scanner": "macguffin_entanglement",
        "schema_version": "1.0",
        "mode": mode,
        "code": ISSUE_CODE,
        "gate_level": "advisory",
        "verdict": "PASS",
        "findings": [],
    }
    project_root = Path(args.project).resolve()
    if mode == "off":
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    macguffins = _read_macguffins(project_root)
    out["macguffin_count"] = len(macguffins)
    if not macguffins:
        out["note"] = "无 is_macguffin=true 声明·跳过 (北极星②·作者未声明)"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    if csr is None:
        out["note"] = "cluster_summary_reader 不可用·跳过"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    try:
        clusters = csr.get_clusters(project_root, last_n=args.last_n)
    except Exception as e:
        out["note"] = f"读取 clusters 失败:{str(e)[:120]}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    if not clusters or len(clusters) < 2:
        out["note"] = f"cluster 数太少 ({len(clusters) if clusters else 0} < 2)"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.exit(0)

    per_mac = compute_entanglement(macguffins, clusters, project_root)
    out["per_macguffin"] = per_mac
    # 顶层 match_method：取任一 macguffin 的实际判定结果（真实反映本次运行是否落到语义路径）
    out["match_method"] = next(iter(per_mac.values()))["match_method"] if per_mac else "lexicon"

    findings = emit_findings(per_mac) if mode == "active" else []
    out["findings"] = findings
    if findings:
        out["verdict"] = "FAIL_MINOR"
        out["warning"] = findings[0]["suggestion"]

    # 写 cross_chapter_scan snapshot
    snap_dir = project_root / "_数据库" / ".cross_chapter_scan"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "scan_type": "macguffin_entanglement",
        "scan_ts": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "mode": mode,
        "n_clusters": len(clusters),
        "match_method": out["match_method"],
        "per_macguffin": per_mac,
        "findings": findings,
        "advisory_codes": sorted({f["code"] for f in findings}),
    }
    try:
        (snap_dir / "macguffin_advisory_snapshot.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[WARN] macguffin snapshot 写失败:{e}", file=sys.stderr)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1 if out.get("warning") else 0)


if __name__ == "__main__":
    main()
