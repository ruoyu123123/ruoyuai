"""TTR / hapax 词汇丰富度双端保真测试 —— 治 LLM 系统性拉平词汇丰富度盲区（2026-05-31）。

背景（3 篇研究 · 实证）：LLM imitation 系统性拉平词汇丰富度（GPT-4o lexical diversity 反转 ·
向 generic-median 回归），复刻稿用词反复趋同 → type_token_ratio / hapax_ratio 被压低。
style_analyzer 早已算 vocabulary_richness，但**两端都没接线**：
  · build_manifest 不注入作者 TTR/hapax 当 writer 显式目标；
  · style_evaluator 的稳定指纹维不含词级 TTR/hapax（盲区）。

本批接线（北极星①贴合作者风格 / ⑤ advisory 不黑箱不干涉模型 / ⑥ 复用现成不新建子系统）：
  (a) build_manifest._build_hard_constraints 注入作者 TTR/hapax 目标（数值剖面同款 · advisory 文案）。
  (b) style_evaluator.compute_programmatic_score 加「词汇丰富度匹配」稳定指纹维（gen-vs-author
      单向距离打分 · 被拉平才扣分 · 列入 _NONCOMP_STABLE_FINGERPRINT_DIMS → 非补偿几何均/floor 抓住）。
  env TTR_FIDELITY_MODE 默认 active（双端同开同关 · off 显式关闭做对照）。

纪律：advisory code 绝不进 HARD_GATE_CODES · 真作者同作者 TTR 应匹配不误拉垮（金标准）·
两套蒸馏 schema 容错 · LLM 脏数值不崩 · off 模式零回归。
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import build_manifest as bm  # noqa: E402
import style_evaluator as se  # noqa: E402


def _reload_se(mode):
    """以指定 TTR_FIDELITY_MODE reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("TTR_FIDELITY_MODE", None)
    else:
        os.environ["TTR_FIDELITY_MODE"] = mode
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（剥离 changes JSON 尾巴）。"""
    import chapter_io as cio
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
    return raw


def _load_cluster(book, count=6, start=10):
    """拼真原文若干章成 cluster（模拟 cluster 草稿）。"""
    d = _ROOT / "workspace" / "styles" / book / "原文"
    if not d.is_dir():
        return None
    import chapter_io as cio
    files = [f for f in sorted(d.glob("第*章.txt")) if "全本" not in f.name][start:start + count]
    if len(files) < 4:
        return None
    parts = []
    for f in files:
        raw = f.read_text(encoding="utf-8")
        for sep in cio.CHANGES_SEPARATORS:
            if sep in raw:
                raw = raw.split(sep)[0].rstrip()
                break
        parts.append(raw)
    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════════
# [A] build_manifest 端：作者 TTR/hapax 目标抽取（schema 容错 + 脏数值）
# ════════════════════════════════════════════════════════════════

def test_A_extract_jingsong_vocab_richness_schema():
    """惊悚乐园 schema：quantitative.vocab_richness.{ttr_mean,ttr_std,hapax_mean,hapax_std}。"""
    q = {"vocab_richness": {"ttr_mean": 0.9706, "ttr_std": 0.0177,
                            "hapax_mean": 0.9498, "hapax_std": 0.0269}}
    vr = bm._extract_author_vocab_richness(q)
    assert vr is not None
    assert vr["ttr"] == 0.9706 and vr["ttr_std"] == 0.0177
    assert vr["hapax"] == 0.9498 and vr["hapax_std"] == 0.0269


def test_A_extract_guzhenren_vocabulary_schema():
    """蛊真人 schema：quantitative.vocabulary.{ttr,hapax_ratio}（值为 null → None 不编造）。"""
    assert bm._extract_author_vocab_richness({"vocabulary": {"ttr": None, "hapax_ratio": None}}) is None
    vr = bm._extract_author_vocab_richness({"vocabulary": {"ttr": 0.91, "hapax_ratio": 0.7}})
    assert vr["ttr"] == 0.91 and vr["hapax"] == 0.7
    assert vr["ttr_std"] is None  # 该 schema 无 std


def test_A_extract_native_analyzer_schema():
    """style_analyzer 原生 schema：vocabulary_richness.{type_token_ratio,hapax_ratio}。"""
    vr = bm._extract_author_vocab_richness(
        {"vocabulary_richness": {"type_token_ratio": 0.88, "hapax_ratio": 0.72}})
    assert vr["ttr"] == 0.88 and vr["hapax"] == 0.72


def test_A_extract_dirty_values_no_crash():
    """LLM 脏数值（'约0.9' / null / bool）→ 跳过不崩；全脏 → None。"""
    assert bm._extract_author_vocab_richness({"vocab_richness": {"ttr_mean": "约0.9", "hapax_mean": None}}) is None
    assert bm._extract_author_vocab_richness({"vocabulary": {"ttr": True, "hapax_ratio": "高"}}) is None
    assert bm._extract_author_vocab_richness({}) is None
    assert bm._extract_author_vocab_richness(None) is None
    assert bm._extract_author_vocab_richness("not_a_dict") is None


def test_A_extract_partial_only_ttr():
    """只有 ttr 没 hapax（或反之）→ 仍返回（部分可用 · 不要求两者都齐）。"""
    vr = bm._extract_author_vocab_richness({"vocab_richness": {"ttr_mean": 0.95}})
    assert vr["ttr"] == 0.95 and vr["hapax"] is None


def test_A_mode_default_active_and_off():
    """TTR_FIDELITY_MODE 默认 active · 非法回退 active · off 原样。"""
    prev = os.environ.pop("TTR_FIDELITY_MODE", None)
    try:
        assert bm._ttr_fidelity_mode() == "active"
        os.environ["TTR_FIDELITY_MODE"] = "off"
        assert bm._ttr_fidelity_mode() == "off"
        os.environ["TTR_FIDELITY_MODE"] = "garbage"
        assert bm._ttr_fidelity_mode() == "active"
        os.environ["TTR_FIDELITY_MODE"] = "ACTIVE"
        assert bm._ttr_fidelity_mode() == "active"
    finally:
        os.environ.pop("TTR_FIDELITY_MODE", None)
        if prev is not None:
            os.environ["TTR_FIDELITY_MODE"] = prev


# ════════════════════════════════════════════════════════════════
# [B] build_manifest 端：hard_constraints 注入（端到端 · env 三态）
# ════════════════════════════════════════════════════════════════

_STYLE_WITH_VOCAB = {
    "analyzed_chapters": 250,
    "quantitative": {
        "sentence_length": {"mean": 30.0, "std": 7.0},
        "vocab_richness": {"ttr_mean": 0.97, "ttr_std": 0.02,
                           "hapax_mean": 0.95, "hapax_std": 0.03},
    },
}


def _mk_project(tmp: Path, style: dict | None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    prog = {
        "volumes": [{"vol": 1, "title": "一卷", "chapter_range": [1, 8]}],
        "cluster_blueprint": {"cluster_001": {
            "chapter_range": [1, 4],
            "scene_storyboard": [{"ch": 1, "characters": ["主角"], "key_events": ["开局"],
                                  "scene_type": ["悬疑"], "summary": "夜行遇怪"}]}},
    }
    (db / "进度.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(
        json.dumps({"characters": [{"id": "m", "name": "主角", "role": "主角"}]},
                   ensure_ascii=False), encoding="utf-8")
    if style is not None:
        (db / "作者风格.json").write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return tmp


def _vr_constraints(tmp: Path) -> list[str]:
    s = bm.DatabaseScanner(tmp, 1)
    hc = bm._build_hard_constraints(s, {"tier1_due_count": 0, "must_reveal_this_ch": 0})
    return [c for c in hc if "词汇丰富度" in c]


def test_B_active_injects_ttr_constraint():
    """默认 active → hard_constraints 含 TTR/hapax 目标行（带作者均值 + advisory 措辞）。"""
    prev = os.environ.pop("TTR_FIDELITY_MODE", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_project(Path(d), _STYLE_WITH_VOCAB)
            vr = _vr_constraints(tmp)
            assert len(vr) == 1, vr
            line = vr[0]
            assert "TTR" in line and "hapax" in line
            assert "0.97" in line and "0.95" in line  # 作者均值显式
            assert "advisory" in line  # 北极星⑤ 不硬锁
    finally:
        if prev is not None:
            os.environ["TTR_FIDELITY_MODE"] = prev


def test_B_off_suppresses_constraint():
    """显式 off → 不注入 TTR 约束（零回归对照）。"""
    os.environ["TTR_FIDELITY_MODE"] = "off"
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_project(Path(d), _STYLE_WITH_VOCAB)
            assert _vr_constraints(tmp) == []
    finally:
        os.environ.pop("TTR_FIDELITY_MODE", None)


def test_B_no_vocab_data_no_constraint():
    """作者档无 TTR/hapax 数据（蛊真人 null）→ 不注入（不编造目标）。"""
    prev = os.environ.pop("TTR_FIDELITY_MODE", None)
    try:
        style = {"quantitative": {"sentence_length": {"mean": 19.0},
                                  "vocabulary": {"ttr": None, "hapax_ratio": None}}}
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_project(Path(d), style)
            assert _vr_constraints(tmp) == []
    finally:
        if prev is not None:
            os.environ["TTR_FIDELITY_MODE"] = prev


def test_B_no_style_profile_no_constraint():
    """无 作者风格.json → 整段量化约束不进，自然无 TTR 约束（不崩）。"""
    prev = os.environ.pop("TTR_FIDELITY_MODE", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_project(Path(d), None)
            assert _vr_constraints(tmp) == []
    finally:
        if prev is not None:
            os.environ["TTR_FIDELITY_MODE"] = prev


def test_B_full_manifest_builds_with_ttr():
    """整条 build_manifest 跑通（TTR 注入不破坏 manifest 生成 · 必跑流水线不崩）。"""
    prev = os.environ.pop("TTR_FIDELITY_MODE", None)
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = _mk_project(Path(d), _STYLE_WITH_VOCAB)
            m = bm.build_manifest(tmp, 1)
            assert m["preflight"]["passed"], m["preflight"]
            assert any("词汇丰富度" in c for c in m["hard_constraints"])
    finally:
        if prev is not None:
            os.environ["TTR_FIDELITY_MODE"] = prev


# ════════════════════════════════════════════════════════════════
# [C] style_evaluator 端：词汇丰富度匹配维 + 单向打分纯函数
# ════════════════════════════════════════════════════════════════

def test_C_vocab_match_equal_full_score():
    """gen 与 ref 词汇丰富度相等 → 满分。"""
    assert se._vocab_richness_match(0.9, 0.9) == 1.0


def test_C_vocab_match_higher_not_penalized():
    """gen 更丰富（高于作者目标）→ 满分（只抓被拉平 · 不矫枉过正 · 北极星⑤）。"""
    assert se._vocab_richness_match(0.85, 0.95) == 1.0


def test_C_vocab_match_lower_penalized():
    """gen 被拉平（显著低于作者目标）→ 扣分（真盲点核心场景）。"""
    sc = se._vocab_richness_match(0.90, 0.45)
    assert sc < 0.6, sc


def test_C_vocab_match_no_baseline_full_score():
    """无作者基线（ref<=0）→ 不可比 → 满分（不凭空扣分）。"""
    assert se._vocab_richness_match(0.0, 0.3) == 1.0


def test_C_vocab_match_interval_within_or_above_full():
    """区间 ref：落入或高于上界得满分，低于下界扣分。"""
    band = {"min": 0.90, "max": 0.98, "mean": 0.94}
    assert se._vocab_richness_match(band, 0.95) == 1.0  # 落入
    assert se._vocab_richness_match(band, 0.99) == 1.0  # 高于上界（更丰富）
    assert se._vocab_richness_match(band, 0.50) < 0.6   # 远低于下界


def test_C_dimension_present_in_programmatic_active():
    """active（默认）→ compute_programmatic_score 含「词汇丰富度匹配」维。"""
    sx = _reload_se("active")
    try:
        ref = "清晨薄雾笼罩寂静山村远处传来零星犬吠鸡鸣老人佝偻着背走向田垄。" * 8
        gen = "傍晚浓雾弥漫荒凉古镇近处响起断续狗叫蝉鸣妇人挺直了腰迈进巷口。" * 8
        ps = sx.compute_programmatic_score(sx.analyze_text(ref), sx.analyze_text(gen), gen)
        assert any(d["name"] == sx._VOCAB_RICHNESS_DIM for d in ps["dimensions"])
    finally:
        _reload_se(None)


def test_C_dimension_absent_when_off():
    """off → compute_programmatic_score 不含 TTR 维（零回归对照）。"""
    sx = _reload_se("off")
    try:
        ref = gen = "他说他来了她说她走了。" * 20
        ps = sx.compute_programmatic_score(sx.analyze_text(ref), sx.analyze_text(gen), gen)
        assert not any(d["name"] == sx._VOCAB_RICHNESS_DIM for d in ps["dimensions"])
    finally:
        _reload_se(None)


def test_C_dim_registered_as_stable_fingerprint():
    """词汇丰富度维已登记进 _NONCOMP_STABLE_FINGERPRINT_DIMS（→ 非补偿几何均/floor 能抓崩）。"""
    assert se._VOCAB_RICHNESS_DIM in se._NONCOMP_STABLE_FINGERPRINT_DIMS


def test_C_collapsed_vocab_caught_by_noncomp():
    """词汇丰富度维被打崩（模拟用词趋同拉平的复刻）→ 非补偿层 floor/几何均抓住 + 触发 advisory。"""
    sx = _reload_se("active")
    try:
        dims = [
            {"name": "句长分布 JSD", "weight": 0.08, "score": 92.0},
            {"name": "功能词指纹", "weight": 0.08, "score": 90.0},
            {"name": "标点密度指纹", "weight": 0.04, "score": 91.0},
            {"name": sx._VOCAB_RICHNESS_DIM, "weight": 0.04, "score": 8.0},  # 词汇被拉平
        ]
        r = sx.compute_noncompensatory_aggregation(dims)
        assert r["worst_dimension"] == sx._VOCAB_RICHNESS_DIM, r
        assert sx._VOCAB_RICHNESS_DIM in r["collapsed_stable_fingerprint"], r
        assert any(i["code"] == "SFS_DIMENSION_COLLAPSE" for i in r["advisory_issues"]), r
    finally:
        _reload_se(None)


# ════════════════════════════════════════════════════════════════
# [D] advisory 绝不进 hard_gate（北极星⑤）
# ════════════════════════════════════════════════════════════════

def test_D_ttr_codes_not_in_hard_gate():
    """词汇丰富度走既有 SFS advisory code，绝不引入新 hard_gate code。"""
    try:
        import audit_hub  # noqa: E402
    except Exception:
        return
    # 既有 SFS code 仍 advisory（本批不新增 code · 复用 SFS_DIMENSION_COLLAPSE/GAP）
    for code in ("SFS_DIMENSION_COLLAPSE", "SFS_COMPENSATION_GAP"):
        assert code not in audit_hub.HARD_GATE_CODES


# ════════════════════════════════════════════════════════════════
# [E] 真作者矫枉过正金标准（北极星纪律 3）：同作者 TTR 应匹配 · 不误拉垮
# ════════════════════════════════════════════════════════════════

def test_E_real_author_vocab_dim_high_not_dragged():
    """真作者两章互比 → 词汇丰富度维高分（同作者用词多样度一致 · 单向匹配不误罚 · 金标准）。"""
    sx = _reload_se("active")
    try:
        found = False
        for book in ("蛊真人", "惊悚乐园"):
            a = _load_chapter(book, "第010章")
            c = _load_chapter(book, "第020章")
            if a is None or c is None:
                continue
            found = True
            ps = sx.compute_programmatic_score(sx.analyze_text(a), sx.analyze_text(c), c,
                                               has_author_profile=True)
            vr = next((d for d in ps["dimensions"] if d["name"] == sx._VOCAB_RICHNESS_DIM), None)
            assert vr is not None, book
            # 同作者词汇丰富度一致 → 该维高分（≥ 80 留余量 · 不被误拉垮）
            assert vr["score"] >= 80.0, (book, vr)
        assert found or _load_chapter("蛊真人", "第010章") is None
    finally:
        _reload_se(None)


def test_E_real_author_self_cluster_no_collapse():
    """真作者 cluster 自比（ref=gen）→ 词汇丰富度维满分 · 非补偿层无崩维 advisory（金标准）。"""
    sx = _reload_se("active")
    try:
        found = False
        for book in ("蛊真人", "惊悚乐园"):
            cluster = _load_cluster(book, count=6, start=10)
            if cluster is None:
                continue
            found = True
            rp = sx.analyze_text(cluster)
            ps = sx.compute_programmatic_score(rp, rp, cluster, has_author_profile=True)
            vr = next((d for d in ps["dimensions"] if d["name"] == sx._VOCAB_RICHNESS_DIM), None)
            assert vr is not None and vr["score"] >= 95.0, (book, vr)
            r = sx.compute_noncompensatory_aggregation(ps["dimensions"])
            # 加入新维后，真作者非补偿几何均仍高、无崩维（新维不误拉垮）
            assert r["geometric_mean"] >= 88.0, (book, r)
            assert sx._VOCAB_RICHNESS_DIM not in r["collapsed_stable_fingerprint"], (book, r)
        assert found or _load_cluster("蛊真人") is None
    finally:
        _reload_se(None)


def test_E_real_author_sfs_not_lowered_by_new_dim():
    """新增维不该把真作者 self-cluster 的 SFS programmatic total 拉垮（off vs active 差距极小）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        cluster = _load_cluster(book, count=6, start=10)
        if cluster is None:
            continue
        found = True
        sx_off = _reload_se("off")
        rp = sx_off.analyze_text(cluster)
        ps_off = sx_off.compute_programmatic_score(rp, rp, cluster, has_author_profile=True)
        sx_on = _reload_se("active")
        ps_on = sx_on.compute_programmatic_score(rp, rp, cluster, has_author_profile=True)
        # 自比 VR 满分 → active total 应 ≈ off total（新维满分不拉垮真作者）
        assert ps_on["total"] >= ps_off["total"] - 0.5, (book, ps_on["total"], ps_off["total"])
    _reload_se(None)
    assert found or _load_cluster("蛊真人") is None
