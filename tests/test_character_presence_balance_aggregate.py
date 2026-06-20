# -*- coding: utf-8 -*-
"""cross_cluster_character_presence_balance_aggregate.py 单测（R7 Batch-D · 2026-06-20）。

确定性·零依赖·零 LLM/零联网。覆盖：
  ① gini_coefficient 基尼系数（完全平等/完全集中/常态）
  ② compute_presence_stats 角色累计 + last_seen
  ③ detect_long_tail_forgotten 末 N cluster 全缺席
  ④ 作者基线 ECDF z-band 偏离上/下 advisory
  ⑤ genre 独角戏豁免
  ⑥ off / shadow / active CLI exit code
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
import cross_cluster_character_presence_balance_aggregate as mod  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_character_presence_balance_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


def _mk_project(clusters, *, genre=None, author_baseline=None):
    proj = Path(tempfile.mkdtemp(prefix="presence_"))
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    summary = {"schema_version": "v2.cluster", "clusters": clusters}
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if genre:
        (proj / "_数据库" / "用户偏好.json").write_text(
            json.dumps({"genre": genre}, ensure_ascii=False), encoding="utf-8")
    if author_baseline:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"character_presence_distribution": author_baseline},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _cluster(cid, char_counts=None, ch_range=(1, 3)):
    """char_counts: dict[char, count]·写入第一章 char_mention_counts。"""
    return {
        "cluster_id": cid, "title": cid,
        "chapter_range": list(ch_range),
        "cluster_end_ch": ch_range[1],
        "status": "done",
        "chapters": {
            str(ch_range[0]): {
                "char_mention_counts": dict(char_counts or {}),
            },
        },
    }


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
    assert stats["char_last_seen"]["bob"] == "c1"
    assert stats["char_last_seen"]["alice"] == "c2"


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
