"""gen_writer 回归测试 — call_gen_model 委托 llm_transport 后的 writer 侧确定性行为守护
（重试/续写/截断机制本身的测试见 tests/test_llm_transport.py）。"""
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


def test_research_cache_prefers_manifest_bound_cache():
    """writer 优先消费 manifest.event_cluster_context.research_ref.cache_path，防最新 cache 串题。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "_数据库"
        cache_dir = db / ".research_cache"
        cache_dir.mkdir(parents=True)
        bound = cache_dir / "inspiration_cluster_001_bound.md"
        unrelated = cache_dir / "inspiration_unrelated_latest.md"
        bound.write_text("BOUND_RESEARCH", encoding="utf-8")
        unrelated.write_text("UNRELATED_RESEARCH", encoding="utf-8")
        manifest = {
            "event_cluster_context": {
                "research_ref": {
                    "cache_path": "_数据库/.research_cache/inspiration_cluster_001_bound.md",
                    "anchors_used": ["anchor_A"],
                }
            }
        }

        text = gw._load_research_cache_for_cluster(db, 1, manifest)

        assert "BOUND_RESEARCH" in text
        assert "UNRELATED_RESEARCH" not in text


def test_research_cache_falls_back_for_legacy_projects():
    """旧项目没有 manifest 绑定时，仍按 cluster 专属 cache 优先，然后才最新 inspiration。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        db = root / "_数据库"
        cache_dir = db / ".research_cache"
        cache_dir.mkdir(parents=True)
        cluster_cache = cache_dir / "inspiration_cluster_001_old.md"
        unrelated = cache_dir / "inspiration_unrelated_latest.md"
        cluster_cache.write_text("CLUSTER_CACHE", encoding="utf-8")
        unrelated.write_text("UNRELATED_RESEARCH", encoding="utf-8")

        text = gw._load_research_cache_for_cluster(db, 1, {})

        assert "CLUSTER_CACHE" in text
        assert "UNRELATED_RESEARCH" not in text


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


# ============ 健壮性回归（空响应/限流·委托 llm_transport 后的 wrapper 契约） ============
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


def test_call_gen_model_empty_response_switches_fallback():
    """空响应回归：active profile 返回零 content → 切 fallback，不当成功。"""
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
    """active + fallback 全空 → raise GenModelExhaustedError（不返回空文本报成功）。"""
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


def test_call_gen_model_retries_same_profile_on_ratelimit():
    """限流在同 profile 做有限重试（不一次就降级 fallback），且同 profile 复用同一 client
    （llm_transport.generate 每 profile 建一次 client · 不因重试轮次反复重建）。"""
    from openai import RateLimitError

    p_active = _profile("active")
    p_fb = _profile("fallback")

    # 构造一个先抛两次 RateLimitError、第三次成功的 client（模拟同 profile 内部重试）
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
    """save_output 空 body 守卫 → 拒写空草稿并 raise，不留 cjk=0 草稿。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raised = False
        try:
            gw.save_output(root, 1, "   \n  ", {}, 1, _profile("p"))
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
        draft_path, cjk = gw.save_output(root, 2, "这是一段真正的正文内容。", {}, 5, _profile("p"))
        assert draft_path.exists()
        assert cjk > 0
        changes_path = root / "章节" / "cluster_002_draft" / "cluster_002_changes.json"
        meta = json.loads(changes_path.read_text(encoding="utf-8"))["self_eval"]["ecas_metadata"]
        assert meta["writer_mode"] == "claude_draft_gemini_polish_v29"
        assert meta["chapter_count_decided_by_splitter"] is True


def test_gen_writer_cli_has_no_locked_chapter_or_target_word_args():
    """cluster-first 唯一入口：CLI 不得重新暴露锁章/锁字数兼容参数。"""
    src = Path(gw.__file__).read_text(encoding="utf-8")
    assert "--chapter-start" not in src
    assert "--chapter-end" not in src
    assert "--target-cjk" not in src
    assert "compat_locked" not in src


# ============ cluster-first：writer 不再按目标 CJK 续写 ============
def _client_seq(specs, capture_msgs=None):
    """按 create 调用序号返回不同 chunks 的 mock。
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


