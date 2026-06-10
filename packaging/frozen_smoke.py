#!/usr/bin/env python3
"""frozen_smoke.py — 阶段A frozen 架构 de-risk harness（PyInstaller onedir · console）

只在真 sys.frozen 下有意义：逐条断言 dev 永远验不到的 frozen 路径。
绝不 import core.gui.* / nicegui / torch / scipy / embedding_store。
诊断走 stderr（feedback_verify_stderr_not_exitcode：验证看 stderr Traceback）。
退出码：0=全过 · 1=有 frozen 路径 FAIL · 2=环境前置错（非 frozen / bootstrap 失败）。

验证的 frozen 路径（对抗审查抓的 FATAL 已在源码修：gen_model_loader/orchestrator 改
frozen_util.bundle_root() 定位·非 __file__ 推算）：
  path1  run_script_in_process 进程内 importlib（正例 rc=0 + 无 main 负例 rc=3 + import-fail rc=3）
  path1b 被试真模块 import 后可执行（FrozenImporter 收到真模块）
  path2  child_python + RUOYU_PYTHON 解析（env 设→返回它·缺→回退 sys.executable+告警）
  path3  secrets_store.is_available()（WinVault 收没收·真活则 set/get/delete 回环）
  path4  config 第三级回落 → bundle_root()/core/config（_dist_mode True·env_path 命中）
  path5  orchestrator.REPO_ROOT == bundle_root()（frozen-aware·非指 bundle 外）
"""
import multiprocessing
multiprocessing.freeze_support()          # 🔴 首句铁律（防 spawn fork 炸）

import os
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# bootstrap sys.path：onedir 下 _internal/core/scripts 须可 import
_ROOT = Path(sys.executable).resolve().parent
_INTERNAL = _ROOT / "_internal"
for _base in (_INTERNAL, _ROOT):
    for _sub in ("", "core/scripts"):
        _p = _base / _sub
        if _p.exists() and str(_p) not in sys.path:
            sys.path.insert(0, str(_p))

if not getattr(sys, "frozen", False):
    print("SKIP: 非 frozen 环境，本 harness 只在 onedir exe 内有意义", file=sys.stderr)
    sys.exit(2)

_IMPORT_ERRS = []
for _name in ("orchestrator", "secrets_store", "frozen_util", "gen_model_loader"):
    try:
        __import__(_name)
    except Exception as e:
        _IMPORT_ERRS.append((f"import {_name}", repr(e)))

if any(n == "import orchestrator" for n, _ in _IMPORT_ERRS):
    for n, d in _IMPORT_ERRS:
        print(f"[FAIL] {n}: {d}", file=sys.stderr)
    print("[FROZEN-SMOKE] BOOTSTRAP FAIL（核心模块未收进 bundle）", file=sys.stderr)
    sys.exit(2)

import orchestrator      # noqa: E402
import secrets_store     # noqa: E402
import frozen_util       # noqa: E402
import gen_model_loader  # noqa: E402

FAILS = list(_IMPORT_ERRS)
for n, d in _IMPORT_ERRS:
    print(f"[FAIL] {n}: {d}", file=sys.stderr)


