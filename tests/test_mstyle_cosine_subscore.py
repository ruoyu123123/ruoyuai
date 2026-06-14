"""C2 mstyle 余弦子分护栏测试（2026-06-14·replication_fidelity·防 hash 冒充风格余弦）。

守护（北极星⑤·绝不假风格信号·永不影响 verdict/exit）：
  1. 不设 EMBED_BACKEND（hash）→ status=invalid（不静默冒充）；
  2. frozen 写作态 → status=skip（不崩写作流水线）；
  3. _mstyle_cosine_subscore 绝不抛异常（main 安全）。
本机连不上 mstyle 模型·正好测 invalid/skip 护栏分支（ok 分支需 mstyle 环境·留 experiment）。
零依赖（stdlib）。
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import replication_fidelity_check as rfc  # noqa: E402


def _reset_embed_backend():
    import embedding_store as es
    es._BACKEND = None


def test_subscore_invalid_when_hash_backend():
    """不设 EMBED_BACKEND（默认 hash）→ status=invalid（绝不 hash 冒充风格余弦）。"""
    old = os.environ.pop("EMBED_BACKEND", None)
    _reset_embed_backend()
    try:
        r = rfc._mstyle_cosine_subscore(Path("."), "测试正文一段")
        assert r["status"] == "invalid", r
        assert ("mstyle" in r["reason"] or "hash" in r["reason"]
                or "EMBED_BACKEND" in r["reason"]), r["reason"]
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        _reset_embed_backend()


def test_subscore_skip_when_frozen():
    """mock frozen_util.is_frozen→True → status=skip（不崩写作流水线）。"""
    import frozen_util
    real = frozen_util.is_frozen
    frozen_util.is_frozen = lambda: True
    try:
        r = rfc._mstyle_cosine_subscore(Path("."), "测试正文一段")
        assert r["status"] == "skip"
        assert "frozen" in r["reason"]
    finally:
        frozen_util.is_frozen = real


def test_subscore_never_raises():
    """任何输入返回 dict 不抛异常（main 安全·永不影响 verdict/exit）。"""
    old = os.environ.pop("EMBED_BACKEND", None)
    _reset_embed_backend()
    try:
        for arg in ("", "正文一段", "字" * 1000):
            r = rfc._mstyle_cosine_subscore(Path("/__nonexistent_proj__"), arg)
            assert isinstance(r, dict) and "status" in r, r
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        _reset_embed_backend()
