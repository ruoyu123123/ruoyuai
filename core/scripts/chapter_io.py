"""core/scripts/chapter_io.py

章节文件统一读写模块 —— 正文/数据分离架构（v18）的地基。

【为什么有这个模块】
v17 及之前，章节 txt 文件 = 正文 + ---CHANGES_FACTUAL--- JSON + ---CHANGES_SELF_EVAL--- JSON
混在一个文件。十几个脚本各自 split，口径不一，导致：
  - validate_style 把 CHANGES JSON 当正文算 → 对话占比/字数虚高
  - save_state split 分隔符与 writer 输出不一致 → 解析失败
  - git commit 字数算错
v18 起：正文 → 第NNN章.txt（纯正文），CHANGES → 第NNN章_changes.json（结构化）。
所有读写章节正文/CHANGES 的脚本必须走本模块，禁止各自 split。

【文件布局】（STRUCTURE.md 第三节 / 第十节）
  章节/第NNN章/第NNN章.txt          —— 纯正文
  章节/第NNN章/第NNN章_changes.json —— {"factual": {...}, "self_eval": {...}}

【_changes.json schema】
  {
    "factual":   { ... },     # 客观事实变更（锁定事实/伏笔/道具/角色...）
    "self_eval": {
      ...,                    # writer 的自评字段
      "waivers": [            # v19 顾问制：AI 对 advisory 检测项的豁免清单
        {"code": "STYLE_拟声格式", "reason": "本章纯心理独白章，拟声不适配"}
      ],
      "uncertainty_flags": [  # P2-13：writer 主动标的「自评不确定」项
        {"aspect": "POV_consistency", "detail": "...", "suggest_judge": "voice-keeper"}
      ]
    }
  }
  v19 豁免协议（块 2.3）：
    - 检测工具是「顾问」不是「法官」。writer 对 audit_hub 报出的 advisory 项，
      如有充分理由可在 self_eval.waivers 写入豁免；理由须具体、< 100 字
      （"本章纯心理独白章拟声不适配" 合格；"不想改" 不合格 —— 空/泛理由不算豁免）。
    - audit_hub 用 --waivers <此文件路径> 读取 self_eval.waivers，对 advisory 项
      code 匹配 → 转 waived，不计入 needs_agent。
    - hard_gate 项（E 层一致性 + 文件契约类，清单见 audit_hub.HARD_GATE_CODES）
      不可豁免 —— 即便写进 waivers 也被 audit_hub 强制忽略。
    - read_changes / write_changes 原样透传 self_eval（含 waivers），无需专用 API。

【公共 API】
  路径:   find_chapter_dir / body_path / changes_path / find_body_file
  读:     read_body / read_changes / parse_legacy_changes
  写:     write_body / write_changes
  字数:   count_words（统一口径：非空白字符数）/ count_cjk（纯汉字）
  迁移:   migrate_legacy_chapter
"""

import json
import re
from pathlib import Path

CHANGES_SEPARATORS = ("---CHANGES_FACTUAL---", "---CHANGES---")
SELF_EVAL_SEP = "---CHANGES_SELF_EVAL---"
END_MARKERS = ("---END_CHANGES_FACTUAL---", "---END_CHANGES_SELF_EVAL---", "---END---")


# ============ 路径定位 ============

def find_chapter_dir(project_root, ch: int):
    """返回章节目录 Path（章节/第NNN章/），找不到返回 None。兼容 4 布局。"""
    root = Path(project_root)
    for d in (root / "章节" / f"第{ch:03d}章", root / "章节" / f"第{ch}章",
              root / f"第{ch:03d}章", root / f"第{ch}章"):
        if d.is_dir():
            return d
    return None


def body_path(project_root, ch: int) -> Path:
    """正文文件标准路径（v18 嵌套-zero-pad 布局）。"""
    return Path(project_root) / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"


def changes_path(project_root, ch: int) -> Path:
    """CHANGES 数据文件标准路径。"""
    return Path(project_root) / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"


def find_body_file(project_root, ch: int):
    """定位正文 txt（兼容 4 布局 + 平铺旧布局），找不到返回 None。"""
    root = Path(project_root)
    candidates = [
        root / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt",
        root / "章节" / f"第{ch}章" / f"第{ch}章.txt",
        root / f"第{ch:03d}章.txt",
        root / f"第{ch}章.txt",
    ]
    for c in candidates:
        if c.is_file():
            return c
    # rglob 兜底（排除归档 / 临时 / changes.json）
    for pat in (f"第{ch:03d}章*.txt", f"第{ch}章*.txt"):
        for f in sorted(root.rglob(pat)):
            if ("_archive" not in f.parts and "_tmp" not in f.parts
                    and not f.name.endswith("_changes.json")):
                return f
    return None


# ============ 读 ============

