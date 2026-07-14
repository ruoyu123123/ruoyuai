"""skill_opt.reward_sfs — 蒸馏路线 reward (SFS verifier-grounded)

业界源 arXiv:2605.23904 §4 SearchQA 实验:
论文 SearchQA 用程序验证器 (是否找到正确答案) 算 binary reward。
我们等价物 = SFS 评分(复刻 vs 原作者基线)。

数据流:
  cluster_id → 读 原文/<cluster 内的章节>.txt → reference 文本
            → distill_replicate(candidate_skill, cluster_id) → 复刻 .txt
            → style_evaluator(复刻, ref, multi-ref-from-dir) → sfs_quick 分数
            → reward = sfs_quick / 100 ∈ [0, 1]

依赖:
- 风格库结构 (workspace/styles/<书名>/): cluster_index.json + 原文/第NNN章.txt
- distill_replicate.py 已就绪
- style_evaluator.py 已就绪

与现有写作路线 reward.py 关系:
- reward.py: 读 _数据库/.audit/.judge_reports → 4 binary 信号 (写作路线)
- reward_sfs.py: 读 style_evaluator 输出 → SFS 分数 (蒸馏路线)
两者解耦, train.py 通过 reward_fn 参数选用。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from . import scene_jobs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distill_av_verify import JOBS_MANIFEST_NAME, jobs_dir_for  # noqa: E402
from proc_utils import run_utf8  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
DISTILL_REPLICATE = REPO_ROOT / "core" / "scripts" / "distill_replicate.py"
STYLE_EVALUATOR = REPO_ROOT / "core" / "scripts" / "style_evaluator.py"
DISTILL_AV_VERIFY = REPO_ROOT / "core" / "scripts" / "distill_av_verify.py"


class AvJudgeJobsRequiredError(RuntimeError):
    """AV 配对判别需要主代理按 av_judge_jobs.json spawn novel-av-judge 补件。"""

# S1 reward slop penalty: 复刻文本含 AI 腔高频词 → reward 扣分
# 让"skill 删反 AI 腔约束后复刻文本 AI 腔回潮"在 reward 可见
AI_SLOP_WORDS = [
    "顿时", "紧锁", "显然", "似乎", "此刻", "淡淡", "心中一凛",
    "眼中闪过一丝", "微微挑眉", "仿佛", "嘴角勾起一抹", "深吸一口气",
    "缓缓地说", "沉吟片刻", "不容置疑", "波涛汹涌",
    "与此同时", "值得一提的是", "不仅如此", "事实上",
]
SLOP_PENALTY_PER_HIT = 0.005  # 每命中一个扣 0.5% reward


@dataclass
class SfsReward:
    sfs_score: float            # 0-100 原始分
    reward: float               # sfs_score / 100, [0, 1]
    replica_path: str
    eval_json_path: str
    duration_sec: float
    distill_exit_code: int
    eval_exit_code: int
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sfs_score": self.sfs_score,
            "reward": self.reward,
            "replica_path": self.replica_path,
            "eval_json_path": self.eval_json_path,
            "duration_sec": self.duration_sec,
            "distill_exit_code": self.distill_exit_code,
            "eval_exit_code": self.eval_exit_code,
            "raw": self.raw,
        }


def _run_distill_replicate(
    style_skill: Path,
    cluster_id: str,
    project: Path,
    output: Path,
    claude_scenes_dir: Path,
    timeout: int = 1500,
) -> tuple[int, float]:
    """subprocess 调 distill_replicate.py。"""
    cmd = [
        sys.executable,
        str(DISTILL_REPLICATE),
        "--style-skill", str(style_skill),
        "--mode", "cluster",
        "--cluster-ref", cluster_id,
        "--project", str(project),
        "--claude-scenes-dir", str(claude_scenes_dir),
        "--output", str(output),
    ]
    t0 = time.time()
    try:
        proc = run_utf8(cmd, timeout=timeout)
        return proc.returncode, time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, time.time() - t0


def _run_style_evaluator(
    replica_path: Path,
    ref_dir: Path,
    output_json: Path,
    multi_ref_count: int = 5,
    multi_ref_seed: int = 42,
    timeout: int = 300,
) -> int:
    """subprocess 调 style_evaluator.py 多基线模式。"""
    cmd = [
        sys.executable,
        str(STYLE_EVALUATOR),
        "--gen", str(replica_path),
        "--multi-ref-from-dir", str(ref_dir),
        "--multi-ref-count", str(multi_ref_count),
        "--multi-ref-seed", str(multi_ref_seed),
        "--output", str(output_json),
    ]
    try:
        proc = run_utf8(cmd, timeout=timeout)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def _run_av_verify(project: Path, cluster_id: str, replica: Path, output: Path,
                   timeout: int = 300) -> int:
    command = [
        sys.executable, str(DISTILL_AV_VERIFY),
        "--project", str(project),
        "--cluster-id", cluster_id,
        "--replica", str(replica),
        "--output", str(output),
    ]
    try:
        # 不捕获输出（直通父进程流）·仍强制子进程 UTF-8 env
        return run_utf8(command, capture_output=False, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return 124


def _read_sfs_score(eval_json: Path) -> float:
    """从 style_evaluator 输出取 sfs_quick 字段。"""
    if not eval_json.exists():
        return 0.0
    try:
        d = json.loads(eval_json.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    # sfs_quick 是 0-100 总分 (实测 distill 旧产物字段)
    s = d.get("sfs_quick") or d.get("sfs_score") or d.get("overall_score")
    if s is None:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def reward_for_cluster_sfs(
    style_skill: Path,
    project_root: Path,
    cluster_id: str,
    out_root: Path,
    run_id: str,
    multi_ref_count: int = 5,
    multi_ref_seed: int = 42,
) -> SfsReward:
    """蒸馏路线单次 reward 计算 (rollout 阶段调用)。

    Args:
        style_skill: 当前候选 skill.md 路径
        project_root: workspace/styles/<书名>/
        cluster_id: 如 "auto_001"
        out_root: _skillopt/ 输出根
        run_id: 唯一标识(同一 step 多次调用区分)
        multi_ref_count: SFS 多基线抽样数 (论文 SearchQA 范式)
        multi_ref_seed: 复现种子

    Returns:
        SfsReward (含 sfs_score 0-100, reward [0,1])
    """
    t0 = time.time()
    replica_dir = out_root / "replicas" / run_id
    replica_dir.mkdir(parents=True, exist_ok=True)
    replica_path = replica_dir / f"{cluster_id}_replica.txt"
    eval_json = replica_dir / f"{cluster_id}_eval.json"
    av_json = replica_dir / f"{cluster_id}_av.json"
    claude_scenes_dir = scene_jobs.require_scene_job(
        skill_path=style_skill,
        cluster_id=cluster_id,
        out_root=out_root,
        run_id=run_id,
    )

    # 1. 复刻（幂等：同 run_id 的 replica 已落盘则复用——AV 两段式 exit 2 补件后
    #    以同一 run_id 恢复重入时，replica 不重润色 → 输入 digest 稳定 → verdict 不作废）
    if replica_path.exists():
        distill_ec, distill_dur = 0, 0.0
    else:
        distill_ec, distill_dur = _run_distill_replicate(
            style_skill=style_skill,
            cluster_id=cluster_id,
            project=project_root,
            output=replica_path,
            claude_scenes_dir=claude_scenes_dir,
        )
    if distill_ec != 0 or not replica_path.exists():
        return SfsReward(
            sfs_score=0.0,
            reward=0.0,
            replica_path=str(replica_path),
            eval_json_path="",
            duration_sec=time.time() - t0,
            distill_exit_code=distill_ec,
            eval_exit_code=-1,
            raw={
                "phase": "distill_failed",
                "distill_dur": distill_dur,
                "claude_scenes_dir": str(claude_scenes_dir),
            },
        )

    # required AV（两段式）：exit 2 + manifest 已渲染 = 投票任务 pending → 抛 required
    # （train.py exit 2 · 主代理 spawn novel-av-judge 补件后同 run_id 恢复）；其余非 0 = 硬失败。
    av_ec = _run_av_verify(project_root, cluster_id, replica_path, av_json)
    av_jobs_manifest = jobs_dir_for(av_json) / JOBS_MANIFEST_NAME
    if av_ec == 2 and av_jobs_manifest.exists() and not av_json.exists():
        raise AvJudgeJobsRequiredError(
            f"AV 配对判别待 novel-av-judge 补件: cluster={cluster_id}；"
            f"任务清单 {av_jobs_manifest}。主代理按 manifest spawn novel-av-judge "
            "写 verdict 与批次回执后，以同一 run_id 重跑恢复。"
        )
    if av_ec != 0 or not av_json.exists():
        raise RuntimeError(
            f"required AV 执行或报告失败: cluster={cluster_id} exit={av_ec} output={av_json}"
        )

    # 2. SFS 评分
    ref_dir = project_root / "原文"
    eval_ec = _run_style_evaluator(
        replica_path=replica_path,
        ref_dir=ref_dir,
        output_json=eval_json,
        multi_ref_count=multi_ref_count,
        multi_ref_seed=multi_ref_seed,
    )
    if eval_ec != 0:
        return SfsReward(
            sfs_score=0.0,
            reward=0.0,
            replica_path=str(replica_path),
            eval_json_path=str(eval_json),
            duration_sec=time.time() - t0,
            distill_exit_code=distill_ec,
            eval_exit_code=eval_ec,
            raw={"phase": "eval_failed"},
        )

    # 3. 取分 + S1 slop penalty
    sfs = _read_sfs_score(eval_json)
    base_reward = sfs / 100.0

    # 读复刻文本算 slop penalty (让删反 AI 腔约束在 reward 可见)
    slop_hits = 0
    try:
        replica_text = replica_path.read_text(encoding="utf-8", errors="replace")
        for word in AI_SLOP_WORDS:
            slop_hits += replica_text.count(word)
    except Exception:
        pass
    slop_penalty = slop_hits * SLOP_PENALTY_PER_HIT
    final_reward = max(0.0, base_reward - slop_penalty)

    return SfsReward(
        sfs_score=sfs,
        reward=final_reward,
        replica_path=str(replica_path),
        eval_json_path=str(eval_json),
        duration_sec=time.time() - t0,
        distill_exit_code=distill_ec,
        eval_exit_code=eval_ec,
        raw={
            "phase": "ok",
            "slop_hits": slop_hits,
            "slop_penalty": slop_penalty,
            "claude_scenes_dir": str(claude_scenes_dir),
            "skill_digest": scene_jobs.skill_digest(style_skill),
            "av_report_path": str(av_json),
        },
    )
