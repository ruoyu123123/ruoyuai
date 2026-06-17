"""cluster_segmenter.py 专属回归测试（零依赖 · 零 LLM · 零联网）。

被测：已蒸馏书 retroactive cluster 切分器的**确定性核心算法**。

已有间接覆盖（test_distill_plan_e2e.py）只在 fake_runner 里**桩掉** segmenter 命令
（手写一份 cluster_index.json 喂 orchestrator data_flow），**从未真 import / 真调用过**
任何 classify_boundary / segment_clusters / collect_transitions /
load_chapter_wordcounts / detect_total_chapters / _infer_genre_from_naming / main()。
本测试聚焦这些尚未被覆盖的真实逻辑，钉死：

  · classify_boundary：strong/weak/unknown 三分类 + 「直接承接（情绪落差子类）」
    必须被 continue 排除回退到 weak（脚本里那个易回退的 if 分支）；
  · segment_clusters：4 条硬约束（max章数 / max字数 / min章数 / min字数累积）、
    strong→切 / weak→不切 / unknown 接近 max 才切、尾 cluster 短并入前块、
    cluster_id 编号 + scenes_estimated 算式、最后一章强制 end_of_book；
  · collect_transitions：相邻章过滤（to==from+1）、跨章忽略、非 dict 跳过、
    同对 ch 去重、按 from_ch 排序；
  · load_chapter_wordcounts：多 schema 字段取数 + nested quantitative_analysis；
  · detect_total_chapters：多命名 regex 取 max；
  · _infer_genre_from_naming：naming_convention 优先 + 书名关键字 fallback；
  · main() CLI 退出码（project 不存在 exit2 / 总章数<2 exit2 / 正常落盘 exit0）。

main() 含 sys.exit，故走 subprocess 跑真 CLI（参照
tests/test_cross_cluster_fate_drift_aggregate.py 的 _run_cli 范式）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_segmenter as mod  # noqa: E402

_TARGET = _SCRIPTS / "cluster_segmenter.py"


# ──────────────────────────────────────────────────────────────────────────
# classify_boundary —— strong / weak / unknown 三分类 + 直接承接排除分支
# ──────────────────────────────────────────────────────────────────────────
def test_classify_boundary_strong_weak_unknown():
    """命中 STRONG → strong；命中 WEAK → weak；都不命中/空串 → unknown。"""
    assert mod.classify_boundary("POV切换") == "strong"
    assert mod.classify_boundary("时间跳跃") == "strong"
    assert mod.classify_boundary("直接承接") == "weak"
    assert mod.classify_boundary("对话接续") == "weak"
    assert mod.classify_boundary("某种没见过的衔接") == "unknown"
    assert mod.classify_boundary("") == "unknown"
    assert mod.classify_boundary(None) == "unknown"


def test_classify_boundary_zhijie_chengjie_overrides_strong_substring():
    """「直接承接（情绪落差子类）」含 strong 模式『情绪落差』，但脚本里 if '直接承接' in ct
    → continue 跳过 strong，最终回退命中 WEAK 的『直接承接』→ weak。
    钉死这个易回退的排除分支：以承接为主的不能被误判成强切边界。"""
    assert mod.classify_boundary("直接承接（情绪落差子类）") == "weak"
    # 反向对照：纯『情绪落差』无『直接承接』前缀 → 仍是 strong
    assert mod.classify_boundary("情绪落差") == "strong"


# ──────────────────────────────────────────────────────────────────────────
# segment_clusters —— 决策树各分支
# ──────────────────────────────────────────────────────────────────────────
def _strong(from_ch):
    return {"from_ch": from_ch, "boundary_class": "strong", "connection_type": "POV切换"}


def _weak(from_ch):
    return {"from_ch": from_ch, "boundary_class": "weak", "connection_type": "直接承接"}


def test_segment_min_chapters_blocks_single_chapter_cluster():
    """ch1 即遇 strong boundary，但当前 cluster 才 1 章 < MIN(2) → 必须继续累积，
    绝不切出单章 cluster。10 章全 strong 边界，受 min/字数约束应切成多块但每块 ≥2 章。"""
    total = 10
    transitions = [_strong(ch) for ch in range(1, total)]
    # 每章给足字数，让 min_words 不成为额外约束
    wc = {ch: 5000 for ch in range(1, total + 1)}
    clusters = mod.segment_clusters(total, transitions, wc)
    for c in clusters:
        assert c["chapters_count"] >= mod.MIN_CHAPTERS_PER_CLUSTER, c
    # 章号连续无缝覆盖 1..10
    assert clusters[0]["chapter_range"][0] == 1
    assert clusters[-1]["chapter_range"][1] == total
    flat = []
    for c in clusters:
        flat.extend(range(c["chapter_range"][0], c["chapter_range"][1] + 1))
    assert flat == list(range(1, total + 1))


def test_segment_strong_boundary_cuts_when_eligible():
    """足额字数 + 足够章数下，strong boundary 即切。
    ch2 处 strong（cluster 已 2 章 + 10000 字 ≥ min）→ cluster_001 = [1,2]，reason 含 strong。"""
    total = 4
    transitions = [_weak(1), _strong(2), _weak(3)]
    wc = {1: 5000, 2: 5000, 3: 5000, 4: 5000}
    clusters = mod.segment_clusters(total, transitions, wc)
    assert clusters[0]["chapter_range"] == [1, 2]
    assert clusters[0]["boundary_reason"].startswith("strong:")


def test_segment_weak_boundary_does_not_cut():
    """全 weak 边界 + 字数未触 max → 直到最后一章才 end_of_book，整本 1 个 cluster。"""
    total = 5
    transitions = [_weak(ch) for ch in range(1, total)]
    wc = {ch: 2000 for ch in range(1, total + 1)}  # 总 10000 < MAX(20000)
    clusters = mod.segment_clusters(total, transitions, wc)
    assert len(clusters) == 1
    assert clusters[0]["chapter_range"] == [1, total]
    assert clusters[0]["boundary_reason"] == "end_of_book"


def test_segment_max_chapters_force_cut():
    """全 weak（永不主动切），每章 2000 字：min_words(4000) 在第 2 章即满足让 cluster
    具备可切资格，但 weak 不主动切 → 一路累积到第 6 章触 MAX_CHAPTERS(6) 强制切；
    且 6×2000=12000 < MAX_WORDS(20000) 不会被 max_words 抢先 → 隔离 max_chapters 分支。"""
    total = 12
    transitions = [_weak(ch) for ch in range(1, total)]
    wc = {ch: 2000 for ch in range(1, total + 1)}
    clusters = mod.segment_clusters(total, transitions, wc)
    reasons = [c["boundary_reason"] for c in clusters]
    assert any(r.startswith("max_chapters") for r in reasons), reasons
    # 没有任何 cluster 超过 MAX_CHAPTERS 章
    for c in clusters:
        assert c["chapters_count"] <= mod.MAX_CHAPTERS_PER_CLUSTER, c


def test_segment_max_words_force_cut():
    """字数超 MAX_WORDS(20000) 时强制切，reason 标 max_words。"""
    total = 6
    transitions = [_weak(ch) for ch in range(1, total)]
    # ch1 起步就 25000 字 ≥ MAX_WORDS → 满足 min_chapters(2) 后下一轮即 max_words 切
    wc = {ch: 25000 for ch in range(1, total + 1)}
    clusters = mod.segment_clusters(total, transitions, wc)
    reasons = [c["boundary_reason"] for c in clusters]
    assert any(r.startswith("max_words") for r in reasons), reasons


def test_segment_short_tail_merged_into_prev():
    """尾 cluster 字数 < MIN_WORDS(4000) 且 ≥2 个 cluster → 并入前一块，
    reason 改 'end_of_book(merged_short_tail)'，章号无缝。"""
    total = 8
    # 前 6 章构成一个大块（strong 在第 6 章后切），后 2 章字数极少做短尾
    transitions = [_weak(ch) for ch in range(1, total)]
    transitions[5] = _strong(6)  # ch6 处 strong → 切出 [1,6]
    wc = {1: 5000, 2: 5000, 3: 5000, 4: 5000, 5: 5000, 6: 5000, 7: 100, 8: 100}
    clusters = mod.segment_clusters(total, transitions, wc)
    # 末块若曾独立则字数 200 < 4000 → 必被并入；最终末块覆盖到第 8 章
    assert clusters[-1]["chapter_range"][1] == total
    assert clusters[-1]["boundary_reason"] == "end_of_book(merged_short_tail)"
    # 并入后末块字数 = 前块 + 短尾，必 ≥ MIN_WORDS
    assert clusters[-1]["estimated_words"] >= mod.MIN_WORDS_PER_CLUSTER


def test_segment_cluster_id_and_scenes_estimated():
    """每个 cluster 编 auto_NNN id + scenes_estimated = max(2, round(chapters*1.3))。"""
    total = 6
    transitions = [_weak(ch) for ch in range(1, total)]
    wc = {ch: 4000 for ch in range(1, total + 1)}
    clusters = mod.segment_clusters(total, transitions, wc)
    for i, c in enumerate(clusters, 1):
        assert c["cluster_id"] == f"auto_{i:03d}"
        assert c["scenes_estimated"] == max(2, round(c["chapters_count"] * 1.3))
        assert c["scenes_estimated"] >= 2


def test_segment_last_chapter_end_of_book():
    """无论中途如何，最后一章必触发 end_of_book 收束（不丢章）。"""
    total = 3
    transitions = [_weak(1), _weak(2)]
    wc = {1: 2000, 2: 2000, 3: 2000}
    clusters = mod.segment_clusters(total, transitions, wc)
    assert clusters[-1]["chapter_range"][1] == total
    assert "end_of_book" in clusters[-1]["boundary_reason"]


# ──────────────────────────────────────────────────────────────────────────
# collect_transitions —— 相邻章过滤 / 跨章忽略 / 非 dict 跳过 / 去重 / 排序
# ──────────────────────────────────────────────────────────────────────────
def _write_continuity(project: Path, fname: str, transitions: list):
    d = project / "衔接分析"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(json.dumps({"transitions": transitions}, ensure_ascii=False),
                           encoding="utf-8")


def test_collect_transitions_adjacent_only_and_classify():
    """只收相邻章（to==from+1），跨章 transition 被忽略；connection_type → boundary_class。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _write_continuity(proj, "a_continuity.json", [
            {"from": 1, "to": 2, "connection_type": "POV切换"},   # 相邻 strong → 收
            {"from": 3, "to": 4, "connection_type": "直接承接"},   # 相邻 weak  → 收
            {"from": 5, "to": 9, "connection_type": "POV切换"},   # 跨章 → 忽略
            {"from": 6, "to": 6, "connection_type": "x"},        # 非相邻（to!=from+1）→ 忽略
        ])
        out = mod.collect_transitions(proj)
        pairs = [(t["from_ch"], t["to_ch"]) for t in out]
        assert pairs == [(1, 2), (3, 4)]
        assert out[0]["boundary_class"] == "strong"
        assert out[1]["boundary_class"] == "weak"


