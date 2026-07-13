"""distill_av_verify.py 两段式测试 — AV 配对判别 scene_jobs 范式验收（北极星①⑤⑥）。

判别由 novel-av-judge agent 亲笔完成，本套件只测**确定性两段**（零模型调用）：
  首跑：从 av_judge rubric 渲染 N 个投票任务 prompt → 写 av_judge_jobs.json → exit 2=pending。
  二跑：逐票严格验收（4 维 verdict 必须「命中/走味」二选一 · 判走味必须给 reason ·
    schema 不符 = 该票退回 pending）+ 批次回执验收（agent 身份 / plan 绑定 / 逐 job SHA-256）
    → aggregate_verdicts 多数票聚合 → build_report 落盘 advisory 报告（永不 hard_gate）。

覆盖：[P] 首跑渲染（manifest 契约 / prompt 内容 / swap 分配 / exit 2 指引）；
  [V] 二跑验收聚合（多数票报告 / 溯源字段 / advisory）；
  [R] 严格验收退回（非法 verdict / 走味缺 reason / 坏 JSON / 回执缺失或 SHA 不符）；
  [D] 输入 digest 幂等（replica 变化 → 旧判别作废重新 pending）；
  [E] 输入错误 exit 2。
"""
import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import distill_av_verify as dav  # noqa: E402
from av_judge import DRIFT_VERDICT, MATCH_VERDICT  # noqa: E402

_DIM_NAMES = ["词汇选择", "句法", "话语连接词", "语用语气"]

_AUTHOR_TEXT = "他停下脚步。\n风很大。\n远处的灯一盏盏灭了，像有人在数着退场。\n" * 8
_REPLICA_TEXT = "她缓缓地停下了脚步，心中涌起一丝难以言喻的复杂情绪。\n然而，风很大。\n" * 8
# 单行文本锚（写盘经 universal newlines 可能变 \r\n · 跨行锚串会失配）
_AUTHOR_SNIP = "远处的灯一盏盏灭了"
_REPLICA_SNIP = "心中涌起一丝难以言喻的复杂情绪"


# ════════════════════════════════════════════════════════════════
# 夹具
# ════════════════════════════════════════════════════════════════

def _make_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """构造最小风格项目：cluster_index + 原文锚 + 仿写终稿。返回 (project, replica, output)。"""
    project = tmp_path / "style"
    (project / "原文").mkdir(parents=True)
    (project / "cluster_index.json").write_text(json.dumps({
        "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}],
    }, ensure_ascii=False), encoding="utf-8")
    (project / "原文" / "第001章.txt").write_text(_AUTHOR_TEXT, encoding="utf-8")
    replica = tmp_path / "replica.txt"
    replica.write_text(_REPLICA_TEXT, encoding="utf-8")
    output = project / "对比报告" / "av_v0.json"
    return project, replica, output


def _run(project: Path, replica: Path, output: Path) -> int:
    return dav.main([
        "--project", str(project), "--cluster-id", "cluster_001",
        "--replica", str(replica), "--output", str(output),
    ])


def _manifest(output: Path) -> dict:
    path = dav.jobs_dir_for(output) / dav.JOBS_MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def _verdict_text(drift_dims=(), reasons=True) -> str:
    dims = {}
    for name in _DIM_NAMES:
        if name in drift_dims:
            dims[name] = {"verdict": DRIFT_VERDICT,
                          "reason": (f"{name}露馅：句式模具化" if reasons else "")}
        else:
            dims[name] = {"verdict": MATCH_VERDICT, "reason": f"{name}贴合作者真迹"}
    return json.dumps({"dimensions": dims}, ensure_ascii=False)


def _write_votes(output: Path, per_vote_drifts: list) -> dict:
    """按 manifest 逐票写合法 verdict（per_vote_drifts: 每票的走味维 tuple 列表）。"""
    manifest = _manifest(output)
    for job, drifts in zip(manifest["jobs"], per_vote_drifts):
        Path(job["output_path"]).write_text(_verdict_text(drifts), encoding="utf-8")
    return manifest


