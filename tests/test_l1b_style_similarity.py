"""L1b 风格相似度漂移分测试 — one-class/OOD scanner（北极星① band 从作者原文涌现 · 2026-05-31）。

背景：L1b 把「作者原文池」当 one-class 参照系——对作者每章算 embedding centroid，得作者风格
中心 + **作者自身章节相对中心的 cosine 分布**（mean/σ）；cluster 草稿算与中心的 cosine，
若 < (mean−2σ) 则是作者风格空间里的离群点（OOD）→ advisory「风格相似度漂移」。
阈值不写死，从作者样本经验分布涌现（不同作者 σ 不同 → 阈值各异）。

复用：严格只读复用 embedding_store（compute_embedding / cosine_similarity / embedding_method），
范式同 embedding_store.compute_character_drift（centroid + cosine + 维度混用防护）。

纪律守护（共同纪律 + 北极星）：
  · 影子并行：env L1B_SIMILARITY_MODE 默认 shadow（只记不判·零回归）/ active（出 advisory）/ off。
  · 顾问非法官：code STYLE_SIMILARITY_DRIFT **绝不进 audit_hub.HARD_GATE_CODES**。
  · backend 默认 hash（占位·语义弱）→ 报告透出 backend_note；不强制下载 bge。
  · 真原文校准金标准：蛊真人 held-out 章 in-dist 不误判 + 惊悚乐园（异作者）OOD 命中。

只测确定性纯函数 + 真原文（程序化校验），不碰 LLM / agent / 网络。
"""
import os
import sys
import json
import importlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import embedding_store as es  # noqa: E402
import style_similarity_scanner as ss  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_GU = _ROOT / "workspace" / "styles" / "蛊真人" / "原文"
_JING = _ROOT / "workspace" / "styles" / "惊悚乐园" / "原文"


def _set_mode(mode):
    """设 L1B_SIMILARITY_MODE（_mode() 每次读 env·无需 reload）。"""
    if mode is None:
        os.environ.pop("L1B_SIMILARITY_MODE", None)
    else:
        os.environ["L1B_SIMILARITY_MODE"] = mode


def _hash_backend():
    """强制 embedding_store 用默认 hash 后端（测试确定性·不触真语义 API）。"""
    es._BACKEND = None
    os.environ.pop("EMBED_BACKEND", None)
    os.environ.pop("GEN_EMBED_ACTIVE", None)


# ════════════════════════════════════════════════════════════════
# [A] 模式解析 _mode()
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_shadow():
    """L1B_SIMILARITY_MODE 默认 shadow · 非法值回退 shadow · {active,off} 原样。"""
    try:
        _set_mode(None)
        assert ss._mode() == "shadow"
        _set_mode("active")
        assert ss._mode() == "active"
        _set_mode("off")
        assert ss._mode() == "off"
        _set_mode("ACTIVE")          # 大小写归一
        assert ss._mode() == "active"
        _set_mode("garbage")         # 非法 → shadow
        assert ss._mode() == "shadow"
    finally:
        _set_mode(None)


# ════════════════════════════════════════════════════════════════
# [B] 纯函数：centroid / mean_centroid / cjk / strip_changes
# ════════════════════════════════════════════════════════════════

def test_B_strip_changes_idempotent():
    """剥离 CHANGES 段·无分隔符时原样返回（幂等）。"""
    assert ss._strip_changes("正文内容。") == "正文内容。"
    assert ss._strip_changes("正文。\n---CHANGES---\n{json}") == "正文。"
    assert ss._strip_changes("正文。\n---CHANGES_FACTUAL---\nx") == "正文。"


def test_B_cjk_count():
    assert ss._cjk_count("方源走出魔窟abc123") == 6
    assert ss._cjk_count("") == 0
    assert ss._cjk_count("pure ascii") == 0


