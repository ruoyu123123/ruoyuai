# -*- coding: utf-8 -*-
"""style_similarity_scanner.py 专属回归测试 — 聚焦尚未被 test_l1b_style_similarity 覆盖的核心确定性逻辑。

被测脚本：core/scripts/style_similarity_scanner.py（L1b 风格相似度漂移 scanner · advisory）。

【与已有间接覆盖的边界】
  已有 tests/test_l1b_style_similarity.py 覆盖：_mode 解析、_strip_changes/_cjk_count/_text_centroid/
  _mean_centroid 纯函数、resolve_author_pool 路径①(显式)②(用户偏好)、build_baseline(真蛊真人原文池)、
  drift 真原文金标准(in-dist/OOD)、shadow/off、method 不匹配防护、HARD_GATE 排除、缺失/太短草稿——
  但全部是 **in-process 函数调用**，且 baseline/drift 用例 **依赖真 `蛊真人` 原文池**（本环境缺失则自动 return 跳过）。
  本文件补 **尚未覆盖** 的部分，且 **不依赖任何真原文池**（全合成 hash 后端数据·确定性·零外部依赖）：
    · _list_author_chapters：第NNN章.txt 过滤 + 章号数值排序 + limit 截断（已有未测）
    · _baseline_path：.embeddings 目录自动创建（已有未测）
    · _load_baseline：缺失/损坏 JSON → None 容错（已有未测）
    · resolve_author_pool 路径③：作者风格.json._meta.work → workspace/styles/<work>/原文（已有仅测①②）
    · _embedding_dim：透出当前后端维度（已有未测）
    · 合成数据的自适应阈值 = mean − 2σ + drift 判定（已有同语义但绑死真原文池·此处脱钩）
    · CLI main() 退出码：rebuild/active-OOD(1)/shadow(0)/off(0)/缺失草稿(2)/参数不足(2)（已有 **完全未测 CLI**）

只测确定性逻辑（hash 后端 = md5 ngram 袋·纯本地·无 LLM/网络/torch）。CLI 走 subprocess 跑真退出码。
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import embedding_store as es  # noqa: E402
import style_similarity_scanner as ss  # noqa: E402

_TARGET = _SCRIPTS / "style_similarity_scanner.py"


# ════════════════════════════════════════════════════════════════
# 测试夹具：强制 hash 后端 + 合成作者原文池（确定性·零外部依赖）
# ════════════════════════════════════════════════════════════════

def _force_hash_backend():
    """强制 embedding_store 用默认 hash 后端（md5 ngram·纯本地·确定性）。"""
    es._BACKEND = None
    os.environ.pop("EMBED_BACKEND", None)


# 5 句风格内聚但 ngram 略有差异的「作者」语料 → 章节自相似 σ>0（能算自适应阈值）
_AUTHOR_SENTS = [
    "方源走出魔窟，眼中再无半分留恋。",
    "他转身踏入风雪，神色冷峻无比。",
    "春秋蝉在袖中轻鸣，灵力缓缓流转。",
    "魔道修士对视一眼，杀意凛然升腾。",
    "古月方源抬手，蛊虫翻涌如潮水。",
]
# 与作者语料 ngram 几乎不重叠的「异作者」文本（现代口语喜剧）→ OOD
_OOD_TEXT = "哥们儿你这操作也太骚了吧，笑死我了哈哈哈，咱们待会去吃顿火锅怎么样？"


def _make_author_pool(root: Path, n: int = 5) -> Path:
    """造一个含 n 个 第NNN章.txt 的合成作者原文池（每章 CJK 远 > 200）。
    各章是句子的轮转拼接 → 章节间 ngram 略不同 → 自相似 std>0。"""
    pool = root / "pool"
    pool.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        seq = _AUTHOR_SENTS[(i - 1) % len(_AUTHOR_SENTS):] + _AUTHOR_SENTS[:(i - 1) % len(_AUTHOR_SENTS)]
        (pool / f"第{i:03d}章.txt").write_text("".join(seq) * 40, encoding="utf-8")
    return pool


def _author_indist_text() -> str:
    """落在作者分布内的草稿（用同一套作者语料）。"""
    return "".join(_AUTHOR_SENTS) * 40


# ════════════════════════════════════════════════════════════════
# [1] _list_author_chapters：文件过滤 + 数值章号排序 + limit 截断
# ════════════════════════════════════════════════════════════════

def test_list_author_chapters_filters_sorts_and_limits():
    """只收 第NNN章.txt · 按章号 **数值** 升序（非字典序）· limit 截断前 N 章。"""
    with tempfile.TemporaryDirectory() as d:
        pool = Path(d)
        # 故意乱序写入，且包含非匹配文件
        for name in ("第010章.txt", "第002章.txt", "第100章.txt", "第001章.txt"):
            (pool / name).write_text("正文", encoding="utf-8")
        (pool / "目录.txt").write_text("不匹配", encoding="utf-8")
        (pool / "作者风格.json").write_text("{}", encoding="utf-8")

        files = ss._list_author_chapters(pool)
        names = [f.name for f in files]
        # 非匹配文件被剔除
        assert names == ["第001章.txt", "第002章.txt", "第010章.txt", "第100章.txt"], names
        # 数值排序而非字典序（"第100章" 不会排到 "第002章" 前面）
        assert names.index("第010章.txt") < names.index("第100章.txt")

        # limit 截断：取前 2 章（仍按章号升序取最小两章）
        limited = ss._list_author_chapters(pool, limit=2)
        assert [f.name for f in limited] == ["第001章.txt", "第002章.txt"], limited


# ════════════════════════════════════════════════════════════════
# [2] _baseline_path：.embeddings 目录自动创建
# ════════════════════════════════════════════════════════════════

def test_baseline_path_creates_embeddings_dir():
    """_baseline_path 触发即 mkdir(parents=True)·返回 _数据库/.embeddings/<file>.json。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        bp = ss._baseline_path(proj)
        assert bp.parent.is_dir(), "应自动创建 .embeddings 目录"
        assert bp.parent.name == ".embeddings"
        assert bp.parent.parent.name == "_数据库"
        assert bp.name == "style_similarity_baseline.json"


