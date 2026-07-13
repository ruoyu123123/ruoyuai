"""Attribute persistent cluster-level style failures to author-skill sections."""
from __future__ import annotations

import json
import os
from pathlib import Path

import learning_loop_store as store

SCRIPT_DIR = Path(__file__).resolve().parent
REFLECT_RECUR_THRESHOLD = 2
# Similarity below this floor has insufficient attribution evidence. 待金标准校准
SEMANTIC_ATTRIB_FLOOR = 0.30

STYLE_CODE_ATTRIB = {
    "STYLE_对话占比": (("对话占比", "对话量", "对话风格", "对话"), "对话占比"),
    "STYLE_段落均长": (("段长", "段落均长", "段落结构", "平均段长", "段落"), "平均段长"),
    "STYLE_单段超长": (("段长", "单段", "段落硬约束", "字数硬上限", "段落"), "单段长度上限"),
    "STYLE_长段计数": (("段长", "长段", "段落硬约束", "段落"), "长段计数"),
    "STYLE_极短段占比": (("极短段", "段长策略", "单句独行", "段落"), "极短段占比"),
    "STYLE_单句成段率": (("单句独行", "单句段", "单句成段", "段落"), "单句成段率"),
    "STYLE_单句独行占比": (("单句独行", "单句段", "段落"), "单句独行占比"),
    "STYLE_拟声格式": (("拟声", "拟声词独段", "拟声段", "战斗描写"), "拟声词独段"),
    "STYLE_禁用词": (("禁用词", "禁用", "AI 套话", "AI套话", "反 AI"), "禁用词"),
    "STYLE_配额词": (("限频", "配额词", "白名单", "禁用词"), "配额词限频"),
    "STYLE_AI对话标签": (("对话标签", "对话", "AI 套话", "AI腔"), "AI 对话标签"),
    "STYLE_逗句比": (("逗号", "逗句比", "长句", "句长", "句式节奏"), "逗号/句号比"),
    "STYLE_极长句": (("句长", "极长句", "长句", "句式节奏"), "极长句"),
    "STYLE_DRIFT": (("量化", "签名", "风格指纹", "节奏"), "整体作者文风"),
    "LONGRANGE_STYLE_DRIFT": (("量化", "签名", "风格指纹", "节奏"), "整体作者文风"),
}


def reflect_enabled() -> bool:
    value = (os.environ.get("LL_REFLECT_ATTRIB") or "").strip().lower()
    return value not in ("off", "0", "false", "no", "disable", "disabled")