def test_call_gen_model_does_not_expand_short_stop_output():
    """cluster-first：finish=stop 的短正文也直接返回，不按目标 CJK 触发续写。"""
    msgs = []
    restore, _ = _patch_openai([_client_seq([
        [("短正文", "stop")],
        [("不应触发的续写", "stop")],
    ], capture_msgs=msgs)])
    try:
        text, _u = gw.call_gen_model(_Loader([_profile("a")]), "sys", "usr")
    finally:
        restore()
    assert text == "短正文"
    assert len(msgs) == 1


def test_removed_legacy_cli_args_exit_2(monkeypatch):
    """旧章级/目标字数参数不注册、不兼容；出现即由 argparse 退出 2。"""
    monkeypatch.setattr(gw, "check_deps", lambda: None)
    for legacy_arg, value in (("--chapter-end", "3"), ("--target-cjk", "16000")):
        monkeypatch.setattr(
            sys,
            "argv",
            ["gen_writer.py", "--project", ".", "--cluster", "1", legacy_arg, value],
        )
        try:
            gw.main()
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError(f"{legacy_arg} must exit 2")


# ============ 短段约束 + 作者节奏基线 ============


def test_enforce_short_paragraphs_splits_long_multi_sentence():
    """超阈值多句非对话段 → 按句末切成短段(只切段不改字)。"""
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
    """情绪标点密的作者(小世界级)→ primacy 块注入情绪标点强调行(防全量 prompt 被写成叙述向)。"""
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
    """推理模型漏出的『### 核心推理概要 … ---』元前言被剥离，正文从故事第一句开始。"""
    reply = ("### 核心推理概要\n\n本故事块对齐小世界风格，采用 in_medias_res。\n\n"
             "配角赋予城府。\n\n---\n\n晚上十一点的江临市下着雨。\n\n陆参跨上电动车。")
    body = gw.clean_polished_body(reply)
    assert body.startswith("晚上十一点"), f"应剥掉元前言, 实际开头: {body[:30]}"
    assert "核心推理概要" not in body


def test_strip_meta_preamble_no_false_strip():
    """正常正文(无元标题)不被误剥。"""
    reply = "晚上十一点的江临市下着雨。\n\n陆参跨上电动车，心里盘算着这单麻辣烫送完能凑满勤奖。"
    body = gw.clean_polished_body(reply)
    assert body.startswith("晚上十一点")


def test_strip_english_meta_commentary_tail():
    """🔴 剥离尾部英文元评论块（模型写完正文后用英文自评漏进 body 会污染草稿）。"""
    reply = ("午夜的钟声连敲三下。\n\n伊莱从干草垫上惊醒，门缝下滑进一封沾血的羊皮纸。\n\n"
             "The narrative chunk is written coherently with deep expansion of the scenes.\n\n"
             "All quantitative self-checks and foreshadowing logs have been submitted.")
    body = gw.clean_polished_body(reply)
    assert body.rstrip().endswith("羊皮纸。"), f"应剥掉英文自评, 实际尾部: {body[-40:]}"
    assert "narrative chunk" not in body
    assert "self-checks" not in body


def test_creative_guard_excludes_flash_tier():
    """🔴 写正文禁 flash-tier 兜底（质量攸关·flash 碎句·静默降质违锁 pro 决策）：
    creative 候选剔除含 'flash' 的 profile，pro 全挂则响亮 GenModelExhaustedError。"""
    import os

    class _P:
        def __init__(self, name, model):
            self.name, self.model = name, model
    cands = [_P("gemini_pro_preview", "gemini-3.1-pro-preview"),
             _P("gemini_pro", "gemini-3.1-pro"),
             _P("gemini_flash", "gemini-3.5-flash")]
    flash_env = "GEN_WRITER_ALLOW_" + "FLASH"
    bak = os.environ.get(flash_env)
    try:
        os.environ[flash_env] = "1"
        out = gw._filter_creative_profiles(cands)
        names = [p.name for p in out]
        assert "gemini_flash" not in names, f"flash 应被排除, 实际 {names}"
        assert "gemini_pro_preview" in names and "gemini_pro" in names
    finally:
        os.environ.pop(flash_env, None)
        if bak is not None:
            os.environ[flash_env] = bak


def test_strip_english_meta_no_false_strip_chinese_with_quote():
    """正文中含英文短引用/人名不被误剥（只剥尾部整段英文·遇中文主导行立停）。"""
    reply = "他低声念出那个名字：Elias。\n\n钟楼的影子落在石板上，伊莱握紧了那封信。"
    body = gw.clean_polished_body(reply)
    assert body.rstrip().endswith("握紧了那封信。")
    assert "Elias" in body  # 正文内的英文人名保留


