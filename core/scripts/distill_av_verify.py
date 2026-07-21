#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distill_av_verify.py — 同栈复刻 AV 配对判别两段式验收（scene_jobs 范式 · advisory 报告）

判别由 novel-av-judge agent 亲笔完成，本脚本零模型调用，只做确定性两段：
  首跑：从 av_judge rubric 单一真理源逐票渲染完整判别 prompt（每配对 N 个投票任务 ·
    env AV_JUDGE_N_SAMPLES 默认 3 · AV_JUDGE_POSITION_SWAP=on 时半数任务换序呈现 ·
    AV_JUDGE_INTENT_DIM=on 时追加「作者思维」第 5 维）→ 写 av_judge_jobs.json manifest
    （job_id / prompt_path / output_path / 输入 digest）→ exit 2=pending；
    主代理按 manifest spawn novel-av-judge（PLAN_ID / STEP / JOBS_MANIFEST_PATH）。
  二跑：逐票严格校验 agent verdict（parse_av_verdicts schema · 4 维 verdict 必须
    「命中/走味」二选一 · 判走味必须给指证 reason；不符 = 该票退回 pending）+ 批次回执
    校验（agent 身份 / plan 绑定 / 逐 job 产物 SHA-256）→ aggregate_verdicts 4 维多数票
    聚合 → build_report 落盘 advisory 报告（AV_TRAIT_DRIFT 永远 advisory · 走味不阻断）。

输入 digest（作者锚 + 仿写 + 任务参数）变化 → 旧 verdict / 回执全部作废重新渲染（防陈旧判别）。
exit：0=advisory 报告落盘 / 2=输入错误或投票任务 pending。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json  # noqa: E402
from av_judge import (  # noqa: E402
    AV_JUDGE_SYSTEM_PROMPT,
    DRIFT_VERDICT,
    MATCH_VERDICT,
    _intent_dim_on,
    _n_samples,
    _position_swap_on,
    _swap_assignment,
    aggregate_verdicts,
    build_av_judge_prompt,
    build_report,
    _emit,
    parse_av_verdicts,
)

ROOT = Path(__file__).resolve().parents[2]

JOBS_MANIFEST_NAME = "av_judge_jobs.json"
RECEIPT_NAME = "agent_receipt.json"
JOBS_CONTRACT = "av_judge_jobs.v1"
RECEIPT_SCHEMA_VERSION = "novel-av-judge.receipt.v1"
AV_AGENT = "novel-av-judge"
SAMPLE_LIMIT = 3000  # 每段送审字数上限（配对判别只需足量语感样本）


