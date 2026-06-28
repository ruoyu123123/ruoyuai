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


# ============ 短段约束 + 作者节奏基线（2026-06-05 句法熔合已删·只留这些）============


def test_enforce_short_paragraphs_splits_long_multi_sentence():
    """[2026-06-04 治本] 超阈值多句非对话段 → 按句末切成短段(只切段不改字)。"""
    long_para = "他推开门走进了那个昏暗的房间打量四周。墙上挂着一幅落满灰尘的旧画像。地上散落着许多被人撕碎的纸片。"
    out = gw.enforce_short_paragraphs(long_para, author_para_mean=20)  # 阈值=max(26,45)=45
    assert out.count("\n\n") >= 2, "多句长段应按句末切成多个短段"
    assert out.replace("\n\n", "").replace("\n", "") == long_para, "只切段·一字不改"


def test_enforce_short_paragraphs_protects_dialogue():
    """对话段(弯引号开头)不切，哪怕很长。"""
    dlg = "“你怎么会知道这扇门走不通，难道你提前来踩过点，还是说你有什么特殊的本事能一眼看穿这一切吗？”"
    out = gw.enforce_short_paragraphs(dlg, author_para_mean=20)
    assert out == dlg, "对话段不切"


def test_enforce_short_paragraphs_keeps_single_long_sentence():
    """单句长段(无第二个句末) → 不切(绝不碰逗号·防切坏语法·小世界长句允许)。"""
    single = "他推开那扇吱呀作响的旧木门缓缓走进了昏暗潮湿的房间仔细打量起落满灰尘的四壁和散落了一地的碎纸片。"
    out = gw.enforce_short_paragraphs(single, author_para_mean=20)
    assert out == single, "单句长段不切(不碰逗号)"


def test_read_author_rhythm_missing_returns_none():
    """无作者风格.json → (None,None,None) 防御，不抛。"""
    from pathlib import Path as _P2
    s, p, r = gw._read_author_rhythm(_P2("D:/__nonexistent_proj_xyz_123__"))
    assert (s, p, r) == (None, None, None)


def test_enforce_short_paragraphs_protects_system_panel():
    """系统面板【】开头的段不切。"""
    panel = "【系统提示：你触发了厚颜无耻判定，对方好感度+1，当前余额9999金币，请勿向NPC透露真相否则惩罚外卖全吐。】"
    out = gw.enforce_short_paragraphs(panel, author_para_mean=20)
    assert out == panel, "系统面板段不切"


def test_primacy_emotive_punct_for_high_density_author():
    """[2026-06-05] 情绪标点密的作者(小世界级)→ primacy 块注入情绪标点强调行(治 flash 全量 prompt 写成叙述向)。"""
    block = gw._build_hard_constraint_primacy_block({"excl": 4.9, "ques": 5.5, "ellipsis": 4.3})
    assert "情绪标点" in block and "感叹≈4.9" in block


def test_primacy_no_emotive_punct_for_measured_author():
    """情绪标点疏的作者/缺基线 → 不注入(避免误伤严肃/measured 作者)。"""
    assert "情绪标点" not in gw._build_hard_constraint_primacy_block(None)
    assert "情绪标点" not in gw._build_hard_constraint_primacy_block({"excl": 0.5, "ques": 1.0, "ellipsis": 1.0})


def test_primacy_includes_anti_pattern_hints():
    """primacy 块注入 3 套路提示（倒装/强度副词/对话标签·与 loop 检测探针
    prose_rhythm 探针4/5 + semantic_slop B+9 呼应·事前避免=事后检测的事前闭环）。"""
    block = gw._build_hard_constraint_primacy_block()
    assert "倒装" in block             # 段首句式骨架多样（倒装模具·prose_rhythm 探针4 呼应）
    assert "强度副词克制" in block      # 强度副词通胀（prose_rhythm 探针5 呼应）
    assert "对话标签疏化" in block      # 对话标签密度（semantic_slop B+9 呼应）


def test_strip_meta_preamble_reasoning_leak():
    """[2026-06-05] 推理模型漏出的『### 核心推理概要 … ---』元前言被剥离，正文从故事第一句开始。"""
    reply = ("### 核心推理概要\n\n本故事块对齐小世界风格，采用 in_medias_res。\n\n"
             "配角赋予城府。\n\n---\n\n晚上十一点的江临市下着雨。\n\n陆参跨上电动车。")
    body, _ = gw.split_text_and_changes(reply)
    assert body.startswith("晚上十一点"), f"应剥掉元前言, 实际开头: {body[:30]}"
    assert "核心推理概要" not in body


