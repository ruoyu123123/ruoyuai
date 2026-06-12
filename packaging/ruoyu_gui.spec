# ruoyu_gui.spec — 阶段B 完整 GUI exe · PyInstaller --onedir · console=True（看 stderr）
# 构建：pyinstaller packaging\ruoyu_gui.spec --noconfirm --clean
# 产物：dist\ruoyu_gui\ruoyu_gui.exe + dist\ruoyu_gui\_internal\
#
# 设计依据（实证 · 见任务报告）：
#  - NiceGUI 3.13.0 无 PyInstaller hook（无 __pyinstaller·无 pyinstaller40 entry_point）→
#    须 collect_all(nicegui) 抓 static/elements/templates/scripts/app/functions 等资源 + 子模块。
#  - nicegui/openai/uvicorn/fastapi/starlette 在 import 时调 importlib.metadata.version()→
#    必须 copy_metadata（collect_all 只带主包自身 metadata·传递依赖须显式补）。
#  - fan-out（audit_hub 等 6 脚本）走 [exe, _internal/core/scripts/X.py, ...] 自我再分派
#    （方案 M·阶段A 真 onedir 7/7 验过）→ 全 core/scripts/*.py 必须以 datas 落
#    bundle_root()/core/scripts·任一进程内 import 的模块必须在 PYZ（collect_submodules）。
#  - judge_runner 读 .claude/agents/*.md（已改 frozen-aware）·orchestrator 读 plans/*.json·
#    gen_model_loader 读 core/config/gen_profiles.default.env → 全部 datas 落 bundle_root() 下。
#  - 🔴 绝不打包任何 .env（开发者私钥·安全铁律）·绝不 Tree 仓库根（防卷入 workspace/私钥）。
import os

from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_submodules, copy_metadata,
)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))     # SPECPATH=packaging/ → 仓库根
SCRIPTS = os.path.join(ROOT, "core", "scripts")

# ============ NiceGUI：无 hook·collect_all 抓全资源+子模块+二进制+metadata ============
# collect_all 返回 (datas, binaries, hiddenimports)。NiceGUI 把 vbuild 也 vendored 进包内
# （nicegui/vbuild.py），故无需单独装 vbuild。
ng_datas, ng_binaries, ng_hidden = collect_all("nicegui")

datas = list(ng_datas)
binaries = list(ng_binaries)
hiddenimports = list(ng_hidden)

# ============ 运行时数据（落 bundle_root() 下·与源码 frozen-aware 路径对齐）============
# 1) 全 core/scripts/*.py —— fan-out 子进程按路径 [exe, core/scripts/X.py] 执行的真源；
#    collect_data_files 带 .py（include_py_files=True）整目录落 core/scripts（含 scanner_registry.json
#    等兄弟 JSON / .md）。dest 由 collect_data_files 按包内相对结构自动算（落 core/scripts/）。
datas += collect_data_files("core.scripts", include_py_files=True)
# core/scripts 不是真包（无 __init__.py 时 collect_data_files 可能空手）→ 显式兜底整目录搬运。
for fn in sorted(os.listdir(SCRIPTS)):
    fp = os.path.join(SCRIPTS, fn)
    if os.path.isfile(fp) and (fn.endswith(".py") or fn.endswith(".json")
                               or fn.endswith(".md")):
        datas.append((fp, "core/scripts"))

# 2) judge prompt 单一真理源 .claude/agents/*.md（judge_runner.AGENTS_DIR=bundle_root()/.claude/agents）
AGENTS = os.path.join(ROOT, ".claude", "agents")
for fn in sorted(os.listdir(AGENTS)):
    if fn.endswith(".md"):
        datas.append((os.path.join(AGENTS, fn), ".claude/agents"))

# 3) plan 模板（orchestrator DAG · plan_tracker 模板校验）
PLANS = os.path.join(ROOT, "core", "claude-home", "plans")
for fn in sorted(os.listdir(PLANS)):
    if fn.endswith(".json"):
        datas.append((os.path.join(PLANS, fn), "core/claude-home/plans"))

# 4) 内置 config（gen_model_loader 第三级回落·_dist_mode·无密钥）
datas.append((os.path.join(ROOT, "core", "config", "gen_profiles.default.env"),
              "core/config"))

# 5) ~~系统文档~~（复验收口 2026-06-12：grep 证实 CLAUDE.md/STRUCTURE.md 零运行时
#    消费者——所有代码引用都只在注释里。CLAUDE.md 含系统规则（安全节「绝对不透露」），
#    打进 exe = 泄漏。validate_gui_exe.check_security 已加断言拦回归。）

# 6) subsystem 骨架（scaffold_subsystems 读 · 存在即收）
for rel in ("core/claude-home/templates/subsystem_skeletons.json",):
    fp = os.path.join(ROOT, *rel.split("/"))
    if os.path.isfile(fp):
        datas.append((fp, os.path.dirname(rel)))

