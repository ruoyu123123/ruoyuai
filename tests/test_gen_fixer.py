"""gen_fixer 回归测试 — 守护 2026-05-30 健壮性加固 [#3]。

gen_fixer 是 writer 之外第二个 gen-model 入口，且**原地覆写整章正文**（parse_and_apply），
截断/空响应后果比 gen_writer 写草稿更重 = 销毁已发布章节。本测试守护与 gen_writer 等价的
四道防护 + CJK 守恒校验：
  ① OpenAI client 显式 timeout
  ② finish_reason 捕获 + 截断自动续写
  ③ RateLimit/APITimeout 同 profile 指数退避重试
  ④ 空响应守卫（切 fallback / 全空 raise）
  ⑤ parse_and_apply CJK 守恒（整章 ±30% 超限拒绝覆写）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_fixer as gf
import gen_model_loader as gml


# ============ mock 基础设施（对齐 test_gen_writer 风格） ============
class _P:
    """mock profile。"""
    def __init__(self, max_tokens=None, model="m"):
        self.max_tokens = max_tokens
        self.model = model
        self.temperature = 0.8
        self.name = "mock"


def _profile(name, model="m"):
    p = _P(model=model)
    p.name = name
    p.api_key = "sk-test"
    p.base_url = "http://localhost/v1"
    return p


class _Loader:
    """mock loader：get_callable_profiles 返回给定 profile 列表。"""
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles


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


def _client_returning(chunks_spec):
    return _mock_client(chunks_spec)


def _patch_openai(mock_clients):
    """把 gen_fixer 用到的 OpenAI 工厂换成按构造顺序产出 mock client 的桩。

    返回 (restore_fn, captured_kwargs_list)。
    """
    import openai
    captured = []
    seq = list(mock_clients)

    def fake_openai(**kwargs):
        captured.append(kwargs)
        return seq[len(captured) - 1]

    orig = openai.OpenAI
    openai.OpenAI = fake_openai
    return (lambda: setattr(openai, "OpenAI", orig)), captured


# ============ resolve_max_tokens（沿用纯函数守护） ============
def test_resolve_max_tokens_explicit():
    mt, src = gf.resolve_max_tokens(_P(max_tokens=20000))
    assert mt == 20000 and src == "profile_explicit"


def test_resolve_max_tokens_default_fallback():
    mt, src = gf.resolve_max_tokens(_P(model="__nonexistent_model_xyz__"))
    assert mt == 16000


# ============ ② _stream_once finish_reason 捕获 + 续写 ============
def test_stream_once_captures_finish_reason():
    """核心：原 bug 是 call_gen_model 只累加 piece、从不读 finish_reason → 截断静默。"""
    text, fr = gf._stream_once(_mock_client([("修复正文", None), ("尾", "length")]), _P(),
                               "sys", "usr", 1000)
    assert text == "修复正文尾"
    assert fr == "length"


def test_stream_once_continuation_messages():
    """续写模式：prior_assistant 非空 → messages 含 assistant 回填 + 续写指令（提到补全块）。"""
    cap = {}
    gf._stream_once(_mock_client([("", "stop")], capture=cap), _P(),
                    "sys", "usr", 1000, prior_assistant="已写修复正文")
    msgs = cap["messages"]
    assert len(msgs) == 4
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "已写修复正文"
    assert "截断" in msgs[3]["content"]
    assert "===END===" in msgs[3]["content"]  # fixer 语境：必须提醒补全 FILE 块


def test_call_gen_model_auto_continues_on_length():
    """② 截断自动续写：第一段 finish_reason=length → 触发续写，拼接完整正文。"""
    # 第一次返回截断（length），第二次（续写）返回 stop
    class TwoPhaseCompletions:
        def __init__(s):
            s.calls = 0

        def create(s, **kw):
            s.calls += 1
            if s.calls == 1:
                return _mock_client([("前半", "length")]).chat.completions.create(**kw)
            return _mock_client([("后半", "stop")]).chat.completions.create(**kw)

    class TwoPhaseClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": TwoPhaseCompletions()})()

    client = TwoPhaseClient()
    restore, _ = _patch_openai([client])
    try:
        text, used = gf.call_gen_model(_Loader([_profile("active")]), "sys", "usr")
    finally:
        restore()
    assert text == "前半后半"
    assert client.chat.completions.calls == 2  # 续写了 1 轮


# ============ ① OpenAI client 显式 timeout ============
def test_call_gen_model_openai_has_timeout():
    """① OpenAI client 构造必须带显式 timeout（对齐 ai_wrapper / gen_writer 的 180.0）。"""
    restore, captured = _patch_openai([_client_returning([("正文", "stop")])])
    try:
        gf.call_gen_model(_Loader([_profile("active")]), "sys", "usr")
    finally:
        restore()
    assert captured, "应至少构造一次 OpenAI client"
    assert captured[0].get("timeout") == gf.GEN_MODEL_TIMEOUT == 180.0


# ============ ④ 空响应守卫 ============
def test_call_gen_model_empty_response_switches_fallback():
    """④ 空响应不当成功 → 切 fallback（否则会拿空回复覆写整章）。"""
    empty_client = _client_returning([("", "stop")])
    good_client = _client_returning([("真修复正文", "stop")])
    restore, _ = _patch_openai([empty_client, good_client])
    try:
        text, used = gf.call_gen_model(
            _Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    finally:
        restore()
    assert text == "真修复正文"
    assert used.name == "fallback"


def test_call_gen_model_all_empty_raises():
    """④ active + fallback 全空 → raise GenModelExhaustedError（绝不返回空文本去覆写）。"""
    restore, _ = _patch_openai([
        _client_returning([("   ", "stop")]),
        _client_returning([("", "stop")]),
    ])
    raised = False
    try:
        gf.call_gen_model(_Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    except gml.GenModelExhaustedError:
        raised = True
    finally:
        restore()
    assert raised, "全链空响应必须 raise GenModelExhaustedError"


# ============ ③ 限流同 profile 指数退避重试 ============
class _FakeResp:
    status_code = 429
    headers = {}
    request = None


def test_call_gen_model_retries_same_profile_on_ratelimit():
    """③ 限流在同 profile 做有限重试（不一次就降级 fallback）。"""
    from openai import RateLimitError

    class FlakyCompletions:
        def __init__(s):
            s.calls = 0

        def create(s, **kw):
            s.calls += 1
            if s.calls <= 2:
                raise RateLimitError("rate limited", response=_FakeResp(), body=None)

            class Choice:
                def __init__(c):
                    c.delta = type("D", (), {"content": "重试成功修复正文"})()
                    c.finish_reason = "stop"

            class Chunk:
                def __init__(c):
                    c.choices = [Choice()]
            return iter([Chunk()])

    class FlakyClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": FlakyCompletions()})()

    flaky = FlakyClient()
    restore, _ = _patch_openai([flaky, _client_returning([("不该用到", "stop")])])
    import time as _t
    orig_sleep = _t.sleep
    _t.sleep = lambda *a, **k: None
    try:
        text, used = gf.call_gen_model(
            _Loader([_profile("active"), _profile("fallback")]), "sys", "usr")
    finally:
        _t.sleep = orig_sleep
        restore()
    assert text == "重试成功修复正文"
    assert used.name == "active"  # 同 profile 重试成功，没降级
    assert flaky.chat.completions.calls == 3  # 抛2次 + 成功1次


# ============ ⑤ parse_and_apply CJK 守恒校验 ============
def _reply_with_file(rel_path, body):
    return f"===FILE: {rel_path}===\n{body}\n===END===\n```json\n{{}}\n```"


def _make_chapter(root: Path, rel_path: str, body: str):
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_and_apply_accepts_within_tolerance():
    """⑤ 修复后字数在原文 ±30% 内 → 正常覆写。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "原始正文" * 10  # 40 CJK
        _make_chapter(root, rel, original)
        new_body = "修复后正文" * 8  # 40 CJK（同量级）
        reply = _reply_with_file(rel, new_body)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert len(written) == 1
        assert not rejected
        assert (root / rel).read_text(encoding="utf-8").strip() == new_body