def test_collect_transitions_skips_non_dict_and_dedups_and_sorts():
    """transitions 含 str（坏 schema）被跳过；多文件同对 ch 去重；输出按 from_ch 升序。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _write_continuity(proj, "b_continuity.json", [
            {"from": 3, "to": 4, "connection_type": "对话接续"},
            "我不是 dict",                                         # 非 dict → skip
            {"from": 1, "to": 2, "connection_type": "POV切换"},
        ])
        # 第二个文件重复 (1,2) 这对 → 应被去重
        _write_continuity(proj, "c_continuity.json", [
            {"from": 1, "to": 2, "connection_type": "POV切换"},
        ])
        out = mod.collect_transitions(proj)
        pairs = [(t["from_ch"], t["to_ch"]) for t in out]
        assert pairs == [(1, 2), (3, 4)]   # 排序 + 去重


def test_collect_transitions_no_dir_returns_empty():
    """无 衔接分析/ 目录 → 返回空列表不崩。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.collect_transitions(Path(d)) == []


# ──────────────────────────────────────────────────────────────────────────
# load_chapter_wordcounts —— 多 schema 字段 + nested
# ──────────────────────────────────────────────────────────────────────────
def _write_metrics(project: Path, fname: str, payload: dict):
    d = project / "蒸馏进度"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_wordcounts_flat_and_nested_fields():
    """flat word_count / 中文键 字数 / nested quantitative_analysis.chapter_words.total 都能取数。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _write_metrics(proj, "ch1_metrics.json", {"word_count": 3200})
        _write_metrics(proj, "ch2_metrics.json", {"字数": 4100})
        _write_metrics(proj, "ch3_metrics.json",
                       {"quantitative_analysis": {"chapter_words": {"total": 5500}}})
        wc = mod.load_chapter_wordcounts(proj, total_chapters=3)
        assert wc == {1: 3200, 2: 4100, 3: 5500}


def test_load_wordcounts_missing_dir_returns_empty():
    """无 蒸馏进度/ 目录 → 空 dict。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.load_chapter_wordcounts(Path(d), 5) == {}