# ============ 🔴 伏笔明暗线隔离回归（防 gen_writer 直读 事件簇.json 泄露暗线）============
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

    锁死泄露口——gen_writer 直读 事件簇.json 时若把整 cluster dict（含
    foreshadowing_to_plant.hidden_payoff）原样 json.dumps 进 writer prompt，会绕过 build_manifest 过滤。"""
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
            system, user, _seed = gw.build_prompt(root, 1, 1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})  # v29 polish
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


# ============ 写手信息隔离·人物卡隐藏身份脱敏 ============
def _make_cards_with_hidden_identity():
    """造一份带隐藏身份的人物卡.json（false_hero 表面盟友实为叛徒·concealed_until cluster_005）。"""
    return {
        "characters": [
            {
                "name": "陈默",
                "role": "ally",
                "surface_role": "SURFACEROLE_落魄书生表面盟友_SR",
                "true_role": "TRUEROLE_暗中投敌的叛徒_SECRET",
                "concealed_until_cluster": "cluster_005",
                "propp_function": "false_hero",
                "voice_pack": {"tone": "温和", "catchphrase": "无妨"},
            },
            {"name": "林晚", "role": "主角", "voice_pack": {"tone": "冷峻"}},
        ]
    }


def test_sanitize_cards_strips_untriggered_true_role():
    """未到 concealed_until_cluster：剥 true_role·role 用 surface_role 替换（写手把表面身份当真）。"""
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text(json.dumps(_make_cards_with_hidden_identity(), ensure_ascii=False), encoding="utf-8")
        out = gw._sanitize_character_cards_for_writer(p, 1)  # cluster 1 < 5 未到
    assert "TRUEROLE_暗中投敌的叛徒_SECRET" not in out, "未到揭密的 true_role 必须剥离"
    assert "SURFACEROLE_落魄书生表面盟友_SR" in out, "表面身份 surface_role 应保留供写手当真"
    chen = json.loads(out)["characters"][0]
    assert "true_role" not in chen
    assert chen["role"] == "SURFACEROLE_落魄书生表面盟友_SR"
    assert chen["propp_function"] == "ally", "false_hero 应表面替换成 ally"


def test_sanitize_cards_reveals_at_concealed_cluster():
    """到/越过 concealed_until_cluster：解锁 true_role + reveal_directive（该揭晓的不漏付）。"""
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text(json.dumps(_make_cards_with_hidden_identity(), ensure_ascii=False), encoding="utf-8")
        out = gw._sanitize_character_cards_for_writer(p, 5)  # cluster 5 == concealed_until → due
    assert "TRUEROLE_暗中投敌的叛徒_SECRET" in out, "到揭密 cluster 应解锁 true_role"
    chen = json.loads(out)["characters"][0]
    assert chen["role"] == "TRUEROLE_暗中投敌的叛徒_SECRET"
    assert "reveal_directive" in chen and "陈默" in chen["reveal_directive"]


def test_sanitize_cards_default_safe_passthrough():
    """默认安全闸：无 true_role/concealed/hidden 标记的旧卡 → 原样透传零行为变化（不降级）。"""
    cards = {"characters": [{"name": "路人甲", "role": "配角", "voice_pack": {"tone": "市井"}}]}
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
        out = gw._sanitize_character_cards_for_writer(p, 1)
    assert json.loads(out)["characters"][0] == {"name": "路人甲", "role": "配角", "voice_pack": {"tone": "市井"}}


def test_sanitize_cards_missing_file_and_broken_json():
    """① 文件不存在 → [WARN] 占位（默认安全·不抛）；② JSON 破损 → 退回原文（零回归·不更糟）。"""
    out_missing = gw._sanitize_character_cards_for_writer(Path("____no_such_dir____") / "人物卡.json", 1)
    assert out_missing.startswith("[WARN] 文件不存在")
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "人物卡.json"
        p.write_text("{ broken json", encoding="utf-8")
        out_broken = gw._sanitize_character_cards_for_writer(p, 1)
    assert out_broken == "{ broken json"


def test_build_prompt_excludes_untriggered_true_role():
    """端到端：build_prompt 产的 writer prompt 不含未到揭密的 true_role·含 surface_role·到揭密 cluster 才解锁。

    锁死泄露口——gen_writer 直读 人物卡.json 时不得把整份原文（含 true_role）原样 json.dump 进 writer prompt。"""
    _bak = os.environ.get("SNIPPET_SEED_MODE")
    os.environ["SNIPPET_SEED_MODE"] = "off"
    try:
        with _tf.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "_数据库"
            db.mkdir(parents=True)
            (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                          encoding="utf-8")
            (db / "人物卡.json").write_text(
                json.dumps(_make_cards_with_hidden_identity(), ensure_ascii=False), encoding="utf-8")
            # 未到揭密（cluster 1）
            system1, user1, _ = gw.build_prompt(root, 1, 1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
            full1 = system1 + "\n" + user1
            assert "TRUEROLE_暗中投敌的叛徒_SECRET" not in full1, "未到揭密的 true_role 泄露进 prompt"
            assert "SURFACEROLE_落魄书生表面盟友_SR" in full1, "表面 surface_role 应注入"
            assert "写手信息隔离" in system1, "system H6 应含隐藏身份脱敏指示"
            # 到揭密（cluster 5）
            system5, user5, _ = gw.build_prompt(root, 5, 9, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
            full5 = system5 + "\n" + user5
            assert "TRUEROLE_暗中投敌的叛徒_SECRET" in full5, "到揭密 cluster 应解锁 true_role 供兑现"
    finally:
        if _bak is None:
            os.environ.pop("SNIPPET_SEED_MODE", None)
        else:
            os.environ["SNIPPET_SEED_MODE"] = _bak


# ---------- 🔴 场景级 Appraisal Beat 消费（_build_appraisal_section） ----------

def test_build_appraisal_section_renders_directive():
    """manifest.appraisal_directive mode=on → 拼出含 header + directive 的段（消费结构化方向卡）。"""
    manifest = {
        "appraisal_directive": {
            "mode": "on", "gate_level": "advisory", "residue_count": 1, "planned_count": 1,
            "directive": ("🟢 场景级 Appraisal 情绪方向卡（advisory）：\n"
                          "- 【情绪余烬（上块延续·不归零）】陈默｜触发：老周递来红色档案盒\n"
                          "    · 如何外化（写成动作/细节·非情绪词）：指节抵着盒盖却没掀开"),
        }
    }
    sec = gw._build_appraisal_section(Path("nonexistent_manifest.json"), manifest)
    assert sec, "mode=on 应拼出非空段"
    assert "场景级 Appraisal 情绪方向卡" in sec
    assert "appraisal-as-prose" in sec        # header 强调写法
    assert "指节抵着盒盖却没掀开" in sec        # directive 透传


def test_build_appraisal_section_default_safe_no_directive():
    """无 appraisal_directive / mode!=on / 空 directive → ""（默认安全·零回归）。"""
    assert gw._build_appraisal_section(Path("x.json"), {}) == ""
    assert gw._build_appraisal_section(Path("x.json"), {"appraisal_directive": None}) == ""
    assert gw._build_appraisal_section(
        Path("x.json"), {"appraisal_directive": {"mode": "off"}}) == ""
    assert gw._build_appraisal_section(
        Path("x.json"), {"appraisal_directive": {"mode": "on", "directive": ""}}) == ""


# ============ 🔴 S2 正向行为协议回归锁 ============
# 出处 research/open_source_writing_systems_round2.md S2（借鉴 PlotPilot positive_framing_rules）：
# 否定指令在 Self-Attention 中激活被禁 token——生成端通用兜底段用正向行为协议（每类 1 正 1 反），
# 禁用词全量枚举只留检测端（validate_style / semantic_slop 负向词表）。检测负向 / 生成正向分工。
# 北极星⑤：作者风格档仍第一权威——协议只是兜底，作者档 signature 惯用词按作者档写。

# 预算基线：负向枚举段体量（C5 枚举 192 + H3 注 57 + primacy 禁用词行 107 = 356 字）× 1.3 = 协议段预算上限。
_S2_ORIGINAL_SEGMENT_CHARS = 356
_S2_TOKEN_BUDGET = int(_S2_ORIGINAL_SEGMENT_CHARS * 1.3)  # 462


def _build_minimal_prompts():
    """建最小项目跑 build_prompt 取 (system模板部分, user)（确定性 · 种子注入关闭）。

    system = feedback_rules（随开发机 memory 内容浮动）+ 固定模板——断言只锚模板部分，
    从「你是长篇小说的写作引擎」起切，避免 memory 内容干扰词表断言。
    """
    _bak = os.environ.get("SNIPPET_SEED_MODE")
    os.environ["SNIPPET_SEED_MODE"] = "off"
    try:
        with _tf.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "_数据库"
            db.mkdir(parents=True)
            (db / "进度.json").write_text(json.dumps({"cluster_blueprint": {}}, ensure_ascii=False),
                                          encoding="utf-8")
            system, user, _seed = gw.build_prompt(root, 1, 1, polish_view={'idx': 0, 'total': 1, 'scene_text': '井边的场景稿正文。' * 10})
        tpl = system[system.index("你是长篇小说的写作引擎"):]
        return tpl, user
    finally:
        if _bak is None:
            os.environ.pop("SNIPPET_SEED_MODE", None)
        else:
            os.environ["SNIPPET_SEED_MODE"] = _bak


def test_s2_positive_protocol_present():
    """兜底段=正向行为协议：关键表述在场 + 作者档第一权威让位逻辑保留（北极星⑤）。"""
    tpl, user = _build_minimal_prompts()
    assert "工艺词正向行为协议" in tpl, "C5 兜底段应为正向行为协议"
    assert "情绪落身体与动作" in tpl, "情绪类禁令应转成正向行为指令（情绪走身体和动作）"
    assert "强情绪降一档" in tpl, "情绪降级档思想应在协议内（暴怒→说话变慢变清楚一类）"
    assert "神态写整体姿态" in tpl
    assert "作者签名笔法，按作者档写" in tpl, "作者档第一权威让位逻辑必须保留"
    assert "复刻作者优先" in tpl
    # 生成点近邻 primacy 重述同步为正向口径（默认 SKILL_PRIMACY_MODE=active → 落 user prompt）
    assert "套话防线" in user, "primacy 重述应改为正向口径「套话防线」"
    assert "工艺词按 C5 正向协议写" in user


def test_s2_no_banned_word_enumeration():
    """writer prompt 不含大段禁用词枚举（每类最多 1 反例·不全量罗列激活被禁 token）。"""
    tpl, user = _build_minimal_prompts()
    full = tpl + "\n" + user
    assert "默认避免：" not in full, "负向枚举清单引导语不得复活"
    assert "顿时 / 紧锁" not in full, "12 词工艺禁用词连排枚举不得复活"
    assert "- **禁用词**" not in full, "primacy 禁用词负向 bullet 不得复活"
    # 未用作反例的枚举词不得出现（反例每类最多 1 个：心中一凛/嘴角勾起一抹冷笑/缓缓地说/显然）
    for w in ("顿时", "紧锁", "沉吟片刻", "微微挑眉", "淡淡"):
        assert w not in tpl, f"未作反例的原枚举词「{w}」应从 writer 模板移除"


def test_s2_token_discipline_within_budget():
    """🔴 prompt 膨胀纪律：正向协议段合计 ≤ 基线 356 字 × 1.3 = 462（防 writer prompt 膨胀）。"""
    tpl, user = _build_minimal_prompts()
    c5_seg = "## C5." + tpl.split("## C5.")[1].split("\n# ")[0].rstrip()
    h3_note = next(l for l in tpl.splitlines() if l.startswith("（注：工艺/签名词类"))
    prim_line = next(l for l in user.splitlines() if "套话防线" in l)
    total = len(c5_seg) + len(h3_note) + len(prim_line)
    assert total <= _S2_TOKEN_BUDGET, (
        f"正向协议段合计 {total} 字 > 预算 {_S2_TOKEN_BUDGET}（原段 356 × 1.3）——prompt 膨胀纪律")


def test_s2_detection_side_wordlists_untouched():
    """检测负向 / 生成正向分工：validate_style 检测端工艺词负向词表保持原样（不随生成端移除）。"""
    vs = (Path(__file__).resolve().parents[1] / "core" / "scripts" / "validate_style.py").read_text(
        encoding="utf-8")
    assert "顿时" in vs, "检测端 validate_style 的负向词表必须保留（分工：检测负向/生成正向）"
