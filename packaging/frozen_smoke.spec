# frozen_smoke.spec — 阶段A 最小 de-risk · onedir · console=True（看 stderr）
# 构建：pyinstaller packaging\frozen_smoke.spec --noconfirm --clean
# 产物：dist\frozen_smoke\frozen_smoke.exe + dist\frozen_smoke\_internal\
#
# 对抗审查 FATAL 已在源码修：gen_model_loader/orchestrator 改 frozen_util.bundle_root()
# (=_MEIPASS) 定位·非 __file__ 推算·故扁平 PYZ 收录也无碍。datas 把 config/脚本落到
# bundle_root()/core/config、core/scripts。
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))     # SPECPATH=packaging/ → 仓库根
SCRIPTS = os.path.join(ROOT, "core", "scripts")

hiddenimports = [
    # keyring 25.x 靠 entry_points 发现后端·PyInstaller 不跟 → 全部显式列
    "keyring.backends.Windows",       # WinVault（is_available 探测核心·最易漏）
    "keyring.backends.chainer",
    "keyring.backends.fail",           # is_available 用 isinstance(kr, fail.Keyring)
    "keyring.backends.null",
    "win32ctypes.pywin32",             # WinVault 依赖（pywin32-ctypes）
    "win32ctypes.core",
    "dotenv",                          # gen_model_loader load_dotenv
    # 进程内 import 的被试模块（FrozenImporter 须收进 PYZ）
    "_smoke_inproc_target",            # must_fix#3：路径1 正例（importlib by stem）
    "cluster_lookup",
    "orchestrator",
    "plan_tracker",                    # orchestrator 顶层 import
    "adaptive_runner",                 # orchestrator 间接（run_with_resilience）
    "secrets_store",
    "frozen_util",
    "gen_model_loader",
]

datas = [
    # 第三级回落 config · dest = bundle_root()/core/config（gen_model_loader 期望）
    (os.path.join(ROOT, "core", "config", "gen_profiles.default.env"), "core/config"),
    # 进程内正例被试（带 main()）· path1 的 .py 落点（兼 importlib 兜底）
    (os.path.join(ROOT, "packaging", "_smoke_inproc_target.py"), "packaging"),
    # run_script_in_process 进程内 import 的真源 .py（.exists 检查 + 阶段B fan-out）
    (os.path.join(SCRIPTS, "cluster_lookup.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "orchestrator.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "plan_tracker.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "adaptive_runner.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "secrets_store.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "frozen_util.py"), "core/scripts"),
    (os.path.join(SCRIPTS, "gen_model_loader.py"), "core/scripts"),
    # 🔴 绝不列任何 .env / 绝不 Tree 仓库根（防卷入开发者私钥·安全铁律）
]

excludes = [
    "torch", "torchvision", "torchaudio",        # 🔴 torch 2.10 已装·GB 级·必排
    "sentence_transformers", "transformers",      # torch 主入口
    "scipy", "numpy",                             # 阶段A 闭包不需（阶段B GUI 再留）
    "nicegui", "fastapi", "starlette", "uvicorn", "websockets",
    "tkinter", "matplotlib", "PIL", "pandas",
    "sympy", "networkx", "IPython", "jedi",
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "frozen_smoke.py")],
    pathex=[ROOT, SCRIPTS],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="frozen_smoke",
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name="frozen_smoke",
)
