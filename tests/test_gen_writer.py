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


# ============ 2026-05-30 健壮性加固回归 ============
import json
import tempfile

import gen_model_loader as gml


class _Loader:
    """mock loader：get_callable_profiles 返回给定 profile 列表。"""
    def __init__(self, profiles):
        self._profiles = profiles

    def get_callable_profiles(self):
        return self._profiles


def _profile(name, model="m"):
    p = _P(model=model)
    p.name = name
    p.api_key = "sk-test"
    p.base_url = "http://localhost/v1"
    return p


def _patch_openai(monkey_clients):
    """把 gen_writer 用到的 OpenAI 工厂换成按 profile 顺序产出 mock client 的桩。

    monkey_clients: dict name -> MockClient（按构造顺序消费）。
    返回 (restore_fn, captured_kwargs_list)。
    """
    import openai
    captured = []
    seq = list(monkey_clients)

    def fake_openai(**kwargs):
        captured.append(kwargs)
        return seq[len(captured) - 1]

    orig = openai.OpenAI
    openai.OpenAI = fake_openai
    return (lambda: setattr(openai, "OpenAI", orig)), captured


def _client_returning(chunks_spec):
    """复用 _mock_client：HTTP 200 流式返回 chunks。"""
    return _mock_client(chunks_spec)


def _client_raising(exc):
    """构造一个 _stream_once 调用即抛 exc 的 mock client。"""
    class Completions:
        def create(s, **kw):
            raise exc
    class Client:
        def __init__(s):
            s.chat = type("C", (), {"completions": Completions()})()
    return Client()


def test_call_gen_model_empty_response_switches_fallback():
    """#1 空响应当成功 bug 回归：active profile 返回零 content → 切 fallback，不当成功。"""
    p_active = _profile("active")
    p_fb = _profile("fallback")
    # active 返回空（HTTP 200 但零 content）；fallback 返回真内容
    empty_client = _client_returning([("", "stop")])
    good_client = _client_returning([("真正文", "stop")])
    restore, captured = _patch_openai([empty_client, good_client])
    try:
        text, used = gw.call_gen_model(_Loader([p_active, p_fb]), "sys", "usr")
    finally:
        restore()
    assert text == "真正文"
    assert used.name == "fallback"  # 空响应没被当成功，切到了 fallback


def test_call_gen_model_all_empty_raises():
    """#1：active + fallback 全空 → raise GenModelExhaustedError（不返回空文本报成功）。"""
    p_active = _profile("active")
    p_fb = _profile("fallback")
    restore, _ = _patch_openai([
        _client_returning([("   ", "stop")]),   # 只有空白
        _client_returning([("", "stop")]),       # 完全空
    ])
    raised = False
    try:
        gw.call_gen_model(_Loader([p_active, p_fb]), "sys", "usr")
    except gml.GenModelExhaustedError:
        raised = True
    finally:
        restore()
    assert raised, "全链空响应必须 raise GenModelExhaustedError"


def test_call_gen_model_openai_has_timeout():
    """#2：OpenAI client 构造必须带显式 timeout（对齐 ai_wrapper 的 180.0）。"""
    p = _profile("active")
    restore, captured = _patch_openai([_client_returning([("正文", "stop")])])
    try:
        gw.call_gen_model(_Loader([p]), "sys", "usr")
    finally:
        restore()
    assert captured, "应至少构造一次 OpenAI client"
    assert captured[0].get("timeout") == gw.GEN_MODEL_TIMEOUT == 180.0


def test_call_gen_model_retries_same_profile_on_ratelimit():
    """#2：限流在同 profile 做有限重试（不一次就降级 fallback）。"""
    from openai import RateLimitError

    p_active = _profile("active")
    p_fb = _profile("fallback")

    # 构造一个先抛两次 RateLimitError、第三次成功的 client（模拟 _stream_once 内部重试）
    class FlakyCompletions:
        def __init__(s):
            s.calls = 0
        def create(s, **kw):
            s.calls += 1
            if s.calls <= 2:
                raise RateLimitError("rate limited", response=_FakeResp(), body=None)
            # 第三次成功
            class Choice:
                def __init__(c):
                    c.delta = type("D", (), {"content": "重试成功正文"})()
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
    # 把 sleep 打成 no-op，避免测试真等待
    import time as _t
    orig_sleep = _t.sleep
    _t.sleep = lambda *a, **k: None
    try:
        text, used = gw.call_gen_model(_Loader([p_active, p_fb]), "sys", "usr")
    finally:
        _t.sleep = orig_sleep
        restore()
    assert text == "重试成功正文"
    assert used.name == "active"  # 同 profile 重试成功，没降级到 fallback
    assert flaky.chat.completions.calls == 3  # 抛2次 + 成功1次