# ════════════════════════════════════════════════════════════════
# [3] _load_baseline：缺失 / 损坏 JSON → None（容错·不抛）
# ════════════════════════════════════════════════════════════════

def test_load_baseline_missing_and_corrupt_return_none():
    """无文件 → None；文件存在但非法 JSON → None（顾问制·读不出不报错）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        # 尚未写任何 baseline
        assert ss._load_baseline(proj) is None
        # 写入损坏 JSON
        bp = ss._baseline_path(proj)
        bp.write_text("{ this is not valid json", encoding="utf-8")
        assert ss._load_baseline(proj) is None


def test_load_baseline_roundtrip_after_build():
    """build_baseline 落盘后 _load_baseline 能读回同一份 record（持久化往返）。"""
    _force_hash_backend()
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pool = _make_author_pool(root)
            proj = root / "proj"
            rec = ss.build_baseline(proj, author_pool=pool)
            assert "_skip" not in rec, rec
            loaded = ss._load_baseline(proj)
            assert loaded is not None
            assert loaded["method"] == rec["method"]
            assert loaded["n_chapters_used"] == rec["n_chapters_used"]
            assert loaded["drift_threshold"] == rec["drift_threshold"]
            assert loaded["center_embedding"] == rec["center_embedding"]
    finally:
        es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [4] resolve_author_pool 路径③：作者风格.json._meta.work（已有仅测①②）
# ════════════════════════════════════════════════════════════════

def test_resolve_author_pool_via_style_meta_work():
    """③ 作者风格.json._meta.work → workspace/styles/<work>/原文。
    用真实存在的风格目录验证降级链命中（无则 return 跳过·不挂）。"""
    # 找一个真实存在 原文/ 的风格目录当 work
    styles_root = _ROOT / "workspace" / "styles"
    work = None
    if styles_root.is_dir():
        for cand in sorted(styles_root.iterdir()):
            if (cand / "原文").is_dir():
                work = cand.name
                break
    if work is None:
        return  # 本环境无任何带 原文/ 的风格库 → 跳过
    expected = styles_root / work / "原文"

    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True)
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"_meta": {"work": work}}, ensure_ascii=False), encoding="utf-8")
        got = ss.resolve_author_pool(proj)
        assert got == expected, (got, expected)


def test_resolve_author_pool_explicit_nonexistent_dir_none():
    """① 显式 --author-pool 指向不存在目录 → None（不报错）。"""
    with tempfile.TemporaryDirectory() as d:
        bogus = str(Path(d) / "does_not_exist")
        assert ss.resolve_author_pool(Path(d), explicit=bogus) is None


# ════════════════════════════════════════════════════════════════
# [5] _embedding_dim：透出当前后端维度（hash=384）
# ════════════════════════════════════════════════════════════════

def test_embedding_dim_reports_hash_384():
    """默认 hash 后端 → _embedding_dim() == 384（与 embedding_store._detect_backend 一致）。"""
    _force_hash_backend()
    try:
        assert ss._embedding_dim() == 384
        assert ss._embedding_dim() == es._detect_backend()[1]
    finally:
        es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [6] 合成数据自适应阈值 + drift 判定（脱离真原文池·纯确定性）
# ════════════════════════════════════════════════════════════════

def test_build_baseline_synthetic_adaptive_threshold():
    """合成池建 baseline：n 章·center 维度=384·阈值 == mean − 2σ 且 < mean（自适应标定）。"""
    _force_hash_backend()
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pool = _make_author_pool(root, n=5)
            rec = ss.build_baseline(root / "proj", author_pool=pool)
            assert "_skip" not in rec, rec
            assert rec["n_chapters_used"] == 5
            assert rec["embedding_dim"] == 384
            assert len(rec["center_embedding"]) == 384
            ssim = rec["self_similarity"]
            assert ssim["min"] <= ssim["mean"] <= ssim["max"]
            assert ssim["std"] >= 0.0
            # 阈值精确等于 mean − 2σ（SIGMA_K=2）
            assert abs(rec["drift_threshold"] - (ssim["mean"] - ss.SIGMA_K * ssim["std"])) < 1e-3
            assert rec["sigma_k"] == 2.0
            assert rec["method"] == "hash"
    finally:
        es._BACKEND = None


def test_scan_indist_vs_ood_active():
    """active 模式：in-dist 草稿 sim>阈值 → 非漂·warning=None；
    OOD 草稿 sim<阈值 → is_ood_drift=True·warning 非空·severity=warning·gate_level 永久 advisory。"""
    _force_hash_backend()
    os.environ["L1B_SIMILARITY_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pool = _make_author_pool(root, n=5)
            proj = root / "proj"
            (proj / "_数据库").mkdir(parents=True)
            ss.build_baseline(proj, author_pool=pool)

            # in-dist
            indist = proj / "indist.txt"
            indist.write_text(_author_indist_text(), encoding="utf-8")
            rep_in = ss.scan(proj, indist, author_pool=pool)
            assert rep_in["is_ood_drift"] is False, rep_in.get("cluster_similarity")
            assert rep_in["cluster_similarity"] > rep_in["drift_threshold"]
            assert rep_in["warning"] is None

            # OOD
            ood = proj / "ood.txt"
            ood.write_text(_OOD_TEXT * 200, encoding="utf-8")
            rep_ood = ss.scan(proj, ood, author_pool=pool)
            assert rep_ood["is_ood_drift"] is True, rep_ood.get("cluster_similarity")
            assert rep_ood["cluster_similarity"] < rep_ood["drift_threshold"]
            assert rep_ood["warning"] is not None and "OOD" in rep_ood["warning"]
            assert rep_ood["severity"] == "warning"
            assert rep_ood["gate_level"] == "advisory"  # 北极星⑤ 永久 advisory
            assert rep_ood["code"] == "STYLE_SIMILARITY_DRIFT"
    finally:
        os.environ.pop("L1B_SIMILARITY_MODE", None)
        es._BACKEND = None


def test_scan_shadow_suppresses_ood_advisory():
    """shadow（默认）：OOD 仍 is_ood_drift=True 但 warning=None·severity=info·shadow_note 记诊断
    （零回归核心·audit_hub 收不到 issue）。"""
    _force_hash_backend()
    os.environ["L1B_SIMILARITY_MODE"] = "shadow"
    try:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pool = _make_author_pool(root, n=5)
            proj = root / "proj"
            (proj / "_数据库").mkdir(parents=True)
            ss.build_baseline(proj, author_pool=pool)
            ood = proj / "ood.txt"
            ood.write_text(_OOD_TEXT * 200, encoding="utf-8")
            rep = ss.scan(proj, ood, author_pool=pool)
            assert rep["mode"] == "shadow"
            assert rep["is_ood_drift"] is True   # 仍检出漂移
            assert rep["warning"] is None        # 但不上报
            assert rep["severity"] == "info"
            assert "shadow_note" in rep
    finally:
        os.environ.pop("L1B_SIMILARITY_MODE", None)
        es._BACKEND = None


# ════════════════════════════════════════════════════════════════
# [7] CLI main() 退出码（真 subprocess · 已有完全未测 CLI）
# ════════════════════════════════════════════════════════════════

def _run_cli(args, mode=None):
    """跑真 CLI（utf-8 解码·Windows CJK 输出必须显式 utf-8 否则 reader 线程 GBK 解码崩）。
    返回 CompletedProcess。"""
    env = dict(os.environ)
    env.pop("EMBED_BACKEND", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if mode is not None:
        env["L1B_SIMILARITY_MODE"] = mode
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, encoding="utf-8", env=env,
    )


def test_cli_exit_codes_end_to_end():
    """CLI 退出码契约（真 argparse + 真 build/scan + 真落盘）：
      rebuild → 0
      active 模式 OOD 命中 → 1（待裁决项）
      shadow 模式 OOD → 0（零回归）
      off 模式 → 0
      缺失草稿 → 2（_fatal·缺失≠干净通过）
      参数不足(<2) → 2
      active 模式 in-dist → 0
    """
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        pool = _make_author_pool(root, n=5)
        proj = root / "proj"
        (proj / "_数据库").mkdir(parents=True)

        # rebuild
        r = _run_cli([str(proj), "rebuild", "--author-pool", str(pool)], mode="active")
        assert r.returncode == 0, (r.returncode, r.stderr)
        out = json.loads(r.stdout)
        assert out["n_chapters_used"] == 5
        assert "center_embedding" not in out  # 摘要不含巨大向量

        # OOD 草稿
        ood = proj / "ood.txt"
        ood.write_text(_OOD_TEXT * 200, encoding="utf-8")
        assert _run_cli([str(proj), str(ood), "--author-pool", str(pool)], mode="active").returncode == 1
        assert _run_cli([str(proj), str(ood), "--author-pool", str(pool)], mode="shadow").returncode == 0
        assert _run_cli([str(proj), str(ood), "--author-pool", str(pool)], mode="off").returncode == 0

        # 缺失草稿 → 2
        assert _run_cli([str(proj), str(proj / "nope.txt"), "--author-pool", str(pool)], mode="active").returncode == 2

        # 参数不足 → 2
        assert _run_cli([str(proj)]).returncode == 2

        # in-dist 草稿 active → 0
        indist = proj / "indist.txt"
        indist.write_text(_author_indist_text(), encoding="utf-8")
        assert _run_cli([str(proj), str(indist), "--author-pool", str(pool)], mode="active").returncode == 0


# ════════════════════════════════════════════════════════════════
# 独立运行（与仓库约定一致）
# ════════════════════════════════════════════════════════════════

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