def _write_receipt(output: Path, plan_id="p-test", step="3", agent=dav.AV_AGENT,
                   completed=True, sha_break=None) -> Path:
    """按 manifest 写批次回执（sha_break=某 job_id 时故意写错该票 SHA）。"""
    manifest = _manifest(output)
    entries = []
    for job in manifest["jobs"]:
        digest = hashlib.sha256(Path(job["output_path"]).read_bytes()).hexdigest()
        if job["job_id"] == sha_break:
            digest = "0" * 64
        entries.append({"job_id": job["job_id"], "output_path": job["output_path"],
                        "output_sha256": digest})
    receipt = {
        "schema_version": dav.RECEIPT_SCHEMA_VERSION,
        "agent": agent,
        "plan_id": plan_id,
        "step": step,
        "completed": completed,
        "jobs": entries,
    }
    receipt_path = Path(manifest["receipt_path"])
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return receipt_path


def _complete_all(output: Path, per_vote_drifts: list) -> None:
    _write_votes(output, per_vote_drifts)
    _write_receipt(output)


# ════════════════════════════════════════════════════════════════
# [P] 首跑：渲染投票任务 + manifest + exit 2 指引
# ════════════════════════════════════════════════════════════════

def test_P_first_run_writes_manifest_and_prompts_exit2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_N_SAMPLES", raising=False)
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    monkeypatch.delenv("AV_JUDGE_INTENT_DIM", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _manifest(output)
    assert manifest["contract"] == dav.JOBS_CONTRACT
    assert manifest["agent"] == "novel-av-judge"
    assert manifest["n_samples"] == 3
    assert manifest["inputs_digest"]
    assert [j["job_id"] for j in manifest["jobs"]] == ["vote_1", "vote_2", "vote_3"]
    assert all(j["status"] == "pending" for j in manifest["jobs"])
    # 每票 prompt 已渲染：系统判别纪律 + 两段文本 + 4 维 rubric + 输出 JSON schema
    for job in manifest["jobs"]:
        prompt = Path(job["prompt_path"]).read_text(encoding="utf-8")
        assert "作者验证" in prompt and "配对判别" in prompt
        assert _AUTHOR_SNIP in prompt and _REPLICA_SNIP in prompt
        for name in _DIM_NAMES:
            assert f'"{name}"' in prompt
        assert '"dimensions"' in prompt
    # 回执指示（agent 按 manifest 写批次回执）
    assert manifest["receipt_path"].endswith(dav.RECEIPT_NAME)
    assert manifest["receipt_schema"]["schema_version"] == dav.RECEIPT_SCHEMA_VERSION
    # stderr 给主代理补件指引（JOBS_MANIFEST_PATH 契约锚）
    err = capsys.readouterr().err
    assert "[PENDING]" in err and "novel-av-judge" in err and "JOBS_MANIFEST_PATH" in err


def test_P_default_no_swap_author_block_first(tmp_path, monkeypatch):
    """默认 env → 全票原向（作者真迹块先呈现 · swap=False）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _manifest(output)
    assert [j["swap"] for j in manifest["jobs"]] == [False, False, False]
    for job in manifest["jobs"]:
        prompt = Path(job["prompt_path"]).read_text(encoding="utf-8")
        assert prompt.index(_AUTHOR_SNIP) < prompt.index(_REPLICA_SNIP)


def test_P_position_swap_env_assigns_half(tmp_path, monkeypatch):
    """AV_JUDGE_POSITION_SWAP=on → 后一半投票任务换序呈现（N=3 → [F,F,T]）。"""
    monkeypatch.setenv("AV_JUDGE_POSITION_SWAP", "on")
    monkeypatch.delenv("AV_JUDGE_N_SAMPLES", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _manifest(output)
    assert [j["swap"] for j in manifest["jobs"]] == [False, False, True]
    swapped_prompt = Path(manifest["jobs"][2]["prompt_path"]).read_text(encoding="utf-8")
    assert swapped_prompt.index(_REPLICA_SNIP) < swapped_prompt.index(_AUTHOR_SNIP)


def test_P_first_run_idempotent_keeps_digest(tmp_path, monkeypatch):
    """同输入重复首跑幂等：digest / prompt 不变 · 仍 pending exit 2。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    first = _manifest(output)
    assert _run(project, replica, output) == 2
    second = _manifest(output)
    assert first["inputs_digest"] == second["inputs_digest"]
    assert [j["prompt_sha256"] for j in first["jobs"]] == \
           [j["prompt_sha256"] for j in second["jobs"]]


# ════════════════════════════════════════════════════════════════
# [V] 二跑：严格验收 + 多数票聚合 + advisory 报告
# ════════════════════════════════════════════════════════════════

def test_V_second_run_aggregates_majority_and_emits_report(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    # 3 票：2 票判「句法」走味 + 1 票全命中 → 多数票 drift=["句法"]
    _complete_all(output, [("句法",), ("句法",), ()])
    assert _run(project, replica, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"] == "drift"
    assert report["drift_dims"] == ["句法"]
    assert report["gate_level"] == "advisory"
    assert report["issue_code"] == "AV_TRAIT_DRIFT"
    assert report["profile_used"] == "novel-av-judge"
    assert report["n_samples"] == 3 and report["n_valid_samples"] == 3
    assert report["jobs_manifest"] == str((dav.jobs_dir_for(output) / dav.JOBS_MANIFEST_NAME).resolve())
    assert report["inputs_digest"] == _manifest(output)["inputs_digest"]
    # 每个走味维度是 advisory 待裁决项（可豁免 · 永不 hard_gate）
    assert all(i["gate_level"] == "advisory" for i in report["issues"])


def test_V_all_match_verdict_match_no_issues(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _complete_all(output, [(), (), ()])
    assert _run(project, replica, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"] == "match"
    assert report["issues"] == []


def test_V_minority_drift_smoothed_but_exposed(tmp_path, monkeypatch):
    """1/3 票误判走味被多数票平滑（不进 drift_dims）· 方差进 unstable_dims（透明）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _complete_all(output, [("词汇选择",), (), ()])
    assert _run(project, replica, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["drift_dims"] == []
    assert "词汇选择" in report["unstable_dims"]


def test_V_swap_flag_flows_into_report_transparency(tmp_path, monkeypatch):
    """swap 票的 _swapped 标志进聚合透明字段（n_swapped_samples · 不翻转判别）。"""
    monkeypatch.setenv("AV_JUDGE_POSITION_SWAP", "on")
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _complete_all(output, [("句法",), ("句法",), ("句法",)])
    assert _run(project, replica, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["n_swapped_samples"] == 1
    assert report["drift_dims"] == ["句法"]
    assert [d["swapped"] for d in report["sample_drift_detail"]] == [False, False, True]


# ════════════════════════════════════════════════════════════════
# [R] 严格验收退回（schema 不符 = 该票 pending · 回执缺失/不符 = 批次未完成）
# ════════════════════════════════════════════════════════════════

def test_R_nonbinary_verdict_rejected(tmp_path, monkeypatch):
    """verdict 非「命中/走味」精确二选一 → 该票退回 pending（exit 2）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _write_votes(output, [(), (), ()])
    bad = json.loads(_verdict_text())
    bad["dimensions"]["句法"]["verdict"] = "基本像"
    Path(manifest["jobs"][1]["output_path"]).write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    _write_receipt(output)
    assert _run(project, replica, output) == 2
    refreshed = _manifest(output)
    assert refreshed["jobs"][1]["status"] == "pending"
    assert "二选一" in refreshed["jobs"][1]["diag"]
    assert refreshed["jobs"][0]["status"] == "ready"


def test_R_drift_without_reason_rejected(tmp_path, monkeypatch):
    """判走味缺指证 reason → 该票退回 pending（agent 合约硬要求）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _manifest(output)
    for job, drifts in zip(manifest["jobs"], [(), (), ()]):
        Path(job["output_path"]).write_text(_verdict_text(drifts), encoding="utf-8")
    Path(manifest["jobs"][0]["output_path"]).write_text(
        _verdict_text(("语用语气",), reasons=False), encoding="utf-8")
    _write_receipt(output)
    assert _run(project, replica, output) == 2
    assert "reason" in _manifest(output)["jobs"][0]["diag"]


def test_R_broken_json_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    manifest = _write_votes(output, [(), (), ()])
    Path(manifest["jobs"][2]["output_path"]).write_text("{broken", encoding="utf-8")
    _write_receipt(output)
    assert _run(project, replica, output) == 2
    assert "解析失败" in _manifest(output)["jobs"][2]["diag"]


def test_R_missing_receipt_blocks(tmp_path, capsys, monkeypatch):
    """全票就绪但批次回执缺失 → exit 2（agent 必须写回执 · 防虚报完成）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _write_votes(output, [(), (), ()])
    assert _run(project, replica, output) == 2
    assert "批次回执" in capsys.readouterr().err
    # 写回执后放行
    _write_receipt(output)
    assert _run(project, replica, output) == 0


def test_R_receipt_sha_mismatch_blocks(tmp_path, monkeypatch):
    """回执逐 job SHA-256 与 verdict 文件不符 → exit 2（防产物落盘后被篡改/虚报）。"""
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _write_votes(output, [(), (), ()])
    _write_receipt(output, sha_break="vote_2")
    assert _run(project, replica, output) == 2


def test_R_receipt_wrong_agent_or_no_plan_blocks(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _write_votes(output, [(), (), ()])
    _write_receipt(output, agent="novel-writer")
    assert _run(project, replica, output) == 2
    _write_receipt(output, plan_id="")
    assert _run(project, replica, output) == 2
    _write_receipt(output)
    assert _run(project, replica, output) == 0


# ════════════════════════════════════════════════════════════════
# [D] 输入 digest 幂等：输入变化 → 旧判别作废重新 pending
# ════════════════════════════════════════════════════════════════

def test_D_replica_change_invalidates_old_verdicts(tmp_path, monkeypatch):
    monkeypatch.delenv("AV_JUDGE_POSITION_SWAP", raising=False)
    project, replica, output = _make_project(tmp_path)
    assert _run(project, replica, output) == 2
    _complete_all(output, [(), (), ()])
    assert _run(project, replica, output) == 0
    old_digest = _manifest(output)["inputs_digest"]
    # 仿写更新 → 旧 verdict / 回执作废 · 重新 pending（防陈旧判别冒充当前输入）
    replica.write_text(_REPLICA_TEXT + "\n他补了一句。\n", encoding="utf-8")
    assert _run(project, replica, output) == 2
    refreshed = _manifest(output)
    assert refreshed["inputs_digest"] != old_digest
    assert all(j["status"] == "pending" for j in refreshed["jobs"])
    assert not Path(refreshed["receipt_path"]).exists()


# ════════════════════════════════════════════════════════════════
# [E] 输入错误 exit 2
# ════════════════════════════════════════════════════════════════

def test_E_missing_replica_exit2(tmp_path):
    project, replica, output = _make_project(tmp_path)
    replica.unlink()
    assert _run(project, replica, output) == 2


def test_E_unknown_cluster_exit2(tmp_path):
    project, replica, output = _make_project(tmp_path)
    assert dav.main([
        "--project", str(project), "--cluster-id", "cluster_999",
        "--replica", str(replica), "--output", str(output),
    ]) == 2


def test_E_missing_author_anchor_exit2(tmp_path):
    project, replica, output = _make_project(tmp_path)
    (project / "原文" / "第001章.txt").unlink()
    assert _run(project, replica, output) == 2