class _FakeResp:
    """RateLimitError 构造需要的最小 response 对象。"""
    status_code = 429
    headers = {}
    request = None


def test_save_output_rejects_empty_body():
    """#1：save_output 空 body 守卫 → 拒写空草稿并 raise，不留 cjk=0 草稿。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raised = False
        try:
            gw.save_output(root, 1, "   \n  ", {}, 1, None, _profile("p"))
        except ValueError as e:
            raised = True
            assert "空草稿" in str(e)
        assert raised, "空 body 必须被拒绝"
        # 确认没写出草稿文件
        draft = root / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"
        assert not draft.exists(), "拒写后不应残留空草稿文件"


def test_save_output_writes_nonempty_body():
    """守卫不误伤：非空 body 正常写出。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft_path, cjk = gw.save_output(root, 2, "这是一段真正的正文内容。", {}, 5, None, _profile("p"))
        assert draft_path.exists()
        assert cjk > 0


# ============ 2026-06-04 expand 续写兜底回归（治 pro 等简洁模型单 cluster 偏短）============
def _client_seq(specs, capture_msgs=None):
    """按 create 调用序号返回不同 chunks 的 mock（测 expand 多轮续写）。
    specs: [[(content,finish),...], ...] 每次 create 消费下一个（末个之后重复末个）。"""
    class MockChoice:
        def __init__(s, c, fr):
            s.delta = type("D", (), {"content": c})()
            s.finish_reason = fr
    class MockChunk:
        def __init__(s, c, fr):
            s.choices = [MockChoice(c, fr)]
    class MockStream:
        def __init__(s, spec):
            s.spec = spec
        def __iter__(s):
            return iter([MockChunk(c, fr) for c, fr in s.spec])
    class Comp:
        def __init__(s):
            s.calls = 0
        def create(s, **kw):
            if capture_msgs is not None:
                capture_msgs.append(kw["messages"])
            spec = specs[min(s.calls, len(specs) - 1)]
            s.calls += 1
            return MockStream(spec)
    class Client:
        def __init__(s):
            s.chat = type("C", (), {"completions": Comp()})()
    return Client()


def test_expand_skipped_when_min_cjk_none():
    """零回归：min_cjk=None（蒸馏复刻/ai_wrapper）→ 不触发 expand，单次生成即返回。"""
    msgs = []
    restore, _ = _patch_openai([_client_seq([[("短正文", "stop")]], capture_msgs=msgs)])
    try:
        text, _u = gw.call_gen_model(_Loader([_profile("a")]), "sys", "usr")  # min_cjk 默认 None
    finally:
        restore()
    assert text == "短正文"
    assert len(msgs) == 1  # 只调一次 create，无 expand 续写


def test_expand_triggers_and_reaches_min():
    """正文<min_cjk 且 finish=stop → expand 续写，累积达标后停。"""
    msgs = []
    spec1 = [("字" * 30 + "\n```json\n{\"factual\": {\"a\": 1}}\n```", "stop")]
    spec2 = [("续" * 40 + "\n```json\n{\"factual\": {\"a\": 1}}\n```", "stop")]
    restore, _ = _patch_openai([_client_seq([spec1, spec2], capture_msgs=msgs)])
    orig = gw.FREESTYLE_EXPAND_MIN_GAIN
    gw.FREESTYLE_EXPAND_MIN_GAIN = 5
    try:
        text, _u = gw.call_gen_model(_Loader([_profile("a")]), "sys", "usr", min_cjk=50)
    finally:
        gw.FREESTYLE_EXPAND_MIN_GAIN = orig
        restore()
    assert len(msgs) >= 2, "正文偏短应触发 expand 续写"
    assert "续" in text, "expand 续写内容应并入正文"
    body, changes = gw.split_text_and_changes(text)
    assert changes.get("factual") is not None, "达标后应保留 CHANGES"


def test_expand_stops_on_low_gain():
    """防注水：单轮续写增量 < FREESTYLE_EXPAND_MIN_GAIN → 立即停，不续满 4 轮。"""
    msgs = []
    spec1 = [("字" * 30 + "\n```json\n{\"factual\": {}}\n```", "stop")]
    spec_tiny = [("少", "stop")]  # 每轮才 1 CJK，远低于 GAIN → 应停
    restore, _ = _patch_openai([_client_seq([spec1, spec_tiny], capture_msgs=msgs)])
    try:
        text, _u = gw.call_gen_model(_Loader([_profile("a")]), "sys", "usr", min_cjk=50000)
    finally:
        restore()
    assert len(msgs) <= 3, "增量过小应防注水停止，不应续满 4 轮"


