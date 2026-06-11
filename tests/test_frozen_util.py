#!/usr/bin/env python3
"""frozen_util.child_python() 解析逻辑测试（对抗审查 finding #1·M4 frozen fan-out）。

dev=no-op(sys.executable) / frozen+RUOYU_PYTHON=bundled / frozen 缺 env=回退+告警。
真 onedir 端到端需建 exe 验，本测只锁解析逻辑（monkeypatch frozen + env）。
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import frozen_util as fu  # noqa: E402


def test_dev_returns_sys_executable():
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.child_python() == sys.executable
        assert fu.is_frozen() is False
    finally:
        sys.frozen = saved


# ============ bundle_root frozen-aware（对抗审查 FATAL 修复）============
def test_bundle_root_dev_is_repo_root():
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        # dev：core/scripts 的 parents[2] = 仓库根
        repo = Path(__file__).resolve().parent.parent
        assert fu.bundle_root() == repo
        assert fu.resource_path("core", "config") == repo / "core" / "config"
    finally:
        sys.frozen = saved


def test_bundle_root_frozen_is_meipass():
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    sys.frozen = True
    sys._MEIPASS = r"X:\app\_internal"
    try:
        assert fu.bundle_root() == Path(r"X:\app\_internal")
        assert fu.resource_path("core", "config", "x.env") == \
            Path(r"X:\app\_internal") / "core" / "config" / "x.env"
    finally:
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m


def test_bundle_root_frozen_no_meipass_falls_to_exe_dir():
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    sys.frozen = True
    if hasattr(sys, "_MEIPASS"):
        del sys._MEIPASS
    try:
        assert fu.bundle_root() == Path(sys.executable).resolve().parent
    finally:
        sys.frozen = saved_f
        if saved_m is not None:
            sys._MEIPASS = saved_m


def test_loader_builtin_cfg_frozen_aware():
    """gen_model_loader 在 frozen 下从 bundle_root() 定位 config（非 __file__ 推算）。"""
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
    import gen_model_loader as gml
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    import tempfile
    meipass = tempfile.mkdtemp()
    cfgdir = Path(meipass) / "core" / "config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "gen_profiles.default.env").write_text(
        "GEN__z__MODEL=zz\nGEN_MODEL_ACTIVE=z\n", encoding="utf-8")
    sys.frozen = True
    sys._MEIPASS = meipass
    cwd = os.getcwd()
    try:
        os.chdir(meipass)            # 无 .env 的 cwd → 落第三级 builtin
        gml.reset_default_loader()
        ld = gml.GenModelLoader()
        assert ld._dist_mode is True
        assert Path(ld.env_path).resolve() == (cfgdir / "gen_profiles.default.env").resolve()
        assert "z" in {p.name for p in ld.list_profiles()}
    finally:
        os.chdir(cwd)
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m
        gml.reset_default_loader()
        import shutil
        shutil.rmtree(meipass, ignore_errors=True)


def test_frozen_with_ruoyu_python_uses_bundled():
    saved = getattr(sys, "frozen", False)
    saved_env = os.environ.get("RUOYU_PYTHON")
    sys.frozen = True
    os.environ["RUOYU_PYTHON"] = r"C:\app\_internal\python.exe"
    try:
        assert fu.child_python() == r"C:\app\_internal\python.exe"
        assert fu.is_frozen() is True
    finally:
        sys.frozen = saved
        if saved_env is None:
            os.environ.pop("RUOYU_PYTHON", None)
        else:
            os.environ["RUOYU_PYTHON"] = saved_env


def test_frozen_without_env_returns_exe_for_dispatch():
    """方案 M：frozen 缺 RUOYU_PYTHON → 返 exe 本体（dispatcher 自我再分派·设计正道·
    不再告警）。"""
    saved = getattr(sys, "frozen", False)
    saved_env = os.environ.get("RUOYU_PYTHON")
    sys.frozen = True
    os.environ.pop("RUOYU_PYTHON", None)
    try:
        assert fu.child_python() == sys.executable      # exe 本体·dispatcher 接住
        assert not hasattr(fu, "_warned")               # 告警机制已删（M 不告警）
    finally:
        sys.frozen = saved
        if saved_env is not None:
            os.environ["RUOYU_PYTHON"] = saved_env


# ============ multi-call dispatcher（方案 M）============
def test_is_script_dispatch_dev_never():
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.is_script_dispatch(["exe", "core/scripts/x.py"]) is False  # dev 永不派发
    finally:
        sys.frozen = saved


def test_is_script_dispatch_whitelist_and_guards():
    import tempfile
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    meipass = tempfile.mkdtemp()
    sdir = Path(meipass) / "core" / "scripts"
    sdir.mkdir(parents=True)
    (sdir / "prose_rhythm_scanner.py").write_text("x=1\n", encoding="utf-8")
    sys.frozen = True
    sys._MEIPASS = meipass
    try:
        good = str(sdir / "prose_rhythm_scanner.py")
        assert fu.is_script_dispatch(["exe", good]) is True            # 白名单命中
        assert fu.is_script_dispatch(["exe"]) is False                 # 裸跑→GUI
        assert fu.is_script_dispatch(["exe", "--native"]) is False     # flag→GUI
        assert fu.is_script_dispatch(["exe", "--port", "9000"]) is False
        assert fu.is_script_dispatch(["exe", str(sdir / "ghost.py")]) is False  # 不存在
        # 任意 bundle 外 .py（安全·非白名单根）→ False
        outside = Path(meipass) / "evil.py"
        outside.write_text("x=1", encoding="utf-8")
        assert fu.is_script_dispatch(["exe", str(outside)]) is False
    finally:
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m
        import shutil
        shutil.rmtree(meipass, ignore_errors=True)


def test_scripts_dir_dev_is_core_scripts():
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.scripts_dir() == Path(__file__).resolve().parent.parent / "core" / "scripts"
    finally:
        sys.frozen = saved


def test_user_data_dir_dev_is_repo_root():
    """user_data_dir() dev=仓库根（writable 锚点逐字节零回归）·frozen=%APPDATA%/ruoyuai。"""
    saved = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.user_data_dir() == Path(__file__).resolve().parent.parent
    finally:
        sys.frozen = saved


def test_user_data_dir_frozen_is_appdata():
    saved_f = getattr(sys, "frozen", False)
    saved_a = os.environ.get("APPDATA")
    sys.frozen = True
    import tempfile
    tmp = tempfile.mkdtemp()
    os.environ["APPDATA"] = tmp
    try:
        assert fu.user_data_dir() == Path(tmp) / "ruoyuai"
        assert fu.user_data_dir().exists()    # 自动 mkdir
    finally:
        sys.frozen = saved_f
        if saved_a is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = saved_a
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_user_workspace_dir_dev_and_frozen():
    """user_workspace_dir() dev=仓库根/workspace(零回归)·frozen=%APPDATA%/ruoyuai/workspace。"""
    saved_f = getattr(sys, "frozen", False)
    sys.frozen = False
    try:
        assert fu.user_workspace_dir() == Path(__file__).resolve().parent.parent / "workspace"
    finally:
        sys.frozen = saved_f
    saved_a = os.environ.get("APPDATA")
    import tempfile
    tmp = tempfile.mkdtemp()
    sys.frozen = True
    os.environ["APPDATA"] = tmp
    try:
        assert fu.user_workspace_dir() == Path(tmp) / "ruoyuai" / "workspace"
    finally:
        sys.frozen = saved_f
        if saved_a is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = saved_a
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_workspace_anchors_frozen_aware():
    """plan_tracker.PROJECTS_DIR/STYLES_DIR + state.NOVELS_DIR frozen 下迁 user_workspace
    （GUI 建书落点 + resolve_project_root 统一·真 outline e2e 抓出）。dev 仍仓库根/workspace。"""
    _scripts = str(Path(__file__).resolve().parent.parent / "core" / "scripts")
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    import importlib
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    saved_a = os.environ.get("APPDATA")
    import tempfile
    tmp = tempfile.mkdtemp()
    sys.frozen = True
    sys._MEIPASS = str(Path(tmp) / "_internal")
    os.environ["APPDATA"] = tmp
    try:
        import frozen_util as _fu
        importlib.reload(_fu)
        ws = _fu.user_workspace_dir()
        import plan_tracker as pt
        importlib.reload(pt)
        assert str(pt.PROJECTS_DIR).startswith(str(ws)), "PROJECTS_DIR 未迁 user_workspace"
        assert str(pt.STYLES_DIR).startswith(str(ws)), "STYLES_DIR 未迁 user_workspace"
    finally:
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m
        if saved_a is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = saved_a
        import frozen_util as _fu
        importlib.reload(_fu)
        if "plan_tracker" in sys.modules:
            importlib.reload(sys.modules["plan_tracker"])
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_writable_anchors_frozen_aware():
    """MAPE-K runtime / plan attest / model cache 等可写系统数据 frozen 下迁用户态
    （dev 仍仓库根·零回归）——否则 frozen 写只读 bundle 必崩。"""
    _scripts = str(Path(__file__).resolve().parent.parent / "core" / "scripts")
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    import importlib
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    saved_a = os.environ.get("APPDATA")
    import tempfile
    tmp = tempfile.mkdtemp()
    sys.frozen = True
    sys._MEIPASS = str(Path(tmp) / "_internal")
    os.environ["APPDATA"] = tmp
    try:
        import frozen_util as _fu
        importlib.reload(_fu)
        ud = _fu.user_data_dir()
        import self_heal_engine as she
        importlib.reload(she)
        assert str(she.REPO_ROOT).startswith(str(ud)), "self_heal runtime 未迁用户态"
        import adaptive_runner as ar
        importlib.reload(ar)
        assert str(ar.REPO_ROOT).startswith(str(ud)), "adaptive runtime 未迁用户态"
        import plan_tracker as pt
        importlib.reload(pt)
        assert str(pt.ATTEST_KEY_PATH).startswith(str(ud)), "plan attest_key 未迁用户态"
        import model_probe as mp
        importlib.reload(mp)
        assert str(mp.get_cache_path()).startswith(str(ud)), "model cache 未迁用户态"
    finally:
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m
        if saved_a is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = saved_a
        import frozen_util as _fu
        importlib.reload(_fu)
        for m in ("self_heal_engine", "adaptive_runner", "plan_tracker", "model_probe"):
            if m in sys.modules:
                importlib.reload(sys.modules[m])
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_judge_agents_dir_and_plan_templates_frozen_aware():
    """阶段B GUI exe：judge_runner.AGENTS_DIR(.claude/agents) + plan_tracker.TEMPLATES_DIR
    (core/claude-home/plans) 须 frozen-aware（bundle_root 定位·非 __file__ 扁平推算）——
    否则 frozen 下 judge 读不到 agent prompt / orchestrator 读不到 plan 模板（FATAL 同款）。"""
    _scripts = str(Path(__file__).resolve().parent.parent / "core" / "scripts")
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    import importlib
    saved_f = getattr(sys, "frozen", False)
    saved_m = getattr(sys, "_MEIPASS", None)
    sys.frozen = True
    sys._MEIPASS = r"X:\app\_internal"
    try:
        import judge_runner
        importlib.reload(judge_runner)   # 重读模块级 AGENTS_DIR（依赖 bundle_root）
        assert judge_runner.AGENTS_DIR == Path(r"X:\app\_internal") / ".claude" / "agents"
        import plan_tracker
        importlib.reload(plan_tracker)
        assert plan_tracker.TEMPLATES_DIR == \
            Path(r"X:\app\_internal") / "core" / "claude-home" / "plans"
    finally:
        sys.frozen = saved_f
        if saved_m is None:
            try:
                del sys._MEIPASS
            except AttributeError:
                pass
        else:
            sys._MEIPASS = saved_m
        # 还原 dev 态模块（避免污染后续测试的模块级常量）
        import judge_runner
        importlib.reload(judge_runner)
        import plan_tracker
        importlib.reload(plan_tracker)


def test_no_bare_child_interpreter_anywhere_in_scripts():
    """全 core/scripts 扫描：subprocess 启动子脚本绝不裸用 sys.executable 或 "python"
    字面量（必走 child_python·防 frozen 重启 GUI / PATH 无 python 静默失败）。

    这是 finding #1「comprehensive 根治」的守卫——未来任何新加的 fan-out 漏改都会被
    本测试当场抓住（reviewer 建议：扫全目录而非写死文件名）。
    白名单：frozen_util.py（定义处）、orchestrator.py（docstring 注释 + in-process 分支
    保留 sys.executable 引用做对照说明）。
    """
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    bare_exec = re.compile(r"\[\s*sys\.executable\b")
    bare_python = re.compile(r"\[\s*[\"']python[3]?[\"']\s*,")
    offenders = []
    for p in sorted(scripts.glob("*.py")):
        if p.name == "frozen_util.py":
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = ln.split("#", 1)[0]  # 去行尾注释
            if p.name == "orchestrator.py" and "child_python()" in ln:
                continue  # orchestrator 的 full=[child_python()]+tokens 已正确
            if bare_exec.search(code) or bare_python.search(code):
                offenders.append(f"{p.name}:{i}: {ln.strip()[:70]}")
    assert not offenders, "fan-out 子进程仍裸用解释器（应走 child_python）:\n" + \
        "\n".join(offenders)


def test_wal_recovery_and_maybe_judge_use_child_python():
    """finding #1 第二轮补漏：wal_recovery + maybe_judge_consensus 两处 "python" 字面量
    已改 child_python（这俩 pre-existing fan-out 站点在首轮被漏）。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    for fname in ["wal_recovery.py", "maybe_judge_consensus.py"]:
        text = (scripts / fname).read_text(encoding="utf-8")
        assert "child_python" in text, f"{fname} 未用 child_python"