def check(name, fn):
    try:
        fn()
        print(f"[PASS] {name}", file=sys.stderr)
        print(f"[PASS] {name}")
    except Exception as e:
        import traceback
        FAILS.append((name, repr(e)))
        print(f"[FAIL] {name}: {e!r}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(f"[FAIL] {name}: {e!r}")


def _path1_inproc():
    rc = orchestrator.run_script_in_process(
        ["packaging/_smoke_inproc_target.py"], repo_root=orchestrator.REPO_ROOT,
        label="smoke-pos")
    assert rc == 0, f"_smoke_inproc_target 期望 rc=0，实得 {rc}"
    rc_nomain = orchestrator.run_script_in_process(
        ["core/scripts/cluster_lookup.py"], repo_root=orchestrator.REPO_ROOT,
        label="smoke-nomain")
    assert rc_nomain == 3, f"无 main() 脚本期望 rc=3，实得 {rc_nomain}"
    rc_noimp = orchestrator.run_script_in_process(
        ["core/scripts/__nope_xyz_smoke__.py"], repo_root=orchestrator.REPO_ROOT,
        label="smoke-noimp")
    assert rc_noimp == 3, f"import-fail 脚本期望 rc=3，实得 {rc_noimp}"


def _path1b_real_module():
    import cluster_lookup
    assert cluster_lookup.normalize_cluster_id(6) == "cluster_006"
    assert cluster_lookup.normalize_cluster_id("cluster_2") == "cluster_002"
    assert cluster_lookup.cluster_num("cluster_012") == 12


def _path2_child_python():
    saved = os.environ.get("RUOYU_PYTHON")
    saved_warned = frozen_util._warned
    try:
        os.environ["RUOYU_PYTHON"] = r"X:\fake\python.exe"
        got = frozen_util.child_python()
        assert got == r"X:\fake\python.exe", f"env 分支期望原样，实得 {got!r}"
        os.environ.pop("RUOYU_PYTHON", None)
        frozen_util._warned = False
        got2 = frozen_util.child_python()
        assert got2 == sys.executable, f"缺失回退期望 sys.executable，实得 {got2!r}"
        assert frozen_util._warned is True, "缺失 RUOYU_PYTHON 未触发告警标志"
    finally:
        if saved is None:
            os.environ.pop("RUOYU_PYTHON", None)
        else:
            os.environ["RUOYU_PYTHON"] = saved
        frozen_util._warned = saved_warned


def _path3_secrets():
    av = secrets_store.is_available()
    assert isinstance(av, bool), f"is_available 须返 bool，实得 {type(av).__name__}"
    print(f"  [info] secrets_store.is_available() = {av}", file=sys.stderr)
    if av:
        probe = "__smoke_probe__"
        if secrets_store.set_api_key(probe, "x"):
            assert secrets_store.get_api_key(probe) == "x", "set 后 get 不一致"
            secrets_store.delete_api_key(probe)
            assert secrets_store.get_api_key(probe) is None, "delete 后仍读到"
            print("  [info] WinVault set→get→delete 回环通过（后端真活）", file=sys.stderr)
        else:
            print("  [info] is_available=True 但 set 返 False（写入受限·非 FAIL）",
                  file=sys.stderr)
    else:
        print("  [info] keyring 后端不可用·is_available 正确返 False", file=sys.stderr)


def _path4_config_fallback():
    import tempfile
    import shutil
    saved_cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="smoke_noenv_")
    try:
        os.chdir(tmp)                                  # cwd 无 .env
        gen_model_loader.reset_default_loader()
        loader = gen_model_loader.GenModelLoader()     # 不传 env_path → 第三级 builtin
        assert loader._dist_mode is True, f"_dist_mode 期望 True，实得 {loader._dist_mode}"
        ep = Path(loader.env_path)
        assert ep.exists(), f"config 不存在（datas 没对上）: {ep}"
        assert ep.name == "gen_profiles.default.env", f"env_path 名错: {ep.name}"
        resolved = ep.resolve()
        assert resolved.parent.name == "config", f"config 父目录名错: {resolved.parent.name}"
        assert resolved.parent.parent.name == "core", f"core 名错: {resolved.parent.parent.name}"
        print(f"  [info] loader.env_path.resolve() = {resolved}", file=sys.stderr)
        names = {p.name for p in loader.list_profiles()}
        assert "gemini_pro_preview" in names, f"config 未解析出 preview，实得 {sorted(names)}"
    finally:
        os.chdir(saved_cwd)
        gen_model_loader.reset_default_loader()
        shutil.rmtree(tmp, ignore_errors=True)


def _path5_repo_root_frozen_aware():
    # frozen-aware：REPO_ROOT == bundle_root() == _MEIPASS（非指 bundle 外·FATAL 修复）
    assert orchestrator.REPO_ROOT == frozen_util.bundle_root(), \
        f"REPO_ROOT({orchestrator.REPO_ROOT}) != bundle_root({frozen_util.bundle_root()})"
    print(f"  [info] orchestrator.REPO_ROOT = {orchestrator.REPO_ROOT}", file=sys.stderr)


check("path1_run_script_in_process(pos+neg)", _path1_inproc)
check("path1b_real_module_executable", _path1b_real_module)
check("path2_child_python_RUOYU_PYTHON", _path2_child_python)
check("path3_secrets_store_is_available", _path3_secrets)
check("path4_config_third_fallback_bundle", _path4_config_fallback)
check("path5_repo_root_frozen_aware", _path5_repo_root_frozen_aware)

if FAILS:
    print(f"=== SMOKE FAIL {len(FAILS)} 失败项 ===", file=sys.stderr)
    print(f"=== SMOKE FAIL {len(FAILS)} 失败项 ===")
    for n, d in FAILS:
        print(f"    - {n}: {d}", file=sys.stderr)
    sys.exit(1)
print("=== SMOKE PASS 6/6 === [FROZEN-SMOKE] ALL PASS", file=sys.stderr)
print("=== SMOKE PASS 6/6 === [FROZEN-SMOKE] ALL PASS")
sys.exit(0)