def _strip_changes(text: str) -> str:
    """从混合 txt 中剥离 CHANGES 段，返回纯正文。"""
    for sep in CHANGES_SEPARATORS:
        if sep in text:
            return text.split(sep)[0].rstrip()
    return text.rstrip()


def read_body(project_root, ch: int) -> str:
    """读纯正文。v18 已分离时直接读 txt；遇到旧混合 txt 自动剥离 CHANGES 段。"""
    f = find_body_file(project_root, ch)
    if not f:
        raise FileNotFoundError(f"第{ch}章正文未找到: {project_root}")
    return _strip_changes(f.read_text(encoding="utf-8"))


def parse_legacy_changes(raw_text: str) -> dict:
    """从旧混合 txt 文本中解析出 {"factual": {...}, "self_eval": {...}}。"""
    out = {"factual": {}, "self_eval": {}}
    for sep in CHANGES_SEPARATORS:
        if sep in raw_text:
            seg = raw_text.split(sep, 1)[1]
            for end in END_MARKERS:
                if end in seg:
                    seg = seg.split(end, 1)[0]
                    break
            seg = seg.split(SELF_EVAL_SEP, 1)[0]
            try:
                out["factual"] = json.loads(seg.strip())
            except json.JSONDecodeError:
                pass
            break
    if SELF_EVAL_SEP in raw_text:
        seg = raw_text.split(SELF_EVAL_SEP, 1)[1]
        for end in END_MARKERS:
            if end in seg:
                seg = seg.split(end, 1)[0]
                break
        try:
            out["self_eval"] = json.loads(seg.strip())
        except json.JSONDecodeError:
            pass
    return out


def read_changes(project_root, ch: int) -> dict:
    """读 CHANGES 数据，返回 {"factual": {...}, "self_eval": {...}}。
    优先读 _changes.json；不存在则从旧混合 txt 解析（迁移期兼容）。"""
    cp = changes_path(project_root, ch)
    if cp.is_file():
        data = json.loads(cp.read_text(encoding="utf-8"))
        data.setdefault("factual", {})
        data.setdefault("self_eval", {})
        return data
    f = find_body_file(project_root, ch)
    if not f:
        return {"factual": {}, "self_eval": {}}
    return parse_legacy_changes(f.read_text(encoding="utf-8"))


# ============ 写 ============

def write_body(project_root, ch: int, text: str) -> Path:
    """写纯正文到标准路径（章节/第NNN章/第NNN章.txt）。"""
    p = body_path(project_root, ch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")
    return p


def write_changes(project_root, ch: int, changes: dict) -> Path:
    """写 CHANGES 到 _changes.json。changes 须含 factual / self_eval 键；
    若传入裸 factual dict 则自动包装。"""
    if "factual" not in changes and "self_eval" not in changes:
        changes = {"factual": changes, "self_eval": {}}
    changes.setdefault("factual", {})
    changes.setdefault("self_eval", {})
    p = changes_path(project_root, ch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ============ 字数（统一口径，杜绝各脚本各算各的）============

def count_words(text: str) -> int:
    """统一字数口径：去除所有空白字符后的字符数（含标点）。
    全系统统一用此函数。用户语境的『N 字』即指此口径。"""
    return len(re.sub(r"\s", "", text))


def count_cjk(text: str) -> int:
    """纯中日韩文字数（不含标点 / 空白 / 数字 / 字母）。"""
    return len(re.findall(r"[一-鿿㐀-䶿]", text))


# ============ 迁移 ============

def migrate_legacy_chapter(project_root, ch: int) -> bool:
    """把旧的『正文+CHANGES混合』txt 拆成 txt（纯正文）+ _changes.json。
    返回 True=已迁移，False=无需迁移（已分离 / 文件不存在）。"""
    f = find_body_file(project_root, ch)
    if not f:
        return False
    raw = f.read_text(encoding="utf-8")
    if not any(sep in raw for sep in CHANGES_SEPARATORS):
        return False
    body = _strip_changes(raw)
    changes = parse_legacy_changes(raw)
    write_body(project_root, ch, body)
    write_changes(project_root, ch, changes)
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "migrate":
        proj, ch = sys.argv[2], int(sys.argv[3])
        ok = migrate_legacy_chapter(proj, ch)
        print(f"[migrate] 第{ch}章: {'已拆分为 txt + _changes.json' if ok else '无需迁移'}")
    elif len(sys.argv) >= 4 and sys.argv[1] == "wordcount":
        proj, ch = sys.argv[2], int(sys.argv[3])
        body = read_body(proj, ch)
        print(f"第{ch}章 正文字数(count_words)={count_words(body)} 纯汉字(count_cjk)={count_cjk(body)}")
    else:
        print("用法: chapter_io.py migrate <项目路径> <章节号>")
        print("      chapter_io.py wordcount <项目路径> <章节号>")
