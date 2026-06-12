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


def check_writable_data(exe: Path, dist_dir: Path) -> list:
    """frozen 可写系统数据迁 user_data_dir 的真二进制验证：经 exe 自我再分派跑
    plan_tracker create（写 GLOBAL plan + attest HMAC 密钥·最关键可写路径·每命令触达），
    隔离 APPDATA=临时目录 → 确认写落 %APPDATA%/ruoyuai 而非只读 bundle。"""
    import os
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix="frozen_appdata_"))
    env = dict(os.environ)
    env["APPDATA"] = str(tmp)                      # frozen user_data_dir() = tmp/ruoyuai
    try:
        r = subprocess.run(
            [str(exe), "core/scripts/plan_tracker.py", "create",
             "--command", "cluster-write", "--project", "__frozen_write_probe__",
             "--key", "cluster_001"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=120)
        if r.returncode != 0:
            fails.append(f"plan_tracker create 退出码 {r.returncode}·stderr={r.stderr[-200:]}")
        if "Traceback" in r.stderr:
            fails.append(f"plan_tracker create stderr 有 Traceback: {r.stderr[-200:]}")
        # 写应落 %APPDATA%/ruoyuai（user_data_dir）·attest_key + plan json
        udd = tmp / "ruoyuai"
        plans_dir = udd / "core" / "claude-home" / ".plans"
        if not (plans_dir / ".attest_key").exists():
            fails.append(f"attest_key 未写到 user_data_dir（{plans_dir}）——可写数据迁移失效")
        probe_plans = list(plans_dir.glob("*__frozen_write_probe__*.json")) if plans_dir.exists() else []
        if not probe_plans:
            fails.append(f"probe plan 未写到 user_data_dir（{plans_dir}）")
        # 反面：bundle 内 .plans 不该被写（只读·不存在或无 probe）
        bundle_plans = dist_dir / "_internal" / "core" / "claude-home" / ".plans"
        if bundle_plans.exists() and list(bundle_plans.glob("*__frozen_write_probe__*")):
            fails.append("probe plan 误写进只读 bundle _internal（迁移没生效）")
    except subprocess.TimeoutExpired:
        fails.append("plan_tracker create 超时")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def check_outline_scripts(exe: Path) -> list:
    """阶段2 创建书籍：新增的 outline 脚本在真 frozen exe 经 dispatch 跑通（无 API）。
    init_project --emit-style-options（确定性）+ gen_creative volume_arc --dry-run（不调 API·验
    prompt 组装 + 作者档注入链路在 frozen 下不崩）。"""
    import tempfile
    import json as _json
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix="s2_outline_"))
    (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
    try:
        # init_project --emit-style-options（frozen 下扫风格库·确定性）
        r1 = subprocess.run([str(exe), "core/scripts/init_project.py", str(tmp),
                             "--emit-style-options", "--no-git"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
        if r1.returncode != 0 or "Traceback" in r1.stderr:
            fails.append(f"init_project frozen dispatch rc={r1.returncode}·{r1.stderr[-150:]}")
        so = tmp / "_数据库" / ".wal" / "style_options.json"
        if not so.exists():
            fails.append("init_project 未在 frozen 下写 style_options.json")
        # gen_creative volume_arc --dry-run（不调 API·验 frozen 下 prompt 组装链路）
        card = tmp / "card.json"
        card.write_text(_json.dumps({"answer": {"title": "测试", "logline": "梗概"}},
                                    ensure_ascii=False), encoding="utf-8")
        r2 = subprocess.run([str(exe), "core/scripts/gen_creative.py", "--mode", "volume_arc",
                             "--project", str(tmp), "--selected-card", str(card),
                             "--cluster-count", "8", "--framework", "三幕", "--rhythm", "标准",
                             "--dry-run"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=90)
        if r2.returncode != 0 or "Traceback" in r2.stderr:
            fails.append(f"gen_creative volume_arc dry-run frozen rc={r2.returncode}·{r2.stderr[-150:]}")
        elif "story_destiny" not in r2.stdout or "绝不写" not in r2.stdout:
            fails.append("volume_arc dry-run 输出缺脚手架（prompt 组装异常）")
    except subprocess.TimeoutExpired:
        fails.append("outline 脚本 frozen dispatch 超时")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return fails


def check_datas_parity(dist_dir: Path) -> list:
    """datas 齐全性比对（复验 P2）：仓库里 spec 声明要带的数据集 vs dist/_internal
    实际落盘——抓「仓库新增脚本/plan/agent 但 exe 是旧的（或 spec 漏带）」。"""
    fails = []
    internal = dist_dir / "_internal"
    pairs = [
        (ROOT / "core" / "scripts", internal / "core" / "scripts", "*.py"),
        (ROOT / "core" / "claude-home" / "plans",
         internal / "core" / "claude-home" / "plans", "*.plan.json"),
        (ROOT / ".claude" / "agents", internal / ".claude" / "agents", "*.md"),
        (ROOT / "core" / "claude-home" / "lessons",
         internal / "core" / "claude-home" / "lessons", "*.md"),
    ]
    for src, dst, pat in pairs:
        if not src.exists():
            continue
        missing = [f.name for f in src.glob(pat) if not (dst / f.name).exists()]
        if missing:
            fails.append(f"{dst.relative_to(dist_dir)} 缺 {len(missing)} 个文件"
                         f"（仓库有 exe 没有·exe 过期或 spec 漏带）: {missing[:5]}")
    for rel in ("core/config/gen_profiles.default.env",
                "core/claude-home/templates/subsystem_skeletons.json"):
        if (ROOT / rel).exists() and not (internal / rel).exists():
            fails.append(f"_internal 缺关键单文件: {rel}")
    return fails


def check_scipy_smoke(exe: Path) -> list:
    """style_evaluator(scipy) frozen 冒烟（复验 P2：scipy 从未在 frozen 验证过）。
    --help 即触发顶层 import numpy/scipy——崩了就是 PYZ 缺二进制依赖。"""
    fails = []
    try:
        r = subprocess.run([str(exe), "core/scripts/style_evaluator.py", "--help"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        if r.returncode != 0:
            fails.append(f"style_evaluator --help 退出码 {r.returncode}"
                         f"·stderr={r.stderr[-200:]}")
        if "Traceback" in r.stderr:
            fails.append(f"style_evaluator frozen import 崩（scipy 依赖缺）: "
                         f"{r.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        fails.append("style_evaluator --help 超时")
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
                     ("datas_parity", lambda: check_datas_parity(dist_dir)),
                     ("dispatch", lambda: check_dispatch(exe)),
                     ("scipy_smoke", lambda: check_scipy_smoke(exe)),
                     ("writable_data", lambda: check_writable_data(exe, dist_dir)),
                     ("outline_scripts", lambda: check_outline_scripts(exe)),
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
    _say("RESULT", "GUI EXE 验证 PASS 7/7（安全 + datas齐全 + dispatch + scipy冒烟"
                   " + 可写数据 + outline脚本 + GUI serve）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
