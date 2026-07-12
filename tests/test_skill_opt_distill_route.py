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

from skill_opt import reward_sfs, scene_jobs, train  # noqa: E402

SKILLOPT_PLAN = REPO / "core" / "claude-home" / "plans" / "distill-style-skillopt.plan.json"


def _ready_scene_job(out_root: Path, skill: Path, run_id: str, cluster_id: str,
                     *, plan_id: str = "plan-test", step: str = "4") -> None:
    digest = scene_jobs.skill_digest(skill)
    scenes = scene_jobs.scenes_dir_for(out_root, run_id, digest, cluster_id)
    scenes.mkdir(parents=True, exist_ok=True)
    scene = scenes / "scene_001.txt"
    scene.write_text("这是专用复刻写作代理亲笔完成的场景草稿。" * 24, encoding="utf-8")
    job_key = f"{digest}:{run_id}:{cluster_id}"
    (scenes / "agent_report.json").write_text(json.dumps({
        "schema_version": "novel-replica-writer.receipt.v1",
        "agent": "novel-replica-writer",
        "mode": "style-replica-draft",
        "plan_id": plan_id,
        "step": step,
        "completed": True,
        "job_key": job_key,
        "candidate_skill_digest": digest,
        "cluster_id": cluster_id,
        "style_skill_path": str(skill),
        "scene_files": [scene.name],
    }, ensure_ascii=False), encoding="utf-8")


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
        _ready_scene_job(out_root, skill, "t1", "auto_001")

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
        _ready_scene_job(out_root, skill, "t2", "auto_001")

        def fake_distill(style_skill, cluster_id, project, output, claude_scenes_dir, timeout=1500):
            # 真造一个 replica 文件
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("fake replica content", encoding="utf-8")
            return 0, 0.1

        def fake_eval(*a, **kw):
            return 2

        def fake_av(project, cluster_id, replica, output, **kw):
            output.write_text("{}", encoding="utf-8")
            return 0

        with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill), \
             _patch("skill_opt.reward_sfs._run_av_verify", fake_av), \
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
        _ready_scene_job(out_root, skill, "t3", "auto_001")

        def fake_distill(style_skill, cluster_id, project, output, claude_scenes_dir, timeout=1500):
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

        def fake_av(project, cluster_id, replica, output, **kw):
            output.write_text(json.dumps({"verdict": "advisory"}), encoding="utf-8")
            return 0

        with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill), \
             _patch("skill_opt.reward_sfs._run_av_verify", fake_av), \
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
        assert Path(sr.raw["av_report_path"]).exists()


def test_av_execution_failure_is_hard(tmp_path):
    project = tmp_path / "style"
    project.mkdir()
    skill = project / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    _ready_scene_job(tmp_path, skill, "av-hard", "auto_001")
    def fake_distill(style_skill, cluster_id, project, output, claude_scenes_dir, timeout=1500):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("replica", encoding="utf-8")
        return 0, 0.1
    with _patch("skill_opt.reward_sfs._run_distill_replicate", fake_distill), \
         _patch("skill_opt.reward_sfs._run_av_verify", lambda *a, **kw: 2), \
         pytest.raises(RuntimeError, match="required AV"):
        reward_sfs.reward_for_cluster_sfs(
            style_skill=skill, project_root=project, cluster_id="auto_001",
            out_root=tmp_path, run_id="av-hard",
        )


