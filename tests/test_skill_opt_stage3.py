"""阶段3 测试: train.py - cosine decay + dry-run + thin-slice 集成。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch as _patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import train  # noqa: E402


# ---------- cosine decay ----------

def test_cosine_decay_starts_at_max():
    assert train.cosine_decay_lt(epoch=1, total_epochs=4, l_t_max=4, l_t_min=2) == 4


def test_cosine_decay_ends_at_min():
    assert train.cosine_decay_lt(epoch=4, total_epochs=4, l_t_max=4, l_t_min=2) == 2


def test_cosine_decay_middle_in_range():
    mid = train.cosine_decay_lt(epoch=2, total_epochs=4, l_t_max=4, l_t_min=2)
    assert 2 <= mid <= 4


def test_cosine_decay_single_epoch_returns_max():
    assert train.cosine_decay_lt(epoch=1, total_epochs=1, l_t_max=4, l_t_min=2) == 4


def test_cosine_decay_clamps_to_min():
    """无论 cosine 算出来多小,不低于 l_t_min。"""
    for ep in range(1, 11):
        v = train.cosine_decay_lt(ep, 10, 4, 2)
        assert v >= 2


# ---------- dry-run 不真跑 LLM ----------

def _mk_minimal_project(root: Path, n_clusters: int = 10) -> None:
    """搭最小项目结构: cluster_index + skill_FINAL.md。"""
    clusters = [
        {"cluster_id": f"auto_{i:03d}", "chapter_range": [i * 5 + 1, i * 5 + 5]}
        for i in range(1, n_clusters + 1)
    ]
    (root / "cluster_index.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "skill_FINAL.md").write_text(
        "## 身份\n\n测试 skill。\n\n## 铁律 1\n\n测试。\n",
        encoding="utf-8",
    )


def test_train_dry_run_no_crash(tmp_path):
    _mk_minimal_project(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"
    result = train.train(
        project_root=tmp_path,
        initial_skill_path=skill,
        epochs=2,
        rollout_batch_size=4,
        minibatch_size=2,
        dry_run=True,
    )
    # dry-run 走完不崩,split 已落盘
    assert result.epochs == []
    assert (tmp_path / "_skillopt" / "split.json").exists()


def test_train_dry_run_creates_workdir(tmp_path):
    _mk_minimal_project(tmp_path)
    train.train(
        project_root=tmp_path,
        initial_skill_path=tmp_path / "skill_FINAL.md",
        dry_run=True,
    )
    train_dir = tmp_path / "_skillopt" / "train"
    assert train_dir.exists()
    # 至少一个 timestamp 子目录
    subdirs = [p for p in train_dir.iterdir() if p.is_dir()]
    assert len(subdirs) >= 1


def test_train_reuses_existing_split(tmp_path):
    """同 project 重跑应复用已存在的 split.json。"""
    _mk_minimal_project(tmp_path)
    skill = tmp_path / "skill_FINAL.md"
    train.train(project_root=tmp_path, initial_skill_path=skill, dry_run=True)
    split_path = tmp_path / "_skillopt" / "split.json"
    first_mtime = split_path.stat().st_mtime
    # 二次跑应不重写 split.json
    train.train(project_root=tmp_path, initial_skill_path=skill, dry_run=True)
    assert split_path.stat().st_mtime == first_mtime


def test_train_reads_protected_when_compact_exists(tmp_path):
    """skill_compact/skill_SLOW.md 存在时,protected_titles 被加载。"""
    _mk_minimal_project(tmp_path)
    compact = tmp_path / "skill_compact"
    compact.mkdir()
    (compact / "skill_SLOW.md").write_text(
        "## 量化约束\n\n句长 34\n\n## 标点节奏\n\n逗号 62\n",
        encoding="utf-8",
    )

    # 用 stdout 捕获验证 PROTECTED 段数 ≥ 2
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        train.train(
            project_root=tmp_path,
            initial_skill_path=tmp_path / "skill_FINAL.md",
            dry_run=True,
        )
    out = buf.getvalue()
    assert "PROTECTED 段数: 2" in out


# ---------- 集成 thin-slice: mock rollout/optimizer 跑1 epoch ----------


def test_train_thin_slice_one_epoch(tmp_path):
    """mock rollout/optimizer/patch_applier 跑一轮,验证主循环不崩 + 升级路径。"""
    _mk_minimal_project(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"

    # mock rollout_batch 返回固定 trajectory (reward 递增模拟"接受")
    fake_rewards = iter([0.5, 0.5, 0.5, 0.5] * 50)  # 足够用

    def fake_rollout(style_skill, project, cluster_ids, out_root, run_id, reward_mode):
        from skill_opt.rollout import Trajectory
        trajs = []
        for cid in cluster_ids:
            r = next(fake_rewards)
            trajs.append(
                Trajectory(
                    cluster_id=cid,
                    reward=r,
                    components={},
                    replica_path="",
                    skill_path=str(style_skill),
                    duration_sec=0.1,
                    exit_code=0,
                )
            )
        log = out_root / "trajectories" / run_id / "batch.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("{}", encoding="utf-8")
        return trajs, log

    # mock optimizer 返回一条 add patch (合法,会被应用)
    def fake_optimizer(skill_text, trajectories, **kw):
        return [{"op": "add", "new": "## 新段\n\n内容"}], "fake reply"

    with _patch("skill_opt.train.rollout.rollout_batch", fake_rollout), \
         _patch("skill_opt.train.optimizer.propose_patches", fake_optimizer):
        result = train.train(
            project_root=tmp_path,
            initial_skill_path=skill,
            epochs=1,
            rollout_batch_size=4,
            minibatch_size=2,
            l_t_max=2,
            l_t_min=2,
        )

    # 跑完应有 1 个 epoch log
    assert len(result.epochs) == 1
    ep = result.epochs[0]
    assert ep.epoch == 1
    # rollout_batch_size=4 / minibatch_size=2 → 2 steps
    assert ep.steps_attempted == 2
    # 平局拒绝 (reward 0.5→0.5),应 reject
    assert ep.steps_rejected == 2
    # best_skill 应被落盘
    assert Path(result.final_skill).exists()


def test_train_accepts_when_reward_improves(tmp_path):
    """reward 0.5 → 0.8 时应 accept。"""
    _mk_minimal_project(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"

    # 分两轮:第一轮 0.5(before), 第二轮 0.8(after)
    state = {"call": 0}

    def fake_rollout(style_skill, project, cluster_ids, out_root, run_id, reward_mode):
        from skill_opt.rollout import Trajectory
        # run_id 含 sel_before / sel_after,据此返回不同 reward
        if "sel_after" in run_id:
            r = 0.8
        elif "sel_before" in run_id:
            r = 0.5
        else:
            r = 0.5  # 训练 minibatch
        trajs = [
            Trajectory(
                cluster_id=cid, reward=r, components={},
                replica_path="", skill_path=str(style_skill),
                duration_sec=0.1, exit_code=0,
            )
            for cid in cluster_ids
        ]
        log = out_root / "trajectories" / run_id / "batch.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("{}", encoding="utf-8")
        return trajs, log

    def fake_optimizer(skill_text, trajectories, **kw):
        return [{"op": "add", "new": "## 新段\n\n内容"}], "fake"

    with _patch("skill_opt.train.rollout.rollout_batch", fake_rollout), \
         _patch("skill_opt.train.optimizer.propose_patches", fake_optimizer):
        result = train.train(
            project_root=tmp_path,
            initial_skill_path=skill,
            epochs=1,
            rollout_batch_size=2,
            minibatch_size=2,
            l_t_max=2,
            l_t_min=2,
        )

    ep = result.epochs[0]
    assert ep.steps_accepted >= 1
    # final_skill 应包含新加段
    text = Path(result.final_skill).read_text(encoding="utf-8")
    assert "新段" in text
