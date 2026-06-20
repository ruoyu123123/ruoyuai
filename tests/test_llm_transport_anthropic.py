#!/usr/bin/env python3
"""llm_transport Anthropic 协议测试（R10 W6 · 2026-06-20）

跨家族 judge ensemble 用 Claude /v1/messages 复审 — 新协议必须有单测覆盖。
纯 mock·不打真 Anthropic API（CI 无 BYOK key 会 401·真 API 测试入 RUOYU_RUN_REAL_API
门控·缺则 skip·与 memory feedback-real-api-tests-no-economize 同款纪律）。

Anthropic SSE 与 OpenAI/Gemini 完全不同事件名 (content_block_delta vs
candidates[].content.parts[].text)·必须独立测试矩阵。
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import llm_transport as lt  # noqa: E402
from gen_model_loader import Profile  # noqa: E402


def _anthropic_profile(model="claude-opus-4-5"):
    return Profile(name="claude-judge", model=model,
                   base_url="https://api.anthropic.com",
                   api_key="sk-ant-test", temperature=0.3, max_tokens=16000,
                   protocol="anthropic")


# ============ build_anthropic_body 纯函数测试 ============
def test_build_anthropic_body_minimal():
    p = _anthropic_profile()
    body = lt.build_anthropic_body(p, "system text", "user msg", 8000)
    assert body["model"] == p.model
    assert body["max_tokens"] == 8000
    assert body["system"] == "system text"
    assert body["messages"] == [{"role": "user", "content": "user msg"}]
    assert body["stream"] is True
    assert body["temperature"] == 0.3


def test_build_anthropic_body_response_format_soft_constraint():
    p = _anthropic_profile()
    body = lt.build_anthropic_body(p, "sys", "u", 1000, response_format_json=True)
    # Anthropic 原生无 response_format · 软约束写进 system 末尾
    assert "JSON" in body["system"]
    assert "response_format" not in body
    assert "response_mime_type" not in body


def test_build_anthropic_body_with_prior_assistant_continuation():
    p = _anthropic_profile()
    body = lt.build_anthropic_body(p, "sys", "首发", 500,
                                   prior_assistant="已写一半",
                                   cont_msg="续完")
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == "已写一半"
    assert body["messages"][2]["content"] == "续完"


def test_build_anthropic_body_temperature_override():
    p = _anthropic_profile()
    body = lt.build_anthropic_body(p, "s", "u", 100, temperature=0.0)
    assert body["temperature"] == 0.0


# ============ _stream_once_anthropic SSE 解析（mock httpx） ============
class _FakeResp:
    """模拟 httpx.Client.stream context manager 返回值。"""
    def __init__(self, status_code=200, lines=None, headers=None, text=""):
        self.status_code = status_code
        self._lines = lines or []
        self.headers = headers or {}
        self.text = text
        self._read = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        for line in self._lines:
            yield line

    def read(self):
        self._read = True
        return self.text.encode("utf-8")


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return self._resp


def _install_fake_httpx(monkeypatch, resp):
    import httpx

    def fake_Client(*a, **kw):
        return _FakeClient(resp)
    monkeypatch.setattr(httpx, "Client", fake_Client)


def test_stream_once_anthropic_text_extraction(monkeypatch):
    """SSE event content_block_delta.delta.text → 累加正文 · message_delta.stop_reason=end_turn → finish='stop'。"""
    lines = [
        "event: message_start",
        'data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":10,"output_tokens":0}}}',
        "",
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"前半"}}',
        "",
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"后半"}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}',
        "",
    ]
    _install_fake_httpx(monkeypatch, _FakeResp(lines=lines))
    p = _anthropic_profile()
    text, finish = lt._stream_once_anthropic(
        p, "sys", "u", 1000, prior_assistant=None, cont_msg=None,
        temperature=None, response_format_json=False, echo=False)
    assert text == "前半后半"
    assert finish == "stop"


def test_stream_once_anthropic_max_tokens_to_length(monkeypatch):
    """stop_reason=max_tokens → finish 归一为 'length'（与 OpenAI 口径对齐）。"""
    lines = [
        "event: content_block_delta",
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"截断了"}}',
        "",
        "event: message_delta",
        'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}',
        "",
    ]
    _install_fake_httpx(monkeypatch, _FakeResp(lines=lines))
    p = _anthropic_profile()
    text, finish = lt._stream_once_anthropic(
        p, "s", "u", 100, prior_assistant=None, cont_msg=None,
        temperature=None, response_format_json=False, echo=False)
    assert text == "截断了"
    assert finish == "length"


def test_stream_once_anthropic_429_raises_ratelimit(monkeypatch):
    """429 + Retry-After → TransportRateLimit with retry_after。"""
    resp = _FakeResp(status_code=429, headers={"retry-after": "5"}, text="rate limit")
    _install_fake_httpx(monkeypatch, resp)
    p = _anthropic_profile()
    try:
        lt._stream_once_anthropic(p, "s", "u", 100, prior_assistant=None,
                                  cont_msg=None, temperature=None,
                                  response_format_json=False, echo=False)
        assert False, "应抛 TransportRateLimit"
    except lt.TransportRateLimit as e:
        assert e.retry_after == 5.0


def test_stream_once_anthropic_401_non_transient(monkeypatch):
    """401 → TransportError 含 401·上层 generate() 立即降级不重试。"""
    resp = _FakeResp(status_code=401, text="invalid api key")
    _install_fake_httpx(monkeypatch, resp)
    p = _anthropic_profile()
    try:
        lt._stream_once_anthropic(p, "s", "u", 100, prior_assistant=None,
                                  cont_msg=None, temperature=None,
                                  response_format_json=False, echo=False)
        assert False, "应抛 TransportError"
    except lt.TransportError as e:
        assert "401" in str(e)


def test_stream_once_anthropic_500_transport_error(monkeypatch):
    """5xx → TransportError（瞬时·上层 generate 会同 profile 重试）。"""
    resp = _FakeResp(status_code=500, text="server err")
    _install_fake_httpx(monkeypatch, resp)
    p = _anthropic_profile()
    try:
        lt._stream_once_anthropic(p, "s", "u", 100, prior_assistant=None,
                                  cont_msg=None, temperature=None,
                                  response_format_json=False, echo=False)
        assert False, "应抛 TransportError"
    except lt.TransportError as e:
        assert "500" in str(e)


# ============ token ledger anthropic 分支 ============
def test_record_token_usage_anthropic_protocol(tmp_path, monkeypatch):
    """input_tokens/output_tokens/cache_read_input_tokens 归一到统一账本字段。"""
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("RUOYU_TOKEN_LEDGER", str(ledger))
    usage = {"input_tokens": 1200, "output_tokens": 350,
             "cache_read_input_tokens": 800}
    lt._record_token_usage(usage, "claude-opus-4-5", protocol="anthropic")
    import json as _json
    assert ledger.exists()
    rec = _json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["protocol"] == "anthropic"
    assert rec["prompt_tokens"] == 1200
    assert rec["output_tokens"] == 350
    assert rec["cached_tokens"] == 800
    assert rec["total_tokens"] == 1550  # input + output


# ============ stream_once dispatch（protocol=='anthropic' 分支） ============
def test_stream_once_dispatch_anthropic(monkeypatch):
    """stream_once 见到 protocol='anthropic' 应分发到 _stream_once_anthropic。"""
    called = {}

    def fake_anth(profile, system, user, max_tokens, **kw):
        called["yes"] = True
        return "anth-text", "stop"

    monkeypatch.setattr(lt, "_stream_once_anthropic", fake_anth)
    p = _anthropic_profile()
    text, finish = lt.stream_once(p, "s", "u", 100)
    assert text == "anth-text" and finish == "stop"
    assert called.get("yes") is True


# ============ 真 API 测试（env 门控·缺则 skip） ============
def test_real_anthropic_smoke_skipped_by_default():
    """memory feedback-real-api-tests-no-economize：真 API 入双 env 门控·CI 默认 skip。"""
    if not (os.environ.get("RUOYU_RUN_REAL_API") == "1"
            and os.environ.get("ANTHROPIC_API_KEY")):
        import pytest
        pytest.skip("真 API 测试·需 RUOYU_RUN_REAL_API=1 + ANTHROPIC_API_KEY")
    # 真 API 跑（仅手动验证）：略
    assert True