def _cluster_meta(project: Path, cluster_id: str) -> dict:
    index_path = project / "cluster_index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"cluster_index.json 不存在: {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    target = str(cluster_id).replace("cluster_", "").replace("auto_", "")
    for cluster in data.get("clusters") or []:
        current = str(cluster.get("cluster_id") or "")
        if current == cluster_id or current.replace("cluster_", "").replace("auto_", "") == target:
            return cluster
    raise KeyError(f"cluster_index.json 无 cluster: {cluster_id}")


def _author_anchor(project: Path, cluster: dict) -> Path:
    bounds = cluster.get("chapter_range") or []
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("cluster 缺合法 chapter_range，无法选择作者真迹锚点")
    chapter = int(bounds[0])
    candidates = [
        project / "原文" / f"第{chapter:03d}章.txt",
        project / "原文" / f"第{chapter}章.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"cluster 首章作者原文不存在: {candidates}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def jobs_dir_for(output: Path) -> Path:
    """投票任务工作目录：<报告目录>/<报告 stem>_jobs/（manifest / prompt / verdict / 回执同住）。"""
    return output.parent / f"{output.stem}_jobs"


def _inputs_digest(author_sha: str, replica_sha: str, n: int,
                   swaps: list[bool], intent: bool) -> str:
    """输入 + 任务参数联合 digest：变了 = 旧判别作废（防陈旧 verdict 冒充当前输入的判别）。"""
    config = f"{n}|{''.join('1' if s else '0' for s in swaps)}|{int(intent)}|{SAMPLE_LIMIT}"
    return _sha256_bytes(f"{author_sha}|{replica_sha}|{config}".encode("utf-8"))


def _validate_verdict_text(text: str) -> tuple[dict | None, str]:
    """单票 verdict 严格验收（确定性 · schema 不符 = 该票退回 pending）。

    在 parse_av_verdicts 宽容解析之上加严：4 维 verdict 都必须是「命中/走味」精确二选一，
    判走味必须带非空指证 reason（agent 合约硬要求 · 防空泛判别进聚合）。
    """
    parsed = parse_av_verdicts(text)
    if not parsed.get("parse_ok"):
        return None, "verdict JSON 解析失败"
    for name, entry in parsed["dimensions"].items():
        verdict = entry.get("verdict")
        if verdict not in (MATCH_VERDICT, DRIFT_VERDICT):
            return None, f"维度「{name}」verdict 必须是「{MATCH_VERDICT}/{DRIFT_VERDICT}」二选一（实际 {verdict!r}）"
        if entry.get("drift") and not (entry.get("reason") or "").strip():
            return None, f"维度「{name}」判走味缺指证 reason"
    return parsed, ""


def _receipt_ok(receipt_path: Path, jobs: list[dict]) -> tuple[bool, str]:
    """批次回执验收：agent 身份 + plan 绑定 + 逐 job 产物 SHA-256 全对才算完成。"""
    if not receipt_path.exists():
        return False, "批次回执未落盘"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "批次回执 JSON 破损"
    if not isinstance(receipt, dict):
        return False, "批次回执必须是 object"
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        return False, f"schema_version 必须是 {RECEIPT_SCHEMA_VERSION}"
    if receipt.get("agent") != AV_AGENT:
        return False, f"agent 必须是 {AV_AGENT}"
    if receipt.get("completed") is not True:
        return False, "completed 必须是 true"
    if not (isinstance(receipt.get("plan_id"), str) and receipt["plan_id"].strip()):
        return False, "plan_id 缺失（agent 必须携带 PLAN_ID）"
    if receipt.get("step") is None:
        return False, "step 缺失（agent 必须携带 STEP）"
    entries = receipt.get("jobs")
    if not isinstance(entries, list):
        return False, "jobs 必须是 array"
    by_id = {}
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("job_id"), str):
            by_id[item["job_id"]] = item
    expected_ids = [job["job_id"] for job in jobs]
    if sorted(by_id) != sorted(expected_ids):
        return False, f"jobs 必须逐票覆盖 {expected_ids}（实际 {sorted(by_id)}）"
    for job in jobs:
        actual = _sha256_file(Path(job["output_path"]))
        claimed = by_id[job["job_id"]].get("output_sha256")
        if actual is None or claimed != actual:
            return False, f"{job['job_id']} 的 output_sha256 与 verdict 文件不符"
    return True, ""


def _write_manifest(path: Path, *, author: Path, replica: Path, author_sha: str,
                    replica_sha: str, digest: str, n: int, intent: bool,
                    jobs: list[dict], receipt_path: Path, output: Path) -> None:
    """原子写任务清单（按 job_id 保留 created_at · status/diag 每跑刷新）。"""
    prev: dict[str, dict] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                prev = {j.get("job_id"): j for j in old.get("jobs", []) if isinstance(j, dict)}
        except (OSError, json.JSONDecodeError):
            prev = {}
    now = datetime.now().isoformat(timespec="seconds")
    for job in jobs:
        job["created_at"] = (prev.get(job["job_id"]) or {}).get("created_at", now)
        job["updated_at"] = now
    doc = {
        "version": 1,
        "contract": JOBS_CONTRACT,
        "agent": AV_AGENT,
        "author_path": str(author.resolve()),
        "replica_path": str(replica.resolve()),
        "author_sha256": author_sha,
        "replica_sha256": replica_sha,
        "inputs_digest": digest,
        "n_samples": n,
        "include_intent_dim": intent,
        "sample_limit": SAMPLE_LIMIT,
        "report_output": str(output.resolve()),
        "receipt_path": str(receipt_path.resolve()),
        "receipt_schema": {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "agent": AV_AGENT,
            "plan_id": "<PLAN_ID>",
            "step": "<STEP>",
            "completed": True,
            "jobs": [{"job_id": "<job_id>", "output_path": "<verdict 绝对路径>",
                      "output_sha256": "<verdict 文件 SHA-256>"}],
        },
        "jobs": jobs,
        "updated_at": now,
    }
    atomic_json.atomic_write_json(path, doc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="required 同栈复刻 AV advisory 两段式验收")
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--replica", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        cluster = _cluster_meta(args.project, args.cluster_id)
        author = _author_anchor(args.project, cluster)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[ERROR] AV 验证输入解析失败: {exc}", file=sys.stderr)
        return 2
    if not args.replica.exists():
        print(f"[ERROR] 复刻终稿不存在: {args.replica}", file=sys.stderr)
        return 2

    author_bytes = author.read_bytes()
    replica_bytes = args.replica.read_bytes()
    author_sha = _sha256_bytes(author_bytes)
    replica_sha = _sha256_bytes(replica_bytes)
    author_text = author_bytes.decode("utf-8")
    replica_text = replica_bytes.decode("utf-8")

    n = _n_samples()
    swaps = _swap_assignment(n, _position_swap_on())
    intent = _intent_dim_on()
    digest = _inputs_digest(author_sha, replica_sha, n, swaps, intent)

    jobs_dir = jobs_dir_for(args.output)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = jobs_dir / JOBS_MANIFEST_NAME
    receipt_path = jobs_dir / RECEIPT_NAME

    # ── 输入 digest 幂等：变化 = 旧判别作废（删旧 verdict / 回执 · 重渲染 prompt）──
    stale = True
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            stale = not (isinstance(old, dict) and old.get("inputs_digest") == digest)
        except (OSError, json.JSONDecodeError):
            stale = True
    if stale:
        for leftover in (*jobs_dir.glob("vote_*_verdict.json"), receipt_path):
            try:
                leftover.unlink()
            except OSError:
                pass

    # ── 渲染投票任务（确定性纯函数 · digest 未变时按 sha 幂等补写）──
    jobs: list[dict] = []
    for i, swap in enumerate(swaps, 1):
        job_id = f"vote_{i}"
        prompt_path = jobs_dir / f"{job_id}_prompt.txt"
        verdict_path = jobs_dir / f"{job_id}_verdict.json"
        prompt_text = (
            AV_JUDGE_SYSTEM_PROMPT
            + "\n\n"
            + build_av_judge_prompt(author_text, replica_text, SAMPLE_LIMIT,
                                    swap=swap, include_intent_dim=intent)
        )
        # write_bytes 保证磁盘字节 == 声明哈希（write_text 在 Windows 会把 \n 翻译成 \r\n，
        # manifest 哈希对不上磁盘 → 幂等补写检查永真 + judge 无法核验输入完整性）。
        prompt_bytes = prompt_text.encode("utf-8")
        prompt_sha = _sha256_bytes(prompt_bytes)
        if _sha256_file(prompt_path) != prompt_sha:
            prompt_path.write_bytes(prompt_bytes)
        job = {
            "job_id": job_id,
            "swap": swap,
            "prompt_path": str(prompt_path.resolve()),
            "prompt_sha256": prompt_sha,
            "output_path": str(verdict_path.resolve()),
        }
        # 逐票严格验收（schema 不符 = 该票退回 pending · agent 重判覆盖）
        if verdict_path.exists():
            parsed, diag = _validate_verdict_text(verdict_path.read_text(encoding="utf-8"))
            if parsed is not None:
                job["status"], job["diag"] = "ready", ""
                job["_parsed"] = parsed
            else:
                job["status"], job["diag"] = "pending", f"verdict 验收不过（{diag}）·退回 pending 重判"
                print(f"[WARN] {verdict_path.name} {job['diag']}", file=sys.stderr)
        else:
            job["status"], job["diag"] = "pending", "期望 verdict 未落盘"
        jobs.append(job)

    parsed_by_id = {job["job_id"]: job.pop("_parsed") for job in jobs if "_parsed" in job}

    # ── 批次回执验收（全票 ready 才有意义 · agent 身份 / plan 绑定 / 逐 job SHA-256）──
    pending = [job for job in jobs if job["status"] != "ready"]
    receipt_good, receipt_diag = (False, "待全部投票任务完成后写批次回执")
    if not pending:
        receipt_good, receipt_diag = _receipt_ok(receipt_path, jobs)

    _write_manifest(manifest_path, author=author, replica=args.replica,
                    author_sha=author_sha, replica_sha=replica_sha, digest=digest,
                    n=n, intent=intent, jobs=jobs, receipt_path=receipt_path,
                    output=args.output)

    if pending or not receipt_good:
        what = (", ".join(job["job_id"] for job in pending)
                if pending else f"批次回执（{receipt_diag}）")
        print(f"[PENDING] AV 配对判别待 {AV_AGENT} 补件: {what}\n"
              f"任务清单: {manifest_path}\n"
              f"主代理 spawn {AV_AGENT}（PLAN_ID / STEP / JOBS_MANIFEST_PATH: {manifest_path}）"
              f"逐票独立判别、写 verdict 与批次回执后重跑本命令（同参续跑验收）", file=sys.stderr)
        return 2

    # ── 全票就绪：多数票聚合 → advisory 报告落盘（AV_TRAIT_DRIFT 永远 advisory）──
    samples = []
    for job in jobs:
        sample = parsed_by_id[job["job_id"]]
        sample["_swapped"] = job["swap"]  # G2-CYCLIC 透明：标记该票呈现方向（聚合不翻转·仅上报）
        samples.append(sample)
    agg = aggregate_verdicts(samples)
    report = build_report("active", agg, str(author), str(args.replica),
                          profile_name=AV_AGENT)
    report["jobs_manifest"] = str(manifest_path.resolve())
    report["inputs_digest"] = digest
    _emit(report, str(args.output))
    drift = report.get("drift_dims") or []
    print(f"[distill_av_verify] {n} 票多数票聚合完成 · verdict={report.get('verdict')} · "
          f"走味维度: {drift if drift else '无'}（advisory 不阻断）")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
