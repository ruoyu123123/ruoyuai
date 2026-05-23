"""changes_parser.py — 章节 CHANGES 解析的统一入口（v18 接入 chapter_io）

【v18 架构】正文和 CHANGES 已是两个物理文件：
    第NNN章.txt          —— 纯正文
    第NNN章_changes.json —— {"factual": {...}, "self_eval": {...}}

本模块对外提供两类入口：
  1. 章节号入口（推荐）—— read_chapter_changes(project_root, ch)
     直接走 chapter_io：v18 分离稿读 _changes.json，旧混合稿自动 split。
  2. 裸文本入口（向下兼容）—— parse_chapter_text(raw)
     给只拿到一段 raw 文本的老调用方用，底层委托 cio.parse_legacy_changes。

【为什么保留裸文本入口】历史上有脚本先 read_text 再传文本进来。v18 起这些
脚本应改为传 (project_root, ch)，但在全部改完前，parse_chapter_text 仍兼容。

使用：
    from changes_parser import read_chapter_changes, parse_chapter_text
    # 推荐：章节号入口
    factual, self_eval = read_chapter_changes(project_root, ch)
    # 兼容：裸文本入口
    body, factual_dict, self_eval_dict = parse_chapter_text(raw_text)
"""

import sys
from pathlib import Path

# v18：统一章节读写走 chapter_io
sys.path.insert(0, str(Path(__file__).parent))
import chapter_io as cio


def read_chapter_changes(project_root, ch: int) -> tuple[dict, dict]:
    """章节号入口（推荐）。返回 (factual_dict, self_eval_dict)。

    优先读 第NNN章_changes.json；不存在时由 chapter_io 从旧混合 txt 解析。
    两个返回值恒为 dict（找不到则空 dict），调用方无需判 None。
    """
    data = cio.read_changes(project_root, ch)
    return data.get("factual") or {}, data.get("self_eval") or {}


def parse_chapter_text(raw: str) -> tuple[str, dict | None, dict | None]:
    """裸文本入口（向下兼容）。返回 (body, factual_dict, self_eval_dict)。

    raw 是「正文 + 旧式 CHANGES 分隔符段」的混合文本。v18 起新稿不再有这种
    文本——新调用方应改用 read_chapter_changes(project_root, ch)。
    body 为剥离 CHANGES 后的纯正文；解析失败时对应 dict 为 None。
    """
    body = cio._strip_changes(raw)
    parsed = cio.parse_legacy_changes(raw)
    factual = parsed.get("factual") or None
    self_eval = parsed.get("self_eval") or None
    return body, factual, self_eval


def split_body_only(raw: str) -> str:
    """只返回正文部分。用于 emotion/pacing 等只拿到 raw 文本的脚本。"""
    return cio._strip_changes(raw)


def extract_applied_style(raw: str) -> dict | None:
    """从裸文本提取 writer 的 applied_style（self_eval 段优先，回退 factual 根字段）。"""
    _, factual, self_eval = parse_chapter_text(raw)
    if self_eval and "applied_style" in self_eval:
        return self_eval["applied_style"]
    if factual and "applied_style" in factual:
        return factual["applied_style"]
    return None


if __name__ == "__main__":
    # 自测：裸文本入口仍兼容旧混合格式
    test_new = """正文 abc
---CHANGES_FACTUAL---
{"chapter": 4, "character_changes": []}
---END_CHANGES_FACTUAL---

---CHANGES_SELF_EVAL---
{"applied_style": {"opening_type": "人物内心吐槽", "anchors_hit": ["雾"]}}
---END_CHANGES_SELF_EVAL---
"""
    b, f, se = parse_chapter_text(test_new)
    assert b.strip() == "正文 abc", f"body={b!r}"
    assert f["chapter"] == 4, f"factual={f!r}"
    assert se["applied_style"]["opening_type"] == "人物内心吐槽", f"self_eval={se!r}"

    test_legacy = """正文 xyz
---CHANGES---
{"chapter": 1, "character_changes": []}
---END---
"""
    b, f, se = parse_chapter_text(test_legacy)
    assert b.strip() == "正文 xyz", f"body={b!r}"
    assert f["chapter"] == 1, f"factual={f!r}"
    assert se is None, f"self_eval should be None, got {se!r}"

    # extract_applied_style
    assert extract_applied_style(test_new)["opening_type"] == "人物内心吐槽"
    assert extract_applied_style(test_legacy) is None

    print("[OK] changes_parser self-test 通过（v18 chapter_io 接入）")
