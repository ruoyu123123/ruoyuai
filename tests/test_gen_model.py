#!/usr/bin/env python3
"""gen_model.py CLI 周边逻辑回归测试（profile 管理 CLI·非真 LLM·零依赖）。

🔴 关键事实：gen_model.py 是 **profile 管理 CLI**，本身**不调任何 LLM API**
（无 import openai / urllib / requests / socket）。它只读写 .env / user_override
并 list/show/switch/add profile 配置。故本测试只测**确定性周边逻辑**：
  - mask_key 脱敏
  - cmd_list / cmd_show / cmd_switch / cmd_add 命令处理器（返回码 + stdout/stderr）
  - cmd_add 名校验（非法名拒绝 / 重名拒绝 / 模板追加 .env）
  - set_active dev 无 ACTIVE 行时追加分支
  - main() argparse 分发

本文件补 CLI 处理器 / mask_key / 名校验 / 追加分支。

零依赖：只用标准库 + 假 loader（不碰真 .env / keyring / 网络）。test_* 无参数。
兜底安全：模块顶层把 urllib/socket 真出网点 monkeypatch 成「调用即 raise」，
防任何依赖意外出网真花钱。
"""
import io
import os
import re
import sys
import tempfile
import contextlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_model as mod  # noqa: E402
from gen_model_loader import Profile, GenModelConfigError  # noqa: E402


# ───────────────────────── 网络兜底安全闸 ─────────────────────────
# gen_model.py 本身不出网，但任何依赖若意外出网就立刻炸（防真花钱）。
# 模块导入即生效，所有测试运行期间常驻。
def _boom(*a, **k):  # pragma: no cover - 只在漏 mock 时触发
    raise AssertionError("测试期间禁止真实网络调用（gen_model.py 不应出网）")


import urllib.request as _urlreq  # noqa: E402
import socket as _socket  # noqa: E402

_urlreq.urlopen = _boom
_socket.socket.connect = _boom  # type: ignore[assignment]


# ───────────────────────── 假 loader 工具 ─────────────────────────
class FakeLoader:
    """最小假 GenModelLoader——只实现 cmd_* 用到的方法，绝不读真 .env / keyring / 网络。"""

    def __init__(self, profiles=None, active="", chain=None,
                 env_path=None):
        self._profiles = {p.name: p for p in (profiles or [])}
        self._active = active
        self._chain = chain or []
        self.env_path = env_path

    def list_profiles(self):
        return list(self._profiles.values())

    def get_profile(self, name):
        return self._profiles.get(name)

    def get_active_profile(self):
        if not self._active:
            raise GenModelConfigError("GEN_MODEL_ACTIVE 未设置")
        p = self._profiles.get(self._active)
        if p is None:
            raise GenModelConfigError(f"active '{self._active}' 不存在")
        if not p.api_key:
            raise GenModelConfigError(f"active '{self._active}' 缺 API_KEY")
        return p

    def get_fallback_chain(self):
        return list(self._chain)


def _prof(name, model="m", api_key="", base_url="http://x"):
    return Profile(name=name, model=model, base_url=base_url, api_key=api_key,
                   temperature=0.8, max_tokens=None)


