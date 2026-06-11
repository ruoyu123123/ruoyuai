#!/usr/bin/env python3
"""GUI /new-book 创建书籍页测试（阶段2·NiceGUI User 夹具 + 内存 keyring）。

渲染 / 书名校验 / key 前置校验 / 建书触发(mock RUNNER.start 不跑真管线)。
运行：python -m pytest tests/gui -q
"""
import sys
from pathlib import Path

import pytest

import core.gui.app as app_module
import core.gui.runner as gr
import core.gui.state as gs

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
import gen_model_loader as gml  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402


@pytest.fixture
def nb_env(user, tmp_path, monkeypatch):
    """内存 keyring + 临时 .env（一个 active profile）+ 页面重注册。"""
    env = tmp_path / ".env"
    env.write_text("GEN__demo__MODEL=demo-model\n"
                   "GEN__demo__BASE_URL=https://x.test/v1\n"
                   "GEN_MODEL_ACTIVE=demo\n", encoding="utf-8")
    monkeypatch.setattr(gml, "_default_loader", None)
    orig_init = gml.GenModelLoader.__init__
    monkeypatch.setattr(gml.GenModelLoader, "__init__",
                        lambda self, env_path=None: orig_init(self, env_path=str(env)))
    orig_kr = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    monkeypatch.setenv("GEN_MODEL_ACTIVE", "demo")
    # 🔴 NOVELS_DIR 指临时目录·不污染真 workspace/novels（测试幂等·防「已存在」早返）
    monkeypatch.setattr(gs, "NOVELS_DIR", tmp_path / "novels")
    app_module.init_pages()
    yield
    keyring.set_keyring(orig_kr)
    gml.reset_default_loader()


async def test_new_book_page_renders(user, nb_env):
    await user.open("/new-book")
    await user.should_see(marker="book-name")
    await user.should_see(marker="book-topic")
    await user.should_see(marker="btn-build")


async def test_build_without_name_warns(user, nb_env):
    await user.open("/new-book")
    user.find(marker="btn-build").click()
    await user.should_see("先填书名")


async def test_build_without_key_warns(user, nb_env):
    """active profile 无 key → 红字提醒去设置录 key（不跑管线·防跑到调研才 401）。"""
    await user.open("/new-book")
    user.find(marker="book-name").type("测试新书")
    user.find(marker="btn-build").click()
    await user.should_see("还没填密钥")


async def test_build_with_key_starts(user, nb_env, monkeypatch):
    """有 key + 书名 → 调 RUNNER.start(['outline'], 书名, '')。"""
    ss.set_api_key("demo", "sk-NEWBOOKKEY")
    captured = {}

    def fake_start(commands, project, key, *, auto_pilot=False, resume_plan_id=None):
        captured["commands"] = commands
        captured["project"] = project
        captured["key"] = key
        return True
    monkeypatch.setattr(app_module.RUNNER, "start", fake_start)

    await user.open("/new-book")
    user.find(marker="book-name").type("测试新书ABC")
    user.find(marker="btn-build").click()
    await user.should_see("开始建")
    assert captured.get("commands") == ["outline"]
    assert captured.get("project") == "测试新书ABC"
    assert captured.get("key") == ""   # outline 无 cluster key（放宽）