def test_B_text_centroid_normalized():
    """centroid 输出 L2 归一（模长≈1）· 空文本 → None。"""
    _hash_backend()
    try:
        c = ss._text_centroid("方源乖乖地交出春秋蝉，我给你个痛快。" * 30)
        assert c is not None
        import math
        assert abs(math.sqrt(sum(v * v for v in c)) - 1.0) < 1e-6, "centroid 应 L2 归一"
        assert ss._text_centroid("") is None
        assert ss._text_centroid("   ") is None
    finally:
        es._BACKEND = None


def test_B_mean_centroid_normalized_and_empty():
    """多 centroid 求均值后再归一 · 空集 → None。"""
    import math
    assert ss._mean_centroid([]) is None
    assert ss._mean_centroid([None]) is None
    c = ss._mean_centroid([[1.0, 0.0], [0.0, 1.0]])
    assert abs(math.sqrt(sum(v * v for v in c)) - 1.0) < 1e-6


# ════════════════════════════════════════════════════════════════
# [C] 作者原文池定位 resolve_author_pool（降级链）
# ════════════════════════════════════════════════════════════════

def test_C_resolve_explicit_pool():
    """① 显式 --author-pool：存在目录直接返回·不存在 → None。"""
    if not _GU.is_dir():
        return
    assert ss.resolve_author_pool(Path(tempfile.gettempdir()), explicit=str(_GU)) == _GU
    assert ss.resolve_author_pool(Path(tempfile.gettempdir()), explicit=str(_GU / "不存在")) is None


def test_C_resolve_via_user_pref():
    """② 项目 用户偏好.json.style_baseline_data_path → 同目录 原文/。"""
    if not _JING.is_dir():
        return
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = proj / "_数据库"
        db.mkdir(parents=True)
        # 用真实仓库根下的相对路径（_workspace_root 会找到真 workspace/styles）
        (db / "用户偏好.json").write_text(json.dumps({
            "style_baseline_data_path": "workspace/styles/惊悚乐园/作者风格_FINAL.json"
        }, ensure_ascii=False), encoding="utf-8")
        # proj 在临时目录·向上找不到 workspace → _workspace_root 退到 scanner 仓库根
        pool = ss.resolve_author_pool(proj)
        assert pool == _JING, pool


