"""阶段1 测试: patch_applier (确定性) + optimizer 验收层 + optimizer_jobs (fake agent 产物)。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "scripts"))

from skill_opt import optimizer, optimizer_jobs, patch_applier  # noqa: E402


# ---------- patch_applier ----------

SKILL_SAMPLE = """## 段1 这是第一段的标题

内容 A 第一行
内容 A 第二行

## 段2 这是中间段

内容 B

## 段3 末尾段

内容 C"""


def test_apply_replace_succeeds():
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
            "new": "## 段2 新中间段\n\n新内容",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "新中间段" in r.new_text
    assert "内容 B" not in r.new_text
    assert len(r.applied) == 1
    assert len(r.rejected) == 0


def test_apply_delete_succeeds():
    patches = [
        {
            "op": "delete",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "段2" not in r.new_text
    assert "段1" in r.new_text
    assert "段3" in r.new_text


def test_apply_add_after_anchor_succeeds():
    patches = [
        {
            "op": "add",
            "after_anchor": "## 段1 这是第一段的标题",
            "new": "## 新插入段\n\n插入内容",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert "新插入段" in r.new_text
    # 顺序: 段1 → 新插入 → 段2 → 段3
    idx1 = r.new_text.index("段1")
    idxn = r.new_text.index("新插入段")
    idx2 = r.new_text.index("段2")
    assert idx1 < idxn < idx2


def test_apply_add_no_anchor_inserts_at_head():
    patches = [{"op": "add", "new": "## 文首新段\n\nx"}]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert r.new_text.startswith("## 文首新段")


def test_apply_anchor_not_found_rejected():
    patches = [
        {
            "op": "replace",
            "anchor": "## 不存在的段",
            "old": "xxx",
            "new": "yyy",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert not r.success
    assert len(r.rejected) == 1
    assert "找不到" in r.rejected[0][1]


def test_apply_old_mismatch_rejected():
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "完全错误的旧内容",  # anchor 命中但 old 不对
            "new": "yyy",
        }
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert not r.success
    assert "不匹配" in r.rejected[0][1]


def test_apply_invalid_op_rejected():
    r = patch_applier.apply_patches(SKILL_SAMPLE, [{"op": "rewrite", "new": "x"}])
    assert not r.success
    assert "op 必须是" in r.rejected[0][1]


def test_apply_max_patches_truncates():
    """超过 max_patches 应截断,不应用多余的。"""
    patches = [{"op": "add", "new": f"段{i}"} for i in range(6)]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches, max_patches=3)
    # 只允许应用前 3 条 (本测试都是合法 add)
    assert len(r.applied) == 3


def test_apply_partial_success():
    """1 条好 + 1 条坏 → success=True, applied 1 / rejected 1。"""
    patches = [
        {
            "op": "replace",
            "anchor": "## 段2 这是中间段",
            "old": "## 段2 这是中间段\n\n内容 B",
            "new": "## 段2 新\n\n新",
        },
        {  # 这条坏
            "op": "delete",
            "anchor": "## 不存在",
            "old": "x",
        },
    ]
    r = patch_applier.apply_patches(SKILL_SAMPLE, patches)
    assert r.success
    assert len(r.applied) == 1
    assert len(r.rejected) == 1
    assert "段2 新" in r.new_text


def test_apply_missing_required_fields_rejected():
    r = patch_applier.apply_patches(SKILL_SAMPLE, [{"op": "add"}])  # 缺 new
    assert not r.success
    assert "new" in r.rejected[0][1]


# ---------- optimizer 验收层 (novel-skill-author 产物契约) ----------


def _traj(cid: str = "auto_001", reward: float = 0.5) -> dict:
    return {"cluster_id": cid, "reward": reward, "components": {}, "replica_path": ""}


def test_accept_patches_strict_json():
    text = json.dumps(
        {"patches": [{"op": "replace", "anchor": "## A", "old": "## A\n\nx", "new": "## A\n\ny"}]},
        ensure_ascii=False,
    )
    patches = optimizer.accept_patches(text)
    assert len(patches) == 1
    assert patches[0]["op"] == "replace"


def test_accept_patches_caps_at_max_patches():
    """agent 写 10 条,只取前 4 (L_t 硬截断)。"""
    text = json.dumps({"patches": [{"op": "add", "new": f"段{i}"} for i in range(10)]},
                      ensure_ascii=False)
    patches = optimizer.accept_patches(text, max_patches=4)
    assert len(patches) == 4


def test_accept_patches_empty_list_is_valid():
    """空 patches = agent 明确提案无可改,合法产物。"""
    assert optimizer.accept_patches(json.dumps({"patches": []})) == []


def test_accept_patches_rejects_malformed_product():
    """坏 JSON / 顶层结构错 / 条目非 object → None (job 保持 pending)。"""
    assert optimizer.accept_patches("我无法生成 patch,请提供更多上下文。") is None
    assert optimizer.accept_patches("[1, 2]") is None
    assert optimizer.accept_patches(json.dumps({"patches": "oops"})) is None
    assert optimizer.accept_patches(json.dumps({"patches": [1]})) is None


def test_task_context_includes_reject_buffer():
    """reject buffer 不为空时,任务上下文必须包含 [REJECT_BUFFER]。"""
    rejects = [
        {
            "patch": {"op": "replace", "old": "a", "new": "b"},
            "reward_before": 0.8,
            "reward_after": 0.7,
            "reason": "测试",
        }
    ]
    ctx = optimizer.build_task_context(
        skill_text="x", trajectories=[], rejects=rejects,
    )
    assert "REJECT_BUFFER" in ctx


def test_task_context_marks_protected_sections():
    ctx = optimizer.build_task_context(
        skill_text="x",
        trajectories=[],
        protected_sections=["## 作者数值契约表", "## 句长基线"],
    )
    assert "PROTECTED" in ctx
    assert "作者数值契约表" in ctx


def test_task_context_carries_skill_minibatch_and_budget():
    ctx = optimizer.build_task_context(
        skill_text="## A\n\n骨架内容",
        trajectories=[_traj()],
        skill_version="ep1_step0",
        max_patches=3,
    )
    assert "[ROLLOUT MINIBATCH]" in ctx
    assert "骨架内容" in ctx
    assert "ep1_step0" in ctx
    assert "≤3" in ctx


# ---------- optimizer_jobs (patch 提案任务合同) ----------


def _require(tmp_path: Path, skill: Path, step_tag: str = "ep1_step0", l_t: int = 4):
    return optimizer_jobs.require_patch_job(
        skill_path=skill,
        out_root=tmp_path,
        step_tag=step_tag,
        trajectories=[_traj()],
        protected_sections=["## 量化约束"],
        rejects=[],
        max_patches=l_t,
    )


def _mk_skill(tmp_path: Path, text: str = "## A\n\nx") -> Path:
    skill = tmp_path / "skill.md"
    skill.write_text(text, encoding="utf-8")
    return skill


def test_missing_proposal_registers_pending_and_raises(tmp_path):
    skill = _mk_skill(tmp_path)
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill)
    manifest = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))
    job = manifest["jobs"][0]
    assert job["status"] == "pending"
    assert job["agent"] == "novel-skill-author"
    assert job["mode"] == "patch"
    # job 素材自包含: skill 快照 + 上下文 + 输出位置
    batch = json.loads(Path(job["trajectory_batch_path"]).read_text(encoding="utf-8"))
    assert batch["skill_digest"] == job["skill_digest"]
    assert batch["max_patches"] == 4
    assert batch["protected_sections"] == ["## 量化约束"]
    assert "[ROLLOUT MINIBATCH]" in batch["context_text"]
    assert batch["output_path"] == job["patches_path"]
    assert Path(job["skill_snapshot_path"]).read_text(encoding="utf-8") == "## A\n\nx"


def test_ready_proposal_returns_accepted_patches(tmp_path):
    skill = _mk_skill(tmp_path)
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill)
    job = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"][0]
    Path(job["patches_path"]).write_text(
        json.dumps({"patches": [{"op": "add", "new": "## 新段\n\n内容"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    patches, job_dir = _require(tmp_path, skill)
    assert patches == [{"op": "add", "new": "## 新段\n\n内容"}]
    assert job_dir == optimizer_jobs.job_dir_for(
        tmp_path, "ep1_step0", job["skill_digest"])
    manifest = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert manifest["jobs"][0]["status"] == "ready"


def test_ready_proposal_truncates_over_budget(tmp_path):
    skill = _mk_skill(tmp_path)
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill, l_t=2)
    job = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"][0]
    Path(job["patches_path"]).write_text(
        json.dumps({"patches": [{"op": "add", "new": f"段{i}"} for i in range(6)]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    patches, _ = _require(tmp_path, skill, l_t=2)
    assert len(patches) == 2


def test_malformed_proposal_stays_pending(tmp_path):
    """agent 产物结构不合格 → 不当空提案,仍是 required 缺件。"""
    skill = _mk_skill(tmp_path)
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill)
    job = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"][0]
    Path(job["patches_path"]).write_text("这不是 JSON", encoding="utf-8")
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill)
    manifest = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert manifest["jobs"][0]["status"] == "pending"


def test_patch_jobs_digest_isolated(tmp_path):
    """不同 skill 内容 → 不同 digest → 独立 job 目录,互不串稿。"""
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("candidate A", encoding="utf-8")
    second.write_text("candidate B", encoding="utf-8")
    for skill in (first, second):
        with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
            _require(tmp_path, skill)
    jobs = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["skill_digest"] != jobs[1]["skill_digest"]
    assert jobs[0]["trajectory_batch_path"] != jobs[1]["trajectory_batch_path"]


def test_verified_batch_receipt_requires_all_ready(tmp_path):
    skill = _mk_skill(tmp_path)
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        _require(tmp_path, skill)
    # pending job → 批次回执拒绝
    with pytest.raises(optimizer_jobs.OptimizerJobsRequiredError):
        optimizer_jobs.write_verified_batch_receipt(
            out_root=tmp_path, plan_id="p-1", step=4,
            output=tmp_path / "receipt.json",
        )
    job = json.loads(
        optimizer_jobs.manifest_path(tmp_path).read_text(encoding="utf-8"))["jobs"][0]
    Path(job["patches_path"]).write_text(
        json.dumps({"patches": [{"op": "add", "new": "## 新段\n\n内容"}]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "patch_receipts_step4.json"
    receipt = optimizer_jobs.write_verified_batch_receipt(
        out_root=tmp_path, plan_id="p-1", step=4, output=output,
    )
    assert receipt["agent"] == "novel-skill-author"
    assert receipt["mode"] == "patch-proposal-batch"
    assert receipt["job_count"] == 1
    assert receipt["jobs"][0]["patch_count"] == 1
    assert output.is_file()


def test_verified_batch_receipt_rejects_empty_manifest(tmp_path):
    with pytest.raises(ValueError):
        optimizer_jobs.write_verified_batch_receipt(
            out_root=tmp_path, plan_id="p-1", step=4,
            output=tmp_path / "receipt.json",
        )


def test_verified_batch_receipt_requires_plan_id(tmp_path):
    with pytest.raises(ValueError):
        optimizer_jobs.write_verified_batch_receipt(
            out_root=tmp_path, plan_id="", step=4,
            output=tmp_path / "receipt.json",
        )


# ---------- S1 安全锁: IMMUTABLE_ANCHORS ----------

def test_immutable_rejects_delete_anti_slop_section():
    """S1: 删除含"反模式"/"禁用词"关键词的段 → 无条件拒绝。"""
    skill = "## 反模式（绝不做）\n\n* 拉黑高频雷词\n\n## 正常段\n\n内容"
    patches = [
        {"op": "delete", "anchor": "## 反模式（绝不做）", "old": "## 反模式（绝不做）\n\n* 拉黑高频雷词"}
    ]
    r = patch_applier.apply_patches(skill, patches)
    assert not r.success
    assert "IMMUTABLE" in r.rejected[0][1]


def test_immutable_rejects_replace_banned_words():
    """S1: replace 含"禁用词"关键词 → 拒绝。"""
    skill = "## 禁用词清单\n\n顿时/紧锁\n\n## 其他\n\n内容"
    patches = [
        {"op": "replace", "anchor": "## 禁用词清单", "old": "## 禁用词清单\n\n顿时/紧锁", "new": "## 自由词\n\n随意"}
    ]
    r = patch_applier.apply_patches(skill, patches)
    assert not r.success
    assert "IMMUTABLE" in r.rejected[0][1]


def test_immutable_allows_add_near_protected():
    """S1: add 操作不受 IMMUTABLE 限制（加东西可以）。"""
    skill = "## 反模式（绝不做）\n\n* 雷词\n\n## 正常段\n\n内容"
    patches = [
        {"op": "add", "after_anchor": "## 反模式", "new": "## 补充禁令\n\n新增一条"}
    ]
    r = patch_applier.apply_patches(skill, patches)
    assert r.success


def test_immutable_allows_normal_replace():
    """S1: 不含 IMMUTABLE 关键词的正常 replace → 允许。"""
    skill = "## 句式与节奏\n\n内容 A\n\n## 段落与标点\n\n内容 B"
    patches = [
        {"op": "replace", "anchor": "## 句式与节奏", "old": "## 句式与节奏\n\n内容 A", "new": "## 句式与节奏\n\n改进内容"}
    ]
    r = patch_applier.apply_patches(skill, patches)
    assert r.success