def test_expand_backfills_missing_changes():
    """expand 各轮被要求先别给 CHANGES → 缺 CHANGES 时追加 changes_only 补全请求。"""
    msgs = []
    spec1 = [("字" * 30, "stop")]            # 首轮无 CHANGES
    spec_expand = [("续" * 60, "stop")]       # expand 达标(min=50)，仍无 CHANGES
    spec_changes = [("```json\n{\"factual\": {\"x\": 1}}\n```", "stop")]  # 补全轮给 CHANGES
    restore, _ = _patch_openai([_client_seq([spec1, spec_expand, spec_changes], capture_msgs=msgs)])
    orig = gw.FREESTYLE_EXPAND_MIN_GAIN
    gw.FREESTYLE_EXPAND_MIN_GAIN = 5
    try:
        text, _u = gw.call_gen_model(_Loader([_profile("a")]), "sys", "usr", min_cjk=50)
    finally:
        gw.FREESTYLE_EXPAND_MIN_GAIN = orig
        restore()
    assert any("CHANGES" in m[-1]["content"] and "只输出" in m[-1]["content"] for m in msgs), \
        "缺 CHANGES 应追加 changes_only 补全请求"
    body, changes = gw.split_text_and_changes(text)
    assert changes.get("factual", {}).get("x") == 1, "补全的 CHANGES 应并入最终输出"


# ============ 2026-06-04 句法熔合 pass 回归（治碎句/流水账·CJK 守恒才采用）============
_SHORT = ("他缓缓地睁开了眼睛。\n\n他低头看了看自己的双手。\n\n他又挑了挑眉毛。\n\n"
          "他慢悠悠地往前走了几步。\n\n他在拐角处停下脚步打量四周。")  # 碎句·句长~10
# 同内容「合并版」：省重复主语 + 逗号连缀 → CJK 守恒（≈原文）、句长大幅升
_LONG = "他缓缓地睁开眼睛，低头看了看自己的双手，又挑了挑眉毛，慢悠悠地往前走了几步，在拐角处停下脚步打量四周。"


def test_avg_sentence_cjk_basic():
    assert gw._avg_sentence_cjk("他睁开眼。") < 5
    assert gw._avg_sentence_cjk(_LONG) > 20  # 复合长句句长高


def test_batch_paragraphs_no_split_midparagraph():
    body = "\n\n".join("段落" + str(i) + "内容" * 20 for i in range(10))
    batches = gw._batch_paragraphs(body, target_cjk=200)
    assert len(batches) >= 2  # 分了多批
    assert "\n\n".join(batches).count("段落") == 10  # 没丢段落


def test_fuse_skipped_when_already_long():
    """句长≥阈值 → 跳过熔合，不调 gen-model。"""
    calls = {"n": 0}
    orig = gw.call_gen_model
    gw.call_gen_model = lambda *a, **k: (calls.update(n=calls["n"] + 1), ("x", _P()))[1]
    try:
        out = gw.maybe_fuse_syntax(_Loader([_profile("a")]), _LONG * 3)
    finally:
        gw.call_gen_model = orig
    assert calls["n"] == 0, "句长够时不应调用熔合"
    assert out == _LONG * 3


def test_fuse_adopts_when_improved_and_conserved():
    """碎句 + 熔合返回长句(CJK 守恒) → 采用熔合结果。"""
    orig = gw.call_gen_model
    gw.call_gen_model = lambda loader, s, u, min_cjk=None: (_LONG, _P())
    try:
        out = gw.maybe_fuse_syntax(_Loader([_profile("a")]), _SHORT)
    finally:
        gw.call_gen_model = orig
    assert gw._avg_sentence_cjk(out) > gw._avg_sentence_cjk(_SHORT), "应采用更长句版本"


def test_fuse_keeps_original_when_no_improvement():
    """熔合返回同样碎(没改善) → 保留原文(不退化)。"""
    orig = gw.call_gen_model
    gw.call_gen_model = lambda loader, s, u, min_cjk=None: (_SHORT, _P())  # 没改善
    try:
        out = gw.maybe_fuse_syntax(_Loader([_profile("a")]), _SHORT)
    finally:
        gw.call_gen_model = orig
    assert out == _SHORT, "无改善应保留原文"


def test_fuse_keeps_original_when_cjk_drifts():
    """熔合偷删大半内容(CJK 漂移到 0.4) → 守恒校验失败，保留原文(防删情节)。"""
    orig = gw.call_gen_model
    # 返回长句但内容只剩一句(CJK 远少于原文) → ratio<0.8 拒绝
    gw.call_gen_model = lambda loader, s, u, min_cjk=None: ("他睁开眼，低头挑眉。", _P())
    try:
        out = gw.maybe_fuse_syntax(_Loader([_profile("a")]), _SHORT)
    finally:
        gw.call_gen_model = orig
    assert out == _SHORT, "CJK 漂移(疑似偷删情节)应拒绝熔合保留原文"
