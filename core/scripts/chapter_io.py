"""core/scripts/chapter_io.py

章节格式层的统一读写模块。正文与 `self_eval` 分文件保存；客观状态不进入章节文件。

【文件布局】（STRUCTURE.md 第三节 / 第十节）
  章节/第NNN章/第NNN章.txt          —— 纯正文
  章节/第NNN章/第NNN章_changes.json —— {"self_eval": {...}}

【_changes.json schema】
  {
    "self_eval": {
      ...,                    # writer 的自评字段
      "waivers": [            # 顾问制：AI 对 advisory 检测项的豁免清单
        {"code": "STYLE_拟声格式", "reason": "本章纯心理独白章，拟声不适配"}
      ],
      "uncertainty_flags": [  # writer 主动标的「自评不确定」项
        {"aspect": "POV_consistency", "detail": "...", "suggest_judge": "novel-voice-checker"}
      ]
    }
  }
  豁免协议：
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
  读:     read_body / read_changes
  写:     write_body / write_changes
  字数:   count_words（统一口径：非空白字符数）/ count_cjk（纯汉字）
"""

import json
import re
import sys
from pathlib import Path

# 章节正文 txt / _changes.json 是核心产物，写盘走 atomic_json（tmp pid+uuid + fsync +
# os.replace）——崩溃/断电不留半截章节文件（半截正文/JSON 会被下游 read_body/read_changes
# 当真消费）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json  # noqa: E402

# ============ 路径定位 ============

def find_chapter_dir(project_root, ch: int):
    """返回标准章节目录 Path（章节/第NNN章/），找不到返回 None。"""
    root = Path(project_root)
    directory = root / "章节" / f"第{ch:03d}章"
    return directory if directory.is_dir() else None


def body_path(project_root, ch: int) -> Path:
    """正文文件标准路径（嵌套 zero-pad 布局）。"""
    return Path(project_root) / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章.txt"


def changes_path(project_root, ch: int) -> Path:
    """CHANGES 数据文件标准路径。"""
    return Path(project_root) / "章节" / f"第{ch:03d}章" / f"第{ch:03d}章_changes.json"


def find_body_file(project_root, ch: int):
    """定位标准正文文件，找不到返回 None。"""
    path = body_path(project_root, ch)
    return path if path.is_file() else None


# ============ 读 ============

def read_body(project_root, ch: int) -> str:
    """读取标准章节正文。"""
    f = find_body_file(project_root, ch)
    if not f:
        raise FileNotFoundError(f"第{ch}章正文未找到: {project_root}")
    return f.read_text(encoding="utf-8").rstrip()


def normalize_changes(data: dict) -> dict:
    """把 writer changes 收口为唯一 `self_eval` 合同。"""
    if not isinstance(data, dict):
        return {"self_eval": {"waivers": [], "uncertainty_flags": []}}
    self_eval = data.get("self_eval")
    if not isinstance(self_eval, dict):
        self_eval = {}
    meta = data.get("ecas_metadata")
    if isinstance(meta, dict) and "ecas_metadata" not in self_eval:
        self_eval["ecas_metadata"] = meta
    self_eval.setdefault("waivers", [])
    self_eval.setdefault("uncertainty_flags", [])
    return {"self_eval": self_eval}


def read_changes(project_root, ch: int) -> dict:
    """读 CHANGES 数据，返回 {"factual": {...}, "self_eval": {...}}。
    只读分离的 _changes.json；经 normalize_changes 统一 schema，兼容 writer 的多种输出布局。"""
    cp = changes_path(project_root, ch)
    if cp.is_file():
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            # 损坏/空/半截 _changes.json 不崩溃——返回与缺文件同款兜底（Tolerant Reader
            # 哲学）：validate_chapter 等下游裸调 scanner 直接吃 JSONDecodeError 会让整条
            # 审核管线非零退出。
            return {"self_eval": {"waivers": [], "uncertainty_flags": []}}
        return normalize_changes(data)
    return {"self_eval": {"waivers": [], "uncertainty_flags": []}}


# ============ 写 ============

def write_body(project_root, ch: int, text: str) -> Path:
    """写纯正文到标准路径（章节/第NNN章/第NNN章.txt）。
    原子落盘（内容口径：rstrip + 末尾单 \\n；mkdir 由原子写内置）。"""
    p = body_path(project_root, ch)
    atomic_json.atomic_write_text(p, text.rstrip() + "\n")
    return p


def write_changes(project_root, ch: int, changes: dict) -> Path:
    """写唯一 `self_eval` changes 合同。"""
    changes = normalize_changes(changes)
    p = changes_path(project_root, ch)
    # 半截 _changes.json 会让下游 read_changes/audit_hub 解析崩。
    atomic_json.atomic_write_text(p, json.dumps(changes, ensure_ascii=False, indent=2))
    return p


# ============ 字数（统一口径，杜绝各脚本各算各的）============

def count_words(text: str) -> int:
    """统一字数口径：去除所有空白字符后的字符数（含标点）。
    全系统统一用此函数。用户语境的『N 字』即指此口径。"""
    return len(re.sub(r"\s", "", text))


def count_cjk(text: str) -> int:
    """纯中日韩文字数（不含标点 / 空白 / 数字 / 字母）。"""
    return len(re.findall(r"[一-鿿㐀-䶿]", text))


# ============ 工具 ============

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "wordcount":
        proj, ch = sys.argv[2], int(sys.argv[3])
        body = read_body(proj, ch)
        print(f"第{ch}章 正文字数(count_words)={count_words(body)} 纯汉字(count_cjk)={count_cjk(body)}")
    else:
        print("用法: chapter_io.py migrate <项目路径> <章节号>")
        print("      chapter_io.py wordcount <项目路径> <章节号>")
