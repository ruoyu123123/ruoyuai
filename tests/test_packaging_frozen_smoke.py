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
                "prose_rhythm_scanner",   # path6 multi-call 真 scanner
                "keyring.backends.Windows", "keyring.backends.fail", "dotenv"]
    for mod in required:
        assert f'"{mod}"' in spec or f"'{mod}'" in spec, f"spec hiddenimports 缺 {mod}"


def test_ruoyu_gui_has_frozen_dispatcher():
    """ruoyu_gui.py 入口须含 frozen multi-call dispatcher（exe 兼当 fan-out 解释器）。"""
    src = (_ROOT / "ruoyu_gui.py").read_text(encoding="utf-8")
    assert "dispatch_or_none" in src and 'getattr(sys, "frozen"' in src, \
        "ruoyu_gui 缺 frozen dispatcher（方案 M·exe 自我再分派）"


def test_frozen_util_has_dispatcher_api():
    """frozen_util 须提供共享 dispatcher（ruoyu_gui + frozen_smoke 共用同一段）。"""
    src = (_ROOT / "core" / "scripts" / "frozen_util.py").read_text(encoding="utf-8")
    for fn in ("def is_script_dispatch", "def dispatch_or_none", "def scripts_dir"):
        assert fn in src, f"frozen_util 缺 {fn}"


# ============ 全 GUI onedir spec 守卫（阶段B）============
def test_gui_spec_exists_and_packaging_only():
    """ruoyu_gui.spec 权威版在 packaging/（根目录不留第二份·防构建歧义）。"""
    assert (PKG / "ruoyu_gui.spec").exists(), "缺 packaging/ruoyu_gui.spec"
    assert not (_ROOT / "ruoyu_gui.spec").exists(), \
        "根目录残留 ruoyu_gui.spec（应只留 packaging 版·防从旧 spec 误构建）"


def test_gui_spec_root_derivation():
    """spec 在 packaging/ → ROOT 必须上跳一级（SPECPATH/..）才是仓库根。"""
    spec = (PKG / "ruoyu_gui.spec").read_text(encoding="utf-8")
    assert 'os.path.join(SPECPATH, "..")' in spec, "ROOT 派生须 SPECPATH/..（落点变更必改）"


def test_gui_spec_bundles_critical_resources():
    """frozen 运行时只读资源全须进 datas（与源码 bundle_root() 定位对齐）。"""
    spec = (PKG / "ruoyu_gui.spec").read_text(encoding="utf-8")
    for token in ('collect_all("nicegui")',          # NiceGUI 资源（无 hook）
                  ".claude/agents",                   # judge prompt
                  "core/claude-home/plans",           # plan DAG
                  "core/claude-home/lessons",         # 跨项目教训（北极星·must_fix）
                  "gen_profiles.default.env",         # 内置回落 config
                  "subsystem_skeletons.json",         # scaffold
                  'collect_data_files("numpy")', 'collect_data_files("scipy")'):  # style_evaluator
        assert token in spec, f"ruoyu_gui.spec 缺关键资源 datas: {token}"


def test_gui_spec_excludes_torch_no_dotenv():
    """torch 排净 + 绝不打包 .env（安全铁律）。"""
    spec = (PKG / "ruoyu_gui.spec").read_text(encoding="utf-8")
    assert '"torch"' in spec, "spec 未 exclude torch（GB 级）"
    for ln in spec.splitlines():
        if ".env" in ln and "datas" not in ln.lower() and "SPECPATH" not in ln:
            assert "gen_profiles.default.env" in ln or ln.strip().startswith("#"), \
                f"spec 疑似打包 .env: {ln.strip()[:70]}"


def test_gui_spec_collects_all_scripts_as_hiddenimports():
    """全 core/scripts/*.py stem 须进 hiddenimports（fan-out dispatcher importlib by stem）。"""
    spec = (PKG / "ruoyu_gui.spec").read_text(encoding="utf-8")
    # spec 用 glob 动态生成（for fn in os.listdir(SCRIPTS) ... hiddenimports.append(fn[:-3])）
    assert "os.listdir(SCRIPTS)" in spec and "hiddenimports.append(fn[:-3])" in spec, \
        "spec 未用 glob 把全 core/scripts stem 收进 hiddenimports"


def test_gui_spec_console_env_toggle():
    """console 由 RUOYU_CONSOLE env 控（生产窗口模式·非技术用户双击不弹黑窗）。"""
    spec = (PKG / "ruoyu_gui.spec").read_text(encoding="utf-8")
    assert 'os.environ.get("RUOYU_CONSOLE"' in spec and "console=_CONSOLE" in spec, \
        "spec console 未走 RUOYU_CONSOLE env（build_all.py --production 需此 hook）"


def test_build_all_runbook_exists():
    """一键构建验证 runbook 存在（清理→构建→smoke/GUI 验证→安全闸·可复现出货）。"""
    src = (PKG / "build_all.py").read_text(encoding="utf-8")
    assert "validate_gui_exe" in src and "security_gate" in src and "--production" in src, \
        "build_all.py 缺关键阶段（GUI 验证 / 安全闸 / 生产模式）"


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
