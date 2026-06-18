"""skill_opt.patch_applier — 把 add/delete/replace patch 应用到 skill.md

业界源 (arXiv 2605.23904 §3.2):
- 每 step ≤ L_t (textual learning rate=4) 条 patch
- patch 三类: add / delete / replace
- 论文未公开单条字符上限,本实现按段(\\n\\n分段)颗粒度

Patch JSON schema:
{
  "op": "add" | "delete" | "replace",
  "anchor": "<段首前 40 字符的精确匹配>",  # 用于定位 (delete/replace 必填)
  "old": "<被替换/删除的完整段>",          # delete/replace 必填,用于校验
  "new": "<新增/替换后的完整段>",          # add/replace 必填
  "after_anchor": "<add 模式: 在哪段后插入>",  # add 必填 (None=文首)
}

不用 unified diff 是因为 LLM(尤其 gemini reasoning) 输出标准 diff 经常崩,
JSON 字段更稳定。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


class PatchError(Exception):
    """patch 应用失败 (anchor 找不到 / old 不匹配 / op 非法)。"""


# S1 安全锁: 这些 anchor 关键词命中时 delete/replace 的 patch 被无条件拒绝。
# G5 调研发现 optimizer 可删反 AI 腔约束段且 reward 无感。
# 硬表保护不靠 LLM 自觉——patch_applier 层强制拦截。
IMMUTABLE_KEYWORDS = [
    "反模式",           # "## 反模式（绝不做）"
    "绝不做",
    "拉黑",             # "拉黑高频雷词"
    "禁用词",           # 禁用词清单
    "AI套话",
    "AI 套话",
    "严禁",             # "严禁 AI 套话"
    "北极星",           # 北极星原则
    "hard_gate",        # hard_gate 不可豁免
    "不可豁免",
]


def _is_immutable(anchor: str, old: str) -> bool:
    """检查 anchor 或 old 是否命中 IMMUTABLE_KEYWORDS。"""
    combined = (anchor or "") + " " + (old or "")
    return any(kw in combined for kw in IMMUTABLE_KEYWORDS)


@dataclass
class PatchResult:
    success: bool
    new_text: str
    applied: list[dict]
    rejected: list[tuple[dict, str]]  # (patch, reason)


def _split_paragraphs(text: str) -> list[str]:
    """按双换行切段,保留分隔语义。"""
    parts = text.split("\n\n")
    return [p for p in parts if p.strip() != ""] if parts else []


def _normalize(s: str) -> str:
    """归一化空白: 多空白→单空格, strip, 全角→半角#。"""
    import re
    s = s.strip()
    s = s.replace("＃", "#").replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _anchor_match(paragraph: str, anchor: str) -> bool:
    """多策略模糊匹配 (fix: gemini optimizer 经常写错 anchor 导致全崩)。

    策略 (任一命中即匹配):
    1. 精确段首匹配 (原始逻辑)
    2. 归一化空白后段首匹配
    3. anchor 是段落的子串 (LLM 截了中间一段)
    4. 段落标题完全匹配 (## 标题行 vs anchor)
    """
    if not anchor:
        return False
    p = paragraph.lstrip()
    a = anchor.lstrip()[:60]  # 放宽到 60 字符

    # 策略 1: 精确段首
    if p.startswith(a):
        return True

    # 策略 2: 归一化空白后段首
    pn = _normalize(p)
    an = _normalize(a)
    if pn.startswith(an):
        return True

    # 策略 3: anchor 是段落子串 (LLM 可能截了中间)
    if len(an) >= 8 and an in pn:
        return True

    # 策略 4: 标题行匹配 (段落第一行 vs anchor 都是 ## 开头)
    first_line = p.split("\n")[0].strip()
    anchor_first = a.split("\n")[0].strip()
    if first_line and anchor_first:
        if _normalize(first_line) == _normalize(anchor_first):
            return True
        # 宽松: 标题含 anchor 或反之
        fl = _normalize(first_line)
        af = _normalize(anchor_first)
        if len(af) >= 5 and (af in fl or fl in af):
            return True

    return False


def _validate_patch(p: dict) -> str | None:
    """patch 字段合法性检查,返回错误原因或 None。"""
    op = p.get("op")
    if op not in ("add", "delete", "replace"):
        return f"op 必须是 add/delete/replace,收到 {op!r}"
    # S1 安全锁: delete/replace 命中 IMMUTABLE 关键词 → 无条件拒绝
    if op in ("delete", "replace"):
        if _is_immutable(p.get("anchor", ""), p.get("old", "")):
            return f"IMMUTABLE: 反 AI 腔/北极星约束段不可删改 (anchor 命中安全锁)"
    if op == "add":
        if not p.get("new"):
            return "add 必须给 new"
        # after_anchor 可以为 None (插文首)
    elif op == "delete":
        if not p.get("old"):
            return "delete 必须给 old"
        if not p.get("anchor"):
            return "delete 必须给 anchor"
    elif op == "replace":
        if not p.get("old"):
            return "replace 必须给 old"
        if not p.get("new"):
            return "replace 必须给 new"
        if not p.get("anchor"):
            return "replace 必须给 anchor"
    return None


