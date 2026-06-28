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
    voice_grade: str | None = "A",
    truth_grade: str | None = "A",
    chapter_range: tuple[int, int] = (1, 3),
) -> None:
    """搭真实路径结构: .audit/<cid>_audit.json + .reading_reflection/<cid>_round_*.json
       + .judge_reports/<cid>_voice-checker.json + ch_NNN_writer-truth-check.json
       + _数据库/事件簇.json 含 chapter_range
    """
    db = root / "_数据库"
    audit = db / ".audit"
    judge = db / ".judge_reports"
    reading = db / ".reading_reflection"
    audit.mkdir(parents=True, exist_ok=True)
    judge.mkdir(parents=True, exist_ok=True)
    reading.mkdir(parents=True, exist_ok=True)

    # 事件簇.json: 标 chapter_range
    (db / "事件簇.json").write_text(
        json.dumps(
            {
                "clusters": [
                    {"cluster_id": cluster_id, "chapter_range": list(chapter_range)}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if audit_verdict is not None:
        (audit / f"{cluster_id}_audit.json").write_text(
            json.dumps({"verdict": audit_verdict}, ensure_ascii=False),
            encoding="utf-8",
        )
    if reading_verdict is not None:
        # 最后一 round
        (reading / f"{cluster_id}_round_1.json").write_text(
            json.dumps({"verdict": reading_verdict}, ensure_ascii=False),
            encoding="utf-8",
        )
    if voice_grade is not None:
        (judge / f"{cluster_id}_voice-checker.json").write_text(
            json.dumps({"overall_grade": voice_grade}, ensure_ascii=False),
            encoding="utf-8",
        )
    if truth_grade is not None:
        # 章级,每章一个
        for ch in range(chapter_range[0], chapter_range[1] + 1):
            (judge / f"ch_{ch:03d}_writer-truth-check.json").write_text(
                json.dumps({"overall_grade": truth_grade}, ensure_ascii=False),
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
        _mk_judge_files(root, "auto_001", voice_grade="D")  # voice 失败
        r, _ = reward.reward_for_cluster(root, "auto_001", mode="strict")
        assert r == 0.0


def test_reward_one_fail_soft_3of4():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_judge_files(root, "auto_001", voice_grade="D")  # voice 失败
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
            voice_grade="A",
            truth_grade=None,  # 缺
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


def test_reject_buffer_clear_archives_before_delete():
    """S3: clear_epoch 先归档到 reject_archive.jsonl 再删 epoch 文件。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reject_buffer.record_reject(
            root, 1, "p1", "v3", {"op": "add", "new": "x"}, 0.5, 0.4, "r1"
        )
        reject_buffer.record_reject(
            root, 1, "p2", "v3", {"op": "delete", "old": "y"}, 0.6, 0.5, "r2"
        )
        reject_buffer.clear_epoch(root, 1)
        # epoch 文件已删
        assert reject_buffer.load_epoch_rejects(root, 1) == []
        # archive 保留了 2 条
        archive = reject_buffer.load_archive(root)
        assert len(archive) == 2
        assert archive[0]["patch_id"] == "p1"
        assert archive[1]["patch_id"] == "p2"


def test_reject_buffer_archive_accumulates_across_epochs():
    """跨 epoch 归档累积。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reject_buffer.record_reject(
            root, 1, "ep1", "v3", {"op": "add", "new": "a"}, 0.5, 0.4, "r"
        )
        reject_buffer.clear_epoch(root, 1)
        reject_buffer.record_reject(
            root, 2, "ep2", "v3", {"op": "add", "new": "b"}, 0.6, 0.5, "r"
        )
        reject_buffer.clear_epoch(root, 2)
        archive = reject_buffer.load_archive(root)
        assert len(archive) == 2
        assert archive[0]["patch_id"] == "ep1"
        assert archive[1]["patch_id"] == "ep2"


# -------- L6: validation_gate SFS 地板 --------

def test_gate_floor_rejects_below():
    """L6: score_after < floor → 无条件拒绝 (即使优于 before)。"""
    r = validation_gate.decide([0.3, 0.3], [0.5, 0.5], floor=0.7)
    assert not r.accepted
    assert "FLOOR_REJECT" in r.reason


def test_gate_floor_allows_above():
    """L6: score_after >= floor + 严格优于 → 接受。"""
    r = validation_gate.decide([0.5, 0.5], [0.8, 0.8], floor=0.7)
    assert r.accepted


def test_gate_floor_none_backward_compat():
    """L6: floor=None → 不检查 (向后兼容)。"""
    r = validation_gate.decide([0.3, 0.3], [0.5, 0.5], floor=None)
    assert r.accepted
