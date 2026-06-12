#!/usr/bin/env python3
"""非密 config 分离测试（step5·分发版无 .env 开发者私钥）。

零依赖顶层·临时 .env/APPDATA·内存 keyring·try/finally 清环境。
覆盖：无密钥硬闸 / dist 回落 + 默认 active / keyring 供 dist key / user_override 切 active /
set_active dev vs dist / dev 零回归（.env 存在 → 非 dist）。
"""
import os
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
import gen_model_loader as gml  # noqa: E402
import gen_model  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402

BUILTIN = (_ROOT / "core" / "config" / "gen_profiles.default.env").resolve()


# ① 无密钥硬闸（must_fix#1：无 sk- + 无任何 API_KEY 行）
def test_builtin_config_has_no_secrets():
    text = BUILTIN.read_text(encoding="utf-8")
    assert "sk-" not in text, "内置 config 含 sk- 密钥"
    assert not re.findall(r"^GEN__.+?__API_KEY", text, re.M), \
        "内置 config 仍有 API_KEY 行（留空行会被 override 刷 '' 打断 environ 注入）"
    assert "GEN_MODEL_ACTIVE=" in text   # 出厂默认 active 在


# ② dist 回落：显式指向内置 config → _dist_mode True（must_fix#2）+ profiles 解析
def test_dist_mode_via_builtin_path():
    saved = {k: v for k, v in os.environ.items() if k.startswith("GEN")}
    saved_kr = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())   # 隔离真机 keyring（用户正式 key 在里面·勿依赖勿动）
    try:
        gml.reset_default_loader()
        ld = gml.GenModelLoader(env_path=BUILTIN)
        assert ld._dist_mode is True            # 解析路径==builtin → dist
        names = {p.name for p in ld.list_profiles()}
        assert "gemini_pro_preview" in names and len(names) >= 5
        # api_key 空（无 keyring）→ 走 keyring（dist 唯一来源）
        assert ld.get_profile("gemini_pro_preview").api_key == ""
        # GEN_MODEL_ACTIVE 经 load_dotenv 进 environ
        assert os.environ.get("GEN_MODEL_ACTIVE") == "gemini_pro_preview"
    finally:
        for k in list(os.environ):
            if k.startswith("GEN"):
                os.environ.pop(k, None)
        os.environ.update(saved)
        keyring.set_keyring(saved_kr)
        gml.reset_default_loader()


# ③ dist + keyring：keyring 供 key → get_active_profile 通
def test_dist_keyring_supplies_key():
    orig_kr = keyring.get_keyring()
    saved = {k: v for k, v in os.environ.items() if k.startswith("GEN")}
    keyring.set_keyring(MemKeyring())
    try:
        ss.set_api_key("gemini_pro_preview", "sk-DISTKEYRINGKEY")
        gml.reset_default_loader()
        ld = gml.GenModelLoader(env_path=BUILTIN)
        p = ld.get_active_profile()      # active=gemini_pro_preview·key 走 keyring
        assert p.name == "gemini_pro_preview"
        assert p.api_key == "sk-DISTKEYRINGKEY"
    finally:
        keyring.set_keyring(orig_kr)
        for k in list(os.environ):
            if k.startswith("GEN"):
                os.environ.pop(k, None)
        os.environ.update(saved)
        gml.reset_default_loader()


# ④ user_override 切 active：写 user_overrides.env → loader 读到新 active
def test_user_override_switches_active():
    saved = {k: v for k, v in os.environ.items()}
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["APPDATA"] = tmp
        try:
            ovr = gml._user_override_path()
            ovr.parent.mkdir(parents=True, exist_ok=True)
            ovr.write_text("GEN_MODEL_ACTIVE=gemini_flash\n", encoding="utf-8")
            gml.reset_default_loader()
            ld = gml.GenModelLoader(env_path=BUILTIN)   # dist → 读 user_override
            assert os.environ.get("GEN_MODEL_ACTIVE") == "gemini_flash"
            assert ld._dist_mode is True
        finally:
            for k in list(os.environ):
                if k.startswith("GEN") or k == "APPDATA":
                    os.environ.pop(k, None)
            os.environ.update(saved)
            gml.reset_default_loader()


# ⑤ set_active dist：写 user_overrides.env（不碰只读内置 config）
def test_set_active_dist_writes_user_override():
    saved = {k: v for k, v in os.environ.items()}
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["APPDATA"] = tmp
        try:
            gml.reset_default_loader()
            ld = gml.GenModelLoader(env_path=BUILTIN)
            assert ld._dist_mode is True
            before = BUILTIN.read_text(encoding="utf-8")
            gen_model.set_active(ld, "gemini_flash")
            # 内置 config 没被改（只读资源）
            assert BUILTIN.read_text(encoding="utf-8") == before
            # 用户态覆盖写了
            ovr = gml._user_override_path()
            assert ovr.exists()
            assert "GEN_MODEL_ACTIVE=gemini_flash" in ovr.read_text(encoding="utf-8")
        finally:
            for k in list(os.environ):
                if k.startswith("GEN") or k == "APPDATA":
                    os.environ.pop(k, None)
            os.environ.update(saved)
            gml.reset_default_loader()


# ⑥ set_active dev：改写临时 .env 的 GEN_MODEL_ACTIVE（不写 user_override）
def test_set_active_dev_rewrites_env():
    f = Path(tempfile.mkstemp(suffix=".env")[1])
    f.write_text("GEN__a__MODEL=m\nGEN_MODEL_ACTIVE=a\nGEN__b__MODEL=m2\n",
                 encoding="utf-8")
    saved = {k: v for k, v in os.environ.items() if k.startswith("GEN")}
    try:
        gml.reset_default_loader()
        ld = gml.GenModelLoader(env_path=f)
        assert ld._dist_mode is False        # 非内置 config → dev
        gen_model.set_active(ld, "b")
        assert "GEN_MODEL_ACTIVE=b" in f.read_text(encoding="utf-8")
    finally:
        for k in list(os.environ):
            if k.startswith("GEN"):
                os.environ.pop(k, None)
        os.environ.update(saved)
        gml.reset_default_loader()
        try:
            f.unlink()
        except OSError:
            pass


# ⑦ dev 零回归：仓库根 .env 存在 → 自动选 .env·非 dist
def test_dev_auto_uses_dotenv_not_dist():
    repo_env = _ROOT / ".env"
    if not repo_env.exists():
        return  # CI 无 .env 时跳过（dev 机有）
    saved = {k: v for k, v in os.environ.items() if k.startswith("GEN")}
    try:
        gml.reset_default_loader()
        ld = gml.GenModelLoader()           # 无参 → 自动定位
        assert ld._dist_mode is False        # .env 存在 → 非分发模式
        assert ld.env_path.resolve() != BUILTIN
    finally:
        for k in list(os.environ):
            if k.startswith("GEN"):
                os.environ.pop(k, None)
        os.environ.update(saved)
        gml.reset_default_loader()


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
