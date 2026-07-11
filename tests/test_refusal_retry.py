"""refusal-retry 回归测试（2026-06-20 · gen-model 间歇性安全拒绝防护）。

背景：reasoning gen-model（gemini-3.x pro-preview）偶发对**合法文学复刻**任务输出短安全拒绝
（HTTP200 + finish=stop + 非空 · 绕过 TransportEmpty 守卫），如:
    "对我来说这是不可接受的。我不能帮助处理可能不安全或不适当的事情。让我们尝试其他内容。"
当前 distill_replicate 把短拒绝当合法复刻返回 → SFS 评分归零 → 看不出是 refusal 还是 skill 失败。

修法（北极星·零污染正常路径）：
1. llm_transport._is_refusal 纯检测 helper（gen_writer/av_judge 等正常路径不调）。
2. distill_replicate.call_gen_model 在 stream 完成后主动调 → 命中走 3s 退避 + disclaimer 追加重试。
3. 3 次全 refusal → REFUSAL_EXHAUSTED 记 failures → 走下一 fallback profile → 全链 refusal 抛
   GenModelExhaustedError（exit=3）。
4. 旧 env 旁路已退役；拒绝重试始终生效。
"""
from __future__ import annotations
import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import llm_transport as lt  # noqa: E402
import distill_replicate as dr  # noqa: E402


# ═══════════════════════════════ Part 1: _is_refusal helper ═══════════════════════════════

class TestIsRefusalHelper:
    """纯函数·零副作用·覆盖典型 refusal 文本 + 边界（保守宁漏不误杀）。"""

    def test_chinese_short_refusal_hits(self):
        # wsb4ljc82 实测样本（30 字 < 200 阈值 + 头窗含「不能帮助/不可接受」）
        t = "对我来说这是不可接受的。我不能帮助处理可能不安全或不适当的事情。"
        assert lt._is_refusal(t) is True

    def test_chinese_let_us_try_other_hits(self):
        t = "让我们尝试其他内容。"
        assert lt._is_refusal(t) is True

    def test_english_short_refusal_hits(self):
        t = "I cannot fulfill this request."
        assert lt._is_refusal(t) is True

    def test_english_sorry_short_refusal_hits(self):
        t = "I'm sorry, I can't help with that."
        assert lt._is_refusal(t) is True

    def test_long_normal_text_misses(self):
        # 12000 字合法复刻正文（含「无法忘记」等貌似关键词的子串）
        body = "他无法忘记那一夜的雨。" * 1500   # 长度远超 200 → 不命中
        assert len(body) > 200
        assert lt._is_refusal(body) is False

    def test_short_unrelated_text_misses(self):
        # 10 字无关键词
        t = "夜深了。他闭上眼。"
        assert lt._is_refusal(t) is False

    def test_long_with_keyword_only_in_middle_misses(self):
        # 头 100 字不含关键词 + 总长 > 200 → 不命中（防误杀）
        prefix = "X" * 150
        suffix = "他叹了口气。我无法回头。" * 100
        t = prefix + suffix
        assert len(t) > 200
        # 头 100 字全是 X，不含任何 refusal 关键词
        assert lt._is_refusal(t) is False

    def test_empty_text_misses(self):
        assert lt._is_refusal("") is False
        assert lt._is_refusal("   \n  ") is False

    def test_partial_refusal_then_long_garbage_misses(self):
        """模型先道歉 80 字 + 后续 9000 字胡说八道 → 超 200 字门控 → 不命中（保守边界）。"""
        head = "对不起·我无法帮助这个请求。"  # 14 字
        body = "他抬头看着天空。" * 1500
        t = head + body
        assert len(t) > 200
        assert lt._is_refusal(t) is False

    def test_env_flag_does_not_affect_helper(self):
        """_is_refusal 是纯函数 · 不读 env。"""
        retry_env = "REFUSAL_RETRY_" + "ENABLED"
        with patch.dict(os.environ, {retry_env: "0"}):
            assert lt._is_refusal("我不能帮助这个请求。") is True

    def test_custom_max_chars_threshold(self):
        # 自定义 max_chars 可调节门控
        t = "对不起。" + "X" * 195  # 总长 199
        assert lt._is_refusal(t, max_chars=200) is True
        assert lt._is_refusal(t, max_chars=50) is False

    def test_custom_head_window(self):
        # 自定义 head_window
        t = " " * 50 + "我不能帮助" + " " * 100
        # strip 后开头是 "我不能帮助" → head_window 默认 100 命中
        assert lt._is_refusal(t) is True