def resolve_skill_path(project_root: Path) -> Path | None:
    style_path = store.db_dir(project_root) / "作者风格.json"
    if not style_path.is_file():
        return None
    try:
        style = json.loads(style_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = style.get("style_source") if isinstance(style, dict) else None
    if not isinstance(source, str) or not source:
        return None
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = SCRIPT_DIR.parent.parent / candidate
    return candidate if candidate.is_file() else None


def parse_skill_sections(skill_text: str) -> list[dict]:
    sections, current = [], None
    for line_number, raw in enumerate(skill_text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            if current is not None:
                sections.append(current)
            level = len(stripped) - len(stripped.lstrip("#"))
            current = {
                "heading": stripped.lstrip("#").strip(),
                "level": level,
                "line": line_number,
                "body_lines": [],
            }
        elif current is not None:
            current["body_lines"].append(raw)
    if current is not None:
        sections.append(current)
    for section in sections:
        section["body"] = "\n".join(section.pop("body_lines"))[:600]
    return sections


def has_real_embedding_backend() -> bool:
    """EMBED_BACKEND 非空且非 hash（本地 daemon/ruoyu_style/mstyle/local 链）→ True；
    未设或 =hash（默认 hash 袋·无真语义）→ False。"""
    backend = os.environ.get("EMBED_BACKEND", "").strip().lower()
    return bool(backend) and backend != "hash"


def semantic_attribute_to_skill_section(sections: list, keywords) -> dict | None:
    query = " ".join(str(keyword) for keyword in keywords if keyword).strip()
    if not query:
        return None
    try:
        from embedding_store import compute_embedding, cosine_similarity, prefetch_embeddings

        blobs = [
            (section.get("heading", "") + " " + section.get("body", "")).strip()
            for section in sections
        ]
        prefetch_embeddings([query] + blobs)
        query_embedding = compute_embedding(query)
    except Exception:
        return None
    if not query_embedding:
        return None
    best, best_similarity = None, 0.0
    for section, blob in zip(sections, blobs):
        if not blob:
            continue
        try:
            embedding = compute_embedding(blob)
        except Exception:
            continue
        if not embedding or len(embedding) != len(query_embedding):
            continue
        similarity = cosine_similarity(query_embedding, embedding)
        if similarity > best_similarity:
            best, best_similarity = section, similarity
    if best is None or best_similarity < SEMANTIC_ATTRIB_FLOOR:
        return None
    return {
        "heading": best["heading"],
        "level": best["level"],
        "line": best["line"],
        "score": round(best_similarity, 4),
        "excerpt": best["body"].strip()[:200],
        "method": "semantic",
    }


def attribute_to_skill_section(sections: list, keywords) -> dict | None:
    if not sections:
        return None
    if has_real_embedding_backend():
        semantic = semantic_attribute_to_skill_section(sections, keywords)
        if semantic is not None:
            return semantic
    best, best_score = None, 0
    for section in sections:
        heading = section.get("heading", "")
        body = section.get("body", "")
        score = sum(3 if keyword in heading else 0 for keyword in keywords)
        score += sum(1 if keyword in body else 0 for keyword in keywords)
        if score > best_score:
            best, best_score = section, score
    if best is None or best_score == 0:
        return None
    return {
        "heading": best["heading"],
        "level": best["level"],
        "line": best["line"],
        "score": best_score,
        "excerpt": best["body"].strip()[:200],
    }


def collect_live_drift_findings(project_root: Path) -> list[dict]:
    try:
        import cross_cluster_style_drift_scanner as scanner
        report = scanner.scan(Path(project_root))
    except Exception:
        return []
    if not isinstance(report, dict):
        return []
    findings = []
    for key in ("issues", "shadow_findings"):
        value = report.get(key)
        if isinstance(value, list):
            findings.extend(
                item for item in value
                if isinstance(item, dict) and item.get("code") in STYLE_CODE_ATTRIB
            )
    return findings


def collect_persistent_style_failures(experience: dict, drift_findings=None) -> dict:
    result = {}
    for key, record in experience.get("_recurrence_tracker", {}).items():
        code = key.split("::")[-1]
        if code not in STYLE_CODE_ATTRIB or record.get("count", 0) < REFLECT_RECUR_THRESHOLD:
            continue
        result[code] = {
            "clusters": store.sort_clusters(record.get("clusters", [])),
            "count": record.get("count", 0),
            "dimension": record.get("dimension", "风格"),
            "sample_desc": record.get("sample_desc", ""),
            "source": "recurrence",
        }
    for finding in drift_findings or []:
        if not isinstance(finding, dict):
            continue
        code = finding.get("code")
        if code not in STYLE_CODE_ATTRIB:
            continue
        metric = finding.get("metric") if isinstance(finding.get("metric"), dict) else {}
        result.setdefault(code, {
            "clusters": [],
            "count": metric.get("n_points", REFLECT_RECUR_THRESHOLD),
            "dimension": "风格",
            "sample_desc": (finding.get("message") or "")[:120],
            "source": "longrange_drift",
        })
    return result


def reflect_attribution(project_root: Path, drift_findings=None) -> list[dict]:
    if not reflect_enabled():
        return []
    if drift_findings is None:
        drift_findings = collect_live_drift_findings(project_root)
    experience = store.load_experience(project_root)
    failures = collect_persistent_style_failures(experience, drift_findings)
    if not failures:
        if experience["skill_rewrite_suggestions"]:
            experience["skill_rewrite_suggestions"] = []
            store.save_experience(project_root, experience)
        return []

    skill_path = resolve_skill_path(project_root)
    sections, skill_name = [], None
    if skill_path is not None:
        try:
            sections = parse_skill_sections(skill_path.read_text(encoding="utf-8"))
            skill_name = str(skill_path)
        except OSError:
            sections = []

    suggestions = []
    produced = []
    for code, evidence in failures.items():
        keywords, dimension = STYLE_CODE_ATTRIB[code]
        attribution = attribute_to_skill_section(sections, keywords) if sections else None
        cluster_label = evidence["clusters"] or "多个 cluster"
        if attribution is not None:
            location = f"skill 段落「{attribution['heading']}」（第 {attribution['line']} 行）"
            suggestion = (
                f"风格维度【{dimension}】在 {cluster_label} 持续偏离作者参考"
                f"（复发 {evidence['count']} 次）。建议具体化 {location} 的量化边界、反例和场景适配。"
            )
        else:
            suggestion = (
                f"风格维度【{dimension}】在 {cluster_label} 持续偏离作者参考"
                f"（复发 {evidence['count']} 次）。建议在 skill 中补充对应的量化边界、反例和场景适配。"
            )
        entry = {
            "style_code": code,
            "style_dimension": dimension,
            "evidence_source": evidence["source"],
            "source_clusters": list(evidence["clusters"]),
            "recurrence": evidence["count"],
            "sample_desc": evidence.get("sample_desc", "")[:120],
            "skill_path": skill_name,
            "attributed_section": attribution,
            "suggestion_type": "tighten_clause" if attribution is not None else "add_clause",
            "suggestion": suggestion,
            "gate_level": "advisory",
            "confidence": 0.8 if attribution is not None else 0.6,
            "updated_at": store.now_text(),
        }
        suggestions.append(entry)
        produced.append(entry)

    experience["skill_rewrite_suggestions"] = suggestions
    store.save_experience(project_root, experience)
    if produced:
        print(f"[reflect-attrib] {len(produced)} 类持续风格偏离已归因到作者 skill")
    return produced
