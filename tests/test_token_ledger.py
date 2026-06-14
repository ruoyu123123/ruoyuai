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
