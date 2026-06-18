"""skill_opt.reject_buffer — 失败编辑持久化 + 反哺 optimizer prompt

业界源 (arXiv 2605.23904 Algorithm 1):
- 被 validation gate 毙掉的 patch 进 buffer
- 下次 optimizer prompt 头部 prepend 全量
- epoch-local:每 epoch 清空(避免无限增长)

数据结构: JSONL,每行一个 reject 记录:
{
  "patch_id": "ep1_step3_patch2",
  "epoch": 1,
  "skill_version_before": "v3",
  "patch": {"op": "replace", "old": "...", "new": "..."},
  "reward_before": 0.78,
  "reward_after": 0.72,
  "reason": "selection_set 严格优于失败 (0.72 ≤ 0.78)",
  "ts": "2026-06-18T17:00:00"
}
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _buffer_path(project_root: Path, epoch: int) -> Path:
    out = project_root / "_skillopt" / "reject_buffer"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"epoch_{epoch:03d}.jsonl"


def record_reject(
    project_root: Path,
    epoch: int,
    patch_id: str,
    skill_version_before: str,
    patch: dict,
    reward_before: float,
    reward_after: float,
    reason: str,
) -> Path:
    """落盘一条 reject 记录。"""
    rec = {
        "patch_id": patch_id,
        "epoch": epoch,
        "skill_version_before": skill_version_before,
        "patch": patch,
        "reward_before": reward_before,
        "reward_after": reward_after,
        "reason": reason,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    p = _buffer_path(project_root, epoch)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


def load_epoch_rejects(project_root: Path, epoch: int) -> list[dict]:
    """读本 epoch 已积累的 reject 全量。"""
    p = _buffer_path(project_root, epoch)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def format_for_prompt(rejects: Iterable[dict], max_items: int = 30) -> str:
    """格式化 reject buffer 进 optimizer prompt 头部。

    输出格式 (节省 token):
    [REJECT_BUFFER · 本 epoch 已失败 N 次,避免重蹈]
    1. op=replace · 想把 "<old前40字>" 改成 "<new前40字>"
       结果:reward 0.78 → 0.72 · 原因:selection_set 未严格优于
    2. ...
    """
    items = list(rejects)
    if not items:
        return ""

    n = len(items)
    show = items[-max_items:]  # 只展示最近 N 条 (老的可能已学到)
    lines = [f"[REJECT_BUFFER · 本 epoch 已失败 {n} 次,避免重蹈]"]
    for i, r in enumerate(show, 1):
        p = r.get("patch", {})
        op = p.get("op", "?")
        old = (p.get("old") or "")[:40].replace("\n", " ")
        new = (p.get("new") or "")[:40].replace("\n", " ")
        rb = r.get("reward_before")
        ra = r.get("reward_after")
        reason = r.get("reason", "")[:60]
        lines.append(
            f"{i}. op={op} · 想改 \"{old}\" → \"{new}\"\n"
            f"   reward {rb} → {ra} · {reason}"
        )
    return "\n".join(lines) + "\n"


def _archive_path(project_root: Path) -> Path:
    out = project_root / "_skillopt" / "reject_buffer"
    out.mkdir(parents=True, exist_ok=True)
    return out / "reject_archive.jsonl"


def clear_epoch(project_root: Path, epoch: int) -> None:
    """epoch 结束: 先归档到 reject_archive.jsonl 再清 epoch 文件。

    F4 调研发现: 原始实现直接 unlink 导致失败案例白丢,
    无法做跨 epoch 的 failure taxonomy。
    """
    p = _buffer_path(project_root, epoch)
    if not p.exists():
        return
    # 归档: append 到永久 jsonl
    archive = _archive_path(project_root)
    with archive.open("a", encoding="utf-8") as dst:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                dst.write(line + "\n")
    # 清 epoch 文件
    p.unlink()


def load_archive(project_root: Path) -> list[dict]:
    """读全量历史 reject 归档(跨 epoch 永久)。"""
    archive = _archive_path(project_root)
    if not archive.exists():
        return []
    out = []
    for line in archive.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
