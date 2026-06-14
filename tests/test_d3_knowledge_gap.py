"""D3 读者-角色知识差三态聚合测试（2026-06-14·确定性·仿 D2-3）。

守护（北极星⑤⑥·照顾弱模型·占比需跨 cluster 累积）：
  1. dim32 三态占比(reader_adv/reader_disadv/double_blind) + reader_advantage_pct(D3 验证锚)；
  2. confidence=low 不计入 + 单 cluster 有效点<3 整块不计入(low_confidence)；
  3. 旧格式(dim32 是 str)→ isinstance(dict) 守卫降级不计数(向后兼容零回归)。
零依赖（stdlib）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


def _write_surface(d, key, dim32_points):
    sub = d / "蒸馏进度"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"cluster_{key}_surface.json").write_text(
        json.dumps({"qualitative_dims": {"dim32_信息差管理": {"per_point": dim32_points}}},
                   ensure_ascii=False), encoding="utf-8")


def test_knowledge_gap_distribution():
    """dim32 三态占比 + reader_advantage_pct（≥3 有效点计入）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"span": "段1", "gap_code": "reader_adv", "confidence": "high"},
            {"span": "段3", "gap_code": "reader_adv", "confidence": "high"},
            {"span": "段5", "gap_code": "double_blind", "confidence": "high"},
        ])
        out = cap.aggregate_knowledge_gap(d)
        assert out["knowledge_gap_distribution"]["reader_adv"]["pct"] == 0.667
        assert out["reader_advantage_pct"] == 0.667


def test_low_confidence_and_under_min_excluded():
    """confidence=low 不计 + 有效点<3 整 cluster 不计入。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_surface(d, "001", [
            {"gap_code": "reader_adv", "confidence": "high"},
            {"gap_code": "reader_adv", "confidence": "low"},   # 不计
            {"gap_code": "double_blind", "confidence": "high"},
        ])  # 有效点 2 < 3 → 整 cluster 不计入
        out = cap.aggregate_knowledge_gap(d)
        assert "knowledge_gap_distribution" not in out


def test_knowledge_gap_inject_three_modes():
    """D3-3：_collect_knowledge_gap_directives 三态（默认 shadow 落盘不注入·active 注入·off None）。"""
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

    prof = {"knowledge_gap_profile": {
        "knowledge_gap_distribution": {"reader_adv": {"count": 6, "pct": 0.6},
                                       "double_blind": {"count": 4, "pct": 0.4}},
        "reader_advantage_pct": 0.6,
    }}
    bak = os.environ.get("KNOWLEDGE_GAP_INJECT_MODE")
    try:
        with tempfile.TemporaryDirectory() as td:
            os.environ.pop("KNOWLEDGE_GAP_INJECT_MODE", None)  # 默认 shadow
            assert bm._collect_knowledge_gap_directives(_S(prof, Path(td))) is None
            assert (Path(td) / ".knowledge_gap" / "ch_001.json").exists()  # shadow 落盘
            os.environ["KNOWLEDGE_GAP_INJECT_MODE"] = "active"
            p = bm._collect_knowledge_gap_directives(_S(prof, Path(td)))
            assert p is not None and p["advisory_only"] is True
            assert any("信息差主调" in d for d in p["directives"])
            os.environ["KNOWLEDGE_GAP_INJECT_MODE"] = "off"
            assert bm._collect_knowledge_gap_directives(_S(prof, Path(td))) is None
    finally:
        if bak is None:
            os.environ.pop("KNOWLEDGE_GAP_INJECT_MODE", None)
        else:
            os.environ["KNOWLEDGE_GAP_INJECT_MODE"] = bak


def test_analyzer_dim32_has_gap_code_schema():
    """D3-1：analyzer.md dim32 schema 含 gap_code 受控 enum + per_point/confidence·dim13 release_sequence。"""
    md = (_ROOT / ".claude" / "agents" / "novel-distill-analyzer.md").read_text(encoding="utf-8")
    assert "gap_code" in md and "per_point" in md and "confidence" in md
    assert "reader_adv" in md and "reader_disadv" in md and "double_blind" in md
    assert "release_sequence" in md


def test_backward_compat_str_dim32():
    """旧格式 dim32 是 str → isinstance(dict) 守卫降级不计数（零回归不崩）。"""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        sub = d / "蒸馏进度"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "cluster_001_surface.json").write_text(
            json.dumps({"qualitative_dims": {"dim32_信息差管理": "极致上帝视角信息差自由文本"}},
                       ensure_ascii=False), encoding="utf-8")
        out = cap.aggregate_knowledge_gap(d)
        assert "knowledge_gap_distribution" not in out


def test_gen_writer_consumes_knowledge_gap_section():
    """D3 消费端（2026-06-14 补全链路）：gen_writer._build_knowledge_gap_section 读
    manifest.knowledge_gap_signature → 非空 prompt 段；None/无 key/空列表/文件缺 → 空段（零回归）。
    此前 build_manifest 产 knowledge_gap_signature slot 但 gen_writer 完全不消费 → 即便切 active 也白注入。"""
    import gen_writer as gw  # noqa: E402  懒 import（gen_writer 顶层依赖重·只在本 test 内拉）
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "manifest.json"
        # ① 有 directives（active 模式 build_manifest 注入）→ 非空段含指令原文 + 醒目段头
        mp.write_text(json.dumps({"knowledge_gap_signature": {
            "advisory_only": True,
            "directives": ["信息差主调（读者-角色知识差三态·作者基线）：reader_adv60%、double_blind40%"],
        }}, ensure_ascii=False), encoding="utf-8")
        sec = gw._build_knowledge_gap_section(mp)
        assert "信息差主调" in sec and "reader_adv60%" in sec and sec.startswith("## ")
        # ② None（shadow 默认·build_manifest 不注入）→ 空段（零回归）
        mp.write_text(json.dumps({"knowledge_gap_signature": None}, ensure_ascii=False), encoding="utf-8")
        assert gw._build_knowledge_gap_section(mp) == ""
        # ③ 无 key → 空段
        mp.write_text("{}", encoding="utf-8")
        assert gw._build_knowledge_gap_section(mp) == ""
        # ④ directives 空列表 → 空段
        mp.write_text(json.dumps({"knowledge_gap_signature": {"directives": []}}, ensure_ascii=False),
                      encoding="utf-8")
        assert gw._build_knowledge_gap_section(mp) == ""
        # ⑤ 文件不存在 → 空段（不崩）
        assert gw._build_knowledge_gap_section(Path(td) / "nope.json") == ""
