"""Character-presence balance uses cluster records and author cluster baselines."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
_TESTS = _ROOT / "tests"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_TESTS))
import cross_cluster_character_presence_balance_aggregate as mod  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_character_presence_balance_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


def _mk_project(clusters, *, genre=None, author_baseline=None):
    proj = Path(tempfile.mkdtemp(prefix="presence_"))
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    write_cluster_summary(proj, clusters)
    if genre:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps({"genre": genre}, ensure_ascii=False), encoding="utf-8")
    if author_baseline:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"character_presence_distribution": author_baseline},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _canonical_cluster_id(cid) -> str:
    """把测试简写（"c1"）归一成账本合同要求的 cluster_XXX 形式。"""
    digits = re.search(r"(\d+)$", str(cid))
    if not digits:
        raise ValueError(f"cid 必须以数字结尾: {cid!r}")
    return f"cluster_{int(digits.group(1)):03d}"


def _cluster(cid, char_counts=None):
    """char_counts: dict[char, count]，写入 cluster 顶层 char_mention_counts。"""
    counts = dict(char_counts or {})
    return cluster_record(
        _canonical_cluster_id(cid),
        char_mention_counts=counts,
        characters=list(counts.keys()),
    )


# ── 单元：gini_coefficient ───────────────────────────────
def test_gini_perfect_equality():
    assert mod.gini_coefficient([1, 1, 1, 1]) == 0.0


def test_gini_perfect_concentration():
    # 一个有值其他全 0 → 接近 1（仅一个非零项 → n=1 → 返回 0；要构造多元素）
    g = mod.gini_coefficient([0.001, 0.001, 0.001, 100])
    assert g > 0.6


def test_gini_zero_or_empty():
    assert mod.gini_coefficient([]) == 0.0
    assert mod.gini_coefficient([0, 0]) == 0.0


# ── 单元：compute_presence_stats ─────────────────────────
def test_presence_stats_accumulates():
    clusters = [
        _cluster("c1", {"alice": 5, "bob": 3}),
        _cluster("c2", {"alice": 2, "carol": 7}),
    ]
    stats = mod.compute_presence_stats(clusters)
    assert stats["char_total"]["alice"] == 7
    assert stats["char_total"]["bob"] == 3
    assert stats["char_total"]["carol"] == 7
    assert stats["char_last_seen"]["bob"] == "cluster_001"
    assert stats["char_last_seen"]["alice"] == "cluster_002"


def test_author_baseline_contract_uses_source_clusters():
    proj = _mk_project([], author_baseline={
        "gini_mean": 0.2,
        "gini_std": 0.05,
        "long_tail_pct": 0.1,
        "source_clusters": ["cluster_001", "cluster_002"],
    })
    baseline = mod._read_author_presence_baseline(proj)
    assert baseline["source_clusters"] == ["cluster_001", "cluster_002"]
    assert "source_" + "chapters" not in baseline


# ── 单元：detect_long_tail_forgotten ──────────────────────
def test_long_tail_detects_absentees():
    clusters = [
        _cluster("c1", {"alice": 5, "bob": 5, "carol": 5, "dave": 5, "eve": 5}),
        _cluster("c2", {"alice": 5}),
        _cluster("c3", {"alice": 5}),
        _cluster("c4", {"alice": 5}),
    ]
    stats = mod.compute_presence_stats(clusters)
    forgotten = mod.detect_long_tail_forgotten(stats, clusters)
    # 末 3 cluster 仅 alice → bob/carol/dave/eve 长尾遗忘
    assert set(forgotten) == {"bob", "carol", "dave", "eve"}


def test_long_tail_no_false_positive_when_too_few_clusters():
    clusters = [_cluster("c1", {"alice": 5, "bob": 5})]
    stats = mod.compute_presence_stats(clusters)
    assert mod.detect_long_tail_forgotten(stats, clusters) == []


# ── CLI：shadow 模式 zero exit + report ──────────────────
def test_cli_shadow_with_baseline():
    clusters = [
        _cluster("c1", {"alice": 10, "bob": 5, "carol": 3}),
        _cluster("c2", {"alice": 10, "bob": 4, "dave": 3}),
        _cluster("c3", {"alice": 12, "carol": 2, "eve": 3}),
    ]
    # 作者基线：低 Gini（群像作者）→ 本书 Gini 偏高应触发 too_centralized
    proj = _mk_project(clusters, author_baseline={
        "gini_mean": 0.20, "gini_std": 0.05,
        "source_clusters": ["cluster_001", "cluster_002", "cluster_003"],
    })
    env = _utf8_env(CHARACTER_PRESENCE_BALANCE_MODE="shadow")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0


# ── CLI：active 模式偏离 advisory exit 1 ─────────────────
def test_cli_active_centralized_gini_exits_1():
    clusters = [
        _cluster("c1", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c2", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c3", {"alice": 100, "bob": 1, "carol": 1}),
    ]
    proj = _mk_project(clusters, author_baseline={
        "gini_mean": 0.20, "gini_std": 0.05,
        "source_clusters": ["cluster_001", "cluster_002", "cluster_003"],
    })
    env = _utf8_env(CHARACTER_PRESENCE_BALANCE_MODE="active")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 1


# ── CLI：genre 独角戏豁免 skip ───────────────────────────
def test_cli_solo_genre_skipped():
    clusters = [
        _cluster("c1", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c2", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c3", {"alice": 100, "bob": 1, "carol": 1}),
    ]
    proj = _mk_project(clusters, genre="solo_cultivation")
    env = _utf8_env(CHARACTER_PRESENCE_BALANCE_MODE="active")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0
    assert "SKIP" in r.stdout


# ── CLI：off 模式 ───────────────────────────────────────
def test_cli_off_mode():
    clusters = [_cluster(f"c{i}", {"alice": 5, "bob": 3}) for i in range(3)]
    proj = _mk_project(clusters)
    env = _utf8_env(CHARACTER_PRESENCE_BALANCE_MODE="off")
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    assert r.returncode == 0


# ── CLI：太少角色 skip ───────────────────────────────
def test_cli_too_few_chars_skip():
    clusters = [_cluster(f"c{i}", {"alice": 5, "bob": 3}) for i in range(3)]
    proj = _mk_project(clusters)
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)],
                       capture_output=True, text=True, env=_utf8_env(), encoding="utf-8")
    assert r.returncode == 0
    assert "SKIP" in r.stdout


def test_cli_without_author_baseline_does_not_apply_generic_gini():
    clusters = [
        _cluster("c1", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c2", {"alice": 100, "bob": 1, "carol": 1}),
        _cluster("c3", {"alice": 100, "bob": 1, "carol": 1}),
    ]
    proj = _mk_project(clusters)
    r = subprocess.run([sys.executable, str(_TARGET), str(proj)], capture_output=True,
                       text=True, env=_utf8_env(CHARACTER_PRESENCE_BALANCE_MODE="active"),
                       encoding="utf-8")
    assert r.returncode == 0
    report = max((proj / "_数据库" / ".cross_cluster_scan").glob(
        "character_presence_balance_*.json"), key=lambda path: path.stat().st_mtime)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["baseline"] is None
    assert payload["baseline_status"] == "not_available"
    assert all(item["code"] != mod.CODE_GINI for item in payload["findings"])
