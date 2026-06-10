#!/usr/bin/env python3
"""packaging/frozen_smoke 一致性守卫（dev·零依赖顶层）。

不构建 exe（那是手动/CI 步骤）——只锁住「smoke 入口 + spec + 被试 target」三者一致，
防未来源码漂移悄悄打破真 onedir 验证。真 frozen 验证见 packaging/frozen_smoke.exe 手跑
（已实证 6/6 PASS + 敌对验证 path4 可被移走 config 抓到）。
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PKG = _ROOT / "packaging"


def test_packaging_files_exist():
    for f in ("frozen_smoke.py", "_smoke_inproc_target.py", "frozen_smoke.spec"):
        assert (PKG / f).exists(), f"缺 packaging/{f}"


def test_inproc_target_has_main():
    """run_script_in_process 调 module.main()——被试 target 必须有 def main()。"""
    src = (PKG / "_smoke_inproc_target.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "main"
               for n in tree.body), "_smoke_inproc_target 缺 def main()"


def test_smoke_dev_run_is_skip_exit2():
    """dev 跑 smoke → 前置闸 SKIP exit 2（不污染 dev 测试·不误判 frozen）。"""
    r = subprocess.run([sys.executable, str(PKG / "frozen_smoke.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 2, f"dev smoke 期望 exit 2，实得 {r.returncode}"
    assert "SKIP" in r.stderr


def test_spec_hiddenimports_cover_smoke_imports():
    """spec 的 hiddenimports 须覆盖 smoke + run_script_in_process 进程内 import 的项目模块
    （FrozenImporter 只认 PYZ·漏一个 frozen 下就 import 失败）。"""
    spec = (PKG / "frozen_smoke.spec").read_text(encoding="utf-8")
    required = ["_smoke_inproc_target", "cluster_lookup", "orchestrator",
                "plan_tracker", "secrets_store", "frozen_util", "gen_model_loader",
                "keyring.backends.Windows", "keyring.backends.fail", "dotenv"]
    for mod in required:
        assert f'"{mod}"' in spec or f"'{mod}'" in spec, f"spec hiddenimports 缺 {mod}"


def test_spec_excludes_torch():
    """torch 已装(GB 级)·spec 必排（否则 onedir 体积爆·实证 _internal 33MB 因排了）。"""
    spec = (PKG / "frozen_smoke.spec").read_text(encoding="utf-8")
    for mod in ("torch", "sentence_transformers", "transformers"):
        assert f'"{mod}"' in spec, f"spec excludes 缺 {mod}"


def test_spec_never_bundles_dotenv():
    """安全铁律：spec datas 绝不列 .env（防卷入开发者私钥）。"""
    spec = (PKG / "frozen_smoke.spec").read_text(encoding="utf-8")
    # datas 段里不得出现裸 ".env"（仅 gen_profiles.default.env 这种允许）
    for ln in spec.splitlines():
        if ".env" in ln and "datas" not in ln.lower():
            # 允许 gen_profiles.default.env / 注释
            assert "gen_profiles.default.env" in ln or ln.strip().startswith("#"), \
                f"spec 疑似打包 .env: {ln.strip()[:70]}"


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
