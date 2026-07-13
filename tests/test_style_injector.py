"""style_injector 专属回归测试 —— 锁核心确定性逻辑（与 test_mmr_voice.py 互补·不重叠）。

test_mmr_voice.py 已覆盖 MMR 层：mmr_select_passages / get_golden_samples_for_type /
_text_cosine / _passage_text / _tag_relevance。

本文件聚焦尚未覆盖的核心确定性算法 + 分支 + 退出码：
  - parse_rule_window          —— 「连续 N 章」正则解析 + 默认窗口
  - get_applied_type_history   —— 历史 cluster 实际开头/结尾类型抽取
  - pick_type_weighted_avoiding—— avoid 过滤 / 空分布 / fallback 退路
  - _char_bigrams              —— 字符 bigram 向量化（MMR 底座·边界）
  - build_directive            —— 端到端：分布优先级/反重复 avoid/确定性 seed/cluster 摘要
  - main()                     —— CLI 退出码（argc / 项目缺失 / style 缺失 / 成功落盘）

零依赖纯标准库·确定性·真 import 真调用。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_injector as si  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "core" / "scripts" / "style_injector.py"


# ════════════════════════════════════════════════════════════════════
# parse_rule_window：从规则文本解析反重复窗口
# ════════════════════════════════════════════════════════════════════
def test_parse_rule_window_extracts_number():
    assert si.parse_rule_window("连续 5 章不得重复开场类型") == 5
    assert si.parse_rule_window("连续3章避免重复") == 3   # 无空格也命中
    assert si.parse_rule_window("连续  12  章") == 12      # 多空格


def test_parse_rule_window_defaults():
    # 空/None/无匹配 → 默认值
    assert si.parse_rule_window("") == 3
    assert si.parse_rule_window(None) == 3
    assert si.parse_rule_window("没有数字的规则文本") == 3
    assert si.parse_rule_window("", default_n=7) == 7     # 自定义默认
    assert si.parse_rule_window("无匹配", default_n=4) == 4


# ════════════════════════════════════════════════════════════════════
# get_applied_type_history：历史 cluster 实际类型抽取
# ════════════════════════════════════════════════════════════════════
def test_get_applied_type_history_basic():
    summaries = [
        {"truth_check": {"detected_opening_type": "悬念"}, "ending_type": "钩子"},
        {"truth_check": {"detected_opening_type": "回忆"}},
        {"cluster_id": "cluster_003"},
        {"truth_check": None},
    ]
    op = si.get_applied_type_history(summaries, "opening_type")
    en = si.get_applied_type_history(summaries, "ending_type")
    assert op == ["悬念", "回忆", None, None]
    assert en == ["钩子", None, None, None]


def test_get_applied_type_history_empty():
    assert si.get_applied_type_history([], "opening_type") == []


# ════════════════════════════════════════════════════════════════════
# pick_type_weighted_avoiding：加权抽样 + avoid 过滤 + fallback
# ════════════════════════════════════════════════════════════════════
def test_pick_avoids_listed_types():
    # 只有「悬念」和「回忆」有正 pct；avoid 掉「悬念」→ 只能落「回忆」
    dist = {
        "悬念": {"pct": 0.9},
        "回忆": {"pct": 0.1},
        "对话": {"pct": 0.0},   # pct=0 不参与
    }
    for _ in range(20):   # 多跑确认绝不命中 avoid 项
        chosen, reason = si.pick_type_weighted_avoiding(dist, avoid=["悬念"])
        assert chosen == "回忆"
        assert "weighted_pick" in reason


def test_pick_empty_distribution():
    chosen, reason = si.pick_type_weighted_avoiding({}, avoid=[])
    assert chosen is None
    assert reason == "no_distribution"


def test_pick_all_avoided_fallback_lowest_pct():
    # avoid 把所有 type 占满 → 退回 pct 最低者
    dist = {"A": {"pct": 0.6}, "B": {"pct": 0.3}, "C": {"pct": 0.1}}
    chosen, reason = si.pick_type_weighted_avoiding(dist, avoid=["A", "B", "C"])
    assert chosen == "C"                      # pct 最低
    assert reason == "fallback_lowest_pct"


def test_pick_all_avoided_no_fallback():
    dist = {"A": {"pct": 0.6}, "B": {"pct": 0.4}}
    chosen, reason = si.pick_type_weighted_avoiding(
        dist, avoid=["A", "B"], allow_fallback=False
    )
    assert chosen is None
    assert reason == "no_candidate"


def test_pick_single_candidate_deterministic():
    dist = {"唯一": {"pct": 1.0}}
    chosen, reason = si.pick_type_weighted_avoiding(dist, avoid=[])
    assert chosen == "唯一"
    assert "weighted_pick" in reason


# ════════════════════════════════════════════════════════════════════
# _char_bigrams：字符 bigram 向量化（MMR 底座·边界）
# ════════════════════════════════════════════════════════════════════
def test_char_bigrams_basic():
    # "刀光闪" → {"刀光":1, "光闪":1}
    bg = si._char_bigrams("刀光闪")
    assert bg == {"刀光": 1, "光闪": 1}


def test_char_bigrams_repeat_counts():
    # "啊啊啊" → bigram "啊啊" 出现 2 次
    assert si._char_bigrams("啊啊啊") == {"啊啊": 2}


def test_char_bigrams_strips_whitespace():
    # 空白被剥离后再切 bigram
    assert si._char_bigrams("刀 光\n闪") == {"刀光": 1, "光闪": 1}


def test_char_bigrams_edge():
    assert si._char_bigrams("") == {}
    assert si._char_bigrams("   ") == {}        # 全空白 → 剥空
    assert si._char_bigrams("甲") == {"甲": 1}   # 单字 → 自身当 key


# ════════════════════════════════════════════════════════════════════
# build_directive：端到端确定性装配
# ════════════════════════════════════════════════════════════════════
def _mk_project(tmp: Path, style: dict, summaries_doc=None, progress=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "作者风格.json").write_text(
        json.dumps(style, ensure_ascii=False), encoding="utf-8"
    )
    if summaries_doc is not None:
        (db / "故事块摘要.json").write_text(
            json.dumps(summaries_doc, ensure_ascii=False), encoding="utf-8"
        )
    if progress is not None:
        (db / "进度.json").write_text(
            json.dumps(progress, ensure_ascii=False), encoding="utf-8"
        )
    return tmp


_FULL_STYLE = {
    "cross_chapter_diversity": {
        "opening_type_distribution_300ch": {"动作开场": {"pct": 1.0}},
        "ending_type_distribution_300ch": {"钩子收尾": {"pct": 1.0}},
        "transition_type_distribution_100seg": {
            "空行": {"pct": 0.5}, "时间副词": {"pct": 0.3},
            "场景切": {"pct": 0.15}, "罕见": {"pct": 0.05},
        },
        "opening_rule": "连续 4 章不得重复开场",
        "ending_rule": "连续 2 章不得重复结尾",
        "env_anchor_high_risk_elements": ["天气", "光线"],
        "narrative_craft": {
            "hooks_per_chapter_avg": 5,
            "subtext_instances_per_chapter_avg": 3,
            "scene_vs_summary": {"range": "0.55-0.90", "scene_pct_avg_300ch": 0.75},
        },
        "author_distinctiveness_indicators": {
            "core_techniques": ["延迟揭示", "感官锚定"]
        },
    },
    "golden_passages": {
        "opening_passages": [{"tag": "动作开场", "text": "刀光劈下"}],
        "ending_passages": [{"tag": "钩子收尾", "text": "门后传来脚步"}],
        "transition_passages": [{"text": "t1"}, {"text": "t2"}, {"text": "t3"}],
    },
    "anti_patterns": {
        "never_words": ["顿时", "似乎"],
        "never_transitions": ["与此同时"],
    },
}


def test_build_directive_full_assembly():
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _FULL_STYLE)
        directive = si.build_directive(tmp, 1)

    assert directive["chapter"] == 1
    # 分布唯一项 → 选定它
    assert directive["opening_type"] == "动作开场"
    assert directive["ending_type"] == "钩子收尾"
    # 反重复窗口从 opening_rule/ending_rule 解析（4 / 2）→ 体现在 rule 文案
    assert "连续 4 章" in directive["opening_rule"]
    assert directive["ending_rule"] == "连续 2 章不得重复结尾"
    # transition top3 按 pct 降序，截前 3（罕见 0.05 被挤出）
    assert directive["transition_top3"] == ["空行", "时间副词", "场景切"]
    assert "罕见" not in directive["transition_top3"]
    # anchor / narrative targets 透传
    assert directive["anchor_strategy"] == ["天气", "光线"]
    assert directive["narrative_targets"]["hooks_per_chapter_target"] == 5
    assert directive["narrative_targets"]["subtext_per_chapter_target"] == 3
    assert directive["narrative_targets"]["scene_pct_avg"] == 0.75
    # core techniques pool 透传
    assert directive["core_techniques_pool"] == ["延迟揭示", "感官锚定"]
    assert directive["core_techniques_min_per_chapter"] == 1
    # anti patterns runtime
    assert directive["anti_patterns_runtime"]["never_words"] == ["顿时", "似乎"]
    assert directive["anti_patterns_runtime"]["never_transitions"] == ["与此同时"]
    # golden samples 命中 type
    assert directive["opening_golden_samples"][0]["text"] == "刀光劈下"
    assert directive["ending_golden_samples"][0]["text"] == "门后传来脚步"
    # transition golden 取前 2
    assert len(directive["transition_golden_samples"]) == 2
    # writer 必报字段 schema 存在
    assert "opening_type" in directive["applied_style_schema"]
    assert "ending_type" in directive["applied_style_schema"]
    assert directive["opening_type_enforcement"] == "strict"


def test_build_directive_deterministic_same_seed():
    # 同 项目名 + 章号 → md5 seed 固定 → 多次构造完全一致（确定性 seed）
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _FULL_STYLE)
        a = si.build_directive(tmp, 7)
        b = si.build_directive(tmp, 7)
    assert a["opening_type"] == b["opening_type"]
    assert a["ending_type"] == b["ending_type"]
    assert a["transition_top3"] == b["transition_top3"]


def test_build_directive_distribution_priority_300_over_60():
    # 300ch 与 60ch 同时存在 → 优先 300ch
    style = {
        "cross_chapter_diversity": {
            "opening_type_distribution_300ch": {"主分布": {"pct": 1.0}},
            "opening_type_distribution_60ch": {"次分布": {"pct": 1.0}},
            "ending_type_distribution_60ch": {"仅60结尾": {"pct": 1.0}},
        }
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style)
        directive = si.build_directive(tmp, 1)
    assert directive["opening_type"] == "主分布"        # 300ch 优先
    assert directive["ending_type"] == "仅60结尾"        # 无 300ch → 退 60ch


def test_build_directive_empty_style_no_crash():
    # 空风格档 → 不崩，type 为 None，列表字段为空
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {})
        directive = si.build_directive(tmp, 1)
    assert directive["opening_type"] is None
    assert directive["ending_type"] is None
    assert directive["opening_pick_reason"] == "no_distribution"
    assert directive["transition_top3"] == []
    assert directive["anchor_strategy"] == []
    assert directive["core_techniques_pool"] == []


def test_build_directive_anti_repeat_avoid_from_history():
    # 历史摘要里前几章已用 "动作开场"，反重复窗口避开它 → 落到 "对话开场"
    style = {
        "cross_chapter_diversity": {
            "opening_type_distribution_300ch": {
                "动作开场": {"pct": 0.9}, "对话开场": {"pct": 0.1},
            },
            "opening_rule": "连续 3 章不得重复",
        }
    }
    summaries_doc = {
        "clusters": [
            {"cluster_id": "cluster_001",
             "truth_check": {"detected_opening_type": "动作开场"}},
            {"cluster_id": "cluster_002",
             "truth_check": {"detected_opening_type": "动作开场"}},
        ]
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style, summaries_doc=summaries_doc)
        directive = si.build_directive(tmp, 3)
    # 窗口 3 → avoid 取最近 2 章用过的 "动作开场" → 必落 "对话开场"
    assert "动作开场" in directive["opening_avoid"]
    assert directive["opening_type"] == "对话开场"


def test_build_directive_uses_strict_opening_contract():
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _FULL_STYLE)
        directive = si.build_directive(tmp, 2)
    assert directive["opening_type_enforcement"] == "strict"


def test_build_directive_cluster_blueprint_summary():
    # 进度.json 的 cluster_blueprint 提供本章 scene_storyboard → title/scene_type 注入
    style = {"cross_chapter_diversity": {}}
    progress = {
        "cluster_blueprint": {
            "cluster_001": {
                "scene_storyboard": [
                    {"ch": 5, "title": "决战前夜", "scene_type": ["对峙", "悬疑"]},
                ]
            }
        }
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style, progress=progress)
        directive = si.build_directive(tmp, 5)
    assert directive["title"] == "决战前夜"
    assert directive["scene_type"] == ["对峙", "悬疑"]


def test_build_directive_cluster_blueprint_list_form_no_crash():
    # SC-1：blueprint 是 list 形态（城南实测）→ 归一不崩
    style = {"cross_chapter_diversity": {}}
    progress = {
        "cluster_blueprint": [
            {"cluster": "cluster_001", "ch": 1, "title": "开篇"},
            {"cluster": "cluster_001", "ch": 2, "title": "推进"},
        ]
    }
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), style, progress=progress)
        directive = si.build_directive(tmp, 1)   # 不抛异常即通过
    assert directive["chapter"] == 1


# ════════════════════════════════════════════════════════════════════
# main()：CLI 退出码 + 落盘
# ════════════════════════════════════════════════════════════════════
def _run_cli(*args):
    """子进程跑真 CLI（参照 tests/test_cross_cluster_fate_drift_aggregate.py 风格）。

    Windows 子进程默认按系统码页（GBK）输出中文，强制 PYTHONIOENCODING=utf-8 让
    print 走 UTF-8，避免父进程 utf-8 解码 stdout 报错（否则 r.stdout 变 None）。
    """
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT),
        env=env,
    )


def test_main_wrong_argc_exits_1():
    r = _run_cli()                      # 缺参数
    assert r.returncode == 1
    assert "用法" in r.stdout


def test_main_missing_project_exits_1():
    with tempfile.TemporaryDirectory() as d:
        missing = str(Path(d) / "不存在的项目")
        r = _run_cli(missing, "1")
    assert r.returncode == 1
    assert "项目路径不存在" in r.stdout


def test_main_missing_style_exits_1():
    # 项目存在但无 作者风格.json → FATAL exit 1
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        r = _run_cli(str(tmp), "1")
    assert r.returncode == 1
    assert "作者风格.json" in r.stdout


def test_main_success_writes_directive_file():
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _FULL_STYLE)
        r = _run_cli(str(tmp), "3")
        assert r.returncode == 0, r.stdout + r.stderr
        out_path = tmp / "_数据库" / ".style_directive" / "ch_003.json"
        assert out_path.exists()
        written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["chapter"] == 3
    assert written["opening_type"] == "动作开场"
    assert "[OK]" in r.stdout


# ════════════════════════════════════════════════════════════════════
# _text_cosine embedding 语义路径（真后端命中 + 门控关零回归）
# 门控只认 EMBED_BACKEND 非空非 hash（本仓约定每文件自留一份同口径判定）。
# 沿用本仓既有测试惯例：手工 os.environ 存/复 + 直接换 embedding_store.compute_embedding
# 属性（不用 pytest monkeypatch fixture · 与 test_topic_drift_scanner / test_agenda_drift_scanner
# 同款 · 兼容本文件可能被直接 python 执行的旧式 __main__ 场景）。
# ════════════════════════════════════════════════════════════════════
def _clear_embed_env():
    return os.environ.pop("EMBED_BACKEND", None)


def _restore_embed_env(bak_eb):
    if bak_eb is not None:
        os.environ["EMBED_BACKEND"] = bak_eb


def test_has_real_embedding_backend_false_by_default():
    bak_eb = _clear_embed_env()
    try:
        assert si._has_real_embedding_backend() is False
    finally:
        _restore_embed_env(bak_eb)


def test_has_real_embedding_backend_true_when_set():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "fake-real"
        assert si._has_real_embedding_backend() is True
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_has_real_embedding_backend_false_when_hash():
    bak = os.environ.get("EMBED_BACKEND")
    try:
        os.environ["EMBED_BACKEND"] = "hash"
        assert si._has_real_embedding_backend() is False
    finally:
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_text_cosine_semantic_path_hits_on_paraphrase():
    """真后端命中：字面 bigram 近乎零重叠的改写句，语义余弦应正确判定为高相似
    （bigram 法测不出"同一笔法换说法"）。"""
    a = "刀光一闪敌人已然倒地"
    b = "剑气纵横对手轰然倒下"
    shared = set(si._char_bigrams(a)) & set(si._char_bigrams(b))
    assert len(shared) <= 1, f"前置条件：字面 bigram 重叠应很低，实际共享 {shared}"

    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _content_aware_embed(text):
        # 两句都描写"敌人被击倒"这同一件事 → content-aware 假向量给相同方向
        if ("倒地" in text) or ("倒下" in text):
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedding_store.compute_embedding = _content_aware_embed
    try:
        sim = si._text_cosine(a, b)
        assert sim == 1.0   # 语义路径命中 → 完全相似（bigram 法在此处会判得远低于此）
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_text_cosine_semantic_unavailable_falls_back_to_bigram():
    """真后端配置但 embedding_store 编码异常 → 回退字符 bigram（不崩·不误判为语义路径）。"""
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise RuntimeError("模拟真后端编码失败")

    embedding_store.compute_embedding = _boom
    try:
        a = "刀光一闪，敌人已然倒地"
        b = "刀光一闪，敌人已然倒地啊"
        assert si._text_cosine(a, b) == si._text_cosine_bigram(a, b)
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_text_cosine_gate_off_matches_bigram_exactly():
    """🔴 零回归锁：门控关（无 EMBED_BACKEND）→ _text_cosine 结果与手算
    字符 bigram 余弦逐字节一致，且即便 embedding_store.compute_embedding 被换成任意值
    也绝不会被调用（门控在语义路径最前面短路）。"""
    bak_eb = _clear_embed_env()
    import embedding_store
    orig = embedding_store.compute_embedding

    def _boom(text):
        raise AssertionError("门控关时绝不应调用 compute_embedding")

    embedding_store.compute_embedding = _boom
    try:
        a = "刀光一闪，敌人已然倒地"
        b = "刀光一闪，敌人已然倒地啊"          # 近重复（高 bigram 重叠）
        c = "雨下了整夜，老人慢慢擦拭旧匕首"     # 迥异

        sim_dup = si._text_cosine(a, b)
        sim_diff = si._text_cosine(a, c)

        assert sim_dup == si._text_cosine_bigram(a, b)
        assert sim_diff == si._text_cosine_bigram(a, c)
        assert sim_dup > 0.8
        assert sim_diff < 0.3
    finally:
        embedding_store.compute_embedding = orig
        _restore_embed_env(bak_eb)


def test_mmr_select_passages_semantic_path_no_crash():
    """MMR 贪心选取逻辑本身不动：真后端接线后 mmr_select_passages 仍正常工作、不崩、
    仍返回 max_samples 个覆盖不同笔法的样本。"""
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig = embedding_store.compute_embedding

    def _content_aware_embed(text):
        if "刀光" in text:
            return [1.0, 0.0, 0.0]
        if "剑气" in text:
            return [0.9, 0.1, 0.0]     # 语义上更接近"刀光"而非"迥异段"
        return [0.0, 0.0, 1.0]

    embedding_store.compute_embedding = _content_aware_embed
    try:
        passages = [
            {"tag": "悬念开场", "text": "刀光一闪敌人已倒地"},
            {"tag": "悬念开场", "text": "剑气纵横对手轰然倒下"},
            {"tag": "悬念开场", "text": "雨下了整夜老人慢慢擦拭旧匕首"},
        ]
        out = si.get_golden_samples_for_type(passages, "悬念", max_samples=2)
        assert len(out) == 2
    finally:
        embedding_store.compute_embedding = orig
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


# ════════════════════════════════════════════════════════════════════
# 🔴 2026-07-03 Wave-4 性能层：MMR 候选段批量 prefetch（真后端子进程按条调用极贵·
# 两两 cosine 前先一次性 prefetch 全部候选文本，其后逐条 compute_embedding 命中缓存）
# ════════════════════════════════════════════════════════════════════
def test_mmr_select_passages_prefetches_candidates_once():
    """MMR 贪心选取前应恰好触发一次批量 prefetch，文本集合 = 全部候选段原文（原序）。"""
    bak = os.environ.get("EMBED_BACKEND")
    os.environ["EMBED_BACKEND"] = "fake-real"
    import embedding_store
    orig_prefetch = embedding_store.prefetch_embeddings
    calls = []

    def _rec_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0}

    embedding_store.prefetch_embeddings = _rec_prefetch
    try:
        passages = [
            {"tag": "悬念开场", "text": "刀光一闪敌人已倒地"},
            {"tag": "悬念开场", "text": "剑气纵横对手轰然倒下"},
            {"tag": "悬念开场", "text": "雨下了整夜老人慢慢擦拭旧匕首"},
        ]
        out = si.mmr_select_passages(passages, "悬念", max_samples=2)
        assert len(out) == 2
        assert len(calls) == 1, f"应恰好一次批量 prefetch·实际 {len(calls)} 次"
        assert calls[0] == [
            "刀光一闪敌人已倒地", "剑气纵横对手轰然倒下", "雨下了整夜老人慢慢擦拭旧匕首",
        ]
    finally:
        embedding_store.prefetch_embeddings = orig_prefetch
        if bak is not None:
            os.environ["EMBED_BACKEND"] = bak
        else:
            os.environ.pop("EMBED_BACKEND", None)


def test_mmr_select_passages_gate_off_never_prefetches():
    """🔴 零回归锁：门控关（无 EMBED_BACKEND）→ prefetch_embeddings
    完全不被调用（与既有 _text_cosine 门控关零回归锁互补）。"""
    bak_eb = _clear_embed_env()
    import embedding_store
    orig_prefetch = embedding_store.prefetch_embeddings

    def _boom(texts):
        raise AssertionError("门控关时绝不应调用 prefetch_embeddings")

    embedding_store.prefetch_embeddings = _boom
    try:
        passages = [
            {"tag": "悬念开场", "text": "刀光一闪敌人已倒地"},
            {"tag": "悬念开场", "text": "剑气纵横对手轰然倒下"},
            {"tag": "悬念开场", "text": "雨下了整夜老人慢慢擦拭旧匕首"},
        ]
        out = si.mmr_select_passages(passages, "悬念", max_samples=2)
        assert len(out) == 2
    finally:
        embedding_store.prefetch_embeddings = orig_prefetch
        _restore_embed_env(bak_eb)
