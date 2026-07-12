"""唯一 replica writer、hook 与 Agent 回执内容校验测试。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core" / "scripts"))
import plan_step_gates as gates  # noqa: E402
import plan_tracker  # noqa: E402


def _plan(name: str) -> dict:
    path = ROOT / "core" / "claude-home" / "plans" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _style_receipt(directory: Path, *, plan_id="p", step="3", job_key=None,
                   digest=None, cluster_id="cluster_001") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    scene = directory / "scene_001.txt"
    scene.write_text("场景正文" * 100, encoding="utf-8")
    report = {
        "schema_version": "novel-replica-writer.receipt.v1",
        "agent": "novel-replica-writer",
        "mode": "style-replica-draft",
        "plan_id": plan_id,
        "step": step,
        "completed": True,
        "cluster_id": cluster_id,
        "style_skill_path": "skill.md",
        "job_key": job_key,
        "candidate_skill_digest": digest,
        "scene_files": [scene.name],
    }
    path = directory / "agent_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def test_all_distill_creation_steps_use_single_replica_writer():
    style = _plan("distill-style.plan.json")
    skillopt = _plan("distill-style-skillopt.plan.json")
    character = _plan("distill-character.plan.json")
    for plan, steps in ((style, (3, 5.5, 7)), (skillopt, (3, 4, 6)),
                        (character, (3,))):
        for n in steps:
            step = next(item for item in plan["steps"] if item["n"] == n)
            assert step["must_spawn_agent"] == "novel-replica-writer"
            assert step["judge_report_path"] in step["expected_outputs"]


def test_only_one_replica_writer_definition_exists():
    agents = ROOT / ".claude" / "agents"
    assert (agents / "novel-replica-writer.md").is_file()
    assert not (agents / "novel-style-replica-writer.md").exists()
    assert not (agents / "novel-voice-sample-writer.md").exists()
    text = (agents / "novel-replica-writer.md").read_text(encoding="utf-8")
    assert "style-replica-draft" in text and "voice-sample-draft" in text
    assert "不写复刻终稿" in text and "voice_samples.json" in text


def test_style_replica_writer_hook_contract():
    prompt = (
        "PLAN_ID: p\nSTEP: 3\nPROJECT: styles/book\nCLUSTER_ID: cluster_001\n"
        "MODE: style-replica-draft\nSTYLE_SKILL_PATH: skill_v0.md\n"
        "SCENES_DIR: 复刻测试/v0/claude_scenes\n"
        "按指定 skill 亲笔写场景草稿并落代理回执，提示长度足够。"
    )
    assert gates.check_agent_injection(
        prompt, "distill-style replica", "novel-replica-writer",
        plan_state="ok",
    )["ok"]
    broken = prompt.replace("STYLE_SKILL_PATH: skill_v0.md\n", "")
    result = gates.check_agent_injection(
        broken, "distill-style replica", "novel-replica-writer",
        plan_state="ok",
    )
    assert not result["ok"] and "STYLE_SKILL_PATH" in result["msg"]


def test_voice_sample_writer_hook_contract():
    prompt = (
        "PLAN_ID: p\nSTEP: 3\nPROJECT: novels/book\nMODE: voice-sample-draft\n"
        "CHARACTER_ID: c1\nMATERIAL_PATH: material.json\nVOICE_DNA_PATH: dna.json\n"
        "OUTPUT_DIR: drafts\n按真实素材亲笔写角色样本草稿并落代理回执。"
    )
    assert gates.check_agent_injection(
        prompt, "distill-character voice samples", "novel-replica-writer",
        plan_state="ok",
    )["ok"]
    broken = prompt.replace("VOICE_DNA_PATH: dna.json\n", "")
    result = gates.check_agent_injection(
        broken, "distill-character voice samples", "novel-replica-writer",
        plan_state="ok",
    )
    assert not result["ok"] and "VOICE_DNA_PATH" in result["msg"]


def test_replica_writer_hook_rejects_unknown_mode():
    result = gates.check_agent_injection(
        "PLAN_ID: p\nSTEP: 3\nPROJECT: p\nMODE: unknown\n任务内容足够长。",
        "distill replica", "novel-replica-writer", plan_state="ok",
    )
    assert not result["ok"] and "MODE" in result["msg"]


def test_plan_tracker_rejects_plain_artifact_as_agent_receipt(tmp_path):
    plain = tmp_path / "claude_scene_jobs.json"
    plain.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    assert not plan_tracker._verify_replica_receipt(plain, plan_id="p", step_n=3)


def test_plan_tracker_validates_direct_receipt_content_and_step(tmp_path):
    report = _style_receipt(tmp_path / "scenes")
    assert plan_tracker._verify_replica_receipt(report, plan_id="p", step_n=3)
    assert not plan_tracker._verify_replica_receipt(report, plan_id="other", step_n=3)
    assert not plan_tracker._verify_replica_receipt(report, plan_id="p", step_n=4)
    (report.parent / "scene_001.txt").unlink()
    assert not plan_tracker._verify_replica_receipt(report, plan_id="p", step_n=3)


def test_plan_tracker_validates_batch_against_each_source_receipt(tmp_path):
    source = _style_receipt(
        tmp_path / "job", job_key="digest:run:cluster_001", digest="digest",
    )
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({
        "schema_version": "novel-replica-writer.receipt.v1",
        "agent": "novel-replica-writer",
        "mode": "style-replica-batch",
        "plan_id": "p",
        "step": "4",
        "completed": True,
        "job_count": 1,
        "jobs": [{
            "job_key": "digest:run:cluster_001",
            "cluster_id": "cluster_001",
            "candidate_skill_digest": "digest",
            "agent_report": str(source),
        }],
    }), encoding="utf-8")
    assert plan_tracker._verify_replica_receipt(batch, plan_id="p", step_n=4)
    source.unlink()
    assert not plan_tracker._verify_replica_receipt(batch, plan_id="p", step_n=4)