# ──────────────────────────────────────────────────────────────────────────
# detect_total_chapters —— 多命名 regex 取 max
# ──────────────────────────────────────────────────────────────────────────
def test_detect_total_chapters_takes_max():
    """蒸馏进度/ 下多种命名混杂 → 取最大章号。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _write_metrics(proj, "ch1_metrics.json", {"word_count": 1})
        _write_metrics(proj, "ch7_metrics.json", {"word_count": 1})
        _write_metrics(proj, "第12章.json", {"字数": 1})
        assert mod.detect_total_chapters(proj) == 12


def test_detect_total_chapters_no_dir_zero():
    """无目录 → 0。"""
    with tempfile.TemporaryDirectory() as d:
        assert mod.detect_total_chapters(Path(d)) == 0


# ──────────────────────────────────────────────────────────────────────────
# _infer_genre_from_naming —— naming_convention 优先 + 书名 fallback
# ──────────────────────────────────────────────────────────────────────────
def test_infer_genre_from_naming_convention_priority():
    """naming_convention.json 的 primary_culture=fantasy → xuanhuan（优先于书名）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "naming_convention.json").write_text(
            json.dumps({"primary_culture": "fantasy"}), encoding="utf-8")
        # 书名含『仙』本会推 xianxia，但 naming_convention 优先 → xuanhuan
        assert mod._infer_genre_from_naming(proj, "修仙传") == "xuanhuan"