def _find_block_match(paragraphs: list[str], anchor: str, old: str):
    """找连续段块匹配 (old 可能跨多段或在段内)。

    返回 (段起始idx, 段跨度, 行级替换信息或None) 或 None。

    匹配策略 (依次尝试):
    1. 段首 anchor 命中 + 段块 join == old (原始逻辑)
    2. 段内行级搜索: 某段内含 anchor 对应的行, old 在该段内
    """
    old_norm = old.strip()

    # 策略 1: 段首 anchor 命中 + 段块 join
    for i, para in enumerate(paragraphs):
        if not _anchor_match(para, anchor):
            continue
        for k in range(1, min(len(paragraphs) - i + 1, 21)):
            block = "\n\n".join(paragraphs[i : i + k]).strip()
            if block == old_norm:
                return i, k, None

    # 策略 2: 段内行级搜索 (skill 用 \n 分列表项, \n\n 分大节)
    for i, para in enumerate(paragraphs):
        lines = para.split("\n")
        for li, line in enumerate(lines):
            if not _anchor_match(line, anchor):
                continue
            # anchor 命中了段内某行, 试行级 old 匹配
            # old 可能是连续 N 行
            old_lines = old_norm.split("\n")
            n_old = len(old_lines)
            if li + n_old <= len(lines):
                block = "\n".join(lines[li : li + n_old]).strip()
                if block == old_norm:
                    return i, 1, {"line_start": li, "line_count": n_old}

    return None


def _apply_one(paragraphs: list[str], patch: dict) -> tuple[list[str], str | None]:
    """应用一条 patch,返回 (新段落列表, 错误原因或 None)。"""
    err = _validate_patch(patch)
    if err:
        return paragraphs, err

    op = patch["op"]

    if op == "add":
        new_block = patch["new"].strip()
        # new 可能含 \n\n,切成多段插入
        new_paras = _split_paragraphs(new_block)
        after = patch.get("after_anchor")
        if not after:
            return new_paras + paragraphs, None
        for i, para in enumerate(paragraphs):
            if _anchor_match(para, after):
                return paragraphs[: i + 1] + new_paras + paragraphs[i + 1 :], None
        return paragraphs, f"add: after_anchor 找不到匹配段 ({after[:30]!r})"

    if op == "delete":
        anchor = patch["anchor"]
        old = patch["old"]
        m = _find_block_match(paragraphs, anchor, old)
        if m is None:
            anchor_hit = any(_anchor_match(p, anchor) for p in paragraphs)
            if not anchor_hit:
                # 也检查段内行级
                anchor_hit = any(
                    any(_anchor_match(line, anchor) for line in p.split("\n"))
                    for p in paragraphs
                )
            if anchor_hit:
                return paragraphs, (
                    f"delete: anchor 命中但 old 不匹配 (old 长 {len(old)})"
                )
            return paragraphs, f"delete: anchor 找不到 ({anchor[:30]!r})"
        i, k, line_info = m
        if line_info is not None:
            # 行级删除: 段内移除匹配行
            lines = paragraphs[i].split("\n")
            ls, lc = line_info["line_start"], line_info["line_count"]
            lines = lines[:ls] + lines[ls + lc:]
            new_para = "\n".join(lines).strip()
            if new_para:
                return paragraphs[:i] + [new_para] + paragraphs[i + 1:], None
            else:
                return paragraphs[:i] + paragraphs[i + 1:], None
        return paragraphs[:i] + paragraphs[i + k :], None

    if op == "replace":
        anchor = patch["anchor"]
        old = patch["old"]
        new = patch["new"].strip()
        m = _find_block_match(paragraphs, anchor, old)
        if m is None:
            anchor_hit = any(_anchor_match(p, anchor) for p in paragraphs)
            if not anchor_hit:
                anchor_hit = any(
                    any(_anchor_match(line, anchor) for line in p.split("\n"))
                    for p in paragraphs
                )
            if anchor_hit:
                return paragraphs, (
                    f"replace: anchor 命中但 old 不匹配 (old 长 {len(old)})"
                )
            return paragraphs, f"replace: anchor 找不到 ({anchor[:30]!r})"
        i, k, line_info = m
        if line_info is not None:
            # 行级替换: 段内替换匹配行
            lines = paragraphs[i].split("\n")
            ls, lc = line_info["line_start"], line_info["line_count"]
            new_lines = new.split("\n")
            lines = lines[:ls] + new_lines + lines[ls + lc:]
            return paragraphs[:i] + ["\n".join(lines)] + paragraphs[i + 1:], None
        new_paras = _split_paragraphs(new)
        return paragraphs[:i] + new_paras + paragraphs[i + k :], None

    return paragraphs, f"未知 op: {op}"


def apply_patches(
    skill_text: str,
    patches: Sequence[dict],
    max_patches: int = 4,
) -> PatchResult:
    """按顺序应用 patches 列表到 skill_text。

    Args:
        skill_text: 原 skill.md 内容
        patches: ≤4 条 patch (论文 L_t=4)
        max_patches: 硬上限,超过截断

    Returns:
        PatchResult(success, new_text, applied, rejected)

    纪律:
    - 任一 patch 失败不阻断,继续尝试后续(applied/rejected 分流)
    - 全部失败 → success=False, new_text=原文不变
    - 至少一条成功 → success=True, new_text=合并应用后的文本
    """
    if len(patches) > max_patches:
        patches = patches[:max_patches]

    paragraphs = _split_paragraphs(skill_text)
    applied: list[dict] = []
    rejected: list[tuple[dict, str]] = []

    for p in patches:
        new_paras, err = _apply_one(paragraphs, p)
        if err is None:
            paragraphs = new_paras
            applied.append(p)
        else:
            rejected.append((p, err))

    new_text = "\n\n".join(paragraphs)
    return PatchResult(
        success=bool(applied),
        new_text=new_text,
        applied=applied,
        rejected=rejected,
    )
