#!/usr/bin/env python3
"""GUI /distill 蒸馏风格页测试（阶段1 复刻半程·NiceGUI User 夹具）。

渲染 / 无可复刻风格兜底 / key 校验 / 复刻触发(mock RUNNER.run_replicate)。
"""
import sys
from pathlib import Path

import pytest

import core.gui.app as app_module
import core.gui.runner as gr

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
import gen_model_loader as gml  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402


@pytest.fixture
def d_env(user, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEN__demo__MODEL=demo-model\nGEN__demo__BASE_URL=https://x.test/v1\n"
                   "GEN_MODEL_ACTIVE=demo\n", encoding="utf-8")
    monkeypatch.setattr(gml, "_default_loader", None)
    orig_init = gml.GenModelLoader.__init__
    monkeypatch.setattr(gml.GenModelLoader, "__init__",
                        lambda self, env_path=None: orig_init(self, env_path=str(env)))
    orig_kr = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    monkeypatch.setenv("GEN_MODEL_ACTIVE", "demo")
    # mock scan_distill_styles 返一个固定风格（不依赖真风格库）
    monkeypatch.setattr(gr, "scan_distill_styles",
                        lambda min_chapters=5: [{"name": "测试风格", "title": "测试风格",
                                                 "skill": "skill.md", "raw_chapters": 9}])
    app_module.init_pages()
    yield
    keyring.set_keyring(orig_kr)
    gml.reset_default_loader()


async def test_distill_page_renders(user, d_env):
    await user.open("/distill")
    await user.should_see(marker="distill-style")
    await user.should_see(marker="distill-ref")
    await user.should_see(marker="btn-replicate")


async def test_replicate_without_key_warns(user, d_env):
    await user.open("/distill")
    user.find(marker="btn-replicate").click()
    await user.should_see("还没填密钥")


async def test_replicate_with_key_starts(user, d_env, monkeypatch):
    ss.set_api_key("demo", "sk-DISTILLKEY")
    captured = {}

    def fake_rep(style, cluster_ref="cluster_001"):
        captured["style"] = style
        captured["ref"] = cluster_ref
        return True
    monkeypatch.setattr(app_module.RUNNER, "run_replicate", fake_rep)

    await user.open("/distill")
    user.find(marker="btn-replicate").click()
    await user.should_see("开始复刻")
    assert captured.get("style") == "测试风格"
    assert captured.get("ref") == "cluster_001"
