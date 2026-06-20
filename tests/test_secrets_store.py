#!/usr/bin/env python3
"""secrets_store.py 测试（BYOK）——全内存后端·绝不碰真 Credential Manager·零依赖顶层。"""
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402


def _with_mem(fn):
    """注入内存后端跑 fn，结束还原原后端（防跨测试污染真 keyring）。"""
    orig = keyring.get_keyring()
    keyring.set_keyring(MemKeyring())
    try:
        fn()
    finally:
        keyring.set_keyring(orig)


def test_set_get_roundtrip():
    def body():
        assert ss.set_api_key("p1", "sk-ABCDEFGH12345") is True
        assert ss.get_api_key("p1") == "sk-ABCDEFGH12345"
        assert ss.has_api_key("p1") is True
    _with_mem(body)


def test_empty_set_is_delete():
    def body():
        ss.set_api_key("p1", "sk-XYZ99999")
        assert ss.set_api_key("p1", "   ") is True     # 空串→delete 既有 key（删成功）
        assert ss.get_api_key("p1") is None            # 已清除
        assert ss.has_api_key("p1") is False
        # 对从未设置过的 key 空 set → delete 不存在 → False（无可删）
        assert ss.set_api_key("p_never", "") is False
    _with_mem(body)


def test_get_strips_and_normalizes_empty():
    def body():
        assert ss.get_api_key("never_set") is None
        assert ss.has_api_key("never_set") is False
    _with_mem(body)


def test_delete_nonexistent_returns_false_no_raise():
    def body():
        assert ss.delete_api_key("ghost") is False     # 不抛
        ss.set_api_key("p2", "sk-REAL12345")
        assert ss.delete_api_key("p2") is True
        assert ss.get_api_key("p2") is None
    _with_mem(body)


def test_is_available_with_mem_backend_true():
    def body():
        assert ss.is_available() is True
    _with_mem(body)


def test_trial_token_separate_namespace():
    def body():
        ss.set_api_key("p1", "sk-PROFILE")
        ss.set_trial_token("sk-TRIAL")
        assert ss.get_trial_token() == "sk-TRIAL"
        assert ss.get_api_key("p1") == "sk-PROFILE"     # 互不串
        assert ss.get_api_key(ss._TRIAL_USERNAME) == "sk-TRIAL"
    _with_mem(body)


def test_soft_degrade_when_keyring_none():
    """keyring 不可用（None）→ 全函数静默退化，绝不抛（loader 据此降级 .env）。"""
    orig = ss._keyring
    ss._keyring = None
    try:
        assert ss.get_api_key("p1") is None
        assert ss.set_api_key("p1", "k") is False
        assert ss.delete_api_key("p1") is False
        assert ss.has_api_key("p1") is False
        assert ss.is_available() is False
    finally:
        ss._keyring = orig


def test_set_get_never_prints_key_to_stderr():
    """安全守卫：set+get 全程不把 key 明文打到 stderr（防未来误加 print）。"""
    def body():
        buf = io.StringIO()
        with redirect_stderr(buf):
            ss.set_api_key("p9", "sk-SECRETSENTINEL999")
            ss.get_api_key("p9")
            ss.has_api_key("p9")
        assert "sk-SECRETSENTINEL999" not in buf.getvalue()
    _with_mem(body)


# ============ redact（gemini key-in-URL 脱敏·must_fix#3）============
def test_redact_url_key():
    s = "https://x/v1beta/models/m:streamGenerateContent?alt=sse&key=sk-LIVEKEY123&x=1"
    out = ss.redact(s)
    assert "sk-LIVEKEY123" not in out and "key=***" in out


def test_redact_sk_pattern():
    out = ss.redact("OpenAI error with sk-ABCDEFGH123456789 in message")
    assert "sk-ABCDEFGH123456789" not in out and "sk-***" in out


def test_redact_empty_safe():
    assert ss.redact("") == "" and ss.redact("no keys here") == "no keys here"


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
