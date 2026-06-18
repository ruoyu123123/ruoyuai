"""阶段0 测试: dataset / reward / reject_buffer / validation_gate

不调真 LLM,只测确定性逻辑。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# 让 import 找到 core/scripts/
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import dataset, reject_buffer, reward, validation_gate  # noqa: E402


# -------- dataset --------

def _mk_cluster_index(root: Path, n_clusters: int) -> None:
    clusters = [
        {"cluster_id": f"auto_{i:03d}", "chapter_range": [i * 5 + 1, i * 5 + 5]}
        for i in range(1, n_clusters + 1)
    ]
    (root / "cluster_index.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_split_60_20_20():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_cluster_index(root, n_clusters=100)
        split = dataset.split_clusters(root, seed="t1")
        assert len(split.train) == 60
        assert len(split.selection) == 20
        assert len(split.test) == 20
        # 无重叠
        assert not (set(split.train) & set(split.selection))
        assert not (set(split.train) & set(split.test))
        assert not (set(split.selection) & set(split.test))


def test_split_deterministic_with_same_seed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_cluster_index(root, n_clusters=50)
        s1 = dataset.split_clusters(root, seed="repro")
        s2 = dataset.split_clusters(root, seed="repro")
        assert s1.train == s2.train
        assert s1.selection == s2.selection
        assert s1.test == s2.test


def test_split_different_seed_differs():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_cluster_index(root, n_clusters=50)
        s1 = dataset.split_clusters(root, seed="a")
        s2 = dataset.split_clusters(root, seed="b")
        # 不同 seed 切分应不同 (除非奇迹般完全相同,概率 ~ 1/C(50,30))
        assert s1.train != s2.train


def test_split_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_cluster_index(root, n_clusters=20)
        s = dataset.split_clusters(root)
        dataset.save_split(s, root)
        s2 = dataset.load_split(root)
        assert s.train == s2.train
        assert s.selection == s2.selection
        assert s.test == s2.test


def test_split_missing_cluster_index_raises():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(FileNotFoundError):
            dataset.split_clusters(Path(td))


def test_split_invalid_ratios_raises():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_cluster_index(root, 10)
        with pytest.raises(ValueError):
            dataset.split_clusters(root, ratios=(0.5, 0.3, 0.3))


# -------- validation_gate --------

def test_gate_strictly_better_accepted():
    r = validation_gate.decide([0.5, 0.6, 0.7], [0.7, 0.8, 0.9])
    assert r.accepted
    assert r.delta == pytest.approx(0.6 / 3, abs=1e-6)


def test_gate_tie_rejected():
    """SkillOpt 论文 Lines 22-26: 平局拒绝。"""
    r = validation_gate.decide([0.5, 0.6], [0.5, 0.6])
    assert not r.accepted
    assert r.delta == 0.0
    assert "平局" in r.reason


def test_gate_worse_rejected():
    r = validation_gate.decide([0.9, 0.9], [0.5, 0.5])
    assert not r.accepted
    assert r.delta < 0


def test_gate_epsilon_threshold():
    """ε 自定义阈值:delta 必须 > ε。"""
    r = validation_gate.decide([0.5], [0.51], epsilon=0.02)
    assert not r.accepted  # 0.01 < 0.02


def test_gate_length_mismatch_raises():
    with pytest.raises(ValueError):
        validation_gate.decide([0.5], [0.7, 0.8])


# -------- reward --------

def _mk_judge_files(
    root: Path,
    cluster_id: str,
    audit_verdict: str | None = "pass",
    reading_verdict: str | None = "pass",
    voice_drift: int | None = 0,
    truth_lies: int | None = 0,
) -> None:
    db = root / "_数据库"
    audit = db / ".audit"
    judge = db / ".judge_reports"
    audit.mkdir(parents=True, exist_ok=True)
    judge.mkdir(parents=True, exist_ok=True)
    if audit_verdict is not None:
        (audit / f"{cluster_id}.json").write_text(
            json.dumps({"verdict": audit_verdict}, ensure_ascii=False),
            encoding="utf-8",
        )
    if reading_verdict is not None:
        (judge / f"{cluster_id}_reading.json").write_text(
            json.dumps({"verdict": reading_verdict}, ensure_ascii=False),
            encoding="utf-8",
        )
    if voice_drift is not None:
        (judge / f"{cluster_id}_voice.json").write_text(
            json.dumps({"voice_drift_count": voice_drift}, ensure_ascii=False),
            encoding="utf-8",
        )
    if truth_lies is not None:
        (judge / f"{cluster_id}_truth.json").write_text(
            json.dumps({"lie_count": truth_lies}, ensure_ascii=False),
            encoding="utf-8",
        )


def test_reward_all_pass_strict_1():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_judge_files(root, "auto_001")
        r, comp = reward.reward_for_cluster(root, "auto_001", mode="strict")
        assert r == 1.0
        assert comp.audit_pass is True
        assert comp.reading_pass is True
        assert comp.voice_clean is True
        assert comp.truth_clean is True


def test_reward_one_fail_strict_0():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_judge_files(root, "auto_001", voice_drift=3)
        r, _ = reward.reward_for_cluster(root, "auto_001", mode="strict")
        assert r == 0.0


def test_reward_one_fail_soft_3of4():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_judge_files(root, "auto_001", voice_drift=3)
        r, _ = reward.reward_for_cluster(root, "auto_001", mode="soft")
        assert r == pytest.approx(3 / 4, abs=1e-6)


def test_reward_missing_judge_strict_0():
    """缺数据时 strict 保守判 0(不奖励)。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 不创建任何 judge 文件
        r, comp = reward.reward_for_cluster(root, "auto_001", mode="strict")
        assert r == 0.0
        assert comp.audit_pass is None


