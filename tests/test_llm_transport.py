#!/usr/bin/env python3
"""llm_transport.py 测试（程序驱动 M1）——纯 mock，不打真 API。

覆盖：异常归一重试 / Retry-After 优先 / fallback 降级 / 截断续写循环 /
空响应守卫 / parse_json_loose 三级抽取 / gemini thinkingConfig 注入 / finish 归一。
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import llm_transport as lt  # noqa: E402
from gen_model_loader import Profile  # noqa: E402


def _profile(name="p1", protocol="openai", thinking_level=None, max_tokens=None):
    return Profile(name=name, model=f"model-{name}", base_url="https://x.test/v1",
                   api_key="sk-test", temperature=0.8, max_tokens=max_tokens,
                   protocol=protocol, thinking_level=thinking_level)


_FAST = lt.RetryPolicy(max_retries=2, base_delay=0.001, max_cont_rounds=3)


# ============ parse_json_loose ============
def test_parse_json_fence_last_wins():
    reply = '草稿```json\n{"a": 1}\n```终稿```json\n{"a": 2}\n```'
    assert lt.parse_json_loose(reply) == {"a": 2}


def test_parse_json_pure():
    assert lt.parse_json_loose('  {"k": "v"}  ') == {"k": "v"}


def test_parse_json_first_last_brace():
    reply = 'thinking 里有 {碎片 然后正文 {"ok": true} 结束'
    # first{...last} 取 "{碎片...true}" 失败后应落 fallback —— 验证不崩
    out = lt.parse_json_loose(reply)
    assert out.get("_parse_failed") or out == {"ok": True}


def test_parse_json_embedded():
    reply = '前置说明文字 {"x": [1, 2]} '
    assert lt.parse_json_loose(reply) == {"x": [1, 2]}


def test_parse_json_failed_fallback():
    out = lt.parse_json_loose("完全不是 JSON")
    assert out["_parse_failed"] is True and "raw_text" in out


def test_parse_json_custom_fallback():
    assert lt.parse_json_loose("nope", fallback={"d": 1}) == {"d": 1}


# ============ RetryPolicy ============
def test_delay_exponential():
    rp = lt.RetryPolicy(base_delay=2.0)
    assert rp.delay_for(1) == 2.0 and rp.delay_for(2) == 4.0 and rp.delay_for(3) == 8.0


def test_delay_retry_after_priority_and_clamp():
    rp = lt.RetryPolicy(base_delay=2.0)
    assert rp.delay_for(1, retry_after=30.0) == 30.0
    assert rp.delay_for(1, retry_after=9999.0) == 120.0  # 钳到 2min


# ============ gemini body（thinkingConfig 注入·关键修复） ============
def test_gemini_body_thinking_config_injected():
    p = _profile(protocol="gemini", thinking_level="LOW")
    body = lt.build_gemini_body(p, "sys", "user", 1000)
    cfg = body["generationConfig"]
    assert cfg["thinkingConfig"] == {"thinkingLevel": "low"}  # 小写档位
    assert cfg["maxOutputTokens"] == 1000
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"


def test_gemini_body_no_thinking_when_absent():
    p = _profile(protocol="gemini", thinking_level=None)
    body = lt.build_gemini_body(p, "s", "u", 500)
    assert "thinkingConfig" not in body["generationConfig"]


def test_gemini_body_json_mime_soft_constraint():
    p = _profile(protocol="gemini")
    body = lt.build_gemini_body(p, "s", "u", 500, response_format_json=True)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" not in body["generationConfig"]  # 绝不上强 schema


def test_gemini_body_continuation_messages():
    p = _profile(protocol="gemini")
    body = lt.build_gemini_body(p, "s", "u", 500, prior_assistant="已写内容",
                                cont_msg="继续")
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "user"]
    assert body["contents"][2]["parts"][0]["text"] == "继续"


def test_gemini_host():
    assert lt.gemini_host("https://api.x.com/v1") == "https://api.x.com"
    assert lt.gemini_host("https://api.x.com/") == "https://api.x.com"


# ============ generate()：fallback / 重试 / 续写 / 空守卫 ============
def _mk_stream(script):
    """script: list of (结果 | 异常)。每次调用弹出一个；异常实例则 raise。
    返回 (fn, calls)——calls 记录每次的 prior_assistant 便于断言续写形态。"""
    calls = []

    def fn(profile, system, user, max_tokens, *, prior_assistant=None, cont_msg=None,
           temperature=None, response_format_json=False, echo=False):
        calls.append({"profile": profile.name, "prior": prior_assistant,
                      "cont_msg": cont_msg, "mt": max_tokens})
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return fn, calls


def test_generate_success_first_try():
    fn, calls = _mk_stream([("正文", "stop")])
    r = lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn)
    assert r.text == "正文" and r.finish_reason == "stop"
    assert r.retries == 0 and r.cont_rounds == 0 and r.fallback_index == 0
    assert len(calls) == 1


def test_generate_ratelimit_retries_same_profile():
    fn, calls = _mk_stream([lt.TransportRateLimit("429"),
                            lt.TransportRateLimit("429"),
                            ("成功", "stop")])
    r = lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn)
    assert r.text == "成功" and r.retries == 2
    assert all(c["profile"] == "p1" for c in calls)  # 同 profile 重试不降级


def test_generate_ratelimit_exhausted_falls_to_next():
    fn, calls = _mk_stream([lt.TransportRateLimit("429")] * 3 + [("备胎成功", "stop")])
    r = lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
    assert r.text == "备胎成功" and r.fallback_index == 1
    assert calls[-1]["profile"] == "p2"


def test_generate_transient_error_retries_same_profile():
    """轮次8 语义升级：瞬时类通用错误（断流/404/5xx）同 profile 重试·不再立即降级
    （主备同主机时立即降级会被亚分钟故障窗 9ms 击穿全链）。"""
    fn, calls = _mk_stream([lt.TransportError("conn broke"), ("ok", "stop")])
    r = lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
    assert r.fallback_index == 0 and r.retries == 1   # 同 profile 重试后成功·未降级
    assert len(calls) == 2


def test_generate_auth_error_degrades_immediately():
    """认证/账号类（401/403/key）不可恢复 → 不重试·立即降级到下一 profile。"""
    fn, calls = _mk_stream([lt.TransportError("Error code: 403 forbidden"),
                            ("ok", "stop")])
    r = lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
    assert r.fallback_index == 1 and len(calls) == 2  # p1 一次即降级 p2


def test_generate_truncation_continuation():
    fn, calls = _mk_stream([("前半", "length"), ("后半", "length"), ("结尾", "stop")])
    r = lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn)
    assert r.text == "前半后半结尾" and r.cont_rounds == 2
    assert calls[1]["prior"] == "前半" and calls[2]["prior"] == "前半后半"


def test_generate_continuation_capped():
    fn, _ = _mk_stream([("a", "length"), ("b", "length"), ("c", "length"),
                        ("d", "length")])
    r = lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn)
    assert r.text == "abcd" and r.cont_rounds == 3 and r.finish_reason == "length"


def test_generate_cont_msg_builder_used():
    seen = []

    def builder(reason, rnd):
        seen.append((reason, rnd))
        return f"续写JSON第{rnd}轮"

    fn, calls = _mk_stream([('{"part', "length"), ('":1}', "stop")])
    r = lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn,
                    cont_msg_builder=builder)
    assert r.text == '{"part":1}'
    assert seen == [("length", 1)] and calls[1]["cont_msg"] == "续写JSON第1轮"


def test_generate_empty_response_degrades():
    fn, _ = _mk_stream([("   ", "stop"), ("有货", "stop")])
    r = lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
    assert r.text == "有货" and r.fallback_index == 1


def test_generate_all_fail_raises_exhausted():
    fn, _ = _mk_stream([lt.TransportError("x"), lt.TransportError("y")])
    try:
        lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
        assert False, "应抛 TransportExhausted"
    except lt.TransportExhausted as e:
        assert len(e.failures) == 2


def test_generate_unexpected_exception_degrades_not_crash():
    fn, _ = _mk_stream([ValueError("SDK 内部炸"), ("ok", "stop")])
    r = lt.generate([_profile("p1"), _profile("p2")], "s", "u",
                    retry=_FAST, _stream_fn=fn)
    assert r.text == "ok" and r.fallback_index == 1


def test_generate_max_tokens_priority():
    # 显式参数 > profile.max_tokens > default
    fn, calls = _mk_stream([("a", "stop")])
    lt.generate([_profile(max_tokens=5000)], "s", "u", max_tokens=1234,
                retry=_FAST, _stream_fn=fn)
    assert calls[0]["mt"] == 1234
    fn2, calls2 = _mk_stream([("a", "stop")])
    lt.generate([_profile(max_tokens=5000)], "s", "u", retry=_FAST, _stream_fn=fn2)
    assert calls2[0]["mt"] == 5000
    fn3, calls3 = _mk_stream([("a", "stop")])
    lt.generate([_profile()], "s", "u", retry=_FAST, _stream_fn=fn3,
                default_max_tokens=777)
    assert calls3[0]["mt"] == 777


def test_generate_no_profiles_raises():
    try:
        lt.generate([], "s", "u", retry=_FAST, _stream_fn=lambda *a, **k: ("x", "stop"))
        assert False
    except lt.TransportExhausted:
        pass


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                print(f"  [FAIL] {nm}: {e}")
    sys.exit(1 if fails else 0)


def _mk_profile():
    class P:
        name = "p1"; model = "m"; protocol = "openai"; api_key = "k"
        base_url = "u"; temperature = 1.0; thinking_level = None; max_tokens = 100
    return P()


def test_transient_404_retried_same_profile():
    """轮次8 实测回归：中转站瞬时 404 nginx 页须同 profile 重试（原零重试立即降级·
    主备同主机时 9ms 内击穿全链）。"""
    calls = {"n": 0}

    def flaky(profile, system, user, mt, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise lt.TransportError("HTTP 404: <!DOCTYPE html> Not Found")
        return "ok text", "stop"
    r = lt.generate([_mk_profile()], "sys", "user", max_tokens=100,
                    retry=lt.RetryPolicy(max_retries=2, base_delay=0.01),
                    label="t", _stream_fn=flaky)
    assert r.text == "ok text" and r.retries == 1


def test_auth_403_not_retried():
    """认证/账号类（403 family disabled 等）不可恢复 → 不浪费退避·立即降级。"""
    calls = {"n": 0}

    def auth_fail(profile, system, user, mt, **kw):
        calls["n"] += 1
        raise lt.TransportError("Error code: 403 model family disabled")
    try:
        lt.generate([_mk_profile()], "sys", "user", max_tokens=100,
                    retry=lt.RetryPolicy(max_retries=2, base_delay=0.01),
                    label="t", _stream_fn=auth_fail)
        assert False, "应抛 TransportExhausted"
    except lt.TransportExhausted:
        assert calls["n"] == 1, f"403 不应重试（调用 {calls['n']} 次）"


# ============ 真实 openai SDK 路径（client 构造·gen_writer/gen_fixer 迁移后唯一覆盖点） ============
def _patch_openai_factory(mock_clients):
    """把 openai.OpenAI 换成按构造顺序产出 mock client 的桩，返回 (restore_fn, captured_kwargs)。"""
    import openai
    captured = []
    seq = list(mock_clients)

    def fake_openai(**kwargs):
        captured.append(kwargs)
        return seq[len(captured) - 1]

    orig = openai.OpenAI
    openai.OpenAI = fake_openai
    return (lambda: setattr(openai, "OpenAI", orig)), captured


def _mock_openai_client(chunks_spec):
    """chunks_spec: [(content, finish_reason), ...] · 单次 create() 消费。"""
    class MockChoice:
        def __init__(s, c, fr):
            s.delta = type("D", (), {"content": c})()
            s.finish_reason = fr

    class MockChunk:
        def __init__(s, c, fr):
            s.choices = [MockChoice(c, fr)]
            s.usage = None

    class MockCompletions:
        def create(s, **kw):
            return iter([MockChunk(c, fr) for c, fr in chunks_spec])

    class MockClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": MockCompletions()})()
    return MockClient()


def test_stream_once_openai_constructs_client_with_explicit_timeout():
    """_stream_once_openai 在 client=None 时必须显式传 timeout（防止无限挂起）。"""
    restore, captured = _patch_openai_factory([_mock_openai_client([("正文", "stop")])])
    try:
        text, finish = lt.stream_once(_profile(), "sys", "usr", 1000)
    finally:
        restore()
    assert text == "正文" and finish == "stop"
    assert captured, "应至少构造一次 OpenAI client"
    assert captured[0].get("timeout") == lt.DEFAULT_TIMEOUT == 180.0


def test_generate_reuses_same_client_across_retry_and_continuation():
    """generate() 对同一 profile 的重试轮 + 续写轮必须复用同一个 OpenAI client
    （不因每次调用重建·省重复握手·对齐迁移前 gen_writer/gen_fixer 语义）。"""
    from openai import RateLimitError

    class _FakeResp:
        status_code = 429
        headers = {}
        request = None

    class FlakyCompletions:
        def __init__(s):
            s.calls = 0

        def create(s, **kw):
            s.calls += 1
            if s.calls == 1:
                raise RateLimitError("rate limited", response=_FakeResp(), body=None)
            if s.calls == 2:
                class Choice:
                    delta = type("D", (), {"content": "前半"})()
                    finish_reason = "length"
                class Chunk:
                    choices = [Choice()]
                    usage = None
                return iter([Chunk()])
            class Choice2:
                delta = type("D", (), {"content": "后半"})()
                finish_reason = "stop"
            class Chunk2:
                choices = [Choice2()]
                usage = None
            return iter([Chunk2()])

    class FlakyClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": FlakyCompletions()})()

    flaky = FlakyClient()
    restore, captured = _patch_openai_factory([flaky])
    orig_sleep = time.sleep
    time.sleep = lambda *a, **k: None
    try:
        r = lt.generate([_profile()], "sys", "usr",
                        retry=lt.RetryPolicy(max_retries=2, base_delay=0.001))
    finally:
        time.sleep = orig_sleep
        restore()
    assert r.text == "前半后半"
    assert len(captured) == 1, f"同 profile 3 次调用应只构造 1 次 client（实际 {len(captured)} 次）"
    assert flaky.chat.completions.calls == 3  # 1次限流 + 1次截断 + 1次成功
