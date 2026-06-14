"""C1 风格余弦护栏测试（2026-06-14·防 hash 静默冒充 mstyle 风格余弦）。

守护（北极星⑤·主题B 判断有效性·绝不假风格信号）：
  1. assert_mstyle_backend：不设 EMBED_BACKEND / 未装包 → raise MstyleBackendError；
  2. .embed_manifest.json 守卫真正落地（write/check roundtrip + 检出 method 变化）——兑现此前 vapor 的 docstring；
  3. 默认后端仍 hash（不设任何 env）→ 零回归。

零依赖（stdlib·monkeypatch import）·不联网不下模型·不碰真 sentence-transformers。
"""
import builtins
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import embedding_store as es  # noqa: E402


def _reset_backend():
    es._BACKEND = None  # 清后端探测缓存（env 变化后须重测）


def test_assert_mstyle_raises_when_not_set():
    """不设 EMBED_BACKEND（默认 hash）→ raise MstyleBackendError。"""
    _reset_backend()
    old = os.environ.pop("EMBED_BACKEND", None)
    try:
        raised = False
        try:
            es.assert_mstyle_backend()
        except es.MstyleBackendError:
            raised = True
        assert raised, "默认 hash 后端应 raise MstyleBackendError"
    finally:
        if old is not None:
            os.environ["EMBED_BACKEND"] = old
        _reset_backend()


def test_assert_mstyle_raises_when_no_package():
    """EMBED_BACKEND=mstyle 但未装 sentence-transformers → raise。"""
    _reset_backend()
    os.environ["EMBED_BACKEND"] = "mstyle"
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "sentence_transformers":
            raise ImportError("mocked: no sentence_transformers")
        return real_import(name, *a, **k)

    builtins.__import__ = fake_import
    try:
        raised = False
        try:
            es.assert_mstyle_backend()
        except es.MstyleBackendError:
            raised = True
        assert raised, "未装 sentence-transformers 应 raise MstyleBackendError"
    finally:
        builtins.__import__ = real_import
        os.environ.pop("EMBED_BACKEND", None)
        _reset_backend()


def test_write_then_check_manifest_roundtrip():
    """write_embed_manifest 后 check_embed_manifest 返回 (True, ...)。"""
    _reset_backend()
    os.environ.pop("EMBED_BACKEND", None)  # 默认 hash
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        es.write_embed_manifest(root)
        ok, reason = es.check_embed_manifest(root)
        assert ok is True, reason
    _reset_backend()


def test_check_manifest_detects_method_change():
    """manifest 记的 method 与当前不符（换后端没 rebuild）→ check 返回 (False, ...)。"""
    _reset_backend()
    os.environ.pop("EMBED_BACKEND", None)  # 当前 hash
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        es._embed_manifest_path(root).write_text(
            json.dumps({"method": "mstyle:StyleDistance/mstyledistance", "dim": 768}),
            encoding="utf-8")
        ok, reason = es.check_embed_manifest(root)
        assert ok is False, reason
        assert "hash" in reason  # 当前后端是 hash
    _reset_backend()


def test_check_manifest_no_file():
    """无 manifest 文件 → (False, 'no_manifest')。"""
    _reset_backend()
    with tempfile.TemporaryDirectory() as td:
        ok, reason = es.check_embed_manifest(Path(td))
        assert ok is False and reason == "no_manifest"


def test_default_backend_still_hash_zero_regression():
    """不设任何 env → _detect_backend()[0]=='hash'（C1 新增函数不改默认·零回归）。"""
    _reset_backend()
    os.environ.pop("EMBED_BACKEND", None)
    assert es._detect_backend()[0] == "hash"
    _reset_backend()
