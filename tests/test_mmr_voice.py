"""P2 测试：MMR 覆盖式示范选择 + 跨角色 voice 坍缩（VOICE_COLLAPSE_CROSS_CHAR）。

对标作者思维蒸馏 G6-MMR（治高方差作者近重复检索冗余）+ R1 P2（声音坍缩盲点）。
全 advisory · 零依赖纯统计（不碰 LLM / embedding API）· 确定性脚手架。

覆盖：
  [A] rag_retriever.mmr_rerank          —— MMR 选更分散的文档（vs 纯 top-k 近重复）
  [B] style_injector.mmr_select_passages —— golden 样本覆盖不同笔法
  [C] VOICE_COLLAPSE_CROSS_CHAR          —— 所有人一个味→报；真作者多角色→不误报
  [D] advisory 锁死 + 不进 HARD_GATE_CODES
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

# active 态确保坍缩 issue 真升起（scanner 默认本就 active·显式钉住防环境漂移）
os.environ["VOICE_D4D8_MODE"] = "active"

import rag_retriever as rr          # noqa: E402
import style_injector as si         # noqa: E402
import cross_scene_voice_drift_scanner as vd  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# [A] rag_retriever.mmr_rerank：覆盖多样性 vs 纯 top-k 近重复
# ════════════════════════════════════════════════════════════════════
def _make_sim_fn(sim):
    """sim: dict[(i,j)] -> 相似度（对称·缺省 0）。"""
    def fn(i, j):
        if i == j:
            return 1.0
        return sim.get((i, j), sim.get((j, i), 0.0))
    return fn


def test_A_mmr_picks_diverse_over_near_duplicate():
    # 0/1/2 相关性都很高，但 0 与 1 近乎重复（讲同一段笔法），2 与它们都不同。
    # 纯 top-k（按相关性）会选 {0,1}（冗余）；MMR 应选 {0,2}（覆盖）。
    rel = {0: 0.95, 1: 0.93, 2: 0.80}
    sim = {(0, 1): 0.97, (0, 2): 0.05, (1, 2): 0.06}
    sim_fn = _make_sim_fn(sim)

    pure = sorted([0, 1, 2], key=lambda i: (-rel[i], i))[:2]
    assert pure == [0, 1]   # 纯相关性 → 拿到近重复对

    mmr = rr.mmr_rerank([0, 1, 2], rel, sim_fn, k=2, alpha=0.6)
    assert mmr[0] == 0                  # 起点=最相关
    assert 2 in mmr and 1 not in mmr    # MMR 去冗余：跳过近重复的 1，纳入分散的 2


def test_A_mmr_high_alpha_favors_relevance():
    # alpha→0.9 偏相关性：即便 0/1 相似，相关性差距足够大时仍会取相关性次高。
    rel = {0: 0.99, 1: 0.98, 2: 0.30}
    sim = {(0, 1): 0.9, (0, 2): 0.1, (1, 2): 0.1}
    sim_fn = _make_sim_fn(sim)
    mmr = rr.mmr_rerank([0, 1, 2], rel, sim_fn, k=2, alpha=0.9)
    # 高 alpha：1 的高相关性(0.98)压过 2 的低相关性(0.30)即便 1 与 0 相似
    assert mmr == [0, 1]


def test_A_mmr_deterministic():
    rel = {0: 0.9, 1: 0.9, 2: 0.9}
    sim = {(0, 1): 0.5, (0, 2): 0.5, (1, 2): 0.5}
    fn = _make_sim_fn(sim)
    r1 = rr.mmr_rerank([0, 1, 2], rel, fn, k=2, alpha=0.7)
    r2 = rr.mmr_rerank([2, 1, 0], rel, fn, k=2, alpha=0.7)   # 打乱入参顺序
    assert r1 == r2          # 并列按下标定序 → 与入参顺序无关·确定性
    assert r1 == [0, 1]      # 全并列 → 下标小优先


def test_A_mmr_edge_cases():
    fn = _make_sim_fn({})
    assert rr.mmr_rerank([], {}, fn, k=3) == []
    assert rr.mmr_rerank([5], {5: 0.5}, fn, k=3) == [5]    # 单候选
    assert rr.mmr_rerank([0, 1], {0: 0.5, 1: 0.4}, fn, k=0) == []  # k=0


# ════════════════════════════════════════════════════════════════════
# [B] style_injector.mmr_select_passages：golden 样本覆盖不同笔法
# ════════════════════════════════════════════════════════════════════
def test_B_golden_mmr_covers_distinct_passages():
    # 三段都 tag 命中 type"悬念"，但 #0 与 #1 正文近重复（同笔法），#2 迥异。
    passages = [
        {"tag": "悬念开场", "text": "刀光闪过，他猛地后仰，血溅在墙上一片猩红刺目"},
        {"tag": "悬念开场", "text": "刀光闪过，他猛地后仰，血溅在墙上一片猩红刺目啊"},  # 近重复
        {"tag": "悬念开场", "text": "雨下了整夜，老人坐在屋檐下慢慢擦拭那把生锈的旧匕首"},  # 不同笔法
    ]
    out = si.get_golden_samples_for_type(passages, "悬念", max_samples=2)
    assert len(out) == 2
    texts = [si._passage_text(p) for p in out]
    # MMR 应覆盖：不能两段都是近重复对（#0/#1），必须纳入迥异的 #2
    assert passages[2]["text"] in texts
    # 不能同时含 #0 和 #1（那是纯 top-k 的近重复冗余结果）
    assert not (passages[0]["text"] in texts and passages[1]["text"] in texts)


def test_B_text_cosine_near_duplicate_high():
    a = "刀光闪过，他猛地后仰，血溅在墙上"
    b = "刀光闪过，他猛地后仰，血溅在墙上啊"
    c = "雨下了整夜，老人慢慢擦拭旧匕首"
    assert si._text_cosine(a, b) > 0.8      # 近重复 → 高
    assert si._text_cosine(a, c) < 0.3      # 迥异 → 低


def test_B_golden_small_pool_no_diversity_forced():
    # 候选 ≤ max_samples → 不强上 MMR（避免过度工程）·原样返回
    passages = [{"tag": "悬念A", "text": "甲"}, {"tag": "悬念B", "text": "乙"}]
    out = si.get_golden_samples_for_type(passages, "悬念", max_samples=2)
    assert len(out) == 2


def test_B_golden_no_match_falls_back():
    passages = [{"tag": "回忆", "text": "甲"}, {"tag": "对话", "text": "乙"}]
    out = si.get_golden_samples_for_type(passages, "悬念", max_samples=2)
    assert len(out) == 1     # 无匹配 → 退回前 1（历史行为）


def test_B_tag_relevance_levels():
    assert si._tag_relevance("悬念开场", "悬念") == 1.0       # 整串命中
    assert si._tag_relevance("紧张 高压", "悬念 高压") == 0.6  # 分词命中
    assert si._tag_relevance("回忆", "悬念") == 0.0           # 不沾


# ════════════════════════════════════════════════════════════════════
# [C] VOICE_COLLAPSE_CROSS_CHAR：声音坍缩
# ════════════════════════════════════════════════════════════════════
def test_C_collapse_all_same_voice_flags():
    # 所有人说话一个味（完全相同对白）→ D4 区分度低 → 坍缩报警
    same = ["你好啊呢", "走吧啊呢", "在吗啊呢", "好的啊呢"]
    d4 = vd.compute_d4_distinctiveness({"甲": same, "乙": list(same), "丙": list(same)})
    cc = vd.compute_cross_char_voice_collapse(d4)
    assert cc is not None
    assert cc["issue_code"] == "VOICE_COLLAPSE_CROSS_CHAR"
    assert cc["gate_level"] == "advisory"
    assert cc["char_count"] == 3
    assert len(cc["collapsed_pairs"]) >= 1


def test_C_distinct_real_author_no_false_positive():
    # 真作者：多角色声音区分明显（短促 vs 长叹语气词）→ 不报坍缩
    chars = {
        "冷面剑客": ["嗯。", "哦。", "好。", "走。"],                       # 极简短
        "话痨少女": ["哎呀这可怎么办呀真是急死人了呢？", "你倒是说句话啊到底行不行嘛？",
                     "我跟你讲这事儿没那么简单啦！", "真的假的呀我都不敢信呢！"],   # 长·语气词·问叹号
    }
    d4 = vd.compute_d4_distinctiveness(chars)
    assert d4["mean_pairwise_distance"] > 0.1    # 区分明显
    cc = vd.compute_cross_char_voice_collapse(d4)
    assert cc is None    # 区分够 → 不误报


def test_C_less_than_two_chars_not_applicable():
    d4 = vd.compute_d4_distinctiveness({"甲": ["你好啊", "走吧", "在吗"]})
    assert vd.compute_cross_char_voice_collapse(d4) is None
    assert vd.compute_cross_char_voice_collapse(None) is None


def test_C_scan_surfaces_collapse_issue_in_active():
    import tempfile
    # 统一用「道：」speaker tag（避开 SPEAKER_PATTERN 对「说道」的贪婪二义·让同名归一）
    text = (
        "阿强道：“你好啊呢。”\n阿明道：“你好啊呢。”\n阿丽道：“你好啊呢。”\n"
        "阿强道：“走吧啊呢。”\n阿明道：“走吧啊呢。”\n阿丽道：“走吧啊呢。”\n"
        "阿强道：“在吗啊呢。”\n阿明道：“在吗啊呢。”\n阿丽道：“在吗啊呢。”\n"
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "_数据库").mkdir(exist_ok=True)
        d = tmp / "draft.txt"
        d.write_text(text, encoding="utf-8")
        rep = vd.scan(tmp, d)
        assert rep["d4d8_mode"] == "active"
        assert rep["cross_char_voice_collapse"] is not None
        # 顶层 issues[] 携带带 code 的可解析项（供 audit_hub / 任意消费方）
        codes = [it["code"] for it in rep.get("issues", [])]
        assert "VOICE_COLLAPSE_CROSS_CHAR" in codes
        coll = [it for it in rep["issues"] if it["code"] == "VOICE_COLLAPSE_CROSS_CHAR"][0]
        assert coll["gate_level"] == "advisory"
        assert rep["warning"] and "坍缩" in rep["warning"]


def test_C_scan_no_issue_when_no_collapse():
    import tempfile
    # 两角色区分明显 → 无坍缩 issue（统一用「道：」tag 避贪婪二义）
    text = (
        "阿强道：“嗯。”\n阿强道：“哦。”\n阿强道：“好。”\n阿强道：“走。”\n"
        "阿明道：“哎呀这可怎么办呀真是急死人了呢？”\n"
        "阿明道：“你倒是说句话啊到底行不行嘛？”\n"
        "阿明道：“我跟你讲这事儿没那么简单啦！”\n"
        "阿明道：“真的假的呀我都不敢信呢！”\n"
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "_数据库").mkdir(exist_ok=True)
        d = tmp / "draft.txt"
        d.write_text(text, encoding="utf-8")
        rep = vd.scan(tmp, d)
        codes = [it["code"] for it in rep.get("issues", [])]
        assert "VOICE_COLLAPSE_CROSS_CHAR" not in codes


def test_C_scan_off_mode_no_collapse():
    import importlib
    import tempfile
    os.environ["VOICE_D4D8_MODE"] = "off"
    try:
        importlib.reload(vd)
        text = (
            "阿强道：“你好啊呢。”\n阿明道：“你好啊呢。”\n阿丽道：“你好啊呢。”\n"
            "阿强道：“走吧啊呢。”\n阿明道：“走吧啊呢。”\n阿丽道：“走吧啊呢。”\n"
            "阿强道：“在吗啊呢。”\n阿明道：“在吗啊呢。”\n阿丽道：“在吗啊呢。”\n"
        )
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "_数据库").mkdir(exist_ok=True)
            d = tmp / "draft.txt"
            d.write_text(text, encoding="utf-8")
            rep = vd.scan(tmp, d)
            assert rep["cross_char_voice_collapse"] is None
            assert rep.get("issues", []) == []
    finally:
        os.environ["VOICE_D4D8_MODE"] = "active"
        importlib.reload(vd)


# ════════════════════════════════════════════════════════════════════
# [D] advisory 锁死 + 不进 HARD_GATE_CODES
# ════════════════════════════════════════════════════════════════════
def test_D_code_not_in_hard_gate_codes():
    import audit_hub
    assert "VOICE_COLLAPSE_CROSS_CHAR" not in set(audit_hub.HARD_GATE_CODES)


def test_D_gate_level_for_returns_advisory():
    import audit_hub
    # 任意 severity 下都判 advisory（不在 HARD_GATE_CODES 白名单）
    assert audit_hub._gate_level_for("VOICE_COLLAPSE_CROSS_CHAR") == "advisory"
    assert audit_hub._gate_level_for("VOICE_COLLAPSE_CROSS_CHAR", "error") == "advisory"
    assert audit_hub._gate_level_for("VOICE_COLLAPSE_CROSS_CHAR", "fatal") == "advisory"


def test_D_scanner_self_reports_advisory():
    # scanner 产物自报 gate_level=advisory（不越权自立 hard_gate）
    same = ["你好啊呢", "走吧啊呢", "在吗啊呢", "好的啊呢"]
    d4 = vd.compute_d4_distinctiveness({"甲": same, "乙": list(same), "丙": list(same)})
    cc = vd.compute_cross_char_voice_collapse(d4)
    assert cc["gate_level"] == "advisory"
