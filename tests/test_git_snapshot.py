#!/usr/bin/env python3
"""git_snapshot.py 确定性回归测试（零 LLM / 零联网 · 仅标准库 + 真 git）。

被测脚本是 CLAUDE.md「Git 版本快照（自动）」的程序驱动实现，安全纪律强：
预检 git+.git / 不 push/pull/force/reset / **不动全局 config**（只设 --local 兜底）/
失败不中断流水线（尽量 exit 0）。本测试在 tempfile 隔离仓库里真跑 git，钉死核心不变量：

- 安全跳过：项目无 .git → 安静 `git init` 后继续；项目目录不存在 → main exit 0。
- 无变更短路：`git diff --cached --quiet` 退出码 0 → 跳过且**不产生新 commit**（return 0）。
- 真快照：有变更 → 产生带指定 message 的 commit，return 0。
- 身份兜底的边界：只写 --local（绝不污染 global）·已有 local 身份则**不覆盖**。
- `_run` timeout 分支：超时返回 returncode=124 的 CompletedProcess（不抛异常）。
- CLI 端到端：subprocess 跑真 argparse + 真 commit，断言退出码 + 真实 commit 落库。

现有间接覆盖：tests/test_plan_script_contract.py 仅静态断言本脚本文件存在（防 plan 幽灵
引用），**完全不触发任何运行时行为**。故本文件聚焦上述运行时核心逻辑，无重叠。

注：每个用例都 `git config --local --add safe.directory <tmp>` 并强制 HOME/全局 config 指向
tempdir，确保「不动全局 config」断言可被独立验证、且 CI 机器 global 身份缺失也能跑。
"""
import os
import subprocess
import sys
import tempfile
import shutil
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "scripts"))
import git_snapshot as mod  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TARGET = _ROOT / "core" / "scripts" / "git_snapshot.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _isolated_env():
    """返回一个把 HOME / 全局 git config 重定向到临时空目录的 env，
    使被测脚本若误改 global config 也只落在弃用临时目录，可独立验证。"""
    env = dict(os.environ)
    cfg_home = tempfile.mkdtemp()
    env["HOME"] = cfg_home
    env["USERPROFILE"] = cfg_home
    env["GIT_CONFIG_GLOBAL"] = str(pathlib.Path(cfg_home) / "gitconfig_global")
    env["GIT_CONFIG_SYSTEM"] = str(pathlib.Path(cfg_home) / "gitconfig_system")
    # 不依赖被测脚本设身份；但这里不预置，留给被测脚本兜底逻辑去证明
    return env, cfg_home


def _git(proj, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(proj), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env)


def _mkproj():
    return pathlib.Path(tempfile.mkdtemp())


def _commit_count(proj, env=None):
    r = _git(proj, "rev-list", "--count", "HEAD", env=env)
    if r.returncode != 0:
        return 0  # 还没有 HEAD（无 commit）
    return int((r.stdout or "0").strip() or "0")


# ──────────────────────────────────────────────────────────────────────────
# snapshot —— 自动 init + 真快照
# ──────────────────────────────────────────────────────────────────────────
def test_auto_init_and_first_commit():
    """项目无 .git → snapshot 应安静 git init，并对有内容的工作区产出首个 commit。"""
    env, cfg = _isolated_env()
    old = dict(os.environ)
    try:
        os.environ.update(env)  # snapshot 内部 subprocess 继承 os.environ
        proj = _mkproj()
        (proj / "正文.txt").write_text("第一章", encoding="utf-8")
        assert not (proj / ".git").is_dir(), "前置：不应已有 .git"
        rc = mod.snapshot(proj, "初始化项目")
        assert rc == 0, f"snapshot 应 return 0，实得 {rc}"
        assert (proj / ".git").is_dir(), "snapshot 应已 git init"
        assert _commit_count(proj) == 1, "应恰好产生 1 个 commit"
        # commit message 落实
        log = _git(proj, "log", "-1", "--pretty=%s")
        assert log.stdout.strip() == "初始化项目", f"message 不符: {log.stdout!r}"
    finally:
        os.environ.clear(); os.environ.update(old)
        shutil.rmtree(cfg, ignore_errors=True)