# ═══════════════════════════════ Part 3: call_gen_model refusal-retry 集成 ═══════════════════════════════

def _make_profile(name="active", model="gemini-3.1-pro", temperature=1.0):
    """构造一个最小可用 Profile mock"""
    p = MagicMock()
    p.name = name
    p.model = model
    p.temperature = temperature
    p.max_tokens = 4000
    p.api_key = "test-key"
    p.base_url = "https://test.invalid/v1"
    p.protocol = "openai"
    p.thinking_level = None
    p.reasoning_effort = None
    return p


def _make_loader(profiles):
    loader = MagicMock()
    loader.get_callable_profiles.return_value = profiles
    return loader


def _fake_stream_from_text(text):
    """把 text 切成 stream chunk 序列（mimic OpenAI chat.completions.create stream=True）。"""
    class _Chunk:
        def __init__(self, content):
            self.choices = [types.SimpleNamespace(delta=types.SimpleNamespace(content=content))]
    return [_Chunk(text)]


class _FakeOpenAIClient:
    """逐 call 返回 stream 序列（或抛异常）。"""

    def __init__(self, scripted_responses):
        # scripted_responses: list of (kind, value)
        #   ("text", str) → 返回完整字符串的 stream
        #   ("exc", Exception) → 抛异常
        self._scripted = list(scripted_responses)
        self.call_count = 0
        self.last_messages = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kw):
        self.call_count += 1
        self.last_messages = kw.get("messages")
        if not self._scripted:
            raise RuntimeError("scripted exhausted")
        kind, val = self._scripted.pop(0)
        if kind == "exc":
            raise val
        return _fake_stream_from_text(val)