def test_strip_meta_preamble_no_false_strip():
    """正常正文(无元标题)不被误剥。"""
    reply = "晚上十一点的江临市下着雨。\n\n陆参跨上电动车，心里盘算着这单麻辣烫送完能凑满勤奖。"
    body, _ = gw.split_text_and_changes(reply)
    assert body.startswith("晚上十一点")


def test_strip_english_meta_commentary_tail():
    """🔴 2026-06-28：剥离尾部英文元评论块（pro-preview 写完正文后用英文自评漏进 body）。

    实证：钟楼弃儿 cluster_001 模型在 4707CJK 中文正文后追加英文『The narrative chunk is
    written coherently...All quantitative self-checks...』自评·污染草稿。"""
    reply = ("午夜的钟声连敲三下。\n\n伊莱从干草垫上惊醒，门缝下滑进一封沾血的羊皮纸。\n\n"
             "The narrative chunk is written coherently with deep expansion of the scenes.\n\n"
             "All quantitative self-checks and foreshadowing logs have been submitted.")
    body, _ = gw.split_text_and_changes(reply)
    assert body.rstrip().endswith("羊皮纸。"), f"应剥掉英文自评, 实际尾部: {body[-40:]}"
    assert "narrative chunk" not in body
    assert "self-checks" not in body


def test_creative_guard_excludes_flash_tier():
    """🔴 2026-06-28：写正文禁 flash-tier 兜底（质量攸关·flash 碎句·静默降质违锁 pro 决策）。

    实证：cluster_002 因 pro_preview 瞬时 502 + pro 持久 503 → 静默掉 flash 写正文。改：creative
    候选剔除含 'flash' 的 profile，pro 全挂则响亮 GenModelExhaustedError·GEN_WRITER_ALLOW_FLASH=1 旁路。"""
    import os

    class _P:
        def __init__(self, name, model):
            self.name, self.model = name, model
    cands = [_P("gemini_pro_preview", "gemini-3.1-pro-preview"),
             _P("gemini_pro", "gemini-3.1-pro"),
             _P("gemini_flash", "gemini-3.5-flash")]
    bak = os.environ.pop("GEN_WRITER_ALLOW_FLASH", None)
    try:
        out = gw._filter_creative_profiles(cands)
        names = [p.name for p in out]
        assert "gemini_flash" not in names, f"flash 应被排除, 实际 {names}"
        assert "gemini_pro_preview" in names and "gemini_pro" in names
        # 旁路
        os.environ["GEN_WRITER_ALLOW_FLASH"] = "1"
        out2 = gw._filter_creative_profiles(cands)
        assert "gemini_flash" in [p.name for p in out2], "旁路应恢复 flash"
    finally:
        os.environ.pop("GEN_WRITER_ALLOW_FLASH", None)
        if bak is not None:
            os.environ["GEN_WRITER_ALLOW_FLASH"] = bak


def test_strip_english_meta_no_false_strip_chinese_with_quote():
    """正文中含英文短引用/人名不被误剥（只剥尾部整段英文·遇中文主导行立停）。"""
    reply = "他低声念出那个名字：Elias。\n\n钟楼的影子落在石板上，伊莱握紧了那封信。"
    body, _ = gw.split_text_and_changes(reply)
    assert body.rstrip().endswith("握紧了那封信。")
    assert "Elias" in body  # 正文内的英文人名保留


# ============ 🔴 2026-06-28 伏笔明暗线隔离回归（防 gen_writer 直读 事件簇.json 泄露暗线）============
import os
import tempfile as _tf


def _make_brief_with_foreshadowing():
    """造一个带明暗线伏笔的 cluster brief（埋设侧 + 揭晓侧·到期/未到期各一）。"""
    return {
        "cluster_id": "cluster_001",
        "scope_summary": "开场强冲突场景",
        "scene_storyboard": [{"ch": 1, "goal": "G", "conflict": "C", "turn": "T",
                              "emotional_tone": "紧张", "key_beats": ["B1"]}],
        "foreshadowing_to_plant": [
            {"fs_id": "FS_PLANT", "surface_clue": "SURFACECLUE_桌上一枚生锈铜钥匙_SC",
             "hidden_payoff": "HIDDENPLANT_铜钥匙能开地窖通真相_SECRET",
             "trigger_cluster": "cluster_005", "tier": "major"},
        ],
        "foreshadowing_to_callback": [
            {"fs_id": "FS_DUE", "surface_clue": "门后脚步声",
             "hidden_payoff": "REVEALDUE_脚步声是叛徒偷听_SECRET",
             "trigger_cluster": "cluster_001", "tier": "minor"},
            {"fs_id": "FS_FUTURE", "surface_clue": "远处钟声",
             "hidden_payoff": "FUTUREPAYOFF_钟声是仪式倒计时_SECRET",
             "trigger_cluster": "cluster_003", "tier": "major"},
        ],
    }