def test_no_change_skips_without_new_commit():
    """有 commit 后再次 snapshot 且工作区无变更 → 短路 return 0，commit 数不变。"""
    env, cfg = _isolated_env()
    old = dict(os.environ)
    try:
        os.environ.update(env)
        proj = _mkproj()
        (proj / "a.txt").write_text("x", encoding="utf-8")
        assert mod.snapshot(proj, "c1") == 0
        before = _commit_count(proj)
        assert before == 1
        # 工作区完全没动
        rc = mod.snapshot(proj, "c2-应被跳过")
        assert rc == 0, "无变更应 return 0"
        assert _commit_count(proj) == before, "无变更不应产生新 commit"
    finally:
        os.environ.clear(); os.environ.update(old)
        shutil.rmtree(cfg, ignore_errors=True)


def test_second_change_makes_second_commit():
    """二次有变更 → 第二个 commit；验证 add -A 抓到新增/修改。"""
    env, cfg = _isolated_env()
    old = dict(os.environ)
    try:
        os.environ.update(env)
        proj = _mkproj()
        (proj / "a.txt").write_text("v1", encoding="utf-8")
        assert mod.snapshot(proj, "c1") == 0
        (proj / "a.txt").write_text("v2 改了", encoding="utf-8")
        (proj / "b.txt").write_text("新文件", encoding="utf-8")
        assert mod.snapshot(proj, "c2") == 0
        assert _commit_count(proj) == 2, "应有 2 个 commit"
        # 第二次 commit 应包含两份文件改动
        show = _git(proj, "show", "--stat", "--pretty=%s", "HEAD")
        assert "c2" in show.stdout
        assert "b.txt" in show.stdout, "add -A 应捕获新增 b.txt"
    finally:
        os.environ.clear(); os.environ.update(old)
        shutil.rmtree(cfg, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# 身份兜底边界 —— 只写 --local，不覆盖既有，不动 global
# ──────────────────────────────────────────────────────────────────────────
def test_identity_bootstrap_writes_local_only():
    """无任何身份时，snapshot 应写 --local user.name/email 兜底，且 global 保持空。"""
    env, cfg = _isolated_env()
    old = dict(os.environ)
    try:
        os.environ.update(env)
        proj = _mkproj()
        (proj / "a.txt").write_text("x", encoding="utf-8")
        assert mod.snapshot(proj, "c1") == 0
        local_name = _git(proj, "config", "--local", "user.name").stdout.strip()
        local_email = _git(proj, "config", "--local", "user.email").stdout.strip()
        assert local_name == "ruoyuai", f"local user.name 兜底应为 ruoyuai，实得 {local_name!r}"
        assert local_email == "ruoyuai@local", f"local user.email 兜底错: {local_email!r}"
        # global config 文件应不含我们写的兜底身份（绝不动 global）
        gpath = pathlib.Path(env["GIT_CONFIG_GLOBAL"])
        gtext = gpath.read_text(encoding="utf-8") if gpath.exists() else ""
        assert "ruoyuai@local" not in gtext, "不得污染 global config"
    finally:
        os.environ.clear(); os.environ.update(old)
        shutil.rmtree(cfg, ignore_errors=True)


def test_existing_local_identity_not_overwritten():
    """项目已配 --local 身份 → snapshot 不得覆盖（条件兜底·只在缺失时写）。"""
    env, cfg = _isolated_env()
    old = dict(os.environ)
    try:
        os.environ.update(env)
        proj = _mkproj()
        _git(proj, "init")
        _git(proj, "config", "--local", "user.name", "作者本人")
        _git(proj, "config", "--local", "user.email", "me@example.com")
        (proj / "a.txt").write_text("x", encoding="utf-8")
        assert mod.snapshot(proj, "c1") == 0
        assert _git(proj, "config", "--local", "user.name").stdout.strip() == "作者本人", \
            "已有 local user.name 不应被覆盖"
        assert _git(proj, "config", "--local", "user.email").stdout.strip() == "me@example.com", \
            "已有 local user.email 不应被覆盖"
        # commit 仍应成功（用既有身份）
        log = _git(proj, "log", "-1", "--pretty=%an <%ae>")
        assert "作者本人" in log.stdout and "me@example.com" in log.stdout
    finally:
        os.environ.clear(); os.environ.update(old)
        shutil.rmtree(cfg, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# _run timeout 分支 —— 不抛异常，返回 returncode 124
# ──────────────────────────────────────────────────────────────────────────
def test_run_timeout_returns_124():
    """_run 命中 TimeoutExpired → 返回 CompletedProcess(returncode=124)，不抛。"""
    proj = _mkproj()
    # 用一个必然超时的 python sleep 命令，timeout 极小
    cp = mod._run([sys.executable, "-c", "import time; time.sleep(5)"], proj, timeout=1)
    assert isinstance(cp, subprocess.CompletedProcess)
    assert cp.returncode == 124, f"超时应返回 124，实得 {cp.returncode}"
    assert "timeout" in (cp.stderr or "").lower()


def test_run_returns_completed_process_normally():
    """_run 正常路径返回真实 CompletedProcess + 捕获 stdout。"""
    proj = _mkproj()
    cp = mod._run([sys.executable, "-c", "print('hi-ruoyu')"], proj, timeout=30)
    assert cp.returncode == 0
    assert "hi-ruoyu" in cp.stdout


# ──────────────────────────────────────────────────────────────────────────
# main / CLI —— 退出码 + 安全跳过 + 端到端真 commit
# ──────────────────────────────────────────────────────────────────────────
def test_main_missing_project_dir_exits_zero():
    """项目目录不存在 → main 安全跳过 exit 0（advisory 不阻断流水线）。"""
    ghost = pathlib.Path(tempfile.mkdtemp()) / "不存在的书"
    assert not ghost.is_dir()
    old_argv = sys.argv
    try:
        sys.argv = ["git_snapshot.py", str(ghost), "--message", "x"]
        try:
            mod.main()
            raised = None
        except SystemExit as e:
            raised = e
        assert raised is not None, "main 应调用 sys.exit"
        assert raised.code == 0, f"目录不存在应 exit 0，实得 {raised.code}"
    finally:
        sys.argv = old_argv


def test_cli_end_to_end_real_commit():
    """subprocess 跑真 CLI（真 argparse + 真 git）→ exit 0 且产生带 message 的 commit。"""
    env, cfg = _isolated_env()
    try:
        proj = _mkproj()
        (proj / "稿子.txt").write_text("正文内容", encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(_TARGET), str(proj), "--message", "feat: 端到端"],
            capture_output=True, cwd=str(_ROOT), env=env)
        assert p.returncode == 0, f"CLI 应 exit 0，实得 {p.returncode} / {p.stderr!r}"
        assert _commit_count(proj, env=env) == 1, "CLI 应产生 1 个 commit"
        log = _git(proj, "log", "-1", "--pretty=%s", env=env)
        assert log.stdout.strip() == "feat: 端到端", f"message 不符: {log.stdout!r}"
    finally:
        shutil.rmtree(cfg, ignore_errors=True)


def test_cli_no_change_after_commit_exit_zero():
    """CLI 二次跑无变更 → exit 0 且不新增 commit。"""
    env, cfg = _isolated_env()
    try:
        proj = _mkproj()
        (proj / "稿子.txt").write_text("正文", encoding="utf-8")
        subprocess.run([sys.executable, str(_TARGET), str(proj), "--message", "c1"],
                       capture_output=True, cwd=str(_ROOT), env=env)
        before = _commit_count(proj, env=env)
        p = subprocess.run([sys.executable, str(_TARGET), str(proj), "--message", "c2"],
                           capture_output=True, cwd=str(_ROOT), env=env)
        assert p.returncode == 0, f"无变更 CLI 应 exit 0，实得 {p.returncode}"
        assert _commit_count(proj, env=env) == before, "无变更不应新增 commit"
    finally:
        shutil.rmtree(cfg, ignore_errors=True)