def test_adaptive_runner_normalizes_python_interpreter():
    """第三轮 finding（high·收口闸抓）：adaptive_runner 内层 'python' 字面量（来自
    plan REMAINDER `-- python core/scripts/Y.py` / auto_heal str cmd）须在
    run_with_resilience 入口归一 child_python——否则 frozen 下整条自学习/演化链静默失效。
    这是静态正则扫不到的运行时字面量（首轮 guard 漏网处），故直接测归一函数。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
    import adaptive_runner as ar

    # list 形态：内层 python 换 child_python
    out = ar._normalize_interpreter(["python", "core/scripts/Y.py", "--flag"])
    assert out[0] == fu.child_python() and out[1:] == ["core/scripts/Y.py", "--flag"]
    out3 = ar._normalize_interpreter(["python3", "x.py"])
    assert out3[0] == fu.child_python()
    # str 形态（auto_heal）：首 token 换（带引号保护空格路径）
    s = ar._normalize_interpreter("python core/scripts/X.py --auto-migrate")
    assert s.startswith(f'"{fu.child_python()}" ') and "core/scripts/X.py" in s
    # 非 python 首位（如直接脚本路径）不动
    assert ar._normalize_interpreter(["core/scripts/Z.py"]) == ["core/scripts/Z.py"]
    assert ar._normalize_interpreter("echo hi") == "echo hi"


def test_no_bare_python_literal_in_any_run_with_resilience_caller():
    """守卫收口：plan 模板里 adaptive_runner 的内层 'python' 由 run_with_resilience 入口
    归一兜住——确认 adaptive_runner 已 import child_python（运行时字面量静态扫不到，
    故验 import 存在 + 归一函数被入口调用）。"""
    scripts = Path(__file__).resolve().parent.parent / "core" / "scripts"
    ar_text = (scripts / "adaptive_runner.py").read_text(encoding="utf-8")
    assert "child_python" in ar_text
    assert "_normalize_interpreter(cmd)" in ar_text  # 入口确实调归一


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