def test_sanitize_cluster_brief_strips_plant_hidden_payoff():
    """埋设侧 foreshadowing_to_plant：只留 surface_clue·剥 hidden_payoff（写手当普通细节埋·不剧透）。"""
    brief = _make_brief_with_foreshadowing()
    safe = gw._sanitize_cluster_brief_foreshadowing(brief, 1)
    plant = safe["foreshadowing_to_plant"][0]
    assert "hidden_payoff" not in plant, "埋设侧暗线 hidden_payoff 必须被剥离"
    assert plant["surface_clue"] == "SURFACECLUE_桌上一枚生锈铜钥匙_SC", "明线 surface_clue 应保留"
    assert plant["fs_id"] == "FS_PLANT" and plant["tier"] == "major", "其余非密字段保留"


def test_sanitize_cluster_brief_callback_due_vs_future():
    """揭晓侧：到期(trigger==当前)暴露 hidden_payoff + reveal_directive；未到期剥离 hidden_payoff。"""
    brief = _make_brief_with_foreshadowing()
    safe = gw._sanitize_cluster_brief_foreshadowing(brief, 1)
    due, fut = safe["foreshadowing_to_callback"]
    assert due["hidden_payoff"] == "REVEALDUE_脚步声是叛徒偷听_SECRET", "到期伏笔应暴露暗线"
    assert "reveal_directive" in due and "FS_DUE" in due["reveal_directive"], "到期应注入 reveal_directive"
    assert "hidden_payoff" not in fut, "未到触发的伏笔 hidden_payoff 必须剥离防提前泄露"


def test_sanitize_cluster_brief_no_mutation_and_legacy_string():
    """① 不改原 brief（原 dict 仍供非密字段消费）；② 旧格式纯字符串伏笔容错当 surface_clue（非降级）。"""
    brief = _make_brief_with_foreshadowing()
    gw._sanitize_cluster_brief_foreshadowing(brief, 1)
    assert "hidden_payoff" in brief["foreshadowing_to_plant"][0], "原 brief 不应被 mutate"
    legacy = {"foreshadowing_to_plant": ["一枚生锈的铜钥匙"]}
    out = gw._sanitize_cluster_brief_foreshadowing(legacy, 1)
    assert out["foreshadowing_to_plant"][0] == {"surface_clue": "一枚生锈的铜钥匙"}, "旧格式字符串当 surface_clue"


def test_build_prompt_excludes_untriggered_hidden_payoff():
    """端到端：build_prompt 产的 writer prompt 不含未到触发的 hidden_payoff·含 surface_clue + 到期 reveal。

    根治原泄露口——gen_writer 直读 事件簇.json 把整 cluster dict（含 foreshadowing_to_plant.hidden_payoff）
    原样 json.dumps 进 writer prompt（绕过 build_manifest 过滤）。"""
    _bak = os.environ.get("SNIPPET_SEED_MODE")
    os.environ["SNIPPET_SEED_MODE"] = "off"  # 禁种子注入·测试确定性
    try:
        with _tf.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "_数据库"
            db.mkdir(parents=True)
            (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                          encoding="utf-8")
            (db / "事件簇.json").write_text(
                json.dumps({"clusters": [_make_brief_with_foreshadowing()]}, ensure_ascii=False),
                encoding="utf-8")
            system, user, _seed = gw.build_prompt(root, 1, 1)  # freestyle
            full = system + "\n" + user
            # 埋设侧暗线：绝不出现在 writer prompt
            assert "HIDDENPLANT_铜钥匙能开地窖通真相_SECRET" not in full, "埋设侧 hidden_payoff 泄露进 prompt"
            # 未到触发的揭晓侧暗线：绝不出现
            assert "FUTUREPAYOFF_钟声是仪式倒计时_SECRET" not in full, "未到触发的 hidden_payoff 泄露进 prompt"
            # 明线 surface_clue：应注入（写手要埋）
            assert "SURFACECLUE_桌上一枚生锈铜钥匙_SC" in full, "明线 surface_clue 应注入"
            # 到期揭晓侧暗线：应注入（该兑现的·正常）
            assert "REVEALDUE_脚步声是叛徒偷听_SECRET" in full, "到期 reveal 暗线应注入供兑现"
            # 埋伏笔工艺 prompt 在场
            assert "伏笔明暗线工艺" in system, "system 应含埋伏笔工艺指示"
    finally:
        if _bak is None:
            os.environ.pop("SNIPPET_SEED_MODE", None)
        else:
            os.environ["SNIPPET_SEED_MODE"] = _bak
