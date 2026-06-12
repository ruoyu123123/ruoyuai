#!/usr/bin/env python3
"""设定库只读查看器 read_db_json 三态单测（P0-3 正典毒化第一级缓解）。

零依赖纯逻辑（不 import nicegui·state.py 零 GUI 依赖层）：
正常 JSON 重排 / 损坏 JSON 返原文+提示 / 缺文件提示 + 白名单拒绝（防越界读）。
运行：python tests/gui/test_gui_db_viewer.py 或 python -m pytest tests/gui -q
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
for p in (str(_ROOT), str(_ROOT / "core" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.gui import state as gs  # noqa: E402


def _mk_db(tmp) -> Path:
    root = Path(tmp) / "书"
    (root / "_数据库").mkdir(parents=True)
    return root


# ============ 态①：正常 JSON → ensure_ascii=False + indent=2 重排 ============
def test_read_db_json_normal_reformatted():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)
        (root / "_数据库" / "人物卡.json").write_text(
            '{"characters":[{"name":"重黎","locked_facts":["断天者·留缝"]}]}',
            encoding="utf-8")
        out = gs.read_db_json(root, "人物卡")
        # 内容等价（重排不改语义）
        assert json.loads(out) == {
            "characters": [{"name": "重黎", "locked_facts": ["断天者·留缝"]}]}
        assert "重黎" in out and "\\u" not in out   # ensure_ascii=False 中文直出
        assert "\n  " in out                        # indent=2 真重排（非单行原文）


# ============ 态②：损坏 JSON → 前缀提示行 + 原文原样 ============
def test_read_db_json_corrupt_returns_raw_with_hint():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)
        (root / "_数据库" / "事件簇.json").write_text("{坏json：缺引号",
                                                    encoding="utf-8")
        out = gs.read_db_json(root, "事件簇")
        assert out.startswith("⚠️")                 # 提示行在前
        assert "不是合法 JSON" in out
        assert "{坏json：缺引号" in out              # 原文原样保留（只看不改）


def test_read_db_json_bad_utf8_bytes_no_crash():
    """非法 UTF-8 字节 → errors=replace 不抛 UnicodeDecodeError·走损坏展示路径。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)
        (root / "_数据库" / "世界状态.json").write_bytes(b"\xff\xfe\x00bad")
        out = gs.read_db_json(root, "世界状态")      # 不抛
        assert "不是合法 JSON" in out


# ============ 态③：缺文件 → 中文提示（缺文件不是故障） ============
def test_read_db_json_missing_file_hint():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)                          # 只有空 _数据库/
        out = gs.read_db_json(root, "大势卡")
        assert "还不存在" in out and "大势卡" in out


# ============ 白名单：越界名拒绝（含路径穿越形态） ============
def test_read_db_json_non_whitelist_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)
        # 进度.json 是真实存在的库文件·但不在查看白名单
        (root / "_数据库" / "进度.json").write_text("{}", encoding="utf-8")
        assert "不支持查看" in gs.read_db_json(root, "进度")
        # 路径穿越形态的 name 也走白名单拒绝（根本到不了拼路径）
        assert "不支持查看" in gs.read_db_json(root, "../../.env")


def test_db_view_whitelist_matches_real_filenames():
    """白名单与 scaffold 真实文件名对齐锚（人物卡/世界状态/伏笔表/大势卡/事件簇）——
    锁定事实无独立文件·挂人物卡.locked_facts（locked_fact_cross_scene_scanner 同源）。"""
    assert gs.DB_VIEW_FILES == ("人物卡", "世界状态", "伏笔表", "大势卡", "事件簇")
    # 全员都能在三态函数里走「缺文件提示」而非异常（契约：永远返回可展示字符串）
    with tempfile.TemporaryDirectory() as tmp:
        root = _mk_db(tmp)
        for n in gs.DB_VIEW_FILES:
            out = gs.read_db_json(root, n)
            assert isinstance(out, str) and out


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
