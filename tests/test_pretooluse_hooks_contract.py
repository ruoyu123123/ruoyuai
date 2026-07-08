#!/usr/bin/env python3
"""PreToolUse hook wrapper subprocess contracts.

这些测试锁 wrapper 层行为：stdin JSON → 真脚本 → exit 2。纯函数测试不够，因为
Claude Code hook 是否阻断取决于进程退出码。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOKS = _ROOT / "core" / "claude-home" / "hooks"
_SCRIPTS = _ROOT / "core" / "scripts"


def _run_hook(script: str, payload: dict, *, cwd: Path | None = None,
              env: dict | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(_HOOKS / script)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        cwd=str(cwd or _ROOT),
        env=merged_env,
        encoding="utf-8",
    )


def _agent_payload(prompt: str, subagent_type: str = "novel-writer") -> dict:
    return {
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": subagent_type,
            "description": "写故事块",
            "prompt": prompt,
        },
    }


def _bash_payload(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _write_runtime_plan(root: Path, plan_id: str, plan: dict) -> Path:
    plans = root / "core" / "claude-home" / ".plans"
    plans.mkdir(parents=True, exist_ok=True)
    path = plans / f"{plan_id}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path


def test_agent_gate_wrapper_exits_2_when_novel_plan_id_missing():
    prompt = (
        "PROJECT: p\nCLUSTER_ID: cluster_001\nMODE: ecas\n"
        "RESEARCH_REF: _数据库/.research_cache/cluster_001.md\n"
        "写正文 cluster_001 这是一段足够长的提示用于过长度下限"
    )
    proc = _run_hook("pretooluse_agent_gate.py", _agent_payload(prompt))
    assert proc.returncode == 2, proc.stderr
    assert "PLAN_ID" in proc.stderr


def test_agent_gate_wrapper_exits_2_when_plan_id_not_found():
    prompt = (
        "PLAN_ID: missing_outline_20990101T000000\nSTEP: 2\n"
        "PROJECT: p\nCLUSTER_ID: cluster_001\nMODE: ecas\n"
        "RESEARCH_REF: _数据库/.research_cache/cluster_001.md\n"
        "写正文 cluster_001 这是一段足够长的提示用于过长度下限"
    )
    proc = _run_hook("pretooluse_agent_gate.py", _agent_payload(prompt))
    assert proc.returncode == 2, proc.stderr
    assert "not_found" in proc.stderr or "PLAN_ID" in proc.stderr


def test_step_research_wrapper_exits_2_when_research_ref_missing(tmp_path):
    plan_id = "Book_outline_20990101T000000"
    _write_runtime_plan(tmp_path, plan_id, {
        "id": plan_id,
        "project": "Book",
        "steps": [{"n": 3, "research_ref": "_数据库/.research_cache/missing.json"}],
    })
    command = f"python core/scripts/plan_tracker.py step {plan_id} --n 3"
    proc = _run_hook(
        "pretooluse_step_research.py",
        _bash_payload(command),
        cwd=tmp_path,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert proc.returncode == 2, proc.stderr
    assert "research_ref" in proc.stderr or "调研先行" in proc.stderr


def test_anti_skip_wrapper_exits_2_when_required_output_skipped(tmp_path):
    plan_id = "Book_outline_20990101T000001"
    _write_runtime_plan(tmp_path, plan_id, {
        "id": plan_id,
        "project": "Book",
        "steps": [{
            "n": 7,
            "name": "plan-end",
            "expected_outputs": ["_数据库/.wal/outline_git_snapshot.json"],
            "skip_output_allowed": False,
        }],
    })
    command = f"python core/scripts/plan_tracker.py step {plan_id} --skip-output --n 7"
    proc = _run_hook(
        "pretooluse_plan_step_anti_skip.py",
        _bash_payload(command),
        cwd=tmp_path,
        env={"CLAUDE_PROJECT_DIR": str(tmp_path)},
    )
    assert proc.returncode == 2, proc.stderr
    assert "skip-output" in proc.stderr


def test_subsystems_wrapper_step4_scaffold_exists_only_but_step7_content_blocks(tmp_path):
    sys.path.insert(0, str(_SCRIPTS))
    import scaffold_subsystems as scaf  # noqa: WPS433

    project = "Book"
    root = tmp_path / "workspace" / "novels" / project
    db = root / "_数据库"
    scaf.cmd_emit(["--db-dir", str(db)])

    plan_id = "Book_outline_20990101T000002"
    plan_path = db / ".plans" / f"{plan_id}.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({
        "id": plan_id,
        "project": project,
        "steps": [
            {"n": 4, "name": "scaffold-34-subsystems"},
            {"n": 7, "name": "plan-end"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}
    step4 = _run_hook(
        "pretooluse_subsystems_gate.py",
        _bash_payload(f"python core/scripts/plan_tracker.py step {plan_id} --n 4"),
        cwd=tmp_path,
        env=env,
    )
    assert step4.returncode == 0, step4.stderr

    step7 = _run_hook(
        "pretooluse_subsystems_gate.py",
        _bash_payload(f"python core/scripts/plan_tracker.py step {plan_id} --n 7"),
        cwd=tmp_path,
        env=env,
    )
    assert step7.returncode == 2, step7.stderr
    assert "载荷子系统空货架" in step7.stderr or "inert" in step7.stderr


# ════════════════════════════════════════════════════════════════════════
# 🔴 2026-07-08 CJK plan_id fail-open 修复回归锁（真机验证抓出）
# ════════════════════════════════════════════════════════════════════════

def test_anti_skip_not_fail_open_for_cjk_plan_id(tmp_path):
    """中文书名 plan_id + --skip-output：hook 必须提取到 plan_id 并（因 plan 缺失）exit 2，
    绝不因首字符类不含 CJK 而 pid_m=None → exit 0 静默放行（防跳步守卫对中文项目形同虚设）。"""
    cmd = ('python core/scripts/plan_tracker.py step '
           '"验证书_大天尊破产结算中心_001_cluster-write_XYZ" --n 3 --skip-output')
    r = _run_hook("pretooluse_plan_step_anti_skip.py", _bash_payload(cmd), cwd=tmp_path)
    assert r.returncode == 2, f"CJK plan_id 应被拦截(找不到 plan)，实际 rc={r.returncode} · {r.stderr}"
    assert "找不到 plan" in r.stderr


def test_step_research_resolves_ref_against_novel_project_dir(tmp_path):
    """research_ref（相对小说项目目录）须以 plan_file 反推的项目根解析·非仓库根。
    plan 在 <tmp>/_数据库/.plans/ → 项目根=<tmp> → research_ref「_数据库/.research_cache」
    解析到 <tmp>/_数据库/.research_cache。CJK plan_id 同时验 regex 修复。"""
    plan_id = "验证书_测试书_001_cluster-write_ABC"
    db_plans = tmp_path / "_数据库" / ".plans"
    db_plans.mkdir(parents=True)
    plan = {"steps": [{"n": 1, "research_ref": "_数据库/.research_cache"}]}
    (db_plans / f"{plan_id}.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    cmd = f'python core/scripts/plan_tracker.py step "{plan_id}" --n 1'
    env = {"CLAUDE_PROJECT_DIR": str(tmp_path)}

    # research_cache 缺失 → exit 2（正确拦截·证明基准解析到了项目根下的真实路径）
    r_missing = _run_hook("pretooluse_step_research.py", _bash_payload(cmd), cwd=tmp_path, env=env)
    assert r_missing.returncode == 2, f"缺 research_cache 应拦截·rc={r_missing.returncode}"

    # research_cache 存在 → exit 0（证明基准是项目根·非仓库根·否则永远误拦）
    (tmp_path / "_数据库" / ".research_cache").mkdir(parents=True)
    r_ok = _run_hook("pretooluse_step_research.py", _bash_payload(cmd), cwd=tmp_path, env=env)
    assert r_ok.returncode == 0, f"有 research_cache 应放行·rc={r_ok.returncode} · {r_ok.stderr}"
