#!/usr/bin/env python3
r"""llm_transport._strip_markdown_fence + _stream_once_* post-process 测试。

背景：真 A/B w5g1636kq 暴露 Gemini 经 elysia 中转输出 JSON 时偶尔用 markdown 围栏
```json ... ``` 包裹·下游 json.loads 直接吃会炸·而 Claude Code Agent 输出裸 JSON。
本测试覆盖 transport 层 post-process 剥外壳的语义。

边界：
  - 仅在 response_format_json=True 时启用·writer 正文+CHANGES 混排路径不受影响
  - 保守剥离：strip 后整段以 ``` 起首 + 匹配围栏 → 剥；前面有非空白文本 → 不剥
  - 多围栏 → 取第一个（lazy match）；不合法 → 原样返回
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import llm_transport as lt  # noqa: E402
from gen_model_loader import Profile  # noqa: E402


# ============ _strip_markdown_fence 纯函数 ============
def test_strip_naked_json_unchanged():
    """裸 JSON（无围栏）→ 原样返回。"""
    s = '{"a": 1}'
    assert lt._strip_markdown_fence(s) == s


def test_strip_naked_array_unchanged():
    """裸 JSON 数组同样不动。"""
    s = '[1, 2, 3]'
    assert lt._strip_markdown_fence(s) == s


def test_strip_lower_json_fence():
    r"""```json ... ``` → 内层 JSON。"""
    src = '```json\n{"a": 1}\n```'
    assert lt._strip_markdown_fence(src) == '{"a": 1}'


def test_strip_upper_json_fence():
    r"""```JSON ... ```（大写）→ 内层。"""
    src = '```JSON\n{"a": 1}\n```'
    assert lt._strip_markdown_fence(src) == '{"a": 1}'


def test_strip_no_lang_tag_fence():
    r"""``` ... ```（无语言标签）→ 内层。"""
    src = '```\n{"a": 1}\n```'
    assert lt._strip_markdown_fence(src) == '{"a": 1}'


def test_strip_leading_trailing_whitespace():
    """前后含空白/换行 → strip 后匹配。"""
    src = '\n\n  ```json\n{"a": 1}\n```  \n\n'
    assert lt._strip_markdown_fence(src) == '{"a": 1}'


def test_strip_text_before_fence_unchanged():
    """前面有非空白文本（'这是结果：```json ...'）→ 保守不剥（下游 parse_json_loose 兜底）。"""
    src = '这是结果：\n```json\n{"a": 1}\n```'
    assert lt._strip_markdown_fence(src) == src


def test_strip_unclosed_fence_unchanged():
    """开围栏无闭合 → 原样返回（续写场景·让 generate() 续写补全）。"""
    src = '```json\n{"a": 1,'
    assert lt._strip_markdown_fence(src) == src


def test_strip_unsupported_lang_unchanged():
    """非 json/JSON/空 的语言标签（yaml 等）→ 不剥。"""
    src = '```yaml\nfoo: 1\n```'
    assert lt._strip_markdown_fence(src) == src


def test_strip_multiple_fences_takes_first():
    """多个围栏 → 取第一个（lazy match·任务约定）。"""
    src = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
    out = lt._strip_markdown_fence(src)
    assert out == '{"a": 1}'


def test_strip_empty_text_unchanged():
    """空文本 → 原样返回（generate() 空响应守卫接管）。"""
    assert lt._strip_markdown_fence("") == ""
    assert lt._strip_markdown_fence("   \n\n  ") == "   \n\n  "


def test_strip_result_parses_as_json():
    """剥离后 json.loads 必通（端到端链路验证）。"""
    src = '```json\n{"verdict": "pass", "score": 88}\n```'
    out = lt._strip_markdown_fence(src)
    parsed = json.loads(out)
    assert parsed["verdict"] == "pass" and parsed["score"] == 88


def test_strip_chained_parse_json_loose():
    """剥过的纯 JSON 喂 parse_json_loose 仍 PASS·链路兼容。"""
    src = '```json\n{"k": [1, 2, 3]}\n```'
    out = lt._strip_markdown_fence(src)
    assert lt.parse_json_loose(out) == {"k": [1, 2, 3]}


# ============ _stream_once_* 末端 post-process 集成 ============
def _profile(name="p1", protocol="openai"):
    return Profile(name=name, model=f"model-{name}", base_url="https://x.test/v1",
                   api_key="sk-test", temperature=0.8, max_tokens=None,
                   protocol=protocol, thinking_level=None)


class _FakeChoice:
    def __init__(self, content=None, finish_reason=None):
        self.delta = type("D", (), {"content": content})()
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kw):
        return _FakeStream(self._chunks)


class _FakeOpenAIClient:
    def __init__(self, chunks):
        self.chat = type("C", (), {"completions": _FakeCompletions(chunks)})()


def test_stream_openai_strips_fence_when_json_flag():
    """OpenAI path · response_format_json=True · 围栏被剥。"""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(content='```json\n{"a": ', finish_reason=None)]),
        _FakeChunk(choices=[_FakeChoice(content='1}\n```', finish_reason="stop")]),
    ]
    client = _FakeOpenAIClient(chunks)
    text, finish = lt._stream_once_openai(
        _profile(), "sys", "user", 1000,
        prior_assistant=None, cont_msg=None, temperature=None,
        response_format_json=True, echo=False, client=client)
    assert text == '{"a": 1}' and finish == "stop"


def test_stream_openai_no_strip_when_json_flag_off():
    """OpenAI path · response_format_json=False · 不剥（writer 正文+CHANGES 路径保护）。"""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(content='正文 ```json\n{"x":1}\n```', finish_reason="stop")]),
    ]
    client = _FakeOpenAIClient(chunks)
    text, finish = lt._stream_once_openai(
        _profile(), "sys", "user", 1000,
        prior_assistant=None, cont_msg=None, temperature=None,
        response_format_json=False, echo=False, client=client)
    # 完整保留正文 + 围栏（gen_writer 自家 finditer 抽 CHANGES JSON）
    assert text == '正文 ```json\n{"x":1}\n```'


def test_stream_openai_naked_json_unchanged():
    """OpenAI path · 已是裸 JSON · response_format_json=True · 原样返回。"""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(content='{"verdict": "pass"}', finish_reason="stop")]),
    ]
    client = _FakeOpenAIClient(chunks)
    text, _ = lt._stream_once_openai(
        _profile(), "sys", "user", 1000,
        prior_assistant=None, cont_msg=None, temperature=None,
        response_format_json=True, echo=False, client=client)
    assert text == '{"verdict": "pass"}'


def test_stream_gemini_strips_fence_when_json_flag(monkeypatch):
    """gemini path · response_format_json=True · 围栏被剥（mock httpx SSE）。"""
    import httpx

    payload1 = json.dumps({
        "candidates": [{"content": {"parts": [{"text": '```json\n{"a": '}]},
                        "finishReason": None}]
    })
    payload2 = json.dumps({
        "candidates": [{"content": {"parts": [{"text": '1}\n```'}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}
    })
    sse_lines = [f"data: {payload1}", f"data: {payload2}"]

    class _FakeResp:
        status_code = 200
        headers = {}
        text = ""

        def iter_lines(self):
            return iter(sse_lines)

        def read(self):
            pass

    class _FakeStreamCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    class _FakeHttpxClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)

    text, finish = lt._stream_once_gemini(
        _profile(protocol="gemini"), "sys", "user", 1000,
        prior_assistant=None, cont_msg=None, temperature=None,
        response_format_json=True, echo=False)
    assert text == '{"a": 1}' and finish == "stop"


def test_stream_gemini_no_strip_when_json_flag_off(monkeypatch):
    """gemini path · response_format_json=False · 不剥。"""
    import httpx

    payload = json.dumps({
        "candidates": [{"content": {"parts": [{"text": '正文 ```json\n{"x":1}\n```'}]},
                        "finishReason": "STOP"}]
    })
    sse_lines = [f"data: {payload}"]

    class _FakeResp:
        status_code = 200
        headers = {}
        text = ""

        def iter_lines(self):
            return iter(sse_lines)

        def read(self):
            pass

    class _FakeStreamCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    class _FakeHttpxClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)

    text, finish = lt._stream_once_gemini(
        _profile(protocol="gemini"), "sys", "user", 1000,
        prior_assistant=None, cont_msg=None, temperature=None,
        response_format_json=False, echo=False)
    assert text == '正文 ```json\n{"x":1}\n```'


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                fn = globals()[nm]
                # 简单跳过需要 monkeypatch 的（pytest 直跑时单测靠 pytest fixture）
                if "monkeypatch" in fn.__code__.co_varnames:
                    print(f"  [SKIP] {nm}（需 pytest monkeypatch fixture）")
                    continue
                fn()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                print(f"  [FAIL] {nm}: {e}")
    sys.exit(1 if fails else 0)
