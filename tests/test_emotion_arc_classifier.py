# 🔴 2026-06-29 NN主题漂移/情感弧线集成
"""test_emotion_arc_classifier — 情感弧线分类 scanner 测试

钉死：
  · nn_vad_bridge 不可用时 → 词典兜底（source="lexicon_fallback"）
  · vad_model 可用时 → source="vad_model"
  · 段落太少 → arc_type="insufficient_data"
  · 六种弧型分类正确性（用词典控制 valence 走向）
  · 词典 valence 计算正确
  · Pearson 相关系数正确
  · scanner: 平坦弧线 → EMOTION_ARC_FLAT
  · scanner: 弱分类 → EMOTION_ARC_MISMATCH
  · 所有 issue 都是 advisory
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import emotion_arc_classifier as eac  # noqa: E402


# ── 测试用段落工厂 ─────────────────────────────────────────────────────────

def _make_positive_para(idx: int = 0) -> str:
    """构造高 valence 段落（正面词多）。"""
    bases = [
        "她笑了笑感到幸福温暖美好的阳光照在身上快乐喜悦满足",
        "他成功了激动自豪开心地拥抱同伴胜利的光明就在眼前",
        "春天的微笑温暖了所有人幸福安心轻松快乐的日子到了",
        "大笑声中充满了欢乐希望和美好甜蜜的未来在前方等待",
    ]
    return bases[idx % len(bases)]


def _make_negative_para(idx: int = 0) -> str:
    """构造低 valence 段落（负面词多）。"""
    bases = [
        "他哭了伤心痛苦绝望黑暗死亡恐惧悲伤孤独压抑泪水横流",
        "冰冷寒风中传来凄凉的哀嚎血腥的灾祸降临恐惧笼罩一切",
        "崩溃沮丧失望痛苦怒火中烧仇恨吞噬了最后一丝希望也没了",
        "暗夜里泪水模糊了双眼悲伤如潮水涌来孤独凶险的境遇令人绝望",
    ]
    return bases[idx % len(bases)]


def _make_neutral_para(idx: int = 0) -> str:
    """构造中性段落（无正负面词）。"""
    bases = [
        "走廊尽头有一扇木门推开后是一间空旷的房间",
        "桌上摆着几本旧书页面泛黄但字迹清晰可辨",
        "窗外的树叶随风摇动投下斑驳的影子在地面",
        "石板路蜿蜒向前两旁是灰色的围墙和铁栅栏",
    ]
    return bases[idx % len(bases)]


def _build_arc_text(pattern: list[str], paras_per_seg: int = 4) -> str:
    """构造指定情绪走向的文本。pattern: ["pos", "neg", "pos"] etc.

    每段按 pattern 对应段的极性生成。总段数 = len(pattern) * paras_per_seg。
    """
    lines: list[str] = []
    for seg_idx, polarity in enumerate(pattern):
        for i in range(paras_per_seg):
            pid = seg_idx * paras_per_seg + i
            if polarity == "pos":
                lines.append(_make_positive_para(pid))
            elif polarity == "neg":
                lines.append(_make_negative_para(pid))
            else:
                lines.append(_make_neutral_para(pid))
    return "\n".join(lines)


# ── 词典 valence ─────────────────────────────────────────────────────────────

class TestLexiconValence:
    """_lexicon_valence 计算正确性。"""

    def test_positive_text(self):
        """正面词多 → valence > 0.5。"""
        v = eac._lexicon_valence("她笑了开心快乐幸福美好温暖")
        assert v > 0.5

    def test_negative_text(self):
        """负面词多 → valence < 0.5。"""
        v = eac._lexicon_valence("他哭了痛苦悲伤绝望恐惧黑暗")
        assert v < 0.5

    def test_neutral_text(self):
        """无情绪词 → valence == 0.5。"""
        v = eac._lexicon_valence("桌上放着一杯水旁边有几本书")
        assert v == 0.5

    def test_mixed_text(self):
        """正负面词等量 → valence ≈ 0.5。"""
        v = eac._lexicon_valence("笑痛")  # 1 pos + 1 neg
        assert abs(v - 0.5) < 0.01

    def test_empty_text(self):
        """空文本 → 0.5。"""
        assert eac._lexicon_valence("") == 0.5


# ── Pearson 相关系数 ─────────────────────────────────────────────────────────

class TestPearsonCorrelation:
    """_pearson_correlation 计算正确性。"""

    def test_perfect_positive(self):
        """完全正相关 → 1.0。"""
        r = eac._pearson_correlation([1, 2, 3], [1, 2, 3])
        assert abs(r - 1.0) < 1e-9

    def test_perfect_negative(self):
        """完全负相关 → -1.0。"""
        r = eac._pearson_correlation([1, 2, 3], [3, 2, 1])
        assert abs(r - (-1.0)) < 1e-9

    def test_constant_returns_zero(self):
        """常数序列（零标准差）→ 0.0。"""
        r = eac._pearson_correlation([1, 1, 1], [1, 2, 3])
        assert r == 0.0

    def test_short_sequence(self):
        """长度 < 2 → 0.0。"""
        assert eac._pearson_correlation([1], [2]) == 0.0

    def test_length_mismatch(self):
        """长度不等 → 0.0。"""
        assert eac._pearson_correlation([1, 2], [1, 2, 3]) == 0.0

    def test_orthogonal(self):
        """无关序列 → 接近 0。"""
        r = eac._pearson_correlation([1, 0, -1], [1, -1, 1])
        # 不一定严格 0，但应该接近
        assert abs(r) < 0.5


# ── 弧线分类 ─────────────────────────────────────────────────────────────────

class TestClassifyEmotionArc:
    """classify_emotion_arc 分类正确性。"""

    def test_empty_text(self):
        """空文本 → insufficient_data。"""
        result = eac.classify_emotion_arc("")
        assert result["arc_type"] == "insufficient_data"
        assert result["confidence"] == 0.0

    def test_too_few_paragraphs(self):
        """段落太少 → insufficient_data。"""
        result = eac.classify_emotion_arc("第一段\n第二段\n第三段")
        assert result["arc_type"] == "insufficient_data"

    def test_rags_to_riches(self):
        """低→高→高 → rags_to_riches 或 cinderella（都是上升趋势）。"""
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] in ("rags_to_riches", "cinderella")
        assert result["source"] == "lexicon_fallback"
        assert result["n_paragraphs"] == 12

    def test_riches_to_rags(self):
        """高→低→低 → riches_to_rags 或 oedipus（都是下降趋势）。"""
        text = _build_arc_text(["pos", "neg", "neg"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] in ("riches_to_rags", "oedipus")

    def test_man_in_a_hole(self):
        """高→低→高 → man_in_a_hole（V 形）。"""
        text = _build_arc_text(["pos", "neg", "pos"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] == "man_in_a_hole"

    def test_icarus(self):
        """低→高→低 → icarus（倒 V 形）。"""
        text = _build_arc_text(["neg", "pos", "neg"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] == "icarus"

    def test_cinderella_gradual_rise(self):
        """低→中→高 → cinderella（渐进上升）。"""
        text = _build_arc_text(["neg", "mid", "pos"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] in ("cinderella", "rags_to_riches")

    def test_oedipus_gradual_fall(self):
        """高→中→低 → oedipus（渐进下降）。"""
        text = _build_arc_text(["pos", "mid", "neg"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert result["arc_type"] in ("oedipus", "riches_to_rags")

    def test_result_structure(self):
        """返回值包含所有必要字段。"""
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert "arc_type" in result
        assert "confidence" in result
        assert "valence_curve" in result
        assert "segments" in result
        assert "segment_means" in result
        assert "source" in result
        assert "n_paragraphs" in result
        assert isinstance(result["valence_curve"], list)
        assert isinstance(result["segments"], list)
        assert len(result["segments"]) == 3

    def test_confidence_range(self):
        """confidence 属于 [0, 1]。"""
        text = _build_arc_text(["neg", "pos", "neg"], paras_per_seg=4)
        result = eac.classify_emotion_arc(text)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_segments_cover_all_paragraphs(self):
        """三段覆盖所有段落（无遗漏无重叠）。"""
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=5)
        result = eac.classify_emotion_arc(text)
        total = sum(s["n_paragraphs"] for s in result["segments"])
        assert total == result["n_paragraphs"]


# ── VAD 桥集成 ───────────────────────────────────────────────────────────────

class TestVADBridgeIntegration:
    """nn_vad_bridge 集成测试。"""

    def test_lexicon_fallback_when_bridge_unavailable(self):
        """bridge 不可用 → 词典兜底 → source="lexicon_fallback"。"""
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=4)
        # nn_vad_bridge 默认 RUOYU_NN_VAD!="1" → predict_batch 返回全 None
        result = eac.classify_emotion_arc(text)
        assert result["source"] == "lexicon_fallback"

    def test_vad_model_source_when_bridge_works(self):
        """bridge 可用（mock）→ source="vad_model"。"""
        n_paras = 12
        mock_bridge = MagicMock()
        # 返回 V 形 valence: 高→低→高
        vals = ([0.8] * 4 + [0.2] * 4 + [0.8] * 4)
        mock_bridge.predict_batch.return_value = [
            {"valence": v, "arousal": 0.5, "dominance": 0.5, "source": "model"}
            for v in vals
        ]
        text = "\n".join([f"段落内容第{i}段文字" for i in range(n_paras)])
        with patch.dict(sys.modules, {"nn_vad_bridge": mock_bridge}):
            result = eac.classify_emotion_arc(text)
        assert result["source"] == "vad_model"
        assert result["arc_type"] == "man_in_a_hole"

    def test_bridge_partial_results(self):
        """bridge 部分返回 None → 混合（>50% 有结果仍用 vad_model）。"""
        n_paras = 12
        mock_bridge = MagicMock()
        # 8/12 有结果（>50%）
        results = []
        for i in range(n_paras):
            if i < 8:
                results.append({"valence": 0.5, "arousal": 0.5,
                                "dominance": 0.5, "source": "model"})
            else:
                results.append(None)
        mock_bridge.predict_batch.return_value = results
        text = "\n".join([f"段落内容第{i}段文字" for i in range(n_paras)])
        with patch.dict(sys.modules, {"nn_vad_bridge": mock_bridge}):
            result = eac.classify_emotion_arc(text)
        assert result["source"] == "vad_model"

    def test_bridge_mostly_none_fallback(self):
        """bridge 大部分返回 None（< 50%）→ 降级词典。"""
        n_paras = 12
        mock_bridge = MagicMock()
        # 只有 2/12 有结果（< 50%）
        results = [None] * n_paras
        results[0] = {"valence": 0.5, "arousal": 0.5,
                       "dominance": 0.5, "source": "model"}
        results[1] = {"valence": 0.5, "arousal": 0.5,
                       "dominance": 0.5, "source": "model"}
        mock_bridge.predict_batch.return_value = results
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=4)
        with patch.dict(sys.modules, {"nn_vad_bridge": mock_bridge}):
            result = eac.classify_emotion_arc(text)
        assert result["source"] == "lexicon_fallback"

    def test_bridge_import_error(self):
        """nn_vad_bridge 导入失败 → 词典兜底（不崩）。"""
        text = _build_arc_text(["neg", "pos", "pos"], paras_per_seg=4)
        with patch.dict(sys.modules, {"nn_vad_bridge": None}):
            result = eac.classify_emotion_arc(text)
        assert result["source"] == "lexicon_fallback"
        assert result["arc_type"] != "insufficient_data"


# ── scanner 函数 ─────────────────────────────────────────────────────────────

class TestScanEmotionArc:
    """scan_emotion_arc advisory scanner 测试。"""

    def test_insufficient_data_returns_empty(self):
        """段落太少 → 空 issue 列表。"""
        issues = eac.scan_emotion_arc("太短")
        assert issues == []

    def test_flat_arc_detected(self):
        """所有段落 valence 相同 → EMOTION_ARC_FLAT。"""
        # 全中性段落 → valence 全 0.5 → 方差 0
        text = "\n".join([_make_neutral_para(i) for i in range(12)])
        issues = eac.scan_emotion_arc(text)
        flat_issues = [i for i in issues if i["code"] == "EMOTION_ARC_FLAT"]
        assert len(flat_issues) >= 1
        assert flat_issues[0]["gate_level"] == "advisory"

    def test_strong_arc_no_flat(self):
        """明显起伏 → 不报 EMOTION_ARC_FLAT。"""
        text = _build_arc_text(["neg", "pos", "neg"], paras_per_seg=4)
        issues = eac.scan_emotion_arc(text)
        flat_issues = [i for i in issues if i["code"] == "EMOTION_ARC_FLAT"]
        assert len(flat_issues) == 0

    def test_all_issues_advisory(self):
        """所有 issue 都是 advisory。"""
        text = "\n".join([_make_neutral_para(i) for i in range(12)])
        issues = eac.scan_emotion_arc(text)
        for issue in issues:
            assert issue["gate_level"] == "advisory"

    def test_issue_structure(self):
        """每个 issue 包含必要字段。"""
        text = "\n".join([_make_neutral_para(i) for i in range(12)])
        issues = eac.scan_emotion_arc(text)
        for issue in issues:
            assert "code" in issue
            assert "gate_level" in issue
            assert "severity" in issue
            assert "message" in issue
            assert issue["code"] in (
                "EMOTION_ARC_FLAT",
                "EMOTION_ARC_MISMATCH",
            )

    def test_mismatch_low_confidence(self):
        """所有段落相同极性（模板匹配差）→ 可能报 EMOTION_ARC_MISMATCH。"""
        # 全正面 → 3 段均值都很高 → 不太匹配任何标准模板的走向
        # 但 rags_to_riches [0.2, 0.7, 0.9] 中 0.9 相关可能也行
        # 这里用全中性来测试
        text = "\n".join([_make_neutral_para(i) for i in range(12)])
        issues = eac.scan_emotion_arc(text)
        # 全中性 → 方差极低 + 可能 confidence 低
        codes = [i["code"] for i in issues]
        assert "EMOTION_ARC_FLAT" in codes or "EMOTION_ARC_MISMATCH" in codes


# ── _classify_from_segments 直接测试 ──────────────────────────────────────────

class TestClassifyFromSegments:
    """_classify_from_segments 模板匹配。"""

    def test_ascending(self):
        """[0.2, 0.7, 0.9] → rags_to_riches（完全匹配模板）。"""
        arc, conf = eac._classify_from_segments([0.2, 0.7, 0.9])
        assert arc == "rags_to_riches"
        assert conf > 0.9

    def test_descending(self):
        """[0.9, 0.3, 0.1] → riches_to_rags。"""
        arc, conf = eac._classify_from_segments([0.9, 0.3, 0.1])
        assert arc == "riches_to_rags"
        assert conf > 0.9

    def test_v_shape(self):
        """[0.7, 0.1, 0.7] → man_in_a_hole。"""
        arc, conf = eac._classify_from_segments([0.7, 0.1, 0.7])
        assert arc == "man_in_a_hole"
        assert conf > 0.9

    def test_inverted_v(self):
        """[0.3, 0.9, 0.3] → icarus。"""
        arc, conf = eac._classify_from_segments([0.3, 0.9, 0.3])
        assert arc == "icarus"
        assert conf > 0.9

    def test_gradual_rise(self):
        """[0.2, 0.5, 0.9] → cinderella。"""
        arc, conf = eac._classify_from_segments([0.2, 0.5, 0.9])
        assert arc == "cinderella"
        assert conf > 0.9

    def test_gradual_fall(self):
        """[0.9, 0.5, 0.1] → oedipus。"""
        arc, conf = eac._classify_from_segments([0.9, 0.5, 0.1])
        assert arc == "oedipus"
        assert conf > 0.9

    def test_flat_low_confidence(self):
        """[0.5, 0.5, 0.5] → 低 confidence（std=0 → Pearson=0）。"""
        arc, conf = eac._classify_from_segments([0.5, 0.5, 0.5])
        assert conf == 0.5  # (0 + 1) / 2 = 0.5