def test_reward_partial_missing_soft_ignores_none():
    """缺数据时 soft 不计入分母(不惩罚也不奖励)。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_judge_files(
            root, "auto_001",
            audit_verdict="pass",
            reading_verdict=None,  # 缺
            voice_drift=0,
            truth_lies=None,  # 缺
        )
        r, _ = reward.reward_for_cluster(root, "auto_001", mode="soft")
        assert r == pytest.approx(2 / 2, abs=1e-6)  # 2 个 pass / 2 个有效


# -------- reject_buffer --------

def test_reject_buffer_record_and_load():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reject_buffer.record_reject(
            project_root=root,
            epoch=1,
            patch_id="ep1_step1_patch1",
            skill_version_before="v3",
            patch={"op": "replace", "old": "AAA", "new": "BBB"},
            reward_before=0.78,
            reward_after=0.72,
            reason="未严格优于",
        )
        reject_buffer.record_reject(
            project_root=root,
            epoch=1,
            patch_id="ep1_step1_patch2",
            skill_version_before="v3",
            patch={"op": "add", "new": "新规则"},
            reward_before=0.78,
            reward_after=0.78,
            reason="平局拒绝",
        )
        items = reject_buffer.load_epoch_rejects(root, 1)
        assert len(items) == 2
        assert items[0]["patch"]["op"] == "replace"
        assert items[1]["patch"]["op"] == "add"


def test_reject_buffer_epoch_isolated():
    """epoch_1 的 reject 不会出现在 epoch_2。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reject_buffer.record_reject(
            root, 1, "p1", "v3", {"op": "add", "new": "x"}, 0.5, 0.4, "r"
        )
        assert len(reject_buffer.load_epoch_rejects(root, 1)) == 1
        assert reject_buffer.load_epoch_rejects(root, 2) == []


def test_reject_buffer_format_for_prompt():
    rejects = [
        {
            "patch": {"op": "replace", "old": "old content here", "new": "new content"},
            "reward_before": 0.8,
            "reward_after": 0.7,
            "reason": "测试",
        }
    ]
    s = reject_buffer.format_for_prompt(rejects)
    assert "REJECT_BUFFER" in s
    assert "op=replace" in s
    assert "0.8" in s


def test_reject_buffer_format_empty_returns_empty():
    assert reject_buffer.format_for_prompt([]) == ""


def test_reject_buffer_clear_epoch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reject_buffer.record_reject(
            root, 1, "p1", "v3", {"op": "add", "new": "x"}, 0.5, 0.4, "r"
        )
        assert len(reject_buffer.load_epoch_rejects(root, 1)) == 1
        reject_buffer.clear_epoch(root, 1)
        assert reject_buffer.load_epoch_rejects(root, 1) == []