def test_parse_and_apply_rejects_truncated_overwrite():
    """⑤ 核心：修复输出被截断（字数暴跌 > 30%）→ 拒绝覆写，保留原文不动。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "已发布的完整正文内容" * 20  # 200 CJK
        _make_chapter(root, rel, original)
        truncated = "只剩半截"  # 4 CJK，远低于 -30%
        reply = _reply_with_file(rel, truncated)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert not written, "截断输出不应覆写"
        assert len(rejected) == 1
        assert rejected[0]["reason"] == "cjk_conservation_violation"
        # 原文必须原封不动
        assert (root / rel).read_text(encoding="utf-8") == original


def test_parse_and_apply_rejects_runaway_expansion():
    """⑤ 修复输出字数暴涨 > 30%（退化/重复输出）→ 同样拒绝覆写。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "原文" * 10  # 20 CJK
        _make_chapter(root, rel, original)
        runaway = "暴涨重复退化输出" * 20  # 160 CJK，远超 +30%
        reply = _reply_with_file(rel, runaway)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original})
        assert not written
        assert len(rejected) == 1
        assert (root / rel).read_text(encoding="utf-8") == original


def test_parse_and_apply_word_count_mode_bypasses_conservation():
    """⑤ word-count 扩写模式（enforce=False）→ 合法大幅增字不被守恒挡下。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        original = "短章" * 5  # 10 CJK
        _make_chapter(root, rel, original)
        expanded = "扩写后的丰满正文内容" * 30  # 300 CJK
        reply = _reply_with_file(rel, expanded)
        written, summary, rejected = gf.parse_and_apply(
            reply, root, before_content_by_path={rel: original},
            enforce_cjk_conservation=False)
        assert len(written) == 1, "word-count 扩写应放行"
        assert not rejected


def test_parse_and_apply_no_before_content_does_not_block():
    """守恒校验不误伤：没有 before_content（拿不到原文）时不阻断覆写（仅缺兜底，不能反而失败）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rel = "章节/第001章/第001章.txt"
        _make_chapter(root, rel, "原文" * 50)
        reply = _reply_with_file(rel, "新")  # 字数差异巨大，但无 before 基线
        written, summary, rejected = gf.parse_and_apply(reply, root)
        assert len(written) == 1
        assert not rejected
