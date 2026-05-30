"""gen_writer 回归测试 — 守护 2026-05-30 截断检测加强（_stream_once 捕获 finish_reason）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw


class _P:
    """mock profile。"""
    def __init__(self, max_tokens=None, model="m"):
        self.max_tokens = max_tokens
        self.model = model
        self.temperature = 0.8
        self.name = "mock"


def test_resolve_max_tokens_explicit():
    mt, src = gw.resolve_max_tokens(_P(max_tokens=20000))
    assert mt == 20000 and src == "profile_explicit"


def test_resolve_max_tokens_default_fallback():
    """无 explicit + model 不在 cache → 16000 保守默认。"""
    mt, src = gw.resolve_max_tokens(_P(model="__nonexistent_model_xyz__"))
    assert mt == 16000


def _mock_client(chunks_spec, capture=None):
    """chunks_spec: [(content, finish_reason), ...]。"""
    class MockChoice:
        def __init__(s, c, fr):
            s.delta = type("D", (), {"content": c})()
            s.finish_reason = fr
    class MockChunk:
        def __init__(s, c, fr):
            s.choices = [MockChoice(c, fr)]
    class MockStream:
        def __iter__(s):
            return iter([MockChunk(c, fr) for c, fr in chunks_spec])
    class MockCompletions:
        def create(s, **kw):
            if capture is not None:
                capture["messages"] = kw["messages"]
            return MockStream()
    class MockClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": MockCompletions()})()
    return MockClient()


def test_stream_once_captures_finish_reason():
    """核心：原 bug 是从不读 finish_reason → 截断静默。验证 length 被捕获。"""
    text, fr = gw._stream_once(_mock_client([("正文", None), ("尾", "length")]), _P(),
                               "sys", "usr", 1000)
    assert text == "正文尾"
    assert fr == "length"


def test_stream_once_continuation_messages():
    """续写模式：prior_assistant 非空 → messages 含 assistant 回填 + 续写指令。"""
    cap = {}
    gw._stream_once(_mock_client([("", "stop")], capture=cap), _P(),
                    "sys", "usr", 1000, prior_assistant="已写正文")
    msgs = cap["messages"]
    assert len(msgs) == 4
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "已写正文"
    assert "截断" in msgs[3]["content"]
