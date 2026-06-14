"""C5 genre_baseline 方向注入测试（2026-06-14·build_manifest G6·三态 shadow 默认）。

守护（北极星⑤⑥·默认 shadow 消融再放量·advisory 永不 hard_gate·兜底非权威）：
  1. off → None；2. shadow（默认）→ None 但落 .genre_baseline/ch_NNN.json；
  3. active + 作者档含 vs_generic_baseline.dims → payload.relative_style_directions 非空·advisory_only；
  4. 无 vs_generic_baseline → None（零回归）；5. _authority=FALLBACK（writer 知道是兜底）。
零依赖（mock DatabaseScanner·不碰真项目）。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


class _FakeS:
    def __init__(self, style, db, ch=1):
        self._style = style
        self.db = db
        self.ch = ch

    def load(self, name, default=None):
        return self._style if name == "作者风格" else default


_VG_STYLE = {"quantitative": {"vs_generic_baseline": {
    "_authority": "FALLBACK_NOT_AUTHORITATIVE",
    "dims": {"sentence_mean": {"direction": "higher", "magnitude": "明显",
                               "label": "明显句子更长更绵密"}},
}}}


def _run(mode, style, td):
    old = os.environ.get("GENREBASE_INJECT_MODE")
    if mode is None:
        os.environ.pop("GENREBASE_INJECT_MODE", None)
    else:
        os.environ["GENREBASE_INJECT_MODE"] = mode
    try:
        return bm._collect_genre_baseline_diff(_FakeS(style, Path(td)))
    finally:
        if old is not None:
            os.environ["GENREBASE_INJECT_MODE"] = old
        else:
            os.environ.pop("GENREBASE_INJECT_MODE", None)


def test_genrebase_off_returns_none():
    with tempfile.TemporaryDirectory() as td:
        assert _run("off", _VG_STYLE, td) is None


def test_genrebase_shadow_dumps_not_inject():
    """shadow（默认）→ 返回 None 但写 .genre_baseline/ch_NNN.json。"""
    with tempfile.TemporaryDirectory() as td:
        assert _run("shadow", _VG_STYLE, td) is None
        assert (Path(td) / ".genre_baseline" / "ch_001.json").exists()


def test_genrebase_active_injects_directions():
    with tempfile.TemporaryDirectory() as td:
        p = _run("active", _VG_STYLE, td)
        assert p is not None
        assert p["relative_style_directions"] == ["明显句子更长更绵密"]
        assert p["advisory_only"] is True
        assert p["_authority"] == "FALLBACK_NOT_AUTHORITATIVE"


def test_genrebase_no_vsbaseline_returns_none():
    """作者档无 vs_generic_baseline → None（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        assert _run("active", {"quantitative": {}}, td) is None


def test_genrebase_default_is_shadow():
    """不设 env → 默认 shadow（返回 None·北极星⑥消融再放量）。"""
    with tempfile.TemporaryDirectory() as td:
        assert _run(None, _VG_STYLE, td) is None
        assert (Path(td) / ".genre_baseline" / "ch_001.json").exists()
