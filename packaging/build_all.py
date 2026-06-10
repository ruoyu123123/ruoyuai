#!/usr/bin/env python3
"""build_all.py — 若渝AI 一键构建 + 验证 onedir exe（可复现出货 runbook）。

流程：清理 → 构建 frozen_smoke + ruoyu_gui onedir → 跑 frozen_smoke.exe(7/7) +
validate_gui_exe.py(4/4) → 安全闸（grep sk-/.env 泄漏）。任一不过退非 0。

用法：python packaging/build_all.py [--skip-smoke] [--gui-port 8137]
退出码 0=全过 / 1=有失败。

🔴 按 feedback_verify_stderr_not_exitcode：frozen 验证看 stderr 无 Traceback（exe 自验
退出码 + validate 脚本断言双保险）。CI 可直接调本脚本当 release gate。
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"


def _run(cmd, **kw):
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=str(ROOT), **kw)


def _clean(*names):
    for sub in ("build", "dist"):
        for n in names:
            p = ROOT / sub / n
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)


def build(spec: str, name: str, production: bool = False) -> bool:
    import os
    _clean(name)
    env = dict(os.environ)
    if production and name == "ruoyu_gui":
        env["RUOYU_CONSOLE"] = "0"   # 窗口模式·非技术用户双击不弹黑窗（仅 GUI 生产构建）
    r = _run([sys.executable, "-m", "PyInstaller", f"packaging/{spec}",
              "--noconfirm", "--clean"], env=env)
    exe = ROOT / "dist" / name / f"{name}.exe"
    if r.returncode != 0 or not exe.exists():
        print(f"[X] 构建失败: {spec}")
        return False
    # 使用说明随 GUI exe 一起出货（非技术用户开包即见）
    if name == "ruoyu_gui":
        manual = PKG / "使用说明.md"
        if manual.exists():
            shutil.copy2(str(manual), str(exe.parent / "使用说明.md"))
            print("[OK] 使用说明.md 已随包")
    print(f"[OK] 构建: dist/{name}/{name}.exe")
    return True


def run_smoke() -> bool:
    exe = ROOT / "dist" / "frozen_smoke" / "frozen_smoke.exe"
    r = subprocess.run([str(exe)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and "ALL PASS" in r.stdout and "Traceback" not in r.stderr
    print(("[OK]" if ok else "[X]") + " frozen_smoke.exe 自验 " +
          ("7/7" if ok else f"rc={r.returncode}"))
    if not ok:
        print(r.stdout[-400:], r.stderr[-400:])
    return ok


def run_gui_validate(port: int) -> bool:
    r = _run([sys.executable, "packaging/validate_gui_exe.py", "--port", str(port)],
             capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print(r.stdout[-600:])
    if not ok:
        print(r.stderr[-400:])
    return ok


def security_gate() -> bool:
    """grep dist 内 sk-/.env 泄漏（安全铁律）。"""
    bad = []
    for dist in ("frozen_smoke", "ruoyu_gui"):
        d = ROOT / "dist" / dist
        if not d.exists():
            continue
        if list(d.rglob(".env")):
            bad.append(f"{dist}: 含 .env")
        for env in d.rglob("*.env"):
            try:
                if "sk-" in env.read_text(encoding="utf-8", errors="ignore"):
                    bad.append(f"{dist}: {env.name} 含 sk-")
            except OSError:
                pass
    if bad:
        for b in bad:
            print(f"[X] 安全闸: {b}")
        return False
    print("[OK] 安全闸: dist 无 .env / 无 sk- 泄漏")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-smoke", action="store_true", help="跳过 frozen_smoke 阶段A")
    ap.add_argument("--production", action="store_true",
                    help="GUI 窗口模式构建（console=False·非技术用户双击不弹黑窗·正式分发）")
    ap.add_argument("--gui-port", type=int, default=8137)
    args = ap.parse_args()

    steps = []
    if not args.skip_smoke:
        steps.append(("build frozen_smoke", lambda: build("frozen_smoke.spec", "frozen_smoke")))
        steps.append(("frozen_smoke 自验", run_smoke))
    steps.append((f"build ruoyu_gui{'(生产窗口模式)' if args.production else ''}",
                  lambda: build("ruoyu_gui.spec", "ruoyu_gui", production=args.production)))
    steps.append(("GUI exe 验证 4/4", lambda: run_gui_validate(args.gui_port)))
    steps.append(("安全闸", security_gate))

    for name, fn in steps:
        print(f"\n========== {name} ==========")
        if not fn():
            print(f"\n[RESULT] 构建验证 FAIL @ {name}")
            return 1
    print("\n[RESULT] 构建验证全过 ✅ — dist/ruoyu_gui 可分发")
    return 0


if __name__ == "__main__":
    sys.exit(main())