def test_missing_scene_job_is_required_not_zero_reward(tmp_path):
    project = tmp_path / "style"
    project.mkdir()
    skill = project / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    with pytest.raises(scene_jobs.SceneJobsRequiredError):
        reward_sfs.reward_for_cluster_sfs(
            style_skill=skill,
            project_root=project,
            cluster_id="auto_001",
            out_root=tmp_path,
            run_id="required",
        )
    manifest = json.loads(scene_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert manifest["jobs"][0]["status"] == "pending"


def test_scene_files_without_writer_report_remain_pending(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    digest = scene_jobs.skill_digest(skill)
    scenes = scene_jobs.scenes_dir_for(tmp_path, "receipt", digest, "auto_001")
    scenes.mkdir(parents=True)
    (scenes / "scene_001.txt").write_text("只有场景没有代理回执。" * 30, encoding="utf-8")
    with pytest.raises(scene_jobs.SceneJobsRequiredError):
        scene_jobs.require_scene_job(
            skill_path=skill, cluster_id="auto_001", out_root=tmp_path,
            run_id="receipt",
        )
    job = json.loads(scene_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"][0]
    assert job["status"] == "pending"
    assert job["agent_report"].endswith("agent_report.json")


def test_candidate_skills_have_isolated_scene_directories(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("candidate A", encoding="utf-8")
    second.write_text("candidate B", encoding="utf-8")
    for skill in (first, second):
        with pytest.raises(scene_jobs.SceneJobsRequiredError):
            scene_jobs.require_scene_job(
                skill_path=skill,
                cluster_id="auto_001",
                out_root=tmp_path,
                run_id="same-run",
            )
    jobs = json.loads(scene_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["candidate_skill_digest"] != jobs[1]["candidate_skill_digest"]
    assert jobs[0]["scenes_dir"] != jobs[1]["scenes_dir"]
    assert all(Path(job["candidate_skill_path"]).read_text(encoding="utf-8").startswith("candidate") for job in jobs)


def test_scene_job_batch_registers_all_missing_clusters(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    with pytest.raises(scene_jobs.SceneJobsRequiredError):
        scene_jobs.require_scene_jobs(
            skill_path=skill,
            cluster_ids=["auto_001", "auto_002", "auto_003"],
            out_root=tmp_path,
            run_id="batch",
        )
    jobs = json.loads(scene_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"]
    assert {job["cluster_id"] for job in jobs} == {"auto_001", "auto_002", "auto_003"}


def test_verified_batch_receipt_requires_real_agent_reports(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    for cluster_id in ("auto_001", "auto_002"):
        _ready_scene_job(tmp_path, skill, "batch-ready", cluster_id, plan_id="p-1", step="3")
    ready = scene_jobs.require_scene_jobs(
        skill_path=skill,
        cluster_ids=["auto_001", "auto_002"],
        out_root=tmp_path,
        run_id="batch-ready",
    )
    assert len(ready) == 2
    output = tmp_path / "agent_receipts_step3.json"
    receipt = scene_jobs.write_verified_batch_receipt(
        out_root=tmp_path, plan_id="p-1", step=3, output=output,
    )
    assert receipt["agent"] == "novel-replica-writer"
    assert receipt["mode"] == "style-replica-batch"
    assert receipt["job_count"] == 2
    assert output.is_file()


def test_verified_batch_receipt_rejects_manifest_without_agent_completion(tmp_path):
    skill = tmp_path / "skill.md"
    skill.write_text("candidate", encoding="utf-8")
    with pytest.raises(scene_jobs.SceneJobsRequiredError):
        scene_jobs.require_scene_job(
            skill_path=skill, cluster_id="auto_001", out_root=tmp_path,
            run_id="pending",
        )
    with pytest.raises(scene_jobs.SceneJobsRequiredError):
        scene_jobs.write_verified_batch_receipt(
            out_root=tmp_path, plan_id="p-1", step=3,
            output=tmp_path / "receipt.json",
        )


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


def test_prepare_scene_jobs_writes_checkpoint_and_schedule(tmp_path):
    _mk_minimal_style_lib(tmp_path, n_clusters=10)
    result = train.train(
        project_root=tmp_path,
        initial_skill_path=tmp_path / "skill_FINAL.md",
        epochs=1,
        rollout_batch_size=4,
        minibatch_size=2,
        reward_route="distill",
        run_id="prepare-contract",
        prepare_scene_jobs=True,
    )
    train_dir = tmp_path / "_skillopt" / "train" / "prepare-contract"
    checkpoint = json.loads((train_dir / "training_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["phase"] == "awaiting_initial_scenes"
    assert (train_dir / "training_schedule.json").exists()
    jobs = json.loads((train_dir / "claude_scene_jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert len(jobs) == 2
    assert result.epochs == []

    first_schedule = (train_dir / "training_schedule.json").read_text(encoding="utf-8")
    train.train(
        project_root=tmp_path,
        initial_skill_path=tmp_path / "skill_FINAL.md",
        epochs=1,
        rollout_batch_size=4,
        minibatch_size=2,
        reward_route="distill",
        run_id="prepare-contract",
        prepare_scene_jobs=True,
    )
    assert (train_dir / "training_schedule.json").read_text(encoding="utf-8") == first_schedule


def test_skillopt_final_verify_is_required_same_stack_and_av():
    plan = json.loads(SKILLOPT_PLAN.read_text(encoding="utf-8"))
    step6 = next(step for step in plan["steps"] if step["n"] == 6)
    assert step6["skip_output_allowed"] is False
    scripts = step6["scripts"]
    assert all(not script.lstrip().startswith("?") for script in scripts)
    blob = " ".join(scripts)
    assert "distill_finalize_verify.py" in blob
    assert "--claude-scenes-dir" in blob
    assert "distill_av_verify.py" in blob
    assert set(step6["expected_outputs"]) >= {
        "复刻测试/writer_feedback_verify/claude_scenes/agent_report.json",
        "对比报告/skillopt_verify.json", "对比报告/skillopt_av_verify.json",
    }
    assert step6["must_spawn_agent"] == "novel-replica-writer"


def test_skillopt_scene_steps_use_dedicated_writer():
    plan = json.loads(SKILLOPT_PLAN.read_text(encoding="utf-8"))
    for n in (3, 4, 6):
        step = next(item for item in plan["steps"] if item["n"] == n)
        assert step["must_spawn_agent"] == "novel-replica-writer"
    assert plan["steps"][2]["agent_input"]["JOB_KEY"] == "<job.job_key>"
    for n in (3, 4):
        step = next(item for item in plan["steps"] if item["n"] == n)
        assert "agent_receipts_step" in step["judge_report_path"]
        assert step["judge_report_path"] in step["expected_outputs"]
        assert "scene_jobs.py --verify-all" in " ".join(step["scripts"])
        assert not step["judge_report_path"].endswith("claude_scene_jobs.json")


def test_resume_reuses_candidate_without_reinvoking_optimizer(tmp_path):
    _mk_minimal_style_lib(tmp_path, n_clusters=10)
    skill = tmp_path / "skill_FINAL.md"
    def fake_sfs(**kwargs):
        return reward_sfs.SfsReward(
            sfs_score=50, reward=0.5, replica_path="", eval_json_path="",
            duration_sec=0.1, distill_exit_code=0, eval_exit_code=0,
        )
    optimizer_calls = {"count": 0}
    def fake_optimizer(*args, **kwargs):
        optimizer_calls["count"] += 1
        return [{"op": "add", "new": "## 新段\n\n内容"}], "reply"
    def interrupt_candidate(*, skill_path, **kwargs):
        if skill_path.name.endswith("_candidate.md"):
            raise scene_jobs.SceneJobsRequiredError("candidate scenes pending")
        return {}
    with _patch("skill_opt.train.reward_sfs.reward_for_cluster_sfs", fake_sfs), \
         _patch("skill_opt.train.optimizer.propose_patches", fake_optimizer), \
         _patch("skill_opt.train.scene_jobs.require_scene_jobs", interrupt_candidate), \
         pytest.raises(scene_jobs.SceneJobsRequiredError):
        train.train(
            project_root=tmp_path, initial_skill_path=skill, epochs=1,
            rollout_batch_size=2, minibatch_size=2, reward_route="distill",
            run_id="resume-candidate",
        )
    train_dir = tmp_path / "_skillopt" / "train" / "resume-candidate"
    checkpoint = json.loads((train_dir / "training_checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["phase"] == "selection"
    assert checkpoint["candidate_skill_digest"]
    assert optimizer_calls["count"] == 1

    def optimizer_must_not_run(*args, **kwargs):
        raise AssertionError("恢复不得重提 candidate")
    with _patch("skill_opt.train.reward_sfs.reward_for_cluster_sfs", fake_sfs), \
         _patch("skill_opt.train.optimizer.propose_patches", optimizer_must_not_run), \
         _patch("skill_opt.train.scene_jobs.require_scene_jobs", lambda **kwargs: {}):
        result = train.train(
            project_root=tmp_path, initial_skill_path=skill, epochs=1,
            rollout_batch_size=2, minibatch_size=2, reward_route="distill",
            run_id="resume-candidate",
        )
    assert Path(result.final_skill).exists()


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
         _patch("skill_opt.train.scene_jobs.require_scene_jobs", lambda **kw: {}), \
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
