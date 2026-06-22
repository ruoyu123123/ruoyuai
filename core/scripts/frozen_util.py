#!/usr/bin/env python3
"""frozen_util.py — frozen exe（PyInstaller onedir）下的子解释器解析（程序驱动 M4）

对抗审查 finding #1（2026-06-10）：orchestrator 顶层脚本调用已 frozen-aware（进程内
importlib），但脚本**内部** fan-out（audit_hub 并行 21 个 scanner / save_state /
gen_writer / gen_fixer / run_cross_cluster_aggregates / evolution_orchestrator …）仍用
`subprocess.run([sys.executable, ...])`。onedir 里 sys.executable = GUI 本体 → 这些内部
调用会重启 GUI 而非跑目标脚本（fix F 同一 bug 深一层）。

统一解决：所有 fan-out 子进程的解释器走 `child_python()`——
- **dev / source 模式**（绝大多数当前用法）：返回 `sys.executable`，行为零变化。
- **frozen onedir**：返回环境变量 `RUOYU_PYTHON` 指向的随包 python 启动器（打包脚本
  bundle 一份精简 python 并 set RUOYU_PYTHON）；未设置则回退 sys.executable 并告警
  （让问题**暴露**而非静默重启 GUI）。

为什么不全改进程内 importlib：audit_hub 的 21 个 scanner 靠进程隔离（任一崩不连累其他
+ ThreadPoolExecutor 并行），改进程内会丢隔离 + 全局状态污染风险。bundle python +
统一解释器解析保留现有 subprocess 架构，是最小风险的根治。

🔴 真 onedir 端到端验证仍需建 exe（dev 无法验 frozen）——本模块在 dev 是 no-op，
frozen 路径靠 RUOYU_PYTHON 注入，二者都可在 dev 用 monkeypatch 测解析逻辑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# dev 仓库根：frozen_util 在 core/scripts/ → parents[2] = 仓库根
_DEV_REPO_ROOT = Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    """是否运行在 PyInstaller/cx_Freeze frozen 二进制里。"""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """资源/代码树的根目录——派生 core/config、core/scripts、core/claude-home 等相对路径的基准。

    🔴 对抗审查 FATAL 修复（2026-06-10）：PyInstaller 把 core/scripts 下的模块收成**扁平
    顶层名**（gen_model_loader.__file__ = _internal/gen_model_loader.py），于是源码里
    `Path(__file__).resolve().parent.parent / "config"` 之类的相对推算在 frozen 下全错
    （core/scripts 两级目录消失）。派生项目路径的模块必须改用本函数当基准，而非 __file__。

    - frozen（PyInstaller onedir）：sys._MEIPASS 指向 _internal/，资源经 datas 落在
      _internal/core/config、_internal/core/scripts 等 → bundle_root()/core/config 命中。
    - dev：返回仓库根（core/scripts 的 parents[2]）→ 与现状逐字节一致。
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # frozen 但无 _MEIPASS（极少·非 PyInstaller 冻结器）→ exe 同级兜底
        return Path(sys.executable).resolve().parent
    return _DEV_REPO_ROOT


def resource_path(*parts: str) -> Path:
    """bundle_root() 下的资源绝对路径（如 resource_path('core','config','x.env')）。"""
    return bundle_root().joinpath(*parts)


def user_data_dir() -> Path:
    """**可写**系统数据的根目录——bundle_root() 的对称面（那是只读资源·这是可写状态）。

    🔴 frozen 可写数据修复（2026-06-10·READ 审计对称面）：MAPE-K runtime（incidents.jsonl/
    self_heal_kb/circuit_state/runtime_lessons）、model_capabilities 缓存、plan_tracker 的
    全局 plan/attest_key 等是**跨项目可写系统数据**，dev 落仓库根，但 frozen 下 bundle 是
    **只读** _internal → 写必失败/崩。frozen 改落用户态 %APPDATA%/ruoyuai（同 BYOK
    user_overrides 范式·_user_override_path）。

    - dev：返回仓库根（_DEV_REPO_ROOT）→ `user_data_dir()/core/claude-home/runtime` 等于
      现状路径·**逐字节零回归**。
    - frozen：%APPDATA%/ruoyuai（无 APPDATA 则 ~/.ruoyuai）·可写。
    """
    if is_frozen():
        base = os.environ.get("APPDATA") or str(Path.home() / ".ruoyuai")
        d = Path(base) / "ruoyuai"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return d
    return _DEV_REPO_ROOT


