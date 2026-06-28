# -*- coding: utf-8 -*-
"""motif_recurrence_ledger.py 专属回归测试 (R8 W4 Batch-G·L20·2026-06-20)。

零依赖·确定性·零 LLM/零联网。覆盖核心 6 分支：
  ① clusters≥3 含 dormant motif → MOTIF_DORMANT advisory
  ② over_saturated (N>7 + coverage>80%) → MOTIF_OVER_SATURATED
  ③ payoff_due (dormant + foreshadowing/promise 标记) → MOTIF_PAYOFF_DUE
  ④ new_seed 过多 (≥SEED_PROLIFERATION_FLOOR) → MOTIF_SEED_PROLIFERATION
  ⑤ shadow 只记不判 / off → 直接退出
  ⑥ 作者档 author_motif_signature 扩 places/catchphrase 词典
+ 辅助：gini_coefficient / build_ledger / scan_motifs_in_text / _build_extra_pattern。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import motif_recurrence_ledger as mod  # noqa: E402

_TARGET = _SCRIPTS / "motif_recurrence_ledger.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("MOTIF_RECURRENCE_MODE", None)
    else:
        os.environ["MOTIF_RECURRENCE_MODE"] = m


def _mk_project_with_summary(clusters_data: list, author_sig: dict | None = None,
                             foreshadowing_targets: list | None = None) -> Path:
    """造带 故事块摘要.json 的项目。clusters_data: [(cluster_id, scope_summary), ...]"""
    proj = Path(tempfile.mkdtemp())
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)

    clusters_list = []
    for i, (cid, scope) in enumerate(clusters_data):
        cluster = {
            "cluster_id": cid,
            "scope_summary": scope,
            "chapter_range": [i * 3 + 1, i * 3 + 3],
            "status": "done",
            "chapters": {str(i * 3 + 1): {"summary": ""}},
        }
        if foreshadowing_targets and i == 0:
            cluster["foreshadowing_planted"] = [{"target": t} for t in foreshadowing_targets]
        clusters_list.append(cluster)
    (db / "故事块摘要.json").write_text(
        json.dumps({"schema_version": "v2.cluster", "clusters": clusters_list},
                   ensure_ascii=False), encoding="utf-8")
    if author_sig is not None:
        (db / "作者风格.json").write_text(
            json.dumps({"author_motif_signature": author_sig},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _run(proj_path, mode="active"):
    return subprocess.run(
        [sys.executable, str(_TARGET), str(proj_path)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "MOTIF_RECURRENCE_MODE": mode, "PYTHONIOENCODING": "utf-8"})


# ── ① dormant 检测 (motif 出现于早期 cluster, 末 3 cluster 缺席) ────────────
def test_dormant_motif_detected():
    # cluster0/1 含「玉佩」, cluster2/3/4 完全缺席 → dormant
    proj = _mk_project_with_summary([
        ("cluster_001", "她把玉佩交给他，玉佩在月光下闪烁。"),
        ("cluster_002", "他又看了玉佩一眼。"),
        ("cluster_003", "山间晨雾弥漫，鸟鸣声起。"),
        ("cluster_004", "他独自一人走在街上。"),
        ("cluster_005", "窗外细雨连绵不绝。"),
    ])
    r = _run(proj, mode="active")
    assert r.returncode == 1, r.stderr  # advisory → exit 1
    snap = json.loads(
        (proj / "_数据库" / ".cross_chapter_scan" / "motif_advisory_snapshot.json")
        .read_text(encoding="utf-8"))
    codes = snap["advisory_codes"]
    assert "MOTIF_DORMANT" in codes
    # 检查 dormant_motifs snapshot
    dormant_terms = {m["term"] for m in snap["dormant_motifs"]}
    assert "玉佩" in dormant_terms


# ── ② over_saturated (N>7 且覆盖率>80%) ────────────────────────────────────
def test_over_saturated_motif_detected():
    # 5 cluster · 每个都含 月光 多次 · N>>7 且 coverage=100%
    proj = _mk_project_with_summary([
        ("cluster_001", "月光月光月光。" * 3),
        ("cluster_002", "月光月光月光。" * 3),
        ("cluster_003", "月光月光月光。" * 3),
        ("cluster_004", "月光月光月光。" * 3),
        ("cluster_005", "月光月光月光。" * 3),
    ])
    r = _run(proj, mode="active")
    assert r.returncode == 1, r.stderr
    snap = json.loads(
        (proj / "_数据库" / ".cross_chapter_scan" / "motif_advisory_snapshot.json")
        .read_text(encoding="utf-8"))
    assert "MOTIF_OVER_SATURATED" in snap["advisory_codes"]
    over_terms = {m["term"] for m in snap["over_saturated_motifs"]}
    assert "月光" in over_terms


# ── ③ payoff_due (dormant + foreshadowing 标记) ─────────────────────────────
def test_payoff_due_motif_detected():
    proj = _mk_project_with_summary(
        clusters_data=[
            ("cluster_001", "她把玉佩交给他。"),
            ("cluster_002", "玉佩再次出现。"),
            ("cluster_003", "山间雾起。"),
            ("cluster_004", "他走在街上。"),
            ("cluster_005", "窗外雨打风吹。"),
        ],
        foreshadowing_targets=["玉佩"],
    )
    r = _run(proj, mode="active")
    assert r.returncode == 1, r.stderr
    snap = json.loads(
        (proj / "_数据库" / ".cross_chapter_scan" / "motif_advisory_snapshot.json")
        .read_text(encoding="utf-8"))
    assert "MOTIF_PAYOFF_DUE" in snap["advisory_codes"]


# ── ④ seed proliferation (太多 motif N=1) ──────────────────────────────────
def test_seed_proliferation_detected():
    # 单 cluster 塞入 7 个不同物件 motif (各 1 次)
    proj = _mk_project_with_summary([
        ("cluster_001", "他拿出玉佩、长剑、银钗、铜镜、油灯、香炉、信物。"),
        ("cluster_002", "她站在门口看着窗外。"),
        ("cluster_003", "窗外的雨依然下着。"),
    ])
    r = _run(proj, mode="active")
    assert r.returncode == 1, r.stderr
    snap = json.loads(
        (proj / "_数据库" / ".cross_chapter_scan" / "motif_advisory_snapshot.json")
        .read_text(encoding="utf-8"))
    assert "MOTIF_SEED_PROLIFERATION" in snap["advisory_codes"]


# ── ⑤ shadow 模式 → exit 0, 但仍写账本 ─────────────────────────────────────
def test_shadow_mode_no_violation_but_writes_ledger():
    proj = _mk_project_with_summary([
        ("cluster_001", "她把玉佩交给他。"),
        ("cluster_002", "他独自走过空巷。"),
        ("cluster_003", "山里下起细雨。"),
        ("cluster_004", "他走在街上。"),
        ("cluster_005", "窗外雨打风吹。"),
    ])
    r = _run(proj, mode="shadow")
    assert r.returncode == 0
    # 账本仍写出
    assert (proj / "_数据库" / ".cross_chapter_scan" / "motif_ledger.json").exists()
    assert (proj / "_数据库" / ".cross_chapter_scan" / "motif_advisory_snapshot.json").exists()


def test_off_mode_skips():
    proj = _mk_project_with_summary([("cluster_001", "玉佩。")])
    r = _run(proj, mode="off")
    assert r.returncode == 0
    assert "[OFF]" in r.stdout


# ── ⑥ author_motif_signature 扩 places/catchphrase 词典 ────────────────────
def test_author_signature_extends_places():
    proj = _mk_project_with_summary(
        clusters_data=[
            ("cluster_001", "他来到杏花村，村口的老槐树仍在。"),
            ("cluster_002", "他又回了杏花村一次。"),
            ("cluster_003", "他离开杏花村去远方。"),
        ],
        author_sig={"places": ["杏花村"]},
    )
    r = _run(proj, mode="active")
    # 必须不崩 · 杏花村 motif 应被识别 (recurring 状态)
    ledger = json.loads((proj / "_数据库" / ".cross_chapter_scan" / "motif_ledger.json").read_text(encoding="utf-8"))
    terms = {m["term"] for m in ledger["motifs"].values()}
    assert "杏花村" in terms


def test_author_signature_extends_catchphrase():
    proj = _mk_project_with_summary(
        clusters_data=[
            ("cluster_001", "他说就这样吧，就这样吧。"),
            ("cluster_002", "她也说就这样吧。"),
            ("cluster_003", "他笑：就这样吧。"),
        ],
        author_sig={"catchphrase": ["就这样吧"]},
    )
    r = _run(proj, mode="active")
    ledger = json.loads((proj / "_数据库" / ".cross_chapter_scan" / "motif_ledger.json").read_text(encoding="utf-8"))
    terms = {m["term"] for m in ledger["motifs"].values()}
    assert "就这样吧" in terms


# ── 边界：cluster 数 < 2 → skip ────────────────────────────────────────────
def test_too_few_clusters_skipped():
    proj = _mk_project_with_summary([("cluster_001", "他拿出玉佩。")])
    r = _run(proj, mode="active")
    assert r.returncode == 0
    assert "cluster 数太少" in r.stdout


def test_no_clusters_skipped():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    # 不写 故事块摘要.json
    r = _run(proj, mode="active")
    assert r.returncode == 0
    assert "无 cluster 记录" in r.stdout


# ── 辅助函数 ────────────────────────────────────────────────────────────────
def test_gini_coefficient_equal():
    assert mod.gini_coefficient([1, 1, 1, 1]) == 0.0


def test_gini_coefficient_concentrated():
    g = mod.gini_coefficient([100, 1, 1, 1])
    assert g > 0.3


def test_gini_coefficient_empty():
    assert mod.gini_coefficient([]) == 0.0
    assert mod.gini_coefficient([5]) == 0.0  # n<2


def test_gini_coefficient_all_zero():
    assert mod.gini_coefficient([0, 0, 0]) == 0.0


def test_scan_motifs_props_dictionary():
    out = mod.scan_motifs_in_text("他拿出玉佩，又掏出银钗。", mod.MOTIF_DICTIONARIES)
    assert "玉佩" in out["props"]
    assert "银钗" in out["props"]


def test_scan_motifs_imagery_dictionary():
    out = mod.scan_motifs_in_text("月光洒下，寒风吹来。", mod.MOTIF_DICTIONARIES)
    assert "月光" in out["imagery"]
    assert "寒风" in out["imagery"]


def test_scan_motifs_sensory_dictionary():
    out = mod.scan_motifs_in_text("檀香袅袅，钟声渐远。", mod.MOTIF_DICTIONARIES)
    assert "檀香" in out["sensory_mark"]
    assert "钟声" in out["sensory_mark"]


def test_build_extra_pattern_none_for_empty():
    assert mod._build_extra_pattern([]) is None
    assert mod._build_extra_pattern(["", "  "]) is None


def test_build_extra_pattern_escapes_metas():
    p = mod._build_extra_pattern(["a.b", "c+d"])
    assert p is not None
    assert p.search("a.b") is not None
    assert p.search("c+d") is not None
    assert p.search("aXb") is None  # 元字符被 escape


def test_mode_default_shadow():
    bak = os.environ.get("MOTIF_RECURRENCE_MODE")
    try:
        os.environ.pop("MOTIF_RECURRENCE_MODE", None)
        assert mod._mode() == "shadow"
        os.environ["MOTIF_RECURRENCE_MODE"] = "bogus"
        assert mod._mode() == "shadow"
    finally:
        if bak is None:
            os.environ.pop("MOTIF_RECURRENCE_MODE", None)
        else:
            os.environ["MOTIF_RECURRENCE_MODE"] = bak


def test_read_author_extra_motifs_no_file():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    assert mod._read_author_extra_motifs(proj) == {}


def test_read_author_extra_motifs_bad_json():
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    (proj / "_数据库" / "作者风格.json").write_text("{ bad", encoding="utf-8")
    assert mod._read_author_extra_motifs(proj) == {}