# 6b) 跨项目 lessons（build_manifest 注入 writer/judge·已改 bundle_root() 定位·
#     直接关系北极星「风格一致」·~196K/12 文件·体积可忽略）
LESSONS = os.path.join(ROOT, "core", "claude-home", "lessons")
if os.path.isdir(LESSONS):
    for fn in sorted(os.listdir(LESSONS)):
        if fn.endswith(".md"):
            datas.append((os.path.join(LESSONS, fn), "core/claude-home/lessons"))

# 7) numpy / scipy 资源（style_evaluator 在 save-state advisory 进程内 import·multi-call 下
#    [exe, style_evaluator.py] 走 exe 进程内 → 必须收进 exe 本体·非外部解释器）
datas += collect_data_files("numpy")
datas += collect_data_files("scipy")
# certifi 的 cacert.pem（httpx/openai TLS·nicegui import 链已触达 certifi）
datas += collect_data_files("certifi")

# ============ metadata（importlib.metadata.version 在 import 期被调·缺则 import 即崩）============
for pkg in ("nicegui", "openai", "uvicorn", "fastapi", "starlette",
            "anyio", "h11", "httpx", "httpcore", "click", "python-multipart",
            "python-socketio", "engineio", "websockets", "pydantic", "pydantic-core"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass  # 个别包名/分发名不一致或未装 → 跳过（已装的核心 5 个上面排在前不会被吞）

# ============ hidden imports ============
hiddenimports += [
    # —— web stack：collect_all(nicegui) 多数已带·补 uvicorn/fastapi 动态加载子模块 ——
    "uvicorn", "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "fastapi", "starlette", "starlette.middleware",
    "websockets", "websockets.legacy",
    # python-socketio / engineio（NiceGUI 双向通信内核·动态选 async 驱动）
    "socketio", "socketio.async_server", "socketio.asgi",
    "engineio", "engineio.async_drivers", "engineio.async_drivers.asgi",
    "anyio", "anyio._backends", "anyio._backends._asyncio",
    "multipart", "python_multipart",
    # —— openai / transport ——
    "openai", "httpx", "httpcore", "h11", "certifi",
    "jiter",                    # openai JSON 解析（Rust 扩展·扁平收易漏）
    "tiktoken", "tiktoken_ext",  # openai 懒加载 token 计数（未装则被上面 copy_metadata 静默跳过·
                                 # 在此列为 hidden 无害·装了才打进去）
    "pydantic", "pydantic_core", "annotated_types",
    "dotenv",                    # gen_model_loader load_dotenv
    # —— keyring 25.x 靠 entry_points 发现后端·PyInstaller 不跟 → 全部显式列 ——
    "keyring.backends.Windows",  # WinVault（is_available 探测核心·最易漏）
    "keyring.backends.chainer",
    "keyring.backends.fail",     # is_available 用 isinstance(kr, fail.Keyring)
    "keyring.backends.null",
    "win32ctypes.pywin32",       # WinVault 依赖（pywin32-ctypes）
    "win32ctypes.core",
    # —— numpy / scipy（style_evaluator advisory·multi-call 进程内 import）——
    "numpy", "scipy", "scipy.stats", "scipy.special", "scipy.spatial",
    # —— 全 core/scripts 模块当顶层名收进 PYZ（fan-out dispatcher 进程内 importlib by stem）——
]
# core/scripts 下全部 .py 的 stem 作 hidden import（FrozenImporter 须在 PYZ 见到它们·
# dispatcher run_script_in_process 用 importlib by stem 进程内加载）。
for fn in sorted(os.listdir(SCRIPTS)):
    if fn.endswith(".py") and not fn.startswith("__"):
        hiddenimports.append(fn[:-3])

hiddenimports = sorted(set(hiddenimports))

# ============ excludes（torch 等 GB 级·绝不进包）============
excludes = [
    "torch", "torchvision", "torchaudio",        # 🔴 torch 2.10 已装·GB 级·必排
    "sentence_transformers", "transformers",      # torch 主入口
    "tensorflow", "jax", "jaxlib",
    "tkinter", "matplotlib", "PIL", "Pillow",
    "pandas", "sympy", "networkx",
    "IPython", "jedi", "notebook", "jupyter",
    "pywebview", "webview",                        # 浏览器模式（native=False）不需桌面壳
    "pytest", "_pytest",
]

# ============ Analysis / EXE / COLLECT ============
a = Analysis(
    [os.path.join(ROOT, "ruoyu_gui.py")],
    pathex=[ROOT, SCRIPTS],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
# console 由 RUOYU_CONSOLE env 控（与 bundling 正交·不影响 frozen 路径/datas/hiddenimports）：
#   "1"(默认·dev/CI/调试)→console=True 看 stderr Traceback；"0"(build_all.py --production)→
#   console=False 窗口模式，非技术用户双击不弹黑色终端窗（正式分发形态）。
_CONSOLE = os.environ.get("RUOYU_CONSOLE", "1") != "0"
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ruoyu_gui",
    console=_CONSOLE,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name="ruoyu_gui",
)