def user_workspace_dir() -> Path:
    """用户**创作产物**根目录（小说项目 novels/ + 风格库 styles/）。

    🔴 workspace-in-frozen 修复（2026-06-11·真 outline e2e 抓出）：novels/styles 是**用户数据**·
    不在只读 bundle。dev=仓库根/workspace（逐字节零回归）·frozen=user_data_dir()/workspace
    （%APPDATA%/ruoyuai/workspace·可写·GUI 实际建书落点）。state.NOVELS_DIR + plan_tracker.
    PROJECTS_DIR/STYLES_DIR + init_project 风格库派生统一用此（否则 frozen 下 REPO_ROOT=dist
    指错 + run_script_in_process chdir _MEIPASS 使相对路径错位）。
    """
    return user_data_dir() / "workspace"


def scripts_dir() -> Path:
    """core/scripts 目录（fan-out 脚本据此定位兄弟 scanner 子脚本）。

    🔴 frozen-aware（对抗审查 FATAL 第三处）：audit_hub 等 6 个 fan-out 脚本原用
    `Path(__file__).parent` 推算脚本目录——frozen 下被 PyInstaller 扁平收录后指向
    `_internal/`（扁平），fan-out 传出 `_internal/X.py`（磁盘不存在·真 .py 经 datas 落
    `_internal/core/scripts/`）→ multi-call dispatcher 白名单 miss → 落 GUI → 子进程
    timeout 全挂。统一改用本函数：frozen=bundle_root()/core/scripts·dev=同值（脚本本就
    在 core/scripts·与 Path(__file__).parent 逐字节一致）。
    """
    return bundle_root() / "core" / "scripts"


def child_python() -> str:
    """返回用于 fan-out 子进程的 python 解释器路径（multi-call 方案 M）。

    dev → sys.executable（真 python.exe）。
    frozen → RUOYU_PYTHON 优先（方案 B 逃生阀·临时指外部 python）；缺则返 exe 本体——
    靠入口的 dispatcher 自我再分派（argv[1] 是 core/scripts 脚本就
    进程内跑·见 dispatch_or_none）。**M 下「frozen+无 env+返 exe」是设计正道，不告警**。
    [ruoyu_gui/frozen_smoke 入口 removed commit 2a4d7ce·frozen 分支留作未来打包基础设施]
    """
    if not is_frozen():
        return sys.executable
    bundled = os.environ.get("RUOYU_PYTHON", "").strip()
    if bundled:
        return bundled
    return sys.executable          # ★方案M：exe 本体·dispatcher 再分派


def is_script_dispatch(argv) -> bool:
    """frozen multi-call：argv[1] 是否为 bundle 内 core/scripts(或 packaging) 下的受控 .py。

    白名单制（合取硬条件·任一不满足即落 GUI·绝不误伤正常启动 / multiprocessing spawn）：
      - is_frozen()（dev 永不派发·走真 python.exe）
      - len(argv) >= 2（exe 裸跑 / exe --native → False）
      - argv[1] 不以 '-' 开头（--native/--port/--multiprocessing-fork → False）
      - argv[1] 以 .py 结尾
      - 规范化后真落在 bundle_root()/core/scripts 或 /packaging 下且 .exists()
        （白名单根·杜绝任意路径执行） [bundle 根 ruoyu_gui.py 入口 removed commit 2a4d7ce]
    """
    if not is_frozen() or len(argv) < 2:
        return False
    a1 = argv[1]
    if not isinstance(a1, str) or a1.startswith("-") or not a1.endswith(".py"):
        return False
    try:
        root = bundle_root()
        cand = Path(a1)
        target = cand.resolve() if cand.is_absolute() else (root / a1).resolve()
        if not target.exists():
            return False
        allowed = ((root / "core" / "scripts").resolve(),
                   (root / "packaging").resolve())
        return any(ar in target.parents for ar in allowed)
    except Exception:
        return False


def dispatch_or_none(argv):
    """命中脚本派发 → 进程内跑该脚本并返回退出码；否则 None（调用方照常启 GUI）。

    复用 orchestrator.run_script_in_process（与顶层脚本调用同一 importlib 内核·已 6/6 PASS）。
    argv[1:] = [脚本路径, *args]。返回 int 退出码 or None。
    """
    if not is_script_dispatch(argv):
        return None
    # 🔴 frozen Windows exe stdout/stderr 默认 GBK(cp936)→ 派发的脚本打印 emoji/非 GBK
    # Unicode(如 prompt 里的 🔴)会 UnicodeEncodeError 崩(真 end-to-end 测试实测)。派发执行前
    # 强制 UTF-8(所有 dispatch 入口共享此防护)。[ruoyu_gui 入口 removed commit 2a4d7ce]
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import orchestrator  # 延迟 import：仅派发分支触达·不拖慢 GUI 冷启
    return orchestrator.run_script_in_process(
        argv[1:], repo_root=orchestrator.REPO_ROOT, label="fanout")
