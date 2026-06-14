"""D2 三向度张力机制（Sternberg suspense/curiosity/surprise）确定性聚合测试（2026-06-14）。

守护（北极星⑤⑥·把「为什么紧张」蒸成确定性占比·非弱模型自由写）：
  1. _tension_type_bucket 确定性归一：受控码直通 + 中文自由描述关键词映射 + 未分类兜底；
  2. aggregate_rhythm 产 tension_type_distribution 三向度占比（low_confidence ≥3 门 + 未分类不计入分母）；
  3. 向后兼容：旧 surface（dim51 无 tension_type 字段）不产该字段、tension_trajectory 仍在 → 零回归。

零依赖（stdlib）·surface JSON 沙箱（tmpdir 写 cluster_*_surface.json）·不碰 LLM/agent。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


def test_tension_type_bucket_controlled_codes():
    """受控码（D2 新 schema）直通，大小写不敏感。"""
    assert cap._tension_type_bucket("suspense") == "suspense"
    assert cap._tension_type_bucket("Curiosity") == "curiosity"
    assert cap._tension_type_bucket("SURPRISE") == "surprise"


def test_tension_type_bucket_chinese_keyword_fallback():
    """旧 surface 中文自由描述走关键词映射兜底。"""
    assert cap._tension_type_bucket("读者已知有炸弹在等它爆") == "suspense"
    assert cap._tension_type_bucket("读者想知道到底发生了什么") == "curiosity"
    assert cap._tension_type_bucket("反转打脸读者预期") == "surprise"
    assert cap._tension_type_bucket("留白反转钩") == "surprise"


def test_tension_type_bucket_unclassified():
    """判不准 / none / 空 → 未分类（保守不误分类）。"""
    assert cap._tension_type_bucket("纯过场平淡过渡") == "未分类"
    assert cap._tension_type_bucket("none") == "未分类"
    assert cap._tension_type_bucket("") == "未分类"
    assert cap._tension_type_bucket(None) == "未分类"


def _write_surface(d: Path, key: str, dim51_points: list):
    """写一个 cluster surface JSON（只含 dim51_张力曲线）。"""
    sub = d / "蒸馏进度"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"cluster_{key}_surface.json").write_text(
        json.dumps({"qualitative_dims": {"dim51_张力曲线": dim51_points}}, ensure_ascii=False),
        encoding="utf-8")


def test_aggregate_rhythm_tension_type_distribution():
    """dim51 三向度占比正确归一（≥3 有效点的 cluster 计入）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"pct": 10, "tension": 5, "tension_type": "suspense"},
            {"pct": 50, "tension": 7, "tension_type": "suspense"},
            {"pct": 90, "tension": 9, "tension_type": "curiosity"},
        ])
        out = cap.aggregate_rhythm(d)
        ttd = out.get("tension_type_distribution")
        assert ttd == {"suspense": 0.667, "curiosity": 0.333}, ttd


def test_aggregate_rhythm_unclassified_not_in_denominator():
    """未分类点不计入分母（占比只算可归类的）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"pct": 10, "tension": 5, "tension_type": "suspense"},
            {"pct": 30, "tension": 6, "tension_type": "suspense"},
            {"pct": 50, "tension": 7, "tension_type": "curiosity"},
            {"pct": 70, "tension": 4, "tension_type": "纯过场"},  # 未分类·不计入分母
        ])
        out = cap.aggregate_rhythm(d)
        assert out.get("tension_type_distribution") == {"suspense": 0.667, "curiosity": 0.333}


def test_aggregate_rhythm_low_confidence_cluster_excluded():
    """单 cluster 有效 type 点 <3 → 整 cluster 不计入（low_confidence 门）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"pct": 10, "tension": 5, "tension_type": "suspense"},
            {"pct": 90, "tension": 9, "tension_type": "curiosity"},
        ])
        out = cap.aggregate_rhythm(d)
        assert "tension_type_distribution" not in out


def test_aggregate_rhythm_backward_compat_no_tension_type():
    """旧 surface（dim51 无 tension_type）→ 不产该字段、tension_trajectory 仍在 → 零回归。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"pct": 10, "tension": 5, "valence": "+"},
            {"pct": 50, "tension": 7, "valence": "-"},
            {"pct": 90, "tension": 9, "valence": "+"},
        ])
        out = cap.aggregate_rhythm(d)
        assert "tension_type_distribution" not in out
        assert "tension_trajectory" in out  # A3 原有聚合不被破坏


def _read_analyzer_md() -> str:
    return (_ROOT / ".claude" / "agents" / "novel-distill-analyzer.md").read_text(encoding="utf-8")


def test_analyzer_dim51_schema_has_tension_type_and_span():
    """D2-1：analyzer.md dim51 schema 含 tension_type 受控枚举 + span 证据字段。"""
    import re
    md = _read_analyzer_md()
    m = re.search(r'"dim51_张力曲线":\[\{[^\]]*\}\]', md)
    assert m, "未找到 dim51 schema 行"
    seg = m.group(0)
    assert "tension_type" in seg, seg
    assert "suspense" in seg and "curiosity" in seg and "surprise" in seg, seg
    assert "span" in seg, seg


def test_analyzer_b7_has_sternberg_fewshot():
    """D2-1：B7 纪律段含 Sternberg 三向度三句填空 few-shot（降弱模型漂移）。"""
    md = _read_analyzer_md()
    assert "suspense" in md and "curiosity" in md and "surprise" in md
    assert "等它爆" in md   # suspense 三句填空判别锚
    assert "想知道" in md   # curiosity
    assert "反转打脸" in md  # surprise


def test_rhythm_tension_type_directive_env_gated():
    """D2-4：三向度配比 directive 受 D2_TENSION_TYPE_INJECT_MODE 控（默认 shadow 不加·active 加）。"""
    import os
    import tempfile
    import build_manifest as bm

    class _S:
        def __init__(self, prof, db):
            self._p = prof
            self.db = db
            self.ch = 1

        def has_style_profile(self):
            return True

        def load(self, name, default=None):
            return self._p if name == "作者风格" else default

    prof = {"narrative_rhythm": {
        "tension_trajectory": {"post_climax_retention": 0.9},
        "tension_type_distribution": {"suspense": 0.5, "curiosity": 0.3, "surprise": 0.2},
    }}
    bak = {k: os.environ.get(k) for k in ("D2_TENSION_TYPE_INJECT_MODE", "RHYTHM_INJECT_MODE")}
    os.environ["RHYTHM_INJECT_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("D2_TENSION_TYPE_INJECT_MODE", None)  # 默认 shadow
            p = bm._collect_author_rhythm_signature(_S(prof, Path(td)))
            assert p is not None
            assert not any("张力机制配比" in d for d in p["directives"]), "默认 shadow 不应加三向度"
            os.environ["D2_TENSION_TYPE_INJECT_MODE"] = "active"
            p2 = bm._collect_author_rhythm_signature(_S(prof, Path(td)))
            assert any("张力机制配比" in d and "suspense50%" in d for d in p2["directives"])
    finally:
        for k, v in bak.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
