"""token ledger 测试（2026-06-15·确定性·零依赖·BYOK token 账本）。

守护（北极星·账本 advisory 不崩主轨）：
  1. _record_token_usage env 未设 → 不记（零侵入零回归）；env 设 → append jsonl；
  2. summarize 汇总（调用次数/总 token/按模型）；坏行跳过容错；
  3. estimate_cost 按价格估算（BYOK 自付）；
  4. 空 usage 不记（防垃圾行）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import token_ledger as tl  # noqa: E402


def _set_ledger(path):
    if path is None:
        os.environ.pop("RUOYU_TOKEN_LEDGER", None)
    else:
        os.environ["RUOYU_TOKEN_LEDGER"] = str(path)


def test_record_no_env_no_write():
    """env RUOYU_TOKEN_LEDGER 未设 → 不记（零侵入·不崩）。"""
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    try:
        _set_ledger(None)
        lt._record_token_usage({"promptTokenCount": 100}, "gemini")  # 无异常即过
    finally:
        _set_ledger(bak)


def test_record_appends_jsonl():
    """env 设 → append 一行归一化 token 记录。"""
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    try:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "_数据库" / ".token_ledger.jsonl"
            _set_ledger(ledger)
            lt._record_token_usage(
                {"promptTokenCount": 1000, "candidatesTokenCount": 500,
                 "cachedContentTokenCount": 200, "totalTokenCount": 1500}, "gemini-3.1-pro")
            assert ledger.exists()
            rec = json.loads(ledger.read_text(encoding="utf-8").strip())
            assert rec["prompt_tokens"] == 1000 and rec["output_tokens"] == 500
            assert rec["cached_tokens"] == 200 and rec["model"] == "gemini-3.1-pro"
    finally:
        _set_ledger(bak)


def test_record_empty_usage_no_write():
    """空 usage → 不记（防垃圾行）。"""
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    try:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "l.jsonl"
            _set_ledger(ledger)
            lt._record_token_usage({}, "gemini")
            assert not ledger.exists()
    finally:
        _set_ledger(bak)


def test_summarize():
    """汇总：调用次数 + 总 token + 按模型分组。"""
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "l.jsonl"
        ledger.write_text("\n".join([
            json.dumps({"model": "gemini-3.1-pro", "prompt_tokens": 1000, "output_tokens": 500,
                        "cached_tokens": 0, "total_tokens": 1500}),
            json.dumps({"model": "gemini-3.1-pro", "prompt_tokens": 2000, "output_tokens": 800,
                        "cached_tokens": 100, "total_tokens": 2800}),
        ]), encoding="utf-8")
        s = tl.summarize(ledger)
        assert s["calls"] == 2
        assert s["prompt_tokens"] == 3000 and s["output_tokens"] == 1300
        assert s["by_model"]["gemini-3.1-pro"]["calls"] == 2


def test_summarize_missing_file():
    """账本缺失 → 全 0（不崩）。"""
    s = tl.summarize("/nonexistent/l.jsonl")
    assert s["calls"] == 0 and s["prompt_tokens"] == 0


def test_summarize_bad_line_skipped():
    """坏 JSON 行跳过（容错·部分写入不毁整账本）。"""
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "l.jsonl"
        ledger.write_text(
            json.dumps({"model": "g", "prompt_tokens": 100}) + "\n{坏行不是JSON\n",
            encoding="utf-8")
        assert tl.summarize(ledger)["calls"] == 1


def test_estimate_cost():
    """成本估算（BYOK 价格用户传·1M input×$1 + 0.5M output×$4 = $3）。"""
    s = {"prompt_tokens": 1_000_000, "output_tokens": 500_000}
    c = tl.estimate_cost(s, price_per_1m_input=1.0, price_per_1m_output=4.0)
    assert c["cost_input"] == 1.0 and c["cost_output"] == 2.0 and c["cost_total"] == 3.0


# ═══════════ 对称双协议（2026-06-16·OpenAI path 补记·judge 全走此 path 此前漏记）═══════════

def test_record_openai_protocol_normalize():
    """OpenAI 兼容 usage（prompt_tokens/completion_tokens…）+ protocol='openai'
    → 归一到统一账本字段（judge 全走 OpenAI path·此前漏记）。"""
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    try:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "_数据库" / ".token_ledger.jsonl"
            _set_ledger(ledger)
            lt._record_token_usage(
                {"prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801,
                 "prompt_tokens_details": {"cached_tokens": 89}},
                "gemini-3.1-pro-preview", protocol="openai")
            rec = json.loads(ledger.read_text(encoding="utf-8").strip())
            assert rec["prompt_tokens"] == 1234 and rec["output_tokens"] == 567
            assert rec["cached_tokens"] == 89 and rec["total_tokens"] == 1801
            assert rec["protocol"] == "openai" and rec["model"] == "gemini-3.1-pro-preview"
    finally:
        _set_ledger(bak)


def test_record_gemini_protocol_still_default():
    """向后兼容：protocol 缺省 → 仍走 gemini 字段（promptTokenCount…·存量 2 参数调用不破）。"""
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    try:
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "l.jsonl"
            _set_ledger(ledger)
            lt._record_token_usage(
                {"promptTokenCount": 100, "candidatesTokenCount": 50,
                 "cachedContentTokenCount": 10, "totalTokenCount": 150}, "g")  # 不传 protocol
            rec = json.loads(ledger.read_text(encoding="utf-8").strip())
            assert rec["prompt_tokens"] == 100 and rec["output_tokens"] == 50
            assert rec["protocol"] == "gemini"
    finally:
        _set_ledger(bak)


def test_openai_usage_to_dict_object():
    """CompletionUsage 对象 → dict：model_dump 优先；无 model_dump 时 getattr fallback；None→{}。"""
    import llm_transport as lt

    class _U1:  # pydantic 风格（有 model_dump）
        def model_dump(self):
            return {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    assert lt._openai_usage_to_dict(_U1())["prompt_tokens"] == 10

    class _Det:
        cached_tokens = 5

    class _U2:  # 无 model_dump → getattr fallback（含 prompt_tokens_details.cached_tokens）
        prompt_tokens = 7
        completion_tokens = 8
        total_tokens = 15
        prompt_tokens_details = _Det()
    d = lt._openai_usage_to_dict(_U2())
    assert d["prompt_tokens"] == 7 and d["total_tokens"] == 15
    assert d["prompt_tokens_details"]["cached_tokens"] == 5
    assert lt._openai_usage_to_dict(None) == {}


def test_stream_openai_records_usage_end_to_end():
    """🔴 端到端：OpenAI path stream 末尾 chunk 带 usage → _record_token_usage 落账（judge 场景·
    缺口修复实证）。注入 fake openai module + fake client（零依赖·不真连网）。"""
    import sys
    import types
    import llm_transport as lt
    bak = os.environ.get("RUOYU_TOKEN_LEDGER")
    openai_bak = sys.modules.get("openai")
    try:
        fake = types.ModuleType("openai")  # _stream_once_openai 顶部 from openai import OpenAI/Errors
        fake.OpenAI = lambda **kw: None
        for en in ("APIConnectionError", "APIStatusError", "APITimeoutError", "RateLimitError"):
            setattr(fake, en, type(en, (Exception,), {}))
        sys.modules["openai"] = fake

        class _Usage:
            def model_dump(self):
                return {"prompt_tokens": 4000, "completion_tokens": 1200,
                        "total_tokens": 5200, "prompt_tokens_details": {"cached_tokens": 300}}

        class _Delta:
            content = "judge verdict text"

        class _Choice:
            delta = _Delta()
            finish_reason = "stop"

        class _ChunkText:
            choices = [_Choice()]
            usage = None

        class _ChunkUsage:
            choices = []
            usage = _Usage()

        class _Completions:
            def create(self, **kw):
                assert kw.get("stream_options") == {"include_usage": True}  # 确认请求了 usage
                return iter([_ChunkText(), _ChunkUsage()])

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        prof = types.SimpleNamespace(model="gemini-3.1-pro-preview", api_key="k",
                                     base_url="http://x", temperature=1.0,
                                     thinking_level=None, protocol="openai")
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "_数据库" / ".token_ledger.jsonl"
            _set_ledger(ledger)
            text, finish = lt._stream_once_openai(
                prof, "sys", "user", 1000, prior_assistant=None, cont_msg=None,
                temperature=None, response_format_json=False, echo=False, client=_Client())
            assert text == "judge verdict text" and finish == "stop"
            rec = json.loads(ledger.read_text(encoding="utf-8").strip())
            assert rec["prompt_tokens"] == 4000 and rec["output_tokens"] == 1200
            assert rec["cached_tokens"] == 300 and rec["protocol"] == "openai"
    finally:
        _set_ledger(bak)
        if openai_bak is not None:
            sys.modules["openai"] = openai_bak
        else:
            sys.modules.pop("openai", None)