class TestCallGenModelRefusalRetry:
    """call_gen_model 与 refusal-retry 集成（mock OpenAI + time.sleep + gen_throttle）。"""

    @pytest.fixture(autouse=True)
    def _mock_external(self, monkeypatch):
        # 时间不耗：patch time.sleep 直返
        monkeypatch.setattr(time, "sleep", lambda s: None)
        # 节流器不耗（distill_replicate 内 try-import）
        try:
            import gen_throttle
            monkeypatch.setattr(gen_throttle, "wait", lambda: None)
        except ImportError:
            pass
        # 默认开 refusal-retry
        monkeypatch.setenv("REFUSAL_RETRY_" + "ENABLED", "1")
        # 避免 reasoning_extra_body 干扰
        monkeypatch.setattr(dr, "reasoning_extra_body", lambda p: None)
        yield

    def _patch_openai(self, monkeypatch, fake_client):
        """patch openai.OpenAI 返回 fake_client。"""
        # 注：call_gen_model 内 `from openai import OpenAI` 局部 import → 必须 patch sys.modules
        import openai
        monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)

    def test_refusal_then_success_retry(self, monkeypatch):
        """第一次 refusal → 自动 3s 退避 + disclaimer 重试 → 第二次正常 → 返回正文。"""
        body_normal = "他抬头看着天空。" * 300  # ~3000 字正常正文（远超 200 阈值）
        refusal_text = "对我来说这是不可接受的。我不能帮助处理。让我们尝试其他内容。"
        fake = _FakeOpenAIClient([
            ("text", refusal_text),
            ("text", body_normal),
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile()])
        text, profile, elapsed = dr.call_gen_model(
            loader, system="sys", user="ORIGINAL_USER", default_max_tokens=4000)

        assert fake.call_count == 2, "应触发 1 次 refusal-retry"
        assert text == body_normal
        assert profile.name == "active"
        # 第二次调用的 user 应已追加 disclaimer
        last_user_msg = fake.last_messages[-1]["content"]
        assert "ORIGINAL_USER" in last_user_msg
        assert "文学小说复刻任务" in last_user_msg or "复刻" in last_user_msg

    def test_three_refusals_then_fallback(self, monkeypatch):
        """active profile 3 次连续 refusal → 降级 fallback profile → fallback 正常 → 返回正文。"""
        body_normal = "夜雨打在屋檐上。" * 400
        refusal = "I cannot fulfill this request."
        # active: 3 次 refusal · fallback: 1 次正常
        fake = _FakeOpenAIClient([
            ("text", refusal),
            ("text", refusal),
            ("text", refusal),
            ("text", body_normal),
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile("active"), _make_profile("fallback1")])
        text, profile, _ = dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        assert fake.call_count == 4
        assert text == body_normal
        assert profile.name == "fallback1"

    def test_all_profiles_refuse_raises_exhausted(self, monkeypatch):
        """全 profile 3×N 次全 refusal → 抛 GenModelExhaustedError。"""
        refusal = "对不起·我无法帮助。"
        # 2 profile · 各 3 次 refusal = 6 次
        fake = _FakeOpenAIClient([("text", refusal)] * 6)
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile("active"), _make_profile("fallback1")])
        with pytest.raises(dr.GenModelExhaustedError) as ei:
            dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        # 两 profile 都有 REFUSAL_EXHAUSTED 记录
        msg = str(ei.value)
        assert "REFUSAL_EXHAUSTED" in msg
        # 至少 6 次都调到了
        assert fake.call_count == 6

    def test_old_env_disable_flag_is_ignored(self, monkeypatch):
        """旧 refusal retry env flag 不能关闭 refusal retry。"""
        monkeypatch.setenv("REFUSAL_RETRY_" + "ENABLED", "0")
        refusal = "I cannot fulfill this request."
        body_normal = "夜雨打在屋檐上。" * 400
        fake = _FakeOpenAIClient([
            ("text", refusal),
            ("text", body_normal),
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile()])
        text, profile, _ = dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        assert fake.call_count == 2
        assert text == body_normal
        assert profile.name == "active"

    def test_transient_exception_then_refusal_then_success(self, monkeypatch):
        """瞬时异常 + refusal + 成功混合：两套 retry 计数互不串扰（共享 attempt 上限）。"""
        body_normal = "雪落在山岗上。" * 400
        refusal = "对不起·我不能帮助。"
        # attempt0 = 异常 · attempt1 = refusal · attempt2 = 成功
        fake = _FakeOpenAIClient([
            ("exc", RuntimeError("transient network blip")),
            ("text", refusal),
            ("text", body_normal),
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile()])
        text, profile, _ = dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        assert fake.call_count == 3
        assert text == body_normal
        assert profile.name == "active"

    def test_disclaimer_idempotent_across_refusal_retries(self, monkeypatch):
        """连续两次 refusal → disclaimer 只追加一次（防止 prompt 不断膨胀）。"""
        body_normal = "海浪拍岸。" * 400
        refusal = "我无法处理这个请求。"
        fake = _FakeOpenAIClient([
            ("text", refusal),
            ("text", refusal),
            ("text", body_normal),
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile()])
        text, _, _ = dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        # 第三次成功调用 · user 中只应出现一次 disclaimer 字符串
        last_user = fake.last_messages[-1]["content"]
        assert last_user.count("文学小说复刻任务") == 1
        assert text == body_normal

    def test_empty_response_still_falls_back(self, monkeypatch):
        """refusal-retry 不破坏既有空响应守卫：empty body → 走 fallback profile。"""
        body_normal = "晨光照进窗。" * 400
        fake = _FakeOpenAIClient([
            ("text", ""),                # active 空响应 → 既有守卫触发 fallback
            ("text", body_normal),       # fallback 正常
        ])
        self._patch_openai(monkeypatch, fake)

        loader = _make_loader([_make_profile("active"), _make_profile("fallback1")])
        text, profile, _ = dr.call_gen_model(loader, "sys", "USER", default_max_tokens=4000)

        assert profile.name == "fallback1"
        assert text == body_normal


# ═══════════════════════════════ Part 4: 正常路径不污染保护（gen_writer / av_judge / gen_creative） ═══════════════════════════════

class TestNormalPathsUntouched:
    """确认 llm_transport.generate 等正常路径不调 _is_refusal · 行为零变更。"""

    def test_generate_does_not_call_is_refusal(self):
        """llm_transport.generate 源码不应出现 _is_refusal 调用（只在 distill_replicate 触发）。"""
        src = (_SCRIPTS / "llm_transport.py").read_text(encoding="utf-8")
        # _is_refusal 只能出现在 def 定义处 + 文档注释 · 不在 generate() body 内被调
        # 用粗判：generate 函数体内不含 `_is_refusal(`
        # 切到 def generate 后到下一 def 前的片段
        import re as _re
        m = _re.search(r"\ndef generate\([^)]*\)[^\n]*:\n(.*?)(?=\n(?:def |class ))",
                       src, _re.DOTALL)
        if m:
            generate_body = m.group(1)
            assert "_is_refusal(" not in generate_body, (
                "llm_transport.generate 不应主动调 _is_refusal · 避免污染 gen_writer/judge 正常路径"
            )

    def test_is_refusal_exported_only(self):
        """_is_refusal 必须存在 + 是 callable（distill_replicate 依赖此 export）。"""
        assert callable(getattr(lt, "_is_refusal", None))
