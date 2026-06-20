# -*- coding: utf-8 -*-
"""cross_family_judge_check 专属测试(advisory · shadow · 2026-06-20 R10 W6 真 BYOK 实装)。

R10 W6 改造：stub→真 Claude /v1/messages 调用·测试用 _generate_fn / monkeypatch 注入
模拟 Claude 复审 verdict·守 6 个原守门测试(off/ineligible/non-finale/no-key/no-draft/
default-shadow) + 真化测试(agree/disagree/cache/调用失败 skip/keyring/作者档注入)。"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_family_judge_check as cfj  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
    else:
        os.environ["CROSS_FAMILY_JUDGE_MODE"] = m


def _clear_keys():
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY"):
        os.environ.pop(k, None)


def _fake_gen_factory(verdict_text):
    """构造 _generate_fn mock·返回带 .text 的 GenResult 风格对象。"""
    class _Result:
        def __init__(self, text):
            self.text = text
            self.profile = types.SimpleNamespace(name="__claude_judge__")
            self.finish_reason = "stop"

    def fn(loader_or_profiles, system, user, **kw):
        return _Result(f'```json\n{{"verdict": "{verdict_text}"}}\n```')

    return fn


def _fake_gen_raises(exc):
    def fn(*a, **kw):
        raise exc
    return fn


# ============ 6 守门测试（原契约逐字节保留） ============
def test_off_mode_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("off")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert r["reason"] == "mode=off"
    finally:
        _set_mode(bak)


def test_ineligible_judge_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="kicker", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "eligible" in r["reason"]
    finally:
        _set_mode(bak)


def test_non_finale_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        _set_mode("active")
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=False,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "finale" in r["reason"]
    finally:
        _set_mode(bak)


def test_no_claude_key_skips_silently(monkeypatch):
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_keys = {k: os.environ.get(k) for k in
                ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "RUOYU_CLAUDE_KEY")}
    try:
        _set_mode("active")
        _clear_keys()
        # 强制 keyring 返 None（防开发机真有 Claude key 误污染）
        monkeypatch.setattr(cfj, "_resolve_claude_key", lambda: None)
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x")
        assert r["status"] == "skipped"
        assert "BYOK" in r["reason"]
    finally:
        _set_mode(bak)
        for k, v in bak_keys.items():
            if v is not None:
                os.environ[k] = v


def test_no_draft_skips():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text=None)
        assert r["status"] == "skipped"
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_mode_default_shadow():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    try:
        os.environ.pop("CROSS_FAMILY_JUDGE_MODE", None)
        assert cfj._mode() == "shadow"
    finally:
        _set_mode(bak)


# ============ 真 Claude 调用路径（_generate_fn 注入） ============
def _mk_project_with_author(tmp: Path) -> Path:
    """造一份 mini 作者风格档·让 judge_runner.build_author_profile_block 命中。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "作者风格.json").write_text(
        json.dumps({"author": "test", "voice": "calm"}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


def test_agree_path_with_key():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x",
                          _generate_fn=_fake_gen_factory("pass"))
        assert r["status"] == "agree", r
        assert r["verdict_pair"] == ["pass", "pass"]
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_disagree_path_with_key():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        # gemini=pass · claude=issues → disagree + build_manifest_hint
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x",
                          _generate_fn=_fake_gen_factory("issues"))
        assert r["status"] == "disagree", r
        assert r["build_manifest_hint"]
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


def test_calibration_cache_written():
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        proj = Path(tempfile.mkdtemp())
        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x",
                          project_root=proj,
                          _generate_fn=_fake_gen_factory("pass"))
        assert r["status"] == "agree"
        cache = proj / "_数据库" / ".judge" / "cross_family_calibration.json"
        assert cache.exists()
        calib = json.loads(cache.read_text(encoding="utf-8"))
        assert calib["audit"]["agree"] >= 1
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


