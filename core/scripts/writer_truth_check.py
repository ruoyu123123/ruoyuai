"""核对 writer 的 cluster 创作自评并产出 JudgeReport。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atomic_json
import chapter_io as cio
import cluster_lookup


class WriterTruthError(ValueError):
    """truth-check 输入合同破损。"""


OPENING_TYPE_PATTERNS = (
    ("拟声定格", lambda text: bool(re.match(r"^[^\n]{1,12}——", text.split("\n")[0]))
     or bool(re.search(r"^(咯|啪|嗒|哒|轰|咚|哗|砰|咳|噗|滋|嘎|吱|咔)——", text))),
    ("纯对话开场", lambda text: text.lstrip().startswith(('"', "“", "「"))),
    ("时间地点锚点", lambda text: bool(re.match(
        r"^[^\n]{1,30}(?:点|时|刻|早晨|清晨|夜里|凌晨|下午|傍晚|黄昏)", text))),
    ("人物内心吐槽", lambda text: any(token in text[:200] for token in
     ("他想", "他笑", "他骂", "她想", "呃……", "他妈"))),
    ("心理铺陈", lambda text: any(token in text[:200] for token in
     ("他记得", "他在想", "他做梦", "他不知"))),
    ("钩子回音式", lambda text: bool(re.match(r"^[^\n]{1,25}(?:仍然|还在|依旧|又|再)", text))),
    ("动作承接", lambda text: bool(re.match(
        r"^[^\n]{1,30}(?:推|拉|按|抬|放|拿|走|跑|站|坐|蹲|睁|闭|握|抓|举|挥|甩|扔|跳)", text))),
    ("感官切入", lambda text: any(token in text[:100] for token in
     ("闻到", "听见", "感到", "触到", "看见"))),
    ("场景型", lambda _text: True),
)

ENDING_TYPE_PATTERNS = (
    ("拟声硬收", lambda text: bool(re.search(
        r"(咯|啪|嗒|哒|轰|咚|哗|砰|咳)——\s*$", text.strip()))),
    ("动作留白", lambda text: bool(re.search(
        r"(放|推|拉|按|举|抬|蹲|站|走|坐|看|闭|睁|握|垂)[^\n]{0,15}[。\.]\s*$",
        text.strip()))),
    ("对话悬念", lambda text: text.rstrip().endswith(('"', "”", "」", "？", "?"))),
    ("独立短句", lambda text: 0 < len(extract_last_line(text)) <= 18),
    ("信息炸弹", lambda text: bool(re.search(r"(?:——|：|:)\s*[^\n]{2,30}[。！？!?]?\s*$", text))),
    ("场景硬收", lambda _text: True),
)


def _canonical_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not cluster_id:
        raise WriterTruthError(f"非法 cluster_id: {value!r}")
    return cluster_id


def _read_utf8(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise WriterTruthError(f"必需输入不存在: {path}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise WriterTruthError(f"输入必须是 UTF-8 无 BOM: {path}")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WriterTruthError(f"输入不是合法 UTF-8: {path}: {exc}") from exc


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(_read_utf8(path))
    except json.JSONDecodeError as exc:
        raise WriterTruthError(f"JSON 损坏: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WriterTruthError(f"JSON 顶层必须是 object: {path}")
    return value


def extract_first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def extract_last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def identify_opening_type(text: str) -> str:
    return next(name for name, predicate in OPENING_TYPE_PATTERNS if predicate(text.lstrip()))


def identify_ending_type(text: str) -> str:
    return next(name for name, predicate in ENDING_TYPE_PATTERNS if predicate(text))


def _evaluate(cluster_id: str, body: str, changes: dict) -> dict:
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        raise WriterTruthError("changes.self_eval 必须是 object")
    applied = self_eval.get("applied_style")
    applied = applied if isinstance(applied, dict) else {}
    metadata = self_eval.get("ecas_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    actual_first = extract_first_line(body)
    actual_last = extract_last_line(body)
    detected_opening = identify_opening_type(body)
    detected_ending = identify_ending_type(body)
    declared_ending = str(applied.get("ending_type") or "")
    cjk_count = cio.count_cjk(body)

    lies = []
    metadata_cluster = metadata.get("cluster_id")
    if metadata_cluster is not None:
        try:
            normalized_metadata_cluster = _canonical_cluster_id(metadata_cluster)
        except WriterTruthError:
            normalized_metadata_cluster = str(metadata_cluster)
        if normalized_metadata_cluster != cluster_id:
            lies.append({
                "field": "ecas_metadata.cluster_id",
                "declared": metadata_cluster,
                "actual": cluster_id,
            })
    declared_cjk = next((metadata.get(key) for key in
                         ("cjk_actual", "word_count_cjk", "final_cjk")
                         if metadata.get(key) is not None), None)
    if declared_cjk is not None and (
        not isinstance(declared_cjk, int) or isinstance(declared_cjk, bool)
        or declared_cjk != cjk_count
    ):
        lies.append({
            "field": "ecas_metadata.cjk_actual",
            "declared": declared_cjk,
            "actual": cjk_count,
        })

    ending_type_match = declared_ending == detected_ending if declared_ending else None
    if ending_type_match is False:
        lies.append({
            "field": "applied_style.ending_type",
            "declared": declared_ending,
            "actual": detected_ending,
        })
    return {
        "cluster_id": cluster_id,
        "detected_opening_type": detected_opening,
        "declared_ending_type": declared_ending,
        "detected_ending_type": detected_ending,
        "ending_type_match": ending_type_match,
        "body_cjk_count": cjk_count,
        "lies_detected": lies,
        "lie_count": len(lies),
        "writer_applied_style_raw": applied,
    }


def truth_check_cluster(project_root, cluster) -> dict:
    project = Path(project_root)
    cluster_id = _canonical_cluster_id(cluster)
    directory = project / "章节" / f"{cluster_id}_draft"
    body = _read_utf8(directory / f"{cluster_id}_draft.txt")
    if not body.strip():
        raise WriterTruthError("cluster 正文为空")
    changes = _read_json(directory / f"{cluster_id}_changes.json")
    result = _evaluate(cluster_id, body, changes)
    verdict = "pass" if result["lie_count"] == 0 else "fail"
    return {
        "schema_version": "1.0.cluster",
        "judge_id": "writer-truth-check",
        "cluster_id": cluster_id,
        "overall_grade": "A" if verdict == "pass" else "C",
        "confidence": 1.0,
        "verdict": verdict,
        "detected_opening_type": result["detected_opening_type"],
        "declared_ending_type": result["declared_ending_type"],
        "detected_ending_type": result["detected_ending_type"],
        "ending_type_match": result["ending_type_match"],
        "body_cjk_count": result["body_cjk_count"],
        "lie_count": result["lie_count"],
        "lies_detected": result["lies_detected"],
        "specific_findings": result,
        "evidence_quotes": [
            {"position": "opening", "quote": extract_first_line(body)[:160]},
            {"position": "ending", "quote": extract_last_line(body)[:160]},
        ],
        "uncertainty_flags": self_eval_uncertainty(changes),
        "waivers": [],
    }


def self_eval_uncertainty(changes: dict) -> list[str]:
    self_eval = changes.get("self_eval")
    if not isinstance(self_eval, dict):
        return []
    flags = self_eval.get("uncertainty_flags")
    return [flag for flag in flags if isinstance(flag, str)] if isinstance(flags, list) else []


def write_report(project_root, report: dict) -> Path:
    project = Path(project_root)
    cluster_id = report["cluster_id"]
    path = project / "_数据库" / ".judge_reports" / f"{cluster_id}_writer-truth-check.json"
    atomic_json.atomic_write_json(path, report)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="核对 cluster writer 创作自评")
    parser.add_argument("project")
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args(argv)
    try:
        report = truth_check_cluster(args.project, args.cluster)
        path = write_report(args.project, report)
    except (OSError, WriterTruthError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    print(
        f"[{report['verdict'].upper()}] {report['cluster_id']} writer truth-check · "
        f"lies={report['lie_count']} · {path}"
    )
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
