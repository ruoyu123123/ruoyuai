"""distill_replicate.py 复刻两 bug 回归测试（2026-05-30 系统验证发现）：

bug1【退化串清洗】replica 实测含 3826 字连续「铛」串（占 27% CJK）· LLM 复读退化 ·
  clean_output 不清洗 → 污染 SFS + 产出。修：collapse_degenerate_runs 检测连续重复
  单字（>30 次）/ 短串（≤4 字连续重复 >20 次）→ 截断保留合理长度 + stderr WARN。
  作者真实拟声只用单行短串（「铛。」独段）· 不会 3826 字 → 短拟声不得被误清洗。

bug2【cluster_index schema 兼容】cluster_index.json 实际 key 是 chapter_range
  ([lo,hi]) + estimated_words + chapters_count，但旧码读 chapter_start/ch_start/
  chapter_end/ch_end + total_words/word_count → gather_cluster_ref_text 返回空
  （参考原文没注入·复刻只靠 skill）+ estimate_words_per_chapter 永回退默认。
  producer/consumer schema 契约不符。修：tolerant 多 schema 读取。
"""
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import distill_replicate as dr  # noqa: E402


# ═══════════════════════════════ bug1 退化串清洗 ═══════════════════════════════

def test_collapse_single_char_degenerate_run():
    """3826 字连续「铛」实测翻车场景 → 截断保留 10 个 + 命中记录。"""
    degen = "铛" * 3826
    text = f"钟声响起。\n{degen}\n他停下脚步。"
    cleaned, hits = dr.collapse_degenerate_runs(text)
    assert "铛" * 3826 not in cleaned
    assert cleaned.count("铛") == 10  # 保留 10 个
    assert "钟声响起。" in cleaned and "他停下脚步。" in cleaned  # 正常文本不动
    assert len(hits) == 1
    assert hits[0]["type"] == "single_char"
    assert hits[0]["char"] == "铛"
    assert hits[0]["run_length"] == 3826


def test_collapse_short_str_degenerate_run():
    """短串复读退化（同一 2-4 字异构短串连续重复 >20 次）→ 截断保留 10 个。

    注意：必须用异构字短串（如「哈嘿」），同字短串（「哈哈」=100 个「哈」）会先被
    单字 run 检测吃掉——这正是期望行为（同字超长也是退化）。
    """
    degen = "哈嘿" * 50  # 100 字 = 50 次「哈嘿」（异构 → 走短串检测）
    text = f"他笑了。{degen}笑声停了。"
    cleaned, hits = dr.collapse_degenerate_runs(text)
    assert "哈嘿" * 50 not in cleaned
    assert cleaned.count("哈嘿") == 10
    assert "他笑了。" in cleaned and "笑声停了。" in cleaned
    assert any(h["type"] == "short_str" and h["unit"] == "哈嘿" for h in hits)


def test_same_char_short_str_caught_by_single_char_pass():
    """同字短串「哈哈」*50 = 100 个连续「哈」→ 由单字 run 检测先吃掉（也是退化·应清洗）。"""
    degen = "哈哈" * 50
    cleaned, hits = dr.collapse_degenerate_runs(degen)
    assert cleaned == "哈" * 10  # 单字 run 截断保留 10
    assert len(hits) == 1 and hits[0]["type"] == "single_char"


def test_short_onomatopoeia_not_clobbered():
    """北极星①：作者真实单行短拟声（「铛。」独段）绝不被误清洗。"""
    legit = "钟声敲响。\n\n铛。\n\n他抬头。\n\n咚咚咚。\n\n门外有人。"
    cleaned, hits = dr.collapse_degenerate_runs(legit)
    assert cleaned == legit  # 一字不动
    assert hits == []


def test_under_threshold_repeat_kept():
    """阈值内的重复（单字 ≤30 / 短串 ≤20）保留，不误伤合法强调。"""
    # 单字 30 次（= 阈值，不超过 → 不触发）
    t1 = "啊" * 30
    c1, h1 = dr.collapse_degenerate_runs(t1)
    assert c1 == t1 and h1 == []
    # 异构短串 20 次（= 阈值，不超过 → 不触发；用异构字避免被单字 run 吃掉）
    t2 = "嘿哈" * 20
    c2, h2 = dr.collapse_degenerate_runs(t2)
    assert c2 == t2 and h2 == []