# ============ 新增：BYOK keyring 解析路径 ============
def test_keyring_path_resolves(monkeypatch):
    """keyring 三级优先：keyring 命中 → 跳过 env 检查。"""
    _clear_keys()
    # mock secrets_store.get_claude_key 返 'kr-key'
    import secrets_store
    monkeypatch.setattr(secrets_store, "get_claude_key", lambda: "kr-key",
                        raising=False)
    assert cfj._resolve_claude_key() == "kr-key"
    assert cfj._has_claude_key() is True


def test_env_fallback_when_no_keyring(monkeypatch):
    """keyring 无 key → 落 env ANTHROPIC_API_KEY。"""
    _clear_keys()
    import secrets_store
    monkeypatch.setattr(secrets_store, "get_claude_key", lambda: None,
                        raising=False)
    os.environ["ANTHROPIC_API_KEY"] = "env-key"
    try:
        assert cfj._resolve_claude_key() == "env-key"
    finally:
        _clear_keys()


# ============ 新增：Claude 调用失败 → 静默 skip ============
def test_claude_call_failure_returns_skipped(monkeypatch):
    """TransportExhausted/异常 → _call_claude_judge 返 None → maybe_run 标 skipped。
    永不阻断主链（北极星② / ⑤）。"""
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        import llm_transport as lt

        def boom(*a, **kw):
            raise lt.TransportExhausted([("__claude_judge__", "anthropic 500")])

        r = cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="x",
                          _generate_fn=boom)
        assert r["status"] == "skipped"
        assert "失败" in r["reason"] or "降级" in r["reason"]
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


# ============ 新增：作者风格档注入 system prompt ============
def test_author_profile_injected_into_system():
    """带 project_root（含作者风格档）→ system prompt 含作者档头·硬契约 1。"""
    bak = os.environ.get("CROSS_FAMILY_JUDGE_MODE")
    bak_key = os.environ.get("ANTHROPIC_API_KEY")
    captured = {}

    class _Result:
        text = '```json\n{"verdict": "pass"}\n```'
        profile = types.SimpleNamespace(name="__claude_judge__")
        finish_reason = "stop"

    def capture(_profiles, system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return _Result()

    try:
        _set_mode("active")
        os.environ["ANTHROPIC_API_KEY"] = "sk-fake"
        with tempfile.TemporaryDirectory() as td:
            proj = _mk_project_with_author(Path(td))
            cfj.maybe_run(judge_name="audit", is_finale_subcluster=True,
                          gemini_verdict="pass", draft_text="测试草稿正文",
                          project_root=proj, _generate_fn=capture)
        assert "作者风格档" in captured["system"], captured["system"][:300]
        assert "测试草稿正文" in captured["user"]
    finally:
        _set_mode(bak)
        if bak_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = bak_key


# ============ Profile 构造 ============
def test_build_claude_profile_uses_env_model_override():
    """env CLAUDE_JUDGE_MODEL 覆盖默认 opus。"""
    bak = os.environ.get("CLAUDE_JUDGE_MODEL")
    try:
        os.environ["CLAUDE_JUDGE_MODEL"] = "claude-haiku-4-5"
        p = cfj._build_claude_profile("sk-fake")
        assert p.model == "claude-haiku-4-5"
        assert p.protocol == "anthropic"
        assert p.api_key == "sk-fake"
    finally:
        if bak is None:
            os.environ.pop("CLAUDE_JUDGE_MODEL", None)
        else:
            os.environ["CLAUDE_JUDGE_MODEL"] = bak


def test_build_claude_profile_default_model():
    bak = os.environ.get("CLAUDE_JUDGE_MODEL")
    try:
        os.environ.pop("CLAUDE_JUDGE_MODEL", None)
        p = cfj._build_claude_profile("sk-fake")
        assert "claude" in p.model.lower()
        assert p.protocol == "anthropic"
    finally:
        if bak is not None:
            os.environ["CLAUDE_JUDGE_MODEL"] = bak