def test_C_resolve_none_when_no_clue():
    """无任何线索 → None（顾问制·不报错只跳过）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        assert ss.resolve_author_pool(proj) is None


# ════════════════════════════════════════════════════════════════
# [D] baseline 构建（真原文·自适应阈值从作者分布涌现）
# ════════════════════════════════════════════════════════════════

def test_D_build_baseline_gu_zhenren():
    """蛊真人原文池建 baseline：n>=3 章·有 center·mean−2σ 阈值 < mean（自适应标定）。"""
    if not _GU.is_dir():
        return
    _hash_backend()
    try:
        with tempfile.TemporaryDirectory() as d:
            rec = ss.build_baseline(Path(d), author_pool=_GU)
            assert "_skip" not in rec, rec
            assert rec["n_chapters_used"] >= ss._MIN_BASELINE_CHAPTERS
            assert len(rec["center_embedding"]) == 384  # hash dim
            ssim = rec["self_similarity"]
            assert ssim["min"] <= ssim["mean"] <= ssim["max"]
            assert ssim["std"] >= 0
            # 阈值 = mean − 2σ < mean（OOD 下界·从作者样本涌现）
            assert rec["drift_threshold"] < ssim["mean"]
            assert abs(rec["drift_threshold"] - (ssim["mean"] - 2.0 * ssim["std"])) < 1e-3
            assert rec["method"] == "hash"
            # 持久化文件落地
            assert Path(rec["_path"]).exists()
    finally:
        es._BACKEND = None


def test_D_build_baseline_insufficient_samples_skips():
    """章节数 < 3 → _skip（样本不足不强建·顾问制）。"""
    _hash_backend()
    try:
        with tempfile.TemporaryDirectory() as d:
            pool = Path(d) / "pool"
            pool.mkdir()
            (pool / "第001章.txt").write_text("方源走出魔窟。" * 100, encoding="utf-8")
            (pool / "第002章.txt").write_text("李青染望着远方。" * 100, encoding="utf-8")
            rec = ss.build_baseline(Path(d) / "proj", author_pool=pool)
            assert "_skip" in rec, rec
    finally:
        es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [E] drift 评分：in-dist 不漂 / OOD 漂（真原文金标准）
# ════════════════════════════════════════════════════════════════

def _scan_with_pool(draft_path, pool, mode):
    """在临时项目里用指定 pool 建 baseline 后 scan draft。"""
    _hash_backend()
    _set_mode(mode)
    d = tempfile.mkdtemp()
    proj = Path(d)
    (proj / "_数据库").mkdir(parents=True)
    return ss.scan(proj, Path(draft_path), author_pool=pool)


def test_E_in_distribution_held_out_chapter_not_drift():
    """金标准①：蛊真人 held-out 章（第045章·不在 baseline 前 40 章）相对蛊真人 baseline
    sim > 阈值 → 非 OOD（同作者章节落在 mean−2σ 之上）。"""
    held = _GU / "第045章.txt"
    if not (held.exists() and _GU.is_dir()):
        return
    try:
        rep = _scan_with_pool(held, _GU, "active")
        assert rep.get("is_ood_drift") is False, (rep.get("cluster_similarity"), rep.get("drift_threshold"))
        assert rep["cluster_similarity"] > rep["drift_threshold"]
        assert rep["warning"] is None  # 不漂 → 无 advisory
    finally:
        _set_mode(None); es._BACKEND = None


def test_E_out_of_distribution_other_author_drifts_active():
    """金标准②：惊悚乐园章（异作者风格）相对蛊真人 baseline → OOD 漂移命中。
    active 模式下 warning 非空·severity=warning（作为 advisory 待裁决项）。"""
    other = _JING / "第001章.txt"
    if not (other.exists() and _GU.is_dir()):
        return
    try:
        rep = _scan_with_pool(other, _GU, "active")
        assert rep.get("is_ood_drift") is True, (rep.get("cluster_similarity"), rep.get("drift_threshold"))
        assert rep["cluster_similarity"] < rep["drift_threshold"]
        assert rep["warning"] is not None and "OOD" in rep["warning"]
        assert rep["severity"] == "warning"
        assert rep["gate_level"] == "advisory"  # 永远 advisory
        assert rep["code"] == "STYLE_SIMILARITY_DRIFT"
    finally:
        _set_mode(None); es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [F] 影子并行：shadow 默认零回归（OOD 也不出 advisory）
# ════════════════════════════════════════════════════════════════

def test_F_shadow_default_no_advisory_even_on_ood():
    """核心零回归：shadow（默认）下即便 OOD 漂移命中，warning 仍 null·severity=info·
    只把诊断写进 shadow_note（不改 audit_hub 判决）。"""
    other = _JING / "第001章.txt"
    if not (other.exists() and _GU.is_dir()):
        return
    try:
        rep = _scan_with_pool(other, _GU, "shadow")
        assert rep["mode"] == "shadow"
        assert rep.get("is_ood_drift") is True       # 仍检出漂移
        assert rep["warning"] is None                # 但不上报（零回归核心）
        assert rep["severity"] == "info"
        assert "shadow_note" in rep                  # 诊断仅记录
    finally:
        _set_mode(None); es._BACKEND = None


def test_F_off_mode_skips_entirely():
    """off：完全跳过（连 embedding 都不算）·warning null·_skip 标记。"""
    other = _JING / "第001章.txt"
    if not (other.exists() and _GU.is_dir()):
        return
    try:
        rep = _scan_with_pool(other, _GU, "off")
        assert rep["mode"] == "off"
        assert rep["warning"] is None
        assert rep.get("_skip") == "mode=off"
        assert "cluster_similarity" not in rep       # 没算
    finally:
        _set_mode(None); es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [G] 维度混用防护（范式同 compute_character_drift）
# ════════════════════════════════════════════════════════════════

def test_G_method_mismatch_skips_not_false_drift():
    """baseline method ≠ 当前后端 → 不误报 drift·提示重建（维度变了 cosine 会假漂）。"""
    if not (_JING.is_dir() and _GU.is_dir()):
        return
    _hash_backend()
    _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "_数据库").mkdir(parents=True)
            ss.build_baseline(proj, author_pool=_GU)   # 以 hash 建
            # 篡改 baseline method 模拟后端切换
            bp = ss._baseline_path(proj)
            rec = json.loads(bp.read_text(encoding="utf-8"))
            rec["method"] = "api:fake-1024"
            bp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            rep = ss.scan(proj, _JING / "第001章.txt", author_pool=_GU)
            assert rep["warning"] is None                       # 不误报
            assert "baseline_skip" in rep and "重建" in rep["baseline_skip"]
            assert "is_ood_drift" not in rep                    # 没进 drift 判定
    finally:
        _set_mode(None); es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [H] 顾问非法官：code 绝不进 HARD_GATE_CODES
# ════════════════════════════════════════════════════════════════

def test_H_code_never_in_hard_gate_codes():
    """北极星⑤ + 共同纪律：STYLE_SIMILARITY_DRIFT 必须是 advisory·绝不在 HARD_GATE_CODES。"""
    import audit_hub
    assert "STYLE_SIMILARITY_DRIFT" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("STYLE_SIMILARITY_DRIFT", "error") == "advisory"
    assert ss.ISSUE_CODE == "STYLE_SIMILARITY_DRIFT"


def test_H_backend_note_on_hash():
    """hash 后端报告透出占位提示（裁决者据此判可信度·不强制下载 bge）。"""
    if not (_JING.is_dir() and _GU.is_dir()):
        return
    try:
        rep = _scan_with_pool(_JING / "第001章.txt", _GU, "active")
        assert rep["backend"] == "hash"
        assert "backend_note" in rep and "占位" in rep["backend_note"]
    finally:
        _set_mode(None); es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [I] 鲁棒性：草稿缺失 / 太短
# ════════════════════════════════════════════════════════════════

def test_I_missing_draft_fatal():
    """草稿文件缺失 → _fatal（与兄弟 scanner 一致·缺失≠干净通过）。"""
    if not _GU.is_dir():
        return
    try:
        _hash_backend(); _set_mode("active")
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "_数据库").mkdir(parents=True)
            rep = ss.scan(proj, proj / "no_such.txt", author_pool=_GU)
            assert "_fatal" in rep
    finally:
        _set_mode(None); es._BACKEND = None


def test_I_too_short_draft_skips():
    """草稿 CJK < 200 → _skip（样本不足不评）。"""
    _hash_backend(); _set_mode("active")
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "_数据库").mkdir(parents=True)
            short = proj / "short.txt"
            short.write_text("很短。", encoding="utf-8")
            rep = ss.scan(proj, short, author_pool=_GU if _GU.is_dir() else None)
            assert rep["warning"] is None
            assert "_skip" in rep
    finally:
        _set_mode(None); es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [J] mstyle 真风格后端 dispatch + 维度（2026-05-31 · 真风格 embedding 接入）
# ════════════════════════════════════════════════════════════════

def _mstyle_available():
    """mstyle 是否可用（装了 sentence-transformers + 模型已缓存/可下载）。
    任何环节失败 → False（测试 skip · 不挂）。
    ⚠️ 不重置 es._MSTYLE_MODEL（让模型对象跨 test 缓存·避免重复 30s 模型加载拖慢 run_tests）。"""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    es._BACKEND = None
    os.environ["EMBED_BACKEND"] = "mstyle"
    try:
        # 探测：method 必须报 mstyle，且能真出 768 维（degrade 到 hash=384 视为不可用）
        if not es.embedding_method().startswith("mstyle:"):
            return False
        v = es.compute_embedding("方源乖乖地交出春秋蝉。" * 5)
        return v is not None and len(v) == 768
    except Exception:
        return False
    finally:
        es._BACKEND = None
        os.environ.pop("EMBED_BACKEND", None)


def test_J_mstyle_backend_dispatch_method_id():
    """EMBED_BACKEND=mstyle → embedding_method() 报 mstyle:StyleDistance/mstyledistance。
    未装包时 degrade 到 hash（dispatch 逻辑仍正确·skip 不挂）。"""
    es._BACKEND = None
    os.environ["EMBED_BACKEND"] = "mstyle"
    try:
        m = es.embedding_method()
        try:
            import sentence_transformers  # noqa: F401
            assert m == "mstyle:StyleDistance/mstyledistance", m
            assert es._detect_backend()[1] == 768, "mstyle 维度应为 768"
        except ImportError:
            assert m == "hash", "未装 sentence-transformers 应 degrade hash（零回归）"
    finally:
        es._BACKEND = None
        os.environ.pop("EMBED_BACKEND", None)


def test_J_mstyle_default_still_hash_zero_regression():
    """关键纪律：不设 EMBED_BACKEND → 默认仍 hash（零回归·即使环境装了 mstyle 也不静默切）。"""
    es._BACKEND = None
    os.environ.pop("EMBED_BACKEND", None)
    os.environ.pop("GEN_EMBED_ACTIVE", None)
    try:
        assert es.embedding_method() == "hash"
        assert es._detect_backend()[1] == 384
    finally:
        es._BACKEND = None


def test_J_mstyle_embedding_768_normalized():
    """mstyle 实跑：768 维 · L2 归一（normalize_embeddings=True）。模型不可用 → skip。"""
    if not _mstyle_available():
        return  # 环境无 torch / 无 HF 网络 → skip（不挂）
    import math
    es._BACKEND = None
    os.environ["EMBED_BACKEND"] = "mstyle"   # 不重置 _MSTYLE_MODEL（复用缓存模型）
    try:
        v = es.compute_embedding("方源走出魔窟，眼中再无半分留恋，转身踏入风雪。")
        assert len(v) == 768
        # normalize_embeddings=True · float32 累加在 768 维上有 ~2e-3 误差 → 容差放 5e-3
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 5e-3, "mstyle 输出应近似 L2 归一"
    finally:
        es._BACKEND = None
        os.environ.pop("EMBED_BACKEND", None)


# ════════════════════════════════════════════════════════════════
# [K] 金标准实测：mstyle vs hash vs bge 中文网文长文区分力（2026-05-31 · 诚实结论）
# ════════════════════════════════════════════════════════════════
#
# 🔴 实测结论（蛊真人 in-dist ch21-30 vs 惊悚乐园 OOD ch1-10 · baseline=蛊真人 ch1-20 ·
#    系统真实 pipeline = chunk-500 + centroid 均值 + cosine）：
#
#   backend |  dim | self_std | in-dist mean | OOD mean |  AUC  | OOD recall | in-dist FP
#   --------+------+----------+--------------+----------+-------+------------+-----------
#   hash    |  384 |  0.0051  |    0.973     |  0.682   | 1.00  |    1.00    |   0.20
#   mstyle  |  768 |  0.0004  |    0.9997    |  0.9996  | 0.64  |    0.10    |   0.00
#   bge     |  512 |  0.0250  |    0.897     |  0.753   | 1.00  |    1.00    |   0.20
#
#   → mstyle **没有拉开差距，反而塌缩**：整章 chunk 均值后 768-dim 向量近乎饱和
#     （in-dist 0.9997 ≈ OOD 0.9996，σ=0.0004），AUC=0.64 < hash/bge 的 1.00。
#     mStyleDistance 论文只验**句子级**风格对比；在「整章 chunk-均值-centroid」这条系统
#     既有 pipeline 上，风格信号被均值洗没（各 chunk 共有的主成分主导 → 余弦全贴 1.0）。
#   → 决策（先实证后切默认）：**默认保持 hash（零回归）·mstyle 接线就绪走 opt-in
#     EMBED_BACKEND=mstyle**。绝不为 SOTA 而 SOTA 硬切。
#   → 复跑实测：见本仓 commit 说明 / 临时脚本（baseline 20 章 + in/ood 各 10 章，CPU ~50 分钟）。
#
# 下面只保留**快速确定性**校验（不在 run_tests.py 里重跑 50 分钟 mstyle 推理）：
#   · hash pipeline 在金标准上确实可分（证明金标准 + pipeline 本身有效）。
#   · mstyle 重推理实测留作 opt-in 手测（_mstyle_available 时才跑·默认 run_tests 不触发）。


def test_K_golden_standard_hash_pipeline_separates_authors():
    """金标准 + 系统 pipeline 自洽性（快·hash 确定性）：蛊真人(in-dist) vs 惊悚乐园(OOD)
    在 hash 后端下 in-dist 相似度 > OOD 相似度（AUC 高于随机）——证明金标准数据 + chunk
    centroid pipeline 本身能区分异作者（mstyle 的塌缩是模型在该 pipeline 的局限·非数据问题）。"""
    if not (_GU.is_dir() and _JING.is_dir()):
        return
    _hash_backend()
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "_数据库").mkdir(parents=True)
            ss.build_baseline(proj, author_pool=_GU)
            rep_in = ss.scan(proj, _GU / "第045章.txt", author_pool=_GU)
            rep_ood = ss.scan(proj, _JING / "第001章.txt", author_pool=_GU)
            # in-dist 章相似度应高于 OOD 章（区分力 > 随机）
            assert rep_in["cluster_similarity"] > rep_ood["cluster_similarity"], (
                rep_in["cluster_similarity"], rep_ood["cluster_similarity"])
    finally:
        es._BACKEND = None


def test_K_mstyle_optin_pipeline_runs_768dim():
    """mstyle opt-in 端到端跑通（仅模型可用时·不可用 skip 不挂）：跑系统真实 pipeline
    （_text_centroid chunk + 均值 + cosine）。**不断言 mstyle 优于 hash**——实测已证其在整章
    均值上塌缩（见上方表）·这里只快验：① 维度真 768 ② centroid/cosine 计算不崩、值合法。
    用短小片段（非整章·避免 run_tests 触发分钟级 CPU 推理）。"""
    if not _mstyle_available():
        return  # 无 torch / 无 HF 网络 → skip
    es._BACKEND = None
    os.environ["EMBED_BACKEND"] = "mstyle"   # 不重置 _MSTYLE_MODEL（复用缓存模型）
    try:
        # 两段风格迥异的短文（设定厚重 vs 黑色幽默口语）·走 scanner 的 _text_centroid
        c_a = ss._text_centroid("方源缓缓踏出魔窟，神色冷峻，再无半分留恋。" * 4)
        c_b = ss._text_centroid("哥们儿你这操作也太骚了吧，笑死我了哈哈哈哈。" * 4)
        assert c_a is not None and len(c_a) == 768, "mstyle centroid 维度应 768"
        assert c_b is not None and len(c_b) == 768
        sim = es.cosine_similarity(c_a, c_b)
        assert -1.0001 <= sim <= 1.0001, sim   # 余弦合法（不断言区分力·实测整章会塌缩）
    finally:
        es._BACKEND = None
        os.environ.pop("EMBED_BACKEND", None)


if __name__ == "__main__":
    import inspect
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and inspect.isfunction(f)]
    passed = failed = 0
    for n, f in fns:
        try:
            f()
            passed += 1
            print(f"  [PASS] {n}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERR ] {n}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed / {len(fns)} total")
    sys.exit(1 if failed else 0)
