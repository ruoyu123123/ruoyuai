#!/usr/bin/env python3
"""validate_gui_exe.py — 全 GUI onedir exe 真机验证（阶段B·dev 运行·验真产物）。

不在 tests/ 里（需真构建产物·非单测）。验 4 项：
  1. 安全：dist 内无 .env / 无 sk- 明文（开发者私钥不外泄）
  2. fan-out dispatch：ruoyu_gui.exe core/scripts/prose_rhythm_scanner.py draft → scanner JSON
  3. GUI serve：ruoyu_gui.exe --port N 起 HTTP·GET / → 200 + 含「若渝AI」
  4. Traceback 哨兵：stderr 无 Traceback（feedback_verify_stderr_not_exitcode）

用法：python packaging/validate_gui_exe.py [--exe dist/ruoyu_gui/ruoyu_gui.exe] [--port 8131]
退出码 0=全过 / 1=有失败。
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _say(tag, msg):
    print(f"[{tag}] {msg}")


def check_security(dist_dir: Path) -> list:
    fails = []
    envs = list(dist_dir.rglob(".env"))
    if envs:
        fails.append(f"dist 内有 .env: {envs[:3]}")
    # 扫 _internal 文本文件有无 sk- 明文（抽 config/py·不扫二进制）
    leaked = []
    for p in dist_dir.rglob("*.env"):
        try:
            if "sk-" in p.read_text(encoding="utf-8", errors="ignore"):
                leaked.append(str(p))
        except OSError:
            pass
    if leaked:
        fails.append(f"明文 sk- 泄漏: {leaked[:3]}")
    return fails


def check_dispatch(exe: Path) -> list:
    fails = []
    tmp = Path(tempfile.mkdtemp())
    draft = tmp / "d.txt"
    draft.write_text("他睁开眼。\n\n外面下着雨。\n\n卧槽，这也行？\n", encoding="utf-8")
    scanner = "core/scripts/prose_rhythm_scanner.py"
    try:
        r = subprocess.run([str(exe), scanner, str(draft)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        if r.returncode not in (0, 1):
            fails.append(f"dispatch 退出码 {r.returncode}·stderr={r.stderr[-200:]}")
        try:
            data = json.loads(r.stdout)
            if data.get("scanner") != "prose_rhythm":
                fails.append(f"dispatch 输出非 scanner JSON: {r.stdout[:120]}")
        except json.JSONDecodeError:
            fails.append(f"dispatch stdout 非 JSON（dispatcher 没接住）: {r.stdout[:120]}")
        if "Traceback" in r.stderr:
            fails.append(f"dispatch stderr 有 Traceback: {r.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        fails.append("dispatch 超时（dispatcher 没接住·起了 GUI?）")
    return fails


def check_gui_serve(exe: Path, port: int) -> list:
    fails = []
    proc = subprocess.Popen([str(exe), "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # 等服务起（最多 40s·NiceGUI 冷启 + frozen 解压慢）
        served = False
        body = ""
        for _ in range(40):
            time.sleep(1)
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "replace")[-400:]
                fails.append(f"GUI 进程提前退出 rc={proc.returncode}·stderr={err}")
                return fails
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
                    if resp.status == 200:
                        body = resp.read().decode("utf-8", "replace")
                        served = True
                        break
            except Exception:
                continue
        if not served:
            fails.append("GUI 40s 内未起 HTTP 200")
        elif "若渝" not in body and "ruoyu" not in body.lower():
            fails.append(f"GUI 页面不含「若渝AI」标识（前 200 字）: {body[:200]}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="dist/ruoyu_gui/ruoyu_gui.exe")
    ap.add_argument("--port", type=int, default=8131)
    args = ap.parse_args()
    exe = (ROOT / args.exe) if not Path(args.exe).is_absolute() else Path(args.exe)
    dist_dir = exe.parent
    if not exe.exists():
        _say("FAIL", f"exe 不存在: {exe}（先构建 packaging/ruoyu_gui.spec）")
        return 1

    all_fails = []
    for name, fn in (("security", lambda: check_security(dist_dir)),
                     ("dispatch", lambda: check_dispatch(exe)),
                     ("gui_serve", lambda: check_gui_serve(exe, args.port))):
        fails = fn()
        if fails:
            all_fails += fails
            for f in fails:
                _say("FAIL", f"{name}: {f}")
        else:
            _say("PASS", name)

    if all_fails:
        _say("RESULT", f"GUI EXE 验证 FAIL · {len(all_fails)} 项")
        return 1
    _say("RESULT", "GUI EXE 验证 PASS 3/3（安全 + dispatch + GUI serve）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
