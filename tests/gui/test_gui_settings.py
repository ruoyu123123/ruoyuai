#!/usr/bin/env python3
"""GUI 设置页 BYOK 密钥录入测试（角度②·NiceGUI User 夹具 + 内存 keyring）。

整链端到端：录入 → keyring → 新 loader → Profile.api_key（核心·防假阴性）。
绝不碰真 Credential Manager（内存后端）、绝不打真 API（monkeypatch transport）。
运行：python -m pytest tests/gui -q
"""
import os
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
def byok_env(user, tmp_path, monkeypatch):
    """内存 keyring + 临时 .env（定义一个 profile·无 key 行）+ 页面重注册。"""
    env = tmp_path / ".env"
    env.write_text("GEN__demo__MODEL=demo-model\n"
                   "GEN__demo__BASE_URL=https://x.test/v1\n"
                   "GEN_MODEL_ACTIVE=demo\n", encoding="utf-8")
    # loader 单例指向这个临时 .env（绕真 .env）
    monkeypatch.setattr(gml, "_default_loader", None)
    orig_loader_init = gml.GenModelLoader.__init__

    def _patched_init(self, env_path=None):
        orig_loader_init(self, env_path=str(env))
    monkeypatch.setattr(gml.GenModelLoader, "__init__", _patched_init)

    orig_kr = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    monkeypatch.setenv("GEN_MODEL_ACTIVE", "demo")
    app_module.init_pages()
    yield
    keyring.set_keyring(orig_kr)
    gml.reset_default_loader()


async def test_settings_renders_badge_unconfigured(user, byok_env):
    await user.open("/settings")
    await user.should_see(marker="badge-demo")
    await user.should_see("未配置")
    await user.should_see(marker="key-input-demo")


async def test_save_key_end_to_end_loader_sees_it(user, byok_env):
    """核心：录入 → keyring → 新 loader.get_profile().api_key 真认到（整链·防假阴性）。"""
    await user.open("/settings")
    user.find(marker="key-input-demo").type("sk-GUISENTINEL777")
    user.find(marker="btn-save-key-demo").click()
    await user.should_see("已保存到本机安全存储")
    # ① 进了 keyring
    assert ss.get_api_key("demo") == "sk-GUISENTINEL777"
    # ② loader 真认到（BYOK 注入面打通·整链核心断言）
    assert gml.GenModelLoader().get_profile("demo").api_key == "sk-GUISENTINEL777"
    # ③ 徽章转「已配置」
    await user.should_see("已配置")


async def test_save_empty_warns(user, byok_env):
    await user.open("/settings")
    user.find(marker="btn-save-key-demo").click()
    await user.should_see("请先粘贴密钥")


async def test_clear_key_resets_badge(user, byok_env):
    ss.set_api_key("demo", "sk-TOCLEAR123")
    await user.open("/settings")
    user.find(marker="btn-clear-key-demo").click()
    await user.should_see("已清除")
    assert ss.get_api_key("demo") is None


async def test_full_key_never_in_page_data(user, byok_env):
    """全量 key 绝不进页面数据（masked / has_api_key·不取明文给 UI）。"""
    ss.set_api_key("demo", "sk-VERYLONGSECRETKEY999888")
    data = gr.list_profiles_masked()
    for row in data["profiles"]:
        assert "sk-VERYLONGSECRETKEY" not in str(row.get("api_key", ""))
        assert row.get("key_in_keyring") in (True, False)  # 布尔状态·非明文


async def test_test_connection_redacts_key_in_error(user, byok_env, monkeypatch):
    """测试连接异常含 key=（gemini URL）→ UI 文本必脱敏（must_fix#3）。"""
    ss.set_api_key("demo", "sk-LIVEKEY555")
    import llm_transport as T

    def _boom(*a, **k):
        raise RuntimeError("HTTP error url=https://x/v1beta?alt=sse&key=sk-LIVEKEY555")
    monkeypatch.setattr(T, "stream_once", _boom)
    r = gr.test_profile_connection("demo")
    assert r["ok"] is False
    assert "sk-LIVEKEY555" not in r["message"]   # key 已脱敏


async def test_keyring_unavailable_banner(user, byok_env, monkeypatch):
    monkeypatch.setattr(ss, "is_available", lambda: False)
    await user.open("/settings")
    await user.should_see(marker="keyring-unavailable")


async def test_active_selector_renders(user, byok_env):
    """设置页有切模型下拉（后端 switch_active 逻辑由 test_config_split 堅牢覆盖）。"""
    await user.open("/settings")
    await user.should_see(marker="active-select")


# ============ D1 research-web 联网调研 search key 录入（BYOK·独立 service）============
async def test_search_key_card_renders_unconfigured(user, byok_env):
    await user.open("/settings")
    await user.should_see(marker="search-key-card")
    await user.should_see(marker="search-key-input")
    await user.should_see("联网调研")


async def test_save_search_key_end_to_end(user, byok_env):
    """录入 search key → keyring（独立 service）→ web_search_client 解析真认到（整链·防假阴性）。"""
    import web_search_client as wsc
    await user.open("/settings")
    user.find(marker="search-key-input").type("tvly-GUISEARCH888")
    user.find(marker="btn-save-search").click()
    await user.should_see("已保存联网调研 key")
    # ① 进了 search keyring（独立 service·不串 gen-model）
    assert ss.get_search_key("tavily") == "tvly-GUISEARCH888"
    assert ss.get_api_key("tavily") is None         # service 隔离·不串 gen-model
    # ② web_search_client 解析真认到（BYOK 整链打通）
    assert wsc._resolve_search_key("tavily") == "tvly-GUISEARCH888"


async def test_save_search_empty_warns(user, byok_env):
    await user.open("/settings")
    user.find(marker="btn-save-search").click()
    await user.should_see("请先粘贴 search key")


async def test_clear_search_key_resets(user, byok_env):
    ss.set_search_key("tavily", "tvly-TOCLEAR")
    await user.open("/settings")
    user.find(marker="btn-clear-search").click()
    await user.should_see("已清除联网调研 key")
    assert ss.get_search_key("tavily") is None
