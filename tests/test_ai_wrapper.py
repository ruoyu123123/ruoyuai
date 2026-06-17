"""ai_wrapper.py 聚焦回归测试 — 守护「脚本输出 AI 二次复核层」的确定性周边逻辑。

ai_wrapper 是一个 LLM-tagged 脚本：唯一真打 API 的点在 `call_gen_model`
（内部 `from openai import OpenAI` → `client.chat.completions.create`）。
本测试 **绝不真打 API**：
  · build_review_prompt / _summarize_original 是纯函数 → 直接测。
  · review_output 的 fallback / profile_lock 分发 → monkeypatch 掉 call_gen_model 测。
  · call_gen_model 的 profile_lock 校验 / fallback / 响应解析 → 注入 fake loader +
    monkeypatch openai.OpenAI（既喂 fake 响应，也作网络兜底：默认 raise 防漏 mock）。

零依赖：只用标准库，test_* 无参数，断言失败 raise AssertionError。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import ai_wrapper as mod


# ============ 网络兜底：任何测试漏 mock 真出网点立即 raise（防花钱） ============
def _install_network_backstop():
    """把真 openai.OpenAI 换成「调用即 raise」，返回 restore。

    ai_wrapper.call_gen_model 内部 `from openai import OpenAI`，所以替换
    openai 模块上的 OpenAI 属性即可拦住唯一真出网点。
    """
    import openai

    orig = openai.OpenAI

    def _boom(*a, **k):
        raise AssertionError("REAL NETWORK CALL — 测试漏 mock openai.OpenAI！")

    openai.OpenAI = _boom
    return lambda: setattr(openai, "OpenAI", orig)


# ============ fake gen_model_loader 基础设施 ============
class _FakeProfile:
    def __init__(self, name="active", model="m", api_key="sk-test",
                 base_url="http://localhost/v1", thinking_level=None,
                 reasoning_effort=None):
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = 0.3
        self.max_tokens = None
        self.thinking_level = thinking_level
        self.reasoning_effort = reasoning_effort


class _FakeLoader:
    def __init__(self, profile):
        self._profile = profile

    def get_active_profile(self):
        return self._profile


def _patch_loader(profile, raise_on_load=False):
    """让 safe_load_gen_model_loader 返回 (FakeLoaderClass, FakeErr)。

    raise_on_load=True → get_active_profile 抛异常（测 profile load 失败 fallback）。
    返回 restore。
    """
    orig = mod.safe_load_gen_model_loader

    class _FakeErr(Exception):
        pass

    def _factory():
        class _LoaderCls:
            def __new__(cls):
                if raise_on_load:
                    inst = object.__new__(cls)
                    inst.get_active_profile = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
                    return inst
                return _FakeLoader(profile)
        return _LoaderCls, _FakeErr

    mod.safe_load_gen_model_loader = _factory
    return lambda: setattr(mod, "safe_load_gen_model_loader", orig)


def _patch_openai_returning(content, capture=None):
    """把 openai.OpenAI 换成返回固定 content 的 fake client。capture 记录 kwargs。"""
    import openai
    orig = openai.OpenAI

    class _Msg:
        def __init__(s, c):
            s.content = c

    class _Choice:
        def __init__(s, c):
            s.message = _Msg(c)

    class _Resp:
        def __init__(s, c):
            s.choices = [_Choice(c)]

    class _Completions:
        def create(s, **kw):
            if capture is not None:
                capture.append(kw)
            return _Resp(content)

    class _Client:
        def __init__(s, **kw):
            if capture is not None:
                capture.append({"_ctor": kw})
            s.chat = type("C", (), {"completions": _Completions()})()

    openai.OpenAI = _Client
    # gen_model_loader.reasoning_extra_body 是真函数，对 fake profile 返回 {} 不出网，保留真。
    return lambda: setattr(openai, "OpenAI", orig)


def _tmpfile(text, suffix=".json"):
    d = tempfile.mkdtemp()
    p = Path(d) / f"in{suffix}"
    p.write_text(text, encoding="utf-8")
    return p


# ============================================================
# 1. build_review_prompt — JSON 输入：包 JSON 块 + 任务/上下文
# ============================================================
def test_build_review_prompt_json_input_wraps_json_block():
    data = {"verdict": "ok", "score": 5}
    system, user = mod.build_review_prompt(data, task="判断切割是否合理", context="第3章", input_type="json")
    assert "复核员" in system, "system prompt 应包含角色设定"
    assert "判断切割是否合理" in user, "task 必须注入 user prompt"
    assert "第3章" in user, "context 必须注入 user prompt"
    assert "```json" in user, "JSON 输入必须包 ```json 代码块"
    assert '"verdict"' in user, "原始 JSON 字段必须出现在 prompt"


# ============================================================
# 2. build_review_prompt — text 输入：取 content，不包 JSON 块
# ============================================================
def test_build_review_prompt_text_input_no_json_block():
    data = {"_input_type": "text", "_file_path": "/x/draft.txt", "content": "正文内容ABC"}
    system, user = mod.build_review_prompt(data, task="判断风格", input_type="text")
    assert "正文内容ABC" in user, "text 输入必须把 content 放进 prompt"
    assert "```json" not in user, "text 输入不应包 JSON 块"
    assert "/x/draft.txt" in user, "text 输入应标注来源文件路径"


# ============================================================
# 3. build_review_prompt — 无 context 时不注入空上下文段
# ============================================================
def test_build_review_prompt_omits_empty_context():
    data = {"a": 1}
    _, user = mod.build_review_prompt(data, task="t", context="", input_type="json")
    assert "# 上下文" not in user, "context 为空时不应出现上下文标题段"


# ============================================================
# 4. _summarize_original — 标量/列表/字典摘要 + 200 字截断
# ============================================================
def test_summarize_original_handles_types_and_truncates():
    data = {
        "name": "x" * 50,          # 长字符串 → 截 30
        "items": [1, 2, 3],        # 列表 → [len=3]
        "meta": {"k1": 1, "k2": 2},  # 字典 → {keys=[...]}
        "flag": True,
    }
    s = mod._summarize_original(data)
    assert "items=[len=3]" in s, "列表应摘要为 [len=N]"
    assert "meta={keys=" in s, "字典应摘要为 {keys=...}"
    assert "flag=True" in s, "布尔标量应出现"
    assert len(s) <= 200, "摘要必须 ≤ 200 字"
    # 非 dict 输入走 str() 分支
    assert mod._summarize_original("plain") == "plain"


# ============================================================
# 5. review_output — 文件不存在 → error 字典，不调 LLM
# ============================================================
def test_review_output_missing_file_returns_error():
    restore_net = _install_network_backstop()
    try:
        res = mod.review_output(Path(tempfile.mkdtemp()) / "nope.json", task="t")
        assert "error" in res, "缺文件应返回 error"
        assert "file not found" in res["error"]
    finally:
        restore_net()


# ============================================================
# 6. review_output — AI 不可用（call_gen_model 返回 None）→ fallback 透传
# ============================================================
def test_review_output_fallback_when_ai_unavailable():
    restore_net = _install_network_backstop()
    orig_call = mod.call_gen_model
    mod.call_gen_model = lambda *a, **k: None
    try:
        p = _tmpfile(json.dumps({"verdict": "ok"}))
        res = mod.review_output(p, task="检查")
        assert res["ai_review_available"] is False, "None 结果应标记 AI 不可用"
        assert res["agreement"] == "skipped", "fallback 应标 skipped 不阻塞"
        assert res["confidence"] == 0
        assert "透传" in res["reasoning"]
    finally:
        mod.call_gen_model = orig_call
        restore_net()


# ============================================================
# 7. review_output — AI 可用 → agreement/confidence/override 透传
# ============================================================
def test_review_output_merges_ai_result():
    restore_net = _install_network_backstop()
    orig_call = mod.call_gen_model
    mod.call_gen_model = lambda *a, **k: {
        "agreement": "disagree",
        "confidence": 0.9,
        "override_recommendation": {"score": 8},
        "reasoning": "切太严",
    }
    try:
        p = _tmpfile(json.dumps({"score": 3}))
        res = mod.review_output(p, task="复核打分")
        assert res["ai_review_available"] is True
        assert res["agreement"] == "disagree"
        assert res["confidence"] == 0.9
        assert res["override_recommendation"] == {"score": 8}
        assert res["_raw_ai"]["reasoning"] == "切太严"
    finally:
        mod.call_gen_model = orig_call
        restore_net()


# ============================================================
# 8. review_output — 非 JSON 文件 → 当 text 处理（不崩）
# ============================================================
def test_review_output_non_json_file_treated_as_text():
    restore_net = _install_network_backstop()
    captured = {}
    orig_call = mod.call_gen_model

    def _fake(system, user, profile_lock_path=None):
        captured["user"] = user
        return {"agreement": "agree", "confidence": 1.0}

    mod.call_gen_model = _fake
    try:
        p = _tmpfile("这不是JSON只是普通正文", suffix=".txt")
        res = mod.review_output(p, task="审风格")
        assert res["agreement"] == "agree"
        # text 路径：原文内容应进 prompt 且不包 JSON 块
        assert "这不是JSON只是普通正文" in captured["user"]
        assert "```json" not in captured["user"]
    finally:
        mod.call_gen_model = orig_call
        restore_net()


# ============================================================
# 9. review_output — profile_lock 违规 → 返回 error 字典
# ============================================================
def test_review_output_profile_lock_violation_returns_error():
    restore_net = _install_network_backstop()
    orig_call = mod.call_gen_model
    mod.call_gen_model = lambda *a, **k: {
        "_profile_lock_violation": True,
        "lock_name": "locked_x",
        "active_name": "active_y",
    }
    try:
        p = _tmpfile(json.dumps({"a": 1}))
        res = mod.review_output(p, task="t", profile_lock_path="/some/lock.json")
        assert res["error"] == "profile_lock_violation"
        assert res["active_profile"] == "active_y"
        assert res["locked_profile"] == "locked_x"
        assert "switch" in res["fix"]
    finally:
        mod.call_gen_model = orig_call
        restore_net()


# ============================================================
# 10. call_gen_model — loader 不可用 → None（fallback 不出网）
# ============================================================
def test_call_gen_model_none_when_loader_unavailable():
    restore_net = _install_network_backstop()
    restore_loader = _patch_loader(None)
    # 让 safe_load_gen_model_loader 返回 (None, None)
    orig = mod.safe_load_gen_model_loader
    mod.safe_load_gen_model_loader = lambda: (None, None)
    try:
        out = mod.call_gen_model("sys", "usr")
        assert out is None, "loader 不可用应返回 None"
    finally:
        mod.safe_load_gen_model_loader = orig
        restore_loader()
        restore_net()


# ============================================================
# 11. call_gen_model — profile load 抛异常 → None
# ============================================================
def test_call_gen_model_none_when_profile_load_raises():
    restore_net = _install_network_backstop()
    restore_loader = _patch_loader(None, raise_on_load=True)
    try:
        out = mod.call_gen_model("sys", "usr")
        assert out is None, "profile 加载异常应 fallback 返回 None"
    finally:
        restore_loader()
        restore_net()


# ============================================================
# 12. call_gen_model — profile_lock 名称不符 → 违规标记（不出网）
# ============================================================
def test_call_gen_model_profile_lock_mismatch():
    restore_net = _install_network_backstop()  # 违规应在调 OpenAI 前拦住
    restore_loader = _patch_loader(_FakeProfile(name="active_y"))
    try:
        lock = _tmpfile(json.dumps({"profile_name": "locked_x"}))
        out = mod.call_gen_model("sys", "usr", profile_lock_path=str(lock))
        assert isinstance(out, dict)
        assert out.get("_profile_lock_violation") is True
        assert out["lock_name"] == "locked_x"
        assert out["active_name"] == "active_y"
    finally:
        restore_loader()
        restore_net()


# ============================================================
# 13. call_gen_model — profile_lock 名称相符 → 放行，正常解析 fake 响应
# ============================================================
def test_call_gen_model_lock_match_then_parses_response():
    restore_loader = _patch_loader(_FakeProfile(name="locked_x"))
    cap = []
    restore_oai = _patch_openai_returning(
        '{"agreement": "agree", "confidence": 0.8, "reasoning": "ok"}', capture=cap)
    try:
        lock = _tmpfile(json.dumps({"profile_name": "locked_x"}))
        out = mod.call_gen_model("sys", "usr", profile_lock_path=str(lock))
        assert out["agreement"] == "agree"
        assert out["confidence"] == 0.8
        # 校验关键调用参数确实组装进 create()
        create_kw = [c for c in cap if "messages" in c][0]
        assert create_kw["messages"][0]["role"] == "system"
        assert create_kw["messages"][0]["content"] == "sys"
        assert create_kw["messages"][1]["content"] == "usr"
        assert create_kw["temperature"] == 0.3
        assert create_kw["max_tokens"] == 8000
    finally:
        restore_oai()
        restore_loader()


# ============================================================
# 14. call_gen_model — reasoning model 前缀 thinking → 提取末尾 JSON 块
# ============================================================
def test_call_gen_model_extracts_json_from_noisy_response():
    restore_loader = _patch_loader(_FakeProfile())
    noisy = 'let me think...\n这是思考过程\n{"agreement": "partial", "confidence": 0.4}'
    restore_oai = _patch_openai_returning(noisy)
    try:
        out = mod.call_gen_model("sys", "usr")
        assert out["agreement"] == "partial", "应从噪声响应中正则抽出 JSON"
        assert out["confidence"] == 0.4
    finally:
        restore_oai()
        restore_loader()


# ============================================================
# 15. call_gen_model — 完全非 JSON 响应 → _parse_failed 兜底
# ============================================================
def test_call_gen_model_parse_failed_fallback():
    restore_loader = _patch_loader(_FakeProfile())
    restore_oai = _patch_openai_returning("完全没有大括号的纯文本回复")
    try:
        out = mod.call_gen_model("sys", "usr")
        assert out["_parse_failed"] is True, "无 JSON 应标 _parse_failed"
        assert "raw_text" in out
    finally:
        restore_oai()
        restore_loader()
