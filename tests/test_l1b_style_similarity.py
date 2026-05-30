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
