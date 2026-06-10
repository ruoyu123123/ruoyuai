#!/usr/bin/env python3
"""若渝AI 图形界面启动器（脱离 Claude CLI · 程序驱动 v28）

    python ruoyu_gui.py            # 浏览器模式（自动开 http://localhost:8080）
    python ruoyu_gui.py --native   # 桌面窗口模式（需 pip install pywebview）
    python ruoyu_gui.py --port 9000

frozen exe 注意（M4）：multiprocessing.freeze_support() 必须是 main guard 第一句。
"""
import multiprocessing
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller spawn 防无限 fork（必须第一句）

    _REPO = Path(__file__).resolve().parent
    for p in (str(_REPO), str(_REPO / "core" / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)

    # === frozen multi-call 自我再分派（方案 M·阶段B）===
    # exe 收到 [exe, core/scripts/X.py, args]（audit_hub fan-out 形态）→ 进程内跑该 scanner
    # 带退出码退出·不启 GUI。判别+执行全在 frozen_util（与 frozen_smoke 共享同一段）。
    # dev 下 is_frozen()=False → dispatch_or_none 恒 None → 照常启 GUI（零回归）。
    if getattr(sys, "frozen", False):
        import frozen_util
        _rc = frozen_util.dispatch_or_none(sys.argv)
        if _rc is not None:
            sys.exit(_rc)            # 带 scanner 退出码退出 → audit_hub 据此判 ok

    # === 否则照常启 GUI ===
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    from core.gui.app import main
    main(native=args.native, port=args.port)
