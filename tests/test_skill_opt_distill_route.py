"""蒸馏路线测试: reward_sfs + train (route=distill) 的确定性逻辑。

不调真 LLM, 用 mock subprocess 验证:
1. _read_sfs_score 从真实 SFS 报告格式取分
2. reward_for_cluster_sfs 在 distill/eval 失败时返回 reward=0
3. train 路由参数 reward_route 切换调用路径正确
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch as _patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import reward_sfs, train  # noqa: E402


# -------- reward_sfs --------

def test_read_sfs_score_from_real_format():
    """匹配 style_evaluator 实际输出字段 sfs_quick (0-100)。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "eval.json"
        p.write_text(
            json.dumps({"sfs_quick": 84.43, "ref_count": 5}),
            encoding="utf-8",
        )
        assert reward_sfs._read_sfs_score(p) == pytest.approx(84.43)


def test_read_sfs_score_fallback_alt_keys():
    """sfs_score / overall_score 备用字段。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "eval.json"
        p.write_text(json.dumps({"overall_score": 72.5}), encoding="utf-8")
        assert reward_sfs._read_sfs_score(p) == 72.5


def test_read_sfs_score_missing_returns_0():
    """eval 文件缺失或字段缺失 → 0.0 不崩。"""
    assert reward_sfs._read_sfs_score(Path("/non/exist.json")) == 0.0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "eval.json"
        p.write_text(json.dumps({"other_field": 1}), encoding="utf-8")
        assert reward_sfs._read_sfs_score(p) == 0.0


def test_reward_for_cluster_sfs_distill_failure_returns_0():
    """distill_replicate exit≠0 → reward=0,不调 eval。"""
    with tempfile.TemporaryDirectory() as td:
        out_root = Path(td)
        proj = Path(td) / "fake_proj"
        proj.mkdir()
        skill = proj / "skill.md"
        skill.write_text("test", encoding="utf-8")

        # mock subprocess 让 distill 失败
        def fake_distill(*a, **kw):
            return 2, 0.1
        with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill):
            sr = reward_sfs.reward_for_cluster_sfs(
                style_skill=skill,
                project_root=proj,
                cluster_id="auto_001",
                out_root=out_root,
                run_id="t1",
            )
        assert sr.reward == 0.0
        assert sr.sfs_score == 0.0
        assert sr.distill_exit_code == 2
        assert sr.raw.get("phase") == "distill_failed"


def test_reward_for_cluster_sfs_eval_failure_returns_0():
    """复刻成功但 eval 失败 → reward=0。"""
    with tempfile.TemporaryDirectory() as td:
        out_root = Path(td)
        proj = Path(td) / "fake_proj"
        proj.mkdir()
        skill = proj / "skill.md"
        skill.write_text("test", encoding="utf-8")

        def fake_distill(style_skill, cluster_id, project, output, timeout=1500):
            # 真造一个 replica 文件
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("fake replica content", encoding="utf-8")
            return 0, 0.1

        def fake_eval(*a, **kw):
            return 2

        with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill), \
             _patch("skill_opt.reward_sfs._run_style_evaluator", fake_eval):
            sr = reward_sfs.reward_for_cluster_sfs(
                style_skill=skill,
                project_root=proj,
                cluster_id="auto_001",
                out_root=out_root,
                run_id="t2",
            )
        assert sr.reward == 0.0
        assert sr.distill_exit_code == 0
        assert sr.eval_exit_code == 2
        assert sr.raw.get("phase") == "eval_failed"


def test_reward_for_cluster_sfs_full_success():
    """全链路 mock 通过 → sfs_score=85 → reward=0.85。"""
    with tempfile.TemporaryDirectory() as td:
        out_root = Path(td)
        proj = Path(td) / "fake_proj"
        proj.mkdir()
        skill = proj / "skill.md"
        skill.write_text("test", encoding="utf-8")

        def fake_distill(style_skill, cluster_id, project, output, timeout=1500):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("fake replica content", encoding="utf-8")
            return 0, 0.5

        def fake_eval(replica_path, ref_dir, output_json, **kw):
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(
                json.dumps({"sfs_quick": 85.0, "ref_count": 5}),
                encoding="utf-8",
            )
            return 0

        with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill), \
             _patch("skill_opt.reward_sfs._run_style_evaluator", fake_eval):
            sr = reward_sfs.reward_for_cluster_sfs(
                style_skill=skill,
                project_root=proj,
                cluster_id="auto_001",
                out_root=out_root,
                run_id="t3",
            )
        assert sr.sfs_score == 85.0
        assert sr.reward == 0.85
        assert sr.distill_exit_code == 0
        assert sr.eval_exit_code == 0
        assert sr.raw.get("phase") == "ok"


# -------- train: 双路由切换 --------

def _mk_minimal_style_lib(root: Path, n_clusters: int = 10) -> None:
    """模拟风格库结构。"""
    clusters = [
        {"cluster_id": f"auto_{i:03d}", "chapter_range": [i, i + 1]}
        for i in range(1, n_clusters + 1)
    ]
    (root / "cluster_index.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "skill_FINAL.md").write_text(
        "## 身份\n\n测试 skill。\n", encoding="utf-8"
    )
    # 原文目录(SFS 评分需要)
    (root / "原文").mkdir(exist_ok=True)
    for i in range(1, n_clusters + 1):
        (root / "原文" / f"第{i:03d}章.txt").write_text(
            f"原文样本{i}" * 100, encoding="utf-8"
        )


def test_train_dry_run_with_distill_route(tmp_path):
    """dry-run 不真跑,验证 reward_route=distill 参数被记录。"""
    _mk_minimal_style_lib(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"
    result = train.train(
        project_root=tmp_path,
        initial_skill_path=skill,
        epochs=2,
        rollout_batch_size=4,
        minibatch_size=2,
        reward_route="distill",
        dry_run=True,
    )
    assert result.hparams["reward_route"] == "distill"
    assert result.hparams["multi_ref_count"] == 5


def test_train_distill_route_calls_reward_sfs(tmp_path):
    """mock reward_sfs,验证 distill 路由调用了它而非 rollout。"""
    _mk_minimal_style_lib(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"

    sfs_calls = {"count": 0}

    def fake_sfs(style_skill, project_root, cluster_id, out_root, run_id, **kw):
        sfs_calls["count"] += 1
        # 返回固定 0.5 的 reward
        return reward_sfs.SfsReward(
            sfs_score=50.0, reward=0.5,
            replica_path="", eval_json_path="",
            duration_sec=0.1,
            distill_exit_code=0, eval_exit_code=0,
        )

    def fake_optimizer(skill_text, trajectories, **kw):
        # 返回一条 add patch (合法)
        return [{"op": "add", "new": "## 新段\n\n内容"}], "fake"

    # 写作路线的 rollout 不应被调
    rollout_calls = {"count": 0}

    def fake_rollout(*a, **kw):
        rollout_calls["count"] += 1
        return [], None

    with _patch("skill_opt.train.reward_sfs.reward_for_cluster_sfs", fake_sfs), \
         _patch("skill_opt.train.optimizer.propose_patches", fake_optimizer), \
         _patch("skill_opt.train.rollout.rollout_batch", fake_rollout):
        result = train.train(
            project_root=tmp_path,
            initial_skill_path=skill,
            epochs=1,
            rollout_batch_size=2,
            minibatch_size=2,
            l_t_max=2,
            l_t_min=2,
            reward_route="distill",
        )

    # 蒸馏路线: reward_sfs 应被多次调用 (mb + sel before + sel after + final test)
    assert sfs_calls["count"] > 0
    # 写作路线 rollout 不应被调
    assert rollout_calls["count"] == 0
    assert result.hparams["reward_route"] == "distill"


def test_train_writing_route_default_still_works(tmp_path):
    """默认 reward_route=writing 不影响旧行为。"""
    _mk_minimal_style_lib(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"
    result = train.train(
        project_root=tmp_path,
        initial_skill_path=skill,
        epochs=1,
        rollout_batch_size=4,
        minibatch_size=2,
        dry_run=True,
    )
    assert result.hparams["reward_route"] == "writing"
