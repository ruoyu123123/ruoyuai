"""skill_opt.optimizer — patch 提案任务的素材组装 + 提案验收

SkillOpt optimizer 组件 (arXiv 2605.23904 §3.2 + Algorithm 1) 的确定性两端:
- 组装 trajectory minibatch 任务上下文 (reject_buffer prepend 头部 + [PROTECTED]
  标注 + [ROLLOUT MINIBATCH])，由 optimizer_jobs 落成 patch 提案 job 素材
- 验收 novel-skill-author (MODE=patch) 亲笔写的 patches JSON: 严格解析 + ≤L_t 硬截断

patch 提案本身由 novel-skill-author agent 亲笔完成；单条 patch 的 op/anchor/old
合法性与 IMMUTABLE 保护由下游 patch_applier 裁决。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from . import reject_buffer as _reject_buffer


def _format_minibatch(trajectories: Sequence[dict], max_preview: int = 400) -> str:
    """把 trajectory minibatch 格式化进任务上下文。

    每条:cluster_id + reward + components + replica 前 max_preview 字。
    """
    lines = ["[ROLLOUT MINIBATCH]"]
    for i, t in enumerate(trajectories, 1):
        cid = t.get("cluster_id", "?")
        r = t.get("reward", 0.0)
        comp = t.get("components", {})
        # 试着读复刻文本前 400 字
        preview = ""
        rp = t.get("replica_path")
        if rp:
            p = Path(rp)
            if p.exists():
                try:
                    preview = p.read_text(encoding="utf-8", errors="replace")[:max_preview]
                except Exception:
                    preview = "<读取失败>"
        lines.append(
            f"--- rollout {i} · cluster={cid} · reward={r:.4f} ---\n"
            f"  signals: audit={comp.get('audit_pass')} reading={comp.get('reading_pass')} "
            f"voice={comp.get('voice_clean')} truth={comp.get('truth_clean')}\n"
            f"  replica_preview: {preview}\n"
        )
    return "\n".join(lines)


def build_task_context(
    skill_text: str,
    trajectories: Sequence[dict],
    protected_sections: Sequence[str] = (),
    rejects: Sequence[dict] = (),
    skill_version: str = "v?",
    max_patches: int = 4,
) -> str:
    """组装 patch 提案任务上下文 (job 素材文件的 context_text 段)。"""
    parts = []

    # 1. reject buffer (论文要求 prepend 头部)
    if rejects:
        parts.append(_reject_buffer.format_for_prompt(rejects))

    # 2. 当前 skill (含 PROTECTED 标记)
    parts.append(f"[CURRENT SKILL · version={skill_version}]")
    if protected_sections:
        parts.append(
            "[PROTECTED 段: 以下段名不可改,只能改其他段]\n  - "
            + "\n  - ".join(protected_sections)
        )
    parts.append("```markdown\n" + skill_text + "\n```")

    # 3. trajectory minibatch
    parts.append(_format_minibatch(trajectories))

    # 4. 任务指令
    parts.append(
        f"[任务] 基于以上 rollout,按 novel-skill-author MODE=patch 合约"
        f"提出 ≤{max_patches} 条能让下一轮 reward 提升的 patch,"
        '写入 OUTPUT_PATH (纯 JSON {"patches": [...]},无围栏)。'
    )

    return "\n\n".join(parts)


def accept_patches(text: str, max_patches: int = 4) -> list[dict] | None:
    """验收 agent 产的 patches JSON 文本 (novel-skill-author OUTPUT_PATH 契约)。

    通过 → 返回 patches (可为空列表=agent 明确提案无可改；超过 max_patches 硬截断)；
    坏 JSON / 顶层非 {"patches": [...]} / 条目非 object → None (job 保持 pending)。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    patches = data.get("patches")
    if not isinstance(patches, list):
        return None
    if any(not isinstance(p, dict) for p in patches):
        return None
    return patches[:max_patches]