def _capture(fn, *args):
    """跑 fn 捕获 (rc, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


# ───────────────────────── mask_key ─────────────────────────
def test_mask_key_empty_and_short_and_normal():
    # 空 → 未填占位
    assert mod.mask_key("") == "(未填)"
    assert mod.mask_key(None or "") == "(未填)"
    # 短 key（<12）→ 全遮
    assert mod.mask_key("sk-123") == "***"
    assert mod.mask_key("a" * 11) == "***"
    # 正常 key → 前6 + ... + 后4，且中段不泄露
    k = "sk-ABCDEFGHIJKLMNOP1234"
    masked = mod.mask_key(k)
    assert masked.startswith("sk-ABC"), masked
    assert masked.endswith("1234"), masked
    assert "..." in masked
    # 中间真实段不出现在脱敏结果里（防泄露）
    assert "GHIJKLMN" not in masked


# ───────────────────────── cmd_list ─────────────────────────
def test_cmd_list_empty_returns_0_with_hint():
    ld = FakeLoader(profiles=[])
    rc, out, err = _capture(mod.cmd_list, None, ld)
    assert rc == 0
    assert "无 profile" in err  # 提示走 stderr


def test_cmd_list_marks_active_and_key_status():
    ld = FakeLoader(
        profiles=[_prof("alpha", api_key="sk-FULLKEYHERE0000"),
                  _prof("beta", api_key="")],
        active="alpha",
        chain=["alpha", "beta"],
    )
    rc, out, err = _capture(mod.cmd_list, None, ld)
    assert rc == 0
    # active 标记 ★ 出现且 fallback chain 打印
    assert "★" in out
    assert "alpha" in out and "beta" in out
    assert "fallback chain: alpha,beta" in out
    # key 状态：有 key ✓ / 无 key ✗ 都出现
    assert "✓" in out and "✗" in out


def test_cmd_list_active_unresolvable_no_crash():
    # active 名存在但缺 key → get_active_profile 抛 → cmd_list 吞成空 active_name 不崩
    ld = FakeLoader(profiles=[_prof("alpha", api_key="")], active="alpha")
    rc, out, err = _capture(mod.cmd_list, None, ld)
    assert rc == 0
    assert "★" not in out  # 无法解析 active → 不打星


# ───────────────────────── cmd_show ─────────────────────────
def test_cmd_show_ok_masks_key():
    ld = FakeLoader(
        profiles=[_prof("alpha", model="gpt-x", api_key="sk-SHOWKEY12345678")],
        active="alpha", chain=["alpha"],
    )
    rc, out, err = _capture(mod.cmd_show, None, ld)
    assert rc == 0
    assert "active profile: alpha" in out
    assert "gpt-x" in out
    # 明文 key 绝不出现，脱敏段出现
    assert "sk-SHOWKEY12345678" not in out
    assert "sk-SHO" in out and "5678" in out


def test_cmd_show_no_active_returns_2():
    ld = FakeLoader(profiles=[], active="")
    rc, out, err = _capture(mod.cmd_show, None, ld)
    assert rc == 2
    assert "[ERROR]" in err


def test_cmd_show_max_tokens_blank_hint():
    ld = FakeLoader(
        profiles=[_prof("alpha", api_key="sk-KEYKEYKEYKEY12")],
        active="alpha",
    )
    rc, out, err = _capture(mod.cmd_show, None, ld)
    assert rc == 0
    # max_tokens=None → 提示从能力表取
    assert "model_capabilities" in out


# ───────────────────────── cmd_switch ─────────────────────────
class _Args:
    def __init__(self, name):
        self.name = name


def test_cmd_switch_missing_profile_returns_2():
    ld = FakeLoader(profiles=[_prof("alpha", api_key="sk-x")], active="alpha")
    rc, out, err = _capture(mod.cmd_switch, _Args("nope"), ld)
    assert rc == 2
    assert "不存在" in err


def test_cmd_switch_ok_calls_set_active(monkeyless=True):
    # 替 set_active 成假实现（避免真写文件），验证 cmd_switch 调到它且返回 0
    ld = FakeLoader(
        profiles=[_prof("beta", model="m2", api_key="sk-BETAKEY1234")],
        active="alpha",
    )
    called = {}
    saved = mod.set_active
    try:
        mod.set_active = lambda loader, name: called.update(loader=loader, name=name)
        rc, out, err = _capture(mod.cmd_switch, _Args("beta"), ld)
    finally:
        mod.set_active = saved
    assert rc == 0
    assert called == {"loader": ld, "name": "beta"}
    assert "[OK] active = beta" in out


def test_cmd_switch_missing_key_warns_but_proceeds():
    # 目标 profile 无 key → 打 WARN 但仍 set_active + 返回 0
    ld = FakeLoader(profiles=[_prof("beta", api_key="")], active="alpha")
    saved = mod.set_active
    try:
        mod.set_active = lambda loader, name: None
        rc, out, err = _capture(mod.cmd_switch, _Args("beta"), ld)
    finally:
        mod.set_active = saved
    assert rc == 0
    assert "[WARN]" in err


# ───────────────────────── cmd_add ─────────────────────────
def test_cmd_add_rejects_illegal_name():
    ld = FakeLoader(profiles=[], env_path=Path("/should/not/be/touched"))
    for bad in ("1abc", "has space", "has-dash", "", "_leading"):
        rc, out, err = _capture(mod.cmd_add, _Args(bad), ld)
        assert rc == 2, f"非法名 {bad!r} 应返回 2"
        assert "字母" in err


def test_cmd_add_rejects_duplicate():
    ld = FakeLoader(profiles=[_prof("dup", api_key="sk-x")])
    rc, out, err = _capture(mod.cmd_add, _Args("dup"), ld)
    assert rc == 2
    assert "已存在" in err


def test_cmd_add_appends_template_to_env():
    tmp = Path(tempfile.mkdtemp())
    env_file = tmp / ".env"
    env_file.write_text("GEN_MODEL_ACTIVE=existing\n", encoding="utf-8")
    ld = FakeLoader(profiles=[], env_path=env_file)
    rc, out, err = _capture(mod.cmd_add, _Args("glm_main"), ld)
    assert rc == 0
    text = env_file.read_text(encoding="utf-8")
    # 原内容保留 + 5 行 profile 模板字段都追加
    assert "GEN_MODEL_ACTIVE=existing" in text
    for field in ("MODEL", "BASE_URL", "API_KEY", "TEMPERATURE", "MAX_TOKENS"):
        assert f"GEN__glm_main__{field}=" in text, f"缺 {field}"
    assert "[OK]" in out


# ───────────────────────── set_active dev 无 ACTIVE 行追加分支 ─────────────────────────
def test_set_active_dev_appends_when_no_active_line():
    # dev 模式 + .env 里没有 GEN_MODEL_ACTIVE 行 → set_active 追加一行（line 100-102 分支）
    tmp = Path(tempfile.mkdtemp())
    env_file = tmp / ".env"
    env_file.write_text("GEN__a__MODEL=m\n", encoding="utf-8")  # 故意无 ACTIVE 行
    ld = FakeLoader(env_path=env_file)
    mod.set_active(ld, "a")
    text = env_file.read_text(encoding="utf-8")
    assert "GEN__a__MODEL=m" in text          # 原内容保留
    assert "GEN_MODEL_ACTIVE=a" in text        # 新增 active 行
    # 只有一条 active 行（没有重复）
    assert len(re.findall(r"^GEN_MODEL_ACTIVE=", text, re.M)) == 1


def test_set_active_dev_replaces_existing_line_once():
    tmp = Path(tempfile.mkdtemp())
    env_file = tmp / ".env"
    env_file.write_text("GEN_MODEL_ACTIVE=old\nGEN__b__MODEL=m\n", encoding="utf-8")
    ld = FakeLoader(env_path=env_file)
    mod.set_active(ld, "newone")
    text = env_file.read_text(encoding="utf-8")
    assert "GEN_MODEL_ACTIVE=newone" in text
    assert "old" not in text
    assert len(re.findall(r"^GEN_MODEL_ACTIVE=", text, re.M)) == 1


# ───────────────────────── main() argparse 分发 ─────────────────────────
def test_main_dispatches_to_handler():
    # 不真构造 GenModelLoader（会读真 .env / keyring）——把 GenModelLoader 与各 handler 都替掉，
    # 只验证 argparse 解析 + 分发选对了 handler。
    import argparse  # noqa
    saved_loader = mod.GenModelLoader
    captured = {}

    def fake_list(args, loader):
        captured["cmd"] = "list"
        captured["loader"] = loader
        return 7  # 自定义返回码确认透传

    saved_handlers = {k: getattr(mod, f"cmd_{k}") for k in ("list",)}
    try:
        mod.GenModelLoader = lambda: "FAKE_LOADER_SENTINEL"
        mod.cmd_list = fake_list
        old_argv = sys.argv
        sys.argv = ["gen_model.py", "list"]
        try:
            rc, out, err = _capture(mod.main)
        finally:
            sys.argv = old_argv
    finally:
        mod.GenModelLoader = saved_loader
        for k, v in saved_handlers.items():
            setattr(mod, f"cmd_{k}", v)
    assert rc == 7
    assert captured["cmd"] == "list"
    assert captured["loader"] == "FAKE_LOADER_SENTINEL"


def test_main_requires_subcommand():
    # 无子命令 → argparse required=True → SystemExit（非 0）
    old_argv = sys.argv
    sys.argv = ["gen_model.py"]
    try:
        raised = False
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                mod.main()
        except SystemExit as e:
            raised = True
            assert e.code != 0
        assert raised, "缺子命令应触发 SystemExit"
    finally:
        sys.argv = old_argv


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
