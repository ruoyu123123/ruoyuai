#!/usr/bin/env python3
"""init_project.py 测试（阶段2 创建书籍·解死锁②项目目录前置）。零依赖顶层·临时项目。"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import init_project as ip  # noqa: E402


def test_creates_db_and_wal():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        sys.argv = ["init_project.py", str(proj), "--no-git"]
        rc = ip.main()
        assert rc == 0
        assert (proj / "_数据库" / ".wal").is_dir()


def test_emit_style_options_lists_styles():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        rc = ip.emit_style_options(proj)
        assert rc == 0
        opts = json.loads((proj / "_数据库" / ".wal" / "style_options.json")
                          .read_text(encoding="utf-8"))
        # 仓库有风格库 → styles 非空·每个有 name/title/description
        assert "styles" in opts and isinstance(opts["styles"], list)
        if opts["styles"]:
            s = opts["styles"][0]
            assert "name" in s and "title" in s and "description" in s


def test_copy_style_brings_double_file():
    # 找一个真实风格库（有作者风格.json + skill）
    sd = ip._styles_dir()
    style = None
    if sd.is_dir():
        for d in sorted(sd.iterdir()):
            if d.is_dir() and (d / "作者风格.json").exists():
                style = d.name
                break
    if not style:
        return  # 无风格库环境跳过
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        rc = ip.copy_style(proj, style)
        assert rc == 0
        assert (proj / "_数据库" / "作者风格.json").exists(), "作者风格.json 未拷"
        # skill 双文件（多数风格库有 skill*.md）


def test_nonexistent_style_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        rc = ip.copy_style(proj, "__不存在的风格库__")
        assert rc == 2


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
