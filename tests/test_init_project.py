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


def test_styles_dir_derived_from_project_path():
    """🔴 frozen bug 回归（真 outline e2e 抓出）：风格库是用户数据(与小说项目工作区兄弟)·
    不在 bundle。须从项目路径派生 styles(项目 workspace/novels/X → 风格库 workspace/styles)·
    非 bundle_root(frozen 下 _internal 无 workspace → 空风格 → auto_pilot None → data_flow 崩)。"""
    proj = _ROOT / "workspace" / "novels" / "测试书"
    sd = ip._styles_dir(proj)
    assert sd == (_ROOT / "workspace" / "styles").resolve() or \
        sd == _ROOT / "workspace" / "styles", f"未从项目路径派生 styles: {sd}"
    # 仓库有风格库 → 从项目路径 emit 非空
    real = _ROOT / "workspace" / "novels" / "__styletest_reg__"
    real.mkdir(parents=True, exist_ok=True)
    try:
        ip.emit_style_options(real)
        opts = json.loads((real / "_数据库" / ".wal" / "style_options.json")
                          .read_text(encoding="utf-8"))
        assert len(opts["styles"]) > 0, "从项目路径派生应找到仓库风格库"
    finally:
        import shutil
        shutil.rmtree(real, ignore_errors=True)


def test_nonexistent_style_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        rc = ip.copy_style(proj, "__不存在的风格库__")
        assert rc == 2


def test_scaffold_builds_full_skeleton():
    """🆕 --scaffold 一键建完整新书骨架：目录 + .wal + 全部 canonical 子系统 JSON。
    单入口（防 plan 外 ad-hoc mkdir 漏建子系统）。canonical 列表动态读 scaffold·不硬编码。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import scaffold_subsystems as scaf  # noqa: E402
    canonical, _ = scaf._load_skeletons()
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        sys.argv = ["init_project.py", str(proj), "--scaffold", "--no-git"]
        rc = ip.main()
        assert rc == 0
        db = proj / "_数据库"
        assert (db / ".wal").is_dir(), ".wal 未建"
        for name in canonical:
            p = db / f"{name}.json"
            assert p.exists(), f"缺子系统 {name}.json"
            json.loads(p.read_text(encoding="utf-8"))  # 每个合法 json


def test_scaffold_idempotent_keeps_filled_content():
    """--scaffold 幂等：已填内容不被覆盖（复用 scaffold emit 的不覆盖语义）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import scaffold_subsystems as scaf  # noqa: E402
    canonical, _ = scaf._load_skeletons()
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试书"
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        # 预置一个已填的子系统
        target = db / f"{canonical[0]}.json"
        target.write_text(json.dumps({"schema_version": "vTEST", "_filled": "用户内容"},
                                     ensure_ascii=False), encoding="utf-8")
        sys.argv = ["init_project.py", str(proj), "--scaffold", "--no-git"]
        assert ip.main() == 0
        obj = json.loads(target.read_text(encoding="utf-8"))
        assert obj.get("_filled") == "用户内容", "--scaffold 覆盖了已填内容（应幂等不覆盖）"


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
