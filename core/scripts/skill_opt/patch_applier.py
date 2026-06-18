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


@dataclass
class PatchResult:
    success: bool
    new_text: str
    applied: list[dict]
    rejected: list[tuple[dict, str]]  # (patch, reason)


def _split_paragraphs(text: str) -> list[str]:
    """按双换行切段,保留分隔语义。"""
    # 用 \n\n 切,但保留段内的单换行
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


def _find_block_match(paragraphs: list[str], anchor: str, old: str) -> int | None:
    """找连续段块匹配 (old 可能跨多段)。返回起始 index 或 None。

    匹配规则:
    - 段 i 的 _anchor_match(anchor) 命中
    - paragraphs[i:i+k] 用 "\\n\\n" join 后 strip == old.strip()
      k 从 1 试到 min(剩余段数, 20) (硬上限防爆)
    """
    old_norm = old.strip()
    for i, para in enumerate(paragraphs):
        if not _anchor_match(para, anchor):
            continue
        # 试 1..min(剩余, 20) 段的合并
        for k in range(1, min(len(paragraphs) - i + 1, 21)):
            block = "\n\n".join(paragraphs[i : i + k]).strip()
            if block == old_norm:
                return i, k  # type: ignore[return-value]
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
            # 区分原因
            anchor_hit = any(_anchor_match(p, anchor) for p in paragraphs)
            if anchor_hit:
                return paragraphs, (
                    f"delete: anchor 命中但 old 不匹配 (old 长 {len(old)})"
                )
            return paragraphs, f"delete: anchor 找不到 ({anchor[:30]!r})"
        i, k = m
        return paragraphs[:i] + paragraphs[i + k :], None

    if op == "replace":
        anchor = patch["anchor"]
        old = patch["old"]
        new = patch["new"].strip()
        m = _find_block_match(paragraphs, anchor, old)
        if m is None:
            anchor_hit = any(_anchor_match(p, anchor) for p in paragraphs)
            if anchor_hit:
                return paragraphs, (
                    f"replace: anchor 命中但 old 不匹配 (old 长 {len(old)})"
                )
            return paragraphs, f"replace: anchor 找不到 ({anchor[:30]!r})"
        i, k = m
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