def test_clean_output_invokes_degenerate_collapse():
    """clean_output 端到端：markdown 清洗 + 退化串清洗一起跑。"""
    raw = "```\n正文开头。\n" + "铛" * 200 + "\n正文结尾。\n```"
    out = dr.clean_output(raw)
    assert "```" not in out
    assert out.count("铛") == 10
    assert "正文开头。" in out and "正文结尾。" in out


def test_collapse_returns_clean_when_no_degen():
    """无退化串时原样返回 + 空命中（不引入副作用）。"""
    normal = "这是一段正常的中文小说正文。\n他走进房间，看见桌上的信。"
    cleaned, hits = dr.collapse_degenerate_runs(normal)
    assert cleaned == normal
    assert hits == []


# ═══════════════════════════════ bug2 schema 兼容 ═══════════════════════════════

# cluster_index.json 真实 schema（chapter_range / estimated_words / chapters_count）
_META_RANGE = {
    "chapter_range": [43, 48],
    "chapters_count": 6,
    "estimated_words": 18000,
    "boundary_reason": "max_chapters(6≥6)",
    "cluster_id": "auto_008",
    "scenes_estimated": 8,
}
# 旧扁平 schema（向后兼容必须不破坏）
_META_FLAT = {
    "chapter_start": 1, "chapter_end": 6, "chapters_count": 6,
    "total_words": 18000, "cluster_id": "cluster_001",
}
# 简写 schema
_META_SHORT = {
    "ch_start": 7, "ch_end": 12, "chapters_count": 6,
    "word_count": 18000, "cluster_id": "cluster_002",
}
# 只有 chapter_range（无 chapters_count）→ 应由 range 推算
_META_RANGE_ONLY = {
    "chapter_range": [13, 18], "estimated_words": 18000, "cluster_id": "auto_003",
}


def test_chapter_bounds_reads_chapter_range():
    """chapter_range [lo,hi] 必须读到（旧码恒返回 None → ref_text 空）。"""
    assert dr.cluster_chapter_bounds(_META_RANGE) == (43, 48)


def test_chapter_bounds_flat_compat():
    """旧扁平 chapter_start/chapter_end 向后兼容。"""
    assert dr.cluster_chapter_bounds(_META_FLAT) == (1, 6)


def test_chapter_bounds_short_compat():
    """简写 ch_start/ch_end 向后兼容。"""
    assert dr.cluster_chapter_bounds(_META_SHORT) == (7, 12)


def test_total_words_reads_estimated_words():
    """estimated_words 必须读到（旧码只读 total_words → 永回退默认 3500）。"""
    assert dr.cluster_total_words(_META_RANGE) == 18000


def test_total_words_flat_compat():
    assert dr.cluster_total_words(_META_FLAT) == 18000
    assert dr.cluster_total_words(_META_SHORT) == 18000


def test_chapters_count_from_range_when_missing():
    """chapters_count 缺失时由 chapter_range 推算（[13,18] → 6 章）。"""
    assert dr.cluster_chapters_count(_META_RANGE_ONLY) == 6


def test_estimate_words_per_chapter_with_range_schema():
    """end-to-end：chapter_range schema 下 estimate 用 estimated_words/章数（18000/6=3000），
    不再回退默认 3500。"""
    assert dr.estimate_words_per_chapter(_META_RANGE) == 3000
    # 旧码下：total_words=0 + chapters_count 读不到 → 回退 3500（回归保护）
    assert dr.estimate_words_per_chapter(_META_RANGE_ONLY) == 3000  # 18000/6


def test_gather_ref_text_reads_original_via_chapter_range():
    """核心修复验证：chapter_range schema 下 gather_cluster_ref_text 必须读到原文章节注入参考。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        orig = root / "原文"
        orig.mkdir(parents=True, exist_ok=True)
        # 造 ch43-45 原文（chapter_range [43,45]）
        for ch in (43, 44, 45):
            (orig / f"第{ch:03d}章.txt").write_text(
                f"第{ch}章正文开头，主角踏入秘境，眼前是一片血色。", encoding="utf-8")
        meta = {"chapter_range": [43, 45], "chapters_count": 3, "estimated_words": 9000}
        ref = dr.gather_cluster_ref_text(root, meta)
        assert ref != ""  # 旧码返回空（读不到 chapter_start）
        assert "ch43 首段" in ref and "ch45 首段" in ref
        assert "踏入秘境" in ref


def test_gather_ref_text_empty_when_no_bounds():
    """无任何章节边界 key → 返回空（不崩）。"""
    assert dr.gather_cluster_ref_text(Path("."), {"cluster_id": "x"}) == ""
