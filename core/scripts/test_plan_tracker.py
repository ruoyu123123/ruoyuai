#!/usr/bin/env python3
"""
test_plan_tracker.py — plan_tracker.py 单元测试

运行：
  python core/scripts/test_plan_tracker.py

覆盖：
  1.  create_plan 对每个模板都能成功
  2.  step 顺序完成 + status 正确
  3.  跳号也能完成（中间步骤未走）
  4.  end_plan 在 required 全完成时 ok=True
  5.  end_plan 在 required 缺失时 ok=False
  6.  step expected_outputs 不存在 → 拒绝（FileNotFoundError）
  7.  step --skip-output 可绕过
  8.  abort_plan 留下 reason + 步骤状态变 aborted
  9.  list_plans active 过滤
 10.  plan_id 唯一性（短时间多次 create）
 11.  模板不存在的命令 → FileNotFoundError
 12.  chapter / project 占位符替换
 13.  step 幂等（重复完成不报错）
 14.  end_plan 缺 expected_outputs 文件 → 报告 missing_outputs
 15.  CLI: create + step --skip-output + end exit 码

约束：测试不依赖 workspace/novels 真实数据；所有写入用临时目录。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 让测试用临时目录覆盖真实路径
TEST_REPO = Path(tempfile.mkdtemp(prefix="plan_tracker_test_"))

# 在 import 前 monkey-patch 环境
sys.path.insert(0, str(Path(__file__).parent))

import plan_tracker as pt  # noqa: E402

# 把模板目录、运行时目录指向测试目录
TEMPLATES_SRC = Path(__file__).resolve().parents[2] / "core" / "claude-home" / "plans"
TEST_TEMPLATES = TEST_REPO / "templates"
TEST_PROJECTS = TEST_REPO / "workspace" / "novels"
TEST_STYLES = TEST_REPO / "workspace" / "styles"
TEST_GLOBAL_PLANS = TEST_REPO / "global_plans"

# 复制模板进测试目录
TEST_TEMPLATES.mkdir(parents=True, exist_ok=True)
for f in TEMPLATES_SRC.glob("*.plan.json"):
    shutil.copy(f, TEST_TEMPLATES / f.name)

TEST_PROJECTS.mkdir(parents=True, exist_ok=True)
TEST_STYLES.mkdir(parents=True, exist_ok=True)
TEST_GLOBAL_PLANS.mkdir(parents=True, exist_ok=True)

# 改写 plan_tracker 的常量
pt.REPO_ROOT = TEST_REPO
pt.TEMPLATES_DIR = TEST_TEMPLATES
pt.PROJECTS_DIR = TEST_PROJECTS
pt.STYLES_DIR = TEST_STYLES
pt.GLOBAL_PLANS_DIR = TEST_GLOBAL_PLANS


def make_test_project(name: str) -> Path:
    """创建一个测试项目（含 _数据库 目录）。"""
    root = TEST_PROJECTS / name
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    return root


def make_test_style(name: str) -> Path:
    """创建一个测试风格库项目。"""
    root = TEST_STYLES / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_expected_file(root: Path, rel: str) -> Path:
    """在项目根创建一个空文件以满足 expected_outputs 校验。"""
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    return p


# ============ 测试用例 ============

class TestCreatePlan(unittest.TestCase):
    """1. 每个模板都能 create 成功"""

    def test_create_all_six_templates(self):
        commands = [
            "save-state", "distill-style", "check-quality",
            "write-chapter", "outline", "reconcile",
        ]
        for cmd in commands:
            with self.subTest(command=cmd):
                make_test_project("书A")
                pid = pt.create_plan(cmd, "书A", chapter=1 if cmd != "distill-style" else None,
                                     key=None if cmd != "distill-style" else "main")
                self.assertIn(cmd, pid)
                plan = pt.get_plan(pid)
                self.assertEqual(plan["command"], cmd)
                self.assertGreater(len(plan["steps"]), 0)


class TestStepCompletion(unittest.TestCase):
    """2. step 顺序完成 + 13. step 幂等"""

    def test_sequential_steps(self):
        root = make_test_project("顺序书")
        pid = pt.create_plan("check-quality", "顺序书", chapter=5)
        pt.step_complete(pid, 1, skip_output=True)
        pt.step_complete(pid, 2, skip_output=True)
        pt.step_complete(pid, 3, skip_output=True)
        plan = pt.get_plan(pid)
        statuses = [s["status"] for s in plan["steps"]]
        self.assertEqual(statuses, ["completed", "completed", "completed"])

    def test_step_idempotent(self):
        make_test_project("幂等书")
        pid = pt.create_plan("check-quality", "幂等书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        first_completed_at = pt.get_plan(pid)["steps"][0]["completed_at"]
        # 第二次调用不应该报错
        ok = pt.step_complete(pid, 1, skip_output=True)
        self.assertTrue(ok)
        second = pt.get_plan(pid)["steps"][0]["completed_at"]
        # 幂等：completed_at 保持原值
        self.assertEqual(first_completed_at, second)


class TestStepSkipDetection(unittest.TestCase):
    """3. 跳号场景：可以跳，end_plan 时检测出来"""

    def test_skip_step_caught_at_end(self):
        make_test_project("跳号书")
        pid = pt.create_plan("check-quality", "跳号书", chapter=1)
        # 只完成第 1 和第 3 步，跳过第 2 步
        pt.step_complete(pid, 1, skip_output=True)
        pt.step_complete(pid, 3, skip_output=True)
        res = pt.end_plan(pid)
        self.assertFalse(res["ok"])
        self.assertIn(2, res["missing_steps"])


class TestEndPlan(unittest.TestCase):
    """4-5. end_plan ok / not ok"""

    def test_end_ok_when_all_required_done(self):
        make_test_project("OK书")
        pid = pt.create_plan("check-quality", "OK书", chapter=2)
        for n in (1, 2, 3):
            pt.step_complete(pid, n, skip_output=True)
        res = pt.end_plan(pid)
        self.assertTrue(res["ok"])
        self.assertEqual(res["missing_steps"], [])

    def test_end_fail_when_required_missing(self):
        make_test_project("缺步书")
        pid = pt.create_plan("save-state", "缺步书", chapter=1)
        # 只完成第 1 步
        # save-state 第 1 步要求 _数据库/.wal/第001章_save_state.json（v17.5 zero-pad）
        root = TEST_PROJECTS / "缺步书"
        make_expected_file(root, "_数据库/.wal/第001章_save_state.json")
        pt.step_complete(pid, 1)
        res = pt.end_plan(pid)
        self.assertFalse(res["ok"])
        # required_steps 包含 1,2,3,4,8,9,10,11,12，第 1 已完成，剩余必缺
        self.assertGreater(len(res["missing_steps"]), 0)
        self.assertNotIn(1, res["missing_steps"])


class TestExpectedOutputs(unittest.TestCase):
    """6-7. expected_outputs 校验"""

    def test_step_rejects_missing_output(self):
        make_test_project("缺输出书")
        pid = pt.create_plan("write-chapter", "缺输出书", chapter=3)
        # 第 1 步的 expected_outputs: _数据库/.manifest/ch_3.json，未创建
        with self.assertRaises(FileNotFoundError):
            pt.step_complete(pid, 1)

    def test_step_accepts_existing_output(self):
        root = make_test_project("有输出书")
        pid = pt.create_plan("write-chapter", "有输出书", chapter=3)
        # write-chapter step 1 现在有 2 个 expected_outputs（manifest + style_directive，
        # v17.5 起加 zero-pad；v18 起加 style_directive），全建才能通过 step
        make_expected_file(root, "_数据库/.manifest/ch_003.json")
        make_expected_file(root, "_数据库/.style_directive/ch_003.json")
        pt.step_complete(pid, 1)
        plan = pt.get_plan(pid)
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(len(plan["steps"][0]["verified_outputs"]), 2)

    def test_skip_output_flag(self):
        make_test_project("跳过输出书")
        pid = pt.create_plan("write-chapter", "跳过输出书", chapter=3)
        # 不创建文件，但用 skip_output
        pt.step_complete(pid, 1, skip_output=True)
        plan = pt.get_plan(pid)
        self.assertEqual(plan["steps"][0]["status"], "completed")


class TestAbort(unittest.TestCase):
    """8. abort_plan"""

    def test_abort_records_reason_and_status(self):
        make_test_project("中止书")
        pid = pt.create_plan("save-state", "中止书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        res = pt.abort_plan(pid, "用户取消")
        self.assertEqual(res["reason"], "用户取消")
        plan = pt.get_plan(pid)
        self.assertEqual(plan["abort_reason"], "用户取消")
        # 第 1 步已完成不会变；其余 pending 转 aborted
        statuses = [s["status"] for s in plan["steps"]]
        self.assertIn("aborted", statuses)
        self.assertIn("completed", statuses)


class TestListPlans(unittest.TestCase):
    """9. list_plans active 过滤"""

    def test_list_active_excludes_completed_and_aborted(self):
        make_test_project("列表书A")
        make_test_project("列表书B")
        make_test_project("列表书C")
        pid_a = pt.create_plan("check-quality", "列表书A", chapter=1)
        pid_b = pt.create_plan("check-quality", "列表书B", chapter=1)
        pid_c = pt.create_plan("check-quality", "列表书C", chapter=1)
        # A 完成
        for n in (1, 2, 3):
            pt.step_complete(pid_a, n, skip_output=True)
        pt.end_plan(pid_a)
        # B 中止
        pt.abort_plan(pid_b, "测试")
        # C 保持活跃
        active = pt.list_plans(active_only=True)
        active_ids = {p["id"] for p in active}
        self.assertIn(pid_c, active_ids)
        self.assertNotIn(pid_a, active_ids)
        self.assertNotIn(pid_b, active_ids)


class TestUniqueness(unittest.TestCase):
    """10. plan_id 唯一性"""

    def test_plan_ids_are_unique(self):
        make_test_project("唯一书")
        ids = set()
        for _ in range(8):
            pid = pt.create_plan("check-quality", "唯一书", chapter=1)
            ids.add(pid)
        self.assertEqual(len(ids), 8)


class TestTemplateErrors(unittest.TestCase):
    """11. 模板不存在"""

    def test_missing_template_raises(self):
        make_test_project("无模板书")
        # 用一个绝对没有模板的命令名（先注册到 KNOWN_COMMANDS 边界外）
        with self.assertRaises(FileNotFoundError):
            pt.create_plan("nonexistent-command", "无模板书", chapter=1)


class TestPlaceholders(unittest.TestCase):
    """12. {ch} / {project} 占位符替换"""

    def test_chapter_placeholder_substituted(self):
        make_test_project("占位书")
        pid = pt.create_plan("save-state", "占位书", chapter=42)
        plan = pt.get_plan(pid)
        # v17.5 起 {ch:03d} 零填充 → 42 替换为 042
        outputs = plan["steps"][0]["expected_outputs"]
        self.assertEqual(outputs, ["_数据库/.wal/第042章_save_state.json"])

    def test_write_chapter_placeholders(self):
        make_test_project("写章占位书")
        pid = pt.create_plan("write-chapter", "写章占位书", chapter=7)
        plan = pt.get_plan(pid)
        outputs_step1 = plan["steps"][0]["expected_outputs"]
        outputs_step2 = plan["steps"][1]["expected_outputs"]
        # v17.5 起 zero-pad：7 → 007
        self.assertIn("_数据库/.manifest/ch_007.json", outputs_step1)
        self.assertIn("章节/第007章/第007章.txt", outputs_step2)


class TestMissingOutputAtEnd(unittest.TestCase):
    """14. end_plan 时发现 expected_outputs 文件被删了"""

    def test_end_reports_missing_outputs(self):
        root = make_test_project("末检书")
        pid = pt.create_plan("write-chapter", "末检书", chapter=1)
        # 创建 4 个文件让 step 1 + step 2 都能通过（v17.5 zero-pad + v18 加 style_directive/changes）
        f1a = make_expected_file(root, "_数据库/.manifest/ch_001.json")
        make_expected_file(root, "_数据库/.style_directive/ch_001.json")
        make_expected_file(root, "章节/第001章/第001章.txt")
        make_expected_file(root, "章节/第001章/第001章_changes.json")
        pt.step_complete(pid, 1)
        pt.step_complete(pid, 2)
        for n in (3, 4, 5):
            pt.step_complete(pid, n, skip_output=True)
        # 删除 step 1 的一个文件 → end 应报 missing
        f1a.unlink()
        res = pt.end_plan(pid)
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["missing_outputs"]), 1)
        self.assertEqual(res["missing_outputs"][0]["step"], 1)


class TestStyleProjectRouting(unittest.TestCase):
    """额外：风格库项目也能正确路由"""

    def test_style_project_plan_in_styles_dir(self):
        make_test_style("风格书")
        pid = pt.create_plan("distill-style", "风格书", key="batch1")
        # 计算预期路径
        expected_dir = TEST_STYLES / "风格书" / ".plans"
        self.assertTrue((expected_dir / f"{pid}.json").exists(),
                        f"plan 没落到风格库目录：{expected_dir}")


class TestNoProjectFallback(unittest.TestCase):
    """额外：项目不存在时落到 GLOBAL_PLANS_DIR"""

    def test_unknown_project_falls_to_global(self):
        pid = pt.create_plan("check-quality", "完全不存在的项目xyz", chapter=1)
        self.assertTrue((TEST_GLOBAL_PLANS / f"{pid}.json").exists())


class TestCLI(unittest.TestCase):
    """15. CLI 端到端：create / step / end 退出码"""

    def test_cli_full_flow(self):
        # 用真实 REPO_ROOT 跑 CLI（CLI 跑的是非 monkey-patched 进程）
        # 改为：直接调 main() 内部，绕过 subprocess
        make_test_project("CLI书")
        # create
        pid = pt.create_plan("check-quality", "CLI书", chapter=1)
        # 模拟 step（用 main）
        rc = pt.main(["step", pid, "--n", "1", "--skip-output"])
        self.assertEqual(rc, 0)
        rc = pt.main(["step", pid, "--n", "2", "--skip-output"])
        self.assertEqual(rc, 0)
        rc = pt.main(["step", pid, "--n", "3", "--skip-output"])
        self.assertEqual(rc, 0)
        # end 应该 ok
        rc = pt.main(["end", pid])
        self.assertEqual(rc, 0)

    def test_cli_end_fails_when_missing(self):
        make_test_project("CLI失败书")
        pid = pt.create_plan("check-quality", "CLI失败书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        # end 不该通过
        rc = pt.main(["end", pid])
        self.assertEqual(rc, 2)


class TestAttestation(unittest.TestCase):
    """P1-1：plan 防篡改 attestation —— 盖章 / 篡改检测 / 阻断 / reattest / 向后兼容"""

    def _tamper(self, pid: str) -> Path:
        """模拟旁路篡改：直接改 plan JSON 不更新 attestation（伪造 step 状态）。"""
        path = pt._find_plan_path(pid)
        d = json.loads(path.read_text(encoding="utf-8"))
        d["steps"][0]["status"] = pt.STATUS_COMPLETED
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_create_attests(self):
        """create 自动盖章，verify → ok。"""
        make_test_project("盖章书")
        pid = pt.create_plan("check-quality", "盖章书", chapter=1)
        plan = pt.get_plan(pid)
        self.assertIn(pt.ATTESTATION_KEY, plan)
        self.assertEqual(pt.verify_attestation(plan), "ok")
        self.assertEqual(pt.verify_plan(pid), "ok")

    def test_tampering_detected(self):
        """旁路篡改 plan JSON → verify 报 tampered。"""
        make_test_project("篡改书")
        pid = pt.create_plan("check-quality", "篡改书", chapter=1)
        self._tamper(pid)
        self.assertEqual(pt.verify_plan(pid), "tampered")

    def test_step_blocks_tampered(self):
        """tampered plan 上 step → 抛 PlanTamperedError（阻断跳步伪造）。"""
        make_test_project("篡改step书")
        pid = pt.create_plan("check-quality", "篡改step书", chapter=1)
        self._tamper(pid)
        with self.assertRaises(pt.PlanTamperedError):
            pt.step_complete(pid, 2, skip_output=True)

    def test_end_blocks_tampered(self):
        """tampered plan 上 end → 抛 PlanTamperedError。"""
        make_test_project("篡改end书")
        pid = pt.create_plan("check-quality", "篡改end书", chapter=1)
        self._tamper(pid)
        with self.assertRaises(pt.PlanTamperedError):
            pt.end_plan(pid)

    def test_get_plan_readonly_no_raise_on_tamper(self):
        """只读 get_plan 遇 tampered 不抛异常（仅 stderr 警告，不阻断观测）。"""
        make_test_project("只读书")
        pid = pt.create_plan("check-quality", "只读书", chapter=1)
        self._tamper(pid)
        plan = pt.get_plan(pid)  # 不应抛
        self.assertIsInstance(plan, dict)

    def test_reattest_restores(self):
        """reattest 后 verify 恢复 ok，step 可正常进行。"""
        make_test_project("重盖章书")
        pid = pt.create_plan("check-quality", "重盖章书", chapter=1)
        self._tamper(pid)
        self.assertEqual(pt.verify_plan(pid), "tampered")
        res = pt.reattest_plan(pid)
        self.assertEqual(res["was"], "tampered")
        self.assertEqual(pt.verify_plan(pid), "ok")
        self.assertTrue(pt.step_complete(pid, 2, skip_output=True))

    def test_legit_step_keeps_attestation_valid(self):
        """plan_tracker 自己的 step 写盘后 attestation 仍有效（自动重新盖章）。"""
        make_test_project("合法step书")
        pid = pt.create_plan("check-quality", "合法step书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        self.assertEqual(pt.verify_plan(pid), "ok")
        pt.step_complete(pid, 2, skip_output=True)
        self.assertEqual(pt.verify_plan(pid), "ok")

    def test_unattested_backward_compat(self):
        """旧 plan（无 _attestation 字段）放行不阻断，下次写入自动盖章。"""
        make_test_project("旧plan书")
        pid = pt.create_plan("check-quality", "旧plan书", chapter=1)
        path = pt._find_plan_path(pid)
        d = json.loads(path.read_text(encoding="utf-8"))
        del d[pt.ATTESTATION_KEY]  # 模拟本功能引入前创建的旧 plan
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(pt.verify_plan(pid), "unattested")
        # 旧 plan 上 step 不应抛异常（向后兼容）
        ok = pt.step_complete(pid, 1, skip_output=True)
        self.assertTrue(ok)
        # step 写盘后自动盖章 → 变 ok
        self.assertEqual(pt.verify_plan(pid), "ok")

    def test_verify_plan_not_found(self):
        """verify_plan 对不存在的 plan_id → not_found（永不抛异常，供 hook 安全调用）。"""
        self.assertEqual(pt.verify_plan("根本不存在的_plan_id_xyz"), "not_found")

    def test_canonical_hash_ignores_formatting(self):
        """规范化哈希与磁盘缩进格式解耦：仅重新美化 JSON（内容不变）不触发 tampered。"""
        make_test_project("格式书")
        pid = pt.create_plan("check-quality", "格式书", chapter=1)
        path = pt._find_plan_path(pid)
        d = json.loads(path.read_text(encoding="utf-8"))
        # 换缩进 + 排序重新写盘 —— 内容没变，只是格式变
        path.write_text(json.dumps(d, ensure_ascii=False, indent=4, sort_keys=True),
                        encoding="utf-8")
        self.assertEqual(pt.verify_plan(pid), "ok")


class TestCostTracking(unittest.TestCase):
    """P2-8：step 记录 subagent token / duration 成本 + end 聚合"""

    def test_step_records_cost(self):
        """step_complete 带 tokens / duration_ms 时持久化到 step 字段。"""
        make_test_project("成本书")
        pid = pt.create_plan("check-quality", "成本书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True, tokens=12500, duration_ms=3200)
        plan = pt.get_plan(pid)
        self.assertEqual(plan["steps"][0]["tokens_used"], 12500)
        self.assertEqual(plan["steps"][0]["duration_ms"], 3200)

    def test_step_without_cost_backward_compat(self):
        """不传 tokens/duration_ms 时不写字段（旧调用零冲击）。"""
        make_test_project("无成本书")
        pid = pt.create_plan("check-quality", "无成本书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        plan = pt.get_plan(pid)
        self.assertNotIn("tokens_used", plan["steps"][0])
        self.assertNotIn("duration_ms", plan["steps"][0])

    def test_end_aggregates_cost(self):
        """end_plan 返回 cost_summary 含 total_tokens + total_duration_ms。"""
        make_test_project("聚合书")
        pid = pt.create_plan("check-quality", "聚合书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True, tokens=10000, duration_ms=2000)
        pt.step_complete(pid, 2, skip_output=True, tokens=20000, duration_ms=4000)
        pt.step_complete(pid, 3, skip_output=True)  # 无 cost
        res = pt.end_plan(pid)
        cost = res["cost_summary"]
        self.assertEqual(cost["total_tokens"], 30000)
        self.assertEqual(cost["total_duration_ms"], 6000)
        self.assertEqual(cost["steps_with_cost"], 2)
        self.assertEqual(cost["steps_total"], 3)

    def test_step_idempotent_supplements_cost(self):
        """已 completed 的 step 可补录 cost（幂等扩展）。"""
        make_test_project("补录书")
        pid = pt.create_plan("check-quality", "补录书", chapter=1)
        pt.step_complete(pid, 1, skip_output=True)
        # 第二次调用补录 cost
        pt.step_complete(pid, 1, skip_output=True, tokens=8000, duration_ms=1500)
        plan = pt.get_plan(pid)
        self.assertEqual(plan["steps"][0]["tokens_used"], 8000)
        self.assertEqual(plan["steps"][0]["duration_ms"], 1500)


# ============ 入口 ============

def main():
    # 收集结果
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestCreatePlan,
        TestStepCompletion,
        TestStepSkipDetection,
        TestEndPlan,
        TestExpectedOutputs,
        TestAbort,
        TestListPlans,
        TestUniqueness,
        TestTemplateErrors,
        TestPlaceholders,
        TestMissingOutputAtEnd,
        TestStyleProjectRouting,
        TestNoProjectFallback,
        TestCLI,
        TestAttestation,
        TestCostTracking,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 清理
    try:
        shutil.rmtree(TEST_REPO, ignore_errors=True)
    except Exception:
        pass

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
