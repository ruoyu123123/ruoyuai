#!/usr/bin/env python3
"""distill-style.plan.json 程序驱动 e2e（阶段3·dev·fake runner/judge·不调 API）。

验 orchestrator 能机械驱动 10 步全程蒸馏 DAG（字段格式/data_flow list 索引/复刻先于 SFS/
C01 收敛闸 step5.5）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import orchestrator as orc  # noqa: E402


def _fake_runner(project_root: Path):
    """模拟各步脚本产出 expected_outputs（不真跑·不调 API）。"""
    def runner(cmd, *, repo_root=None, label="step"):
        if "ingest_author_text" in cmd:
            (project_root / "原文").mkdir(parents=True, exist_ok=True)
            for ch in (1, 2):
                (project_root / "原文" / f"第{ch}章.txt").write_text("正文", encoding="utf-8")
        elif "cluster_segmenter" in cmd:
            (project_root / "cluster_index.json").write_text(json.dumps(
                {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 2]}]},
                ensure_ascii=False), encoding="utf-8")
        elif "consolidate_author_profile" in cmd:
            (project_root / "作者风格.json").write_text("{}", encoding="utf-8")
        elif "distill_reflect" in cmd and "skill-version 0" in cmd:
            (project_root / "skill_v0.md").write_text("# v0", encoding="utf-8")
        elif "distill_reflect" in cmd:
            (project_root / "skill_v1.md").write_text("# v1", encoding="utf-8")
        elif "distill_replicate" in cmd:
            # 验 data_flow first_cluster_id 已解析（无残留占位符）
            assert "<first_cluster_id>" not in cmd, "first_cluster_id 未解析"
            assert "cluster_001" in cmd
            f = project_root / "复刻测试" / "v0" / "replica.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("复刻文本", encoding="utf-8")
        elif "style_evaluator" in cmd:
            # step4 写 eval_v0.json；step5.5 写 eval_v1.json（按 --output 路径落盘）
            out = "eval_v1.json" if "eval_v1.json" in cmd else "eval_v0.json"
            f = project_root / "对比报告" / out
            f.parent.mkdir(parents=True, exist_ok=True)
            sfs = 88 if out == "eval_v1.json" else 85  # v1 略优 → 收敛采纳 v1
            f.write_text(json.dumps({"sfs_quick": sfs, "grade": "B"}), encoding="utf-8")
        elif "distill_convergence_gate" in cmd:
            # 🔴 2026-06-27 C01：收敛闸选中 skill 落 skill_v2.md（finalize 出货最新版）
            (project_root / "skill_v2.md").write_text("# v2 (selected)", encoding="utf-8")
        elif "finalize_distill" in cmd:
            (project_root / "skill_FINAL.md").write_text("# FINAL", encoding="utf-8")
            (project_root / "作者风格_FINAL.json").write_text("{}", encoding="utf-8")
        # arc_aggregator / chapter_metrics / git / verify → no-op 成功
        return 0
    return runner


def test_distill_plan_drives_10_steps():
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "测试风格"
        (proj / "蒸馏进度" / ".wal").mkdir(parents=True)
        (proj / "蒸馏进度" / ".wal" / "raw_author_text.txt").write_text(
            "第1章 标题\n正文\n第2章 标题\n正文", encoding="utf-8")
        summary = orc.run_command(
            "distill-style", str(proj), auto_pilot=True,
            script_runner=_fake_runner(proj), judge_dispatch=None,
            pause_handler=None)
        assert summary.paused_at is None, f"卡在 step {summary.paused_at}"
        # 全程产物落地
        assert (proj / "cluster_index.json").exists()
        assert (proj / "作者风格.json").exists()
        assert (proj / "skill_v0.md").exists()          # 破 chicken-egg
        assert (proj / "复刻测试" / "v0" / "replica.txt").exists()  # 复刻先于 SFS
        assert (proj / "对比报告" / "eval_v0.json").exists()
        assert (proj / "skill_v1.md").exists()          # C01 step5 reflect 强契约(skip_output_allowed=false)
        assert (proj / "skill_v2.md").exists()          # C01 step5.5 收敛闸选中 skill
        assert (proj / "skill_FINAL.md").exists()       # 定稿出货


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