def test_infer_genre_fallback_to_bookname_keywords():
    """无 naming_convention → 走书名关键字：含『副本』→ horror_game；不命中 → unknown。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        assert mod._infer_genre_from_naming(proj, "无限恐怖副本") == "horror_game"
        assert mod._infer_genre_from_naming(proj, "纯文学随笔") == "unknown"


# ──────────────────────────────────────────────────────────────────────────
# main() CLI —— 退出码 + 落盘（subprocess 跑真 CLI）
# ──────────────────────────────────────────────────────────────────────────
def _run_cli(args):
    # errors="replace"：被测脚本 print 中文，Windows 子进程 stdout 可能是 GBK，
    # 强制 utf-8 解码会在 reader 线程抛 UnicodeDecodeError 污染输出。我们只断言
    # 退出码 + 落盘文件，stdout/stderr 文本容错解码即可。
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_cli_missing_project_exit2():
    """--project 指向不存在路径 → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "不存在的书"
        r = _run_cli(["--project", str(missing)])
        assert r.returncode == 2, (r.returncode, r.stderr)


def test_cli_too_few_chapters_exit2():
    """检测到总章数 < 2 → exit 2（空项目目录 detect=0）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空书"
        proj.mkdir()
        r = _run_cli(["--project", str(proj)])
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)


def test_cli_happy_path_writes_index():
    """正常项目 → exit 0 + 落 cluster_index.json，schema 完整、clusters 无缝覆盖、genre 已推断。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "测试副本书"
        proj.mkdir()
        # 8 章字数数据 + 1 个相邻 strong 衔接 + naming 缺失走书名 fallback
        for ch in range(1, 9):
            _write_metrics(proj, f"ch{ch}_metrics.json", {"word_count": 5000})
        _write_continuity(proj, "x_continuity.json", [
            {"from": 4, "to": 5, "connection_type": "POV切换"},
        ])
        r = _run_cli(["--project", str(proj)])
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        idx = proj / "cluster_index.json"
        assert idx.exists()
        data = json.loads(idx.read_text(encoding="utf-8"))
        assert data["schema_version"] == "v22.cluster.2"
        assert data["total_chapters"] == 8
        assert data["genre"] == "horror_game"   # 书名含『副本』
        assert data["total_clusters"] == len(data["clusters"]) >= 1
        # 章号无缝覆盖 1..8
        flat = []
        for c in data["clusters"]:
            assert c["cluster_id"].startswith("auto_")
            flat.extend(range(c["chapter_range"][0], c["chapter_range"][1] + 1))
        assert flat == list(range(1, 9))


def test_cli_explicit_total_chapters_override():
    """--total-chapters 显式覆盖自动检测：给 4 即便目录只有占位也按 4 切。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "显式章数书"
        proj.mkdir()
        for ch in range(1, 5):
            _write_metrics(proj, f"ch{ch}_metrics.json", {"word_count": 6000})
        r = _run_cli(["--project", str(proj), "--total-chapters", "4"])
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        data = json.loads((proj / "cluster_index.json").read_text(encoding="utf-8"))
        assert data["total_chapters"] == 4
