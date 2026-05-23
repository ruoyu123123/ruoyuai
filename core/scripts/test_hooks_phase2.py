#!/usr/bin/env python3
"""
Phase 2 Hook 单元测试

测试覆盖
--------
PreToolUse (pretooluse_agent_gate.py):
- T1  非 Agent 工具直接放行（exit 0）
- T2  多步 Agent 缺 PLAN_ID/STEP → 拦截（exit 2）
- T3  多步 Agent 含 PLAN_ID → 放行
- T4  多步 Agent 含 STEP → 放行
- T5  novel_keywords 误判修复：含 ch121-123 的技术描述不应触发 novel 校验
- T6  PLAN_ID 豁免：含 PLAN_ID 的 writer Agent 可跳过 PROJECT/CHAPTER/MANIFEST
- T7  写作 Agent 缺契约字段 + 无 PLAN_ID → 仍然拦截
- T8  正常写作 Agent 含全部契约字段 → 放行
- T9  prompt 过长 → 拦截
- T10 prompt 含 frontmatter → 拦截

PostToolUse (posttooluse_plan_check.py):
- T11 非 Write/Edit 工具直接放行（exit 0）
- T12 Write 无活跃 plan → 放行（exit 0）
- T13 Write 匹配 expected_outputs → 自动 step_complete + 仍 exit 0
- T14 Write 不匹配任何 plan → 放行（不拦截）
- T15 损坏 JSON 输入 → 放行（exit 0）

执行
----
    python core/scripts/test_hooks_phase2.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_HOOK = REPO_ROOT / "core" / "claude-home" / "hooks" / "pretooluse_agent_gate.py"
POST_HOOK = REPO_ROOT / "core" / "claude-home" / "hooks" / "posttooluse_plan_check.py"
PLAN_TRACKER = REPO_ROOT / "core" / "scripts" / "plan_tracker.py"


# ============ 测试基础设施 ============

def run_hook(hook_path: Path, payload: dict) -> tuple[int, str, str]:
    """以 subprocess 运行 hook，返回 (exit_code, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestResult:
    def __init__(self):
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def assert_(self, cond: bool, name: str, msg: str = ""):
        if cond:
            self.passed.append(name)
            print(f"  [OK] {name}")
        else:
            self.failed.append((name, msg))
            print(f"  [FAIL] {name}: {msg}")

    def summary(self) -> int:
        total = len(self.passed) + len(self.failed)
        print(f"\n========== Phase 2 Hook 测试结果 ==========")
        print(f"  通过: {len(self.passed)}/{total}")
        if self.failed:
            print(f"  失败:")
            for name, msg in self.failed:
                print(f"    - {name}: {msg}")
            return 1
        print(f"  全部通过 ✅")
        return 0


# ============ PreToolUse 测试 ============

def test_pre_hook(r: TestResult):
    print("\n--- PreToolUse Hook 测试 ---")

    # T1: 非 Agent 工具放行
    code, _, _ = run_hook(PRE_HOOK, {
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/x.txt", "content": "abc"},
    })
    r.assert_(code == 0, "T1 非 Agent 工具放行", f"exit={code}")

    # T2: 多步 Agent 缺 PLAN_ID/STEP → 拦截
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "save-state pipeline runner",
            "prompt": "请执行 save-state，完整跑完所有步骤。" * 5,
        },
    })
    r.assert_(code == 2, "T2 多步 Agent 缺 PLAN_ID/STEP 拦截",
              f"exit={code}, err={err[:200]}")
    r.assert_("PLAN_ID" in err, "T2.1 错误信息含 PLAN_ID 提示", f"err={err[:200]}")

    # T3: 多步 Agent 含 PLAN_ID → 放行
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "save-state pipeline runner",
            "prompt": "PLAN_ID: test_main_save-state_20260513T220000123\n"
                      "请执行 save-state 完整流程，跑完所有步骤" + "x" * 20,
        },
    })
    r.assert_(code == 0, "T3 多步 Agent 含 PLAN_ID 放行", f"exit={code}, err={err[:200]}")

    # T4: 多步 Agent 含 STEP → 放行
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "save-state pipeline",
            "prompt": "STEP: 5\n请执行 save-state 第 5 步 novel-summarizer" + "x" * 50,
        },
    })
    r.assert_(code == 0, "T4 多步 Agent 含 STEP 放行", f"exit={code}, err={err[:200]}")

    # T5: novel_keywords 误判修复 — 含"ch121-123"技术描述不触发误判
    # 旧 bug：description 含"章"字 → is_novel_agent=True → 校验契约字段失败
    # 新行为：description 不含 NOVEL_NAME_KEYWORDS 词组 → is_novel_agent=False
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "distill xxx ch121-123 technical task",
            "prompt": "Process ch121 through ch123 distillation. " + "y" * 100,
        },
    })
    r.assert_(code == 0, "T5 novel_keywords 误判修复（ch121-123 不拦截）",
              f"exit={code}, err={err[:200]}")

    # T6: PLAN_ID 豁免 — writer 含 PLAN_ID 但缺 PROJECT/CHAPTER/MANIFEST → 仍放行
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "novel-writer 写第10章",
            "prompt": "PLAN_ID: book_ch10_write-chapter_20260513T220000456\n"
                      "请写第 10 章正文" + "z" * 50,
        },
    })
    r.assert_(code == 0, "T6 PLAN_ID 豁免（writer 跳过 PROJECT/CHAPTER 强制）",
              f"exit={code}, err={err[:200]}")

    # T7: 写作 Agent 缺契约字段且无 PLAN_ID → 拦截
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "novel-writer 写第10章",
            "prompt": "请写第 10 章正文" + "z" * 50,
        },
    })
    r.assert_(code == 2, "T7 写作 Agent 缺契约 + 无 PLAN_ID 拦截",
              f"exit={code}, err={err[:200]}")

    # T8: 写作 Agent 含全部契约字段 → 放行
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "novel-writer 写第10章",
            "prompt": "PROJECT: testbook\nCHAPTER: 10\nMANIFEST: /path/to/manifest.json\n"
                      "请写第 10 章正文" + "z" * 50,
        },
    })
    r.assert_(code == 0, "T8 写作 Agent 含全部契约字段放行",
              f"exit={code}, err={err[:200]}")

    # T9: prompt 过长 → 拦截
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "novel-writer 写第10章",
            "prompt": "PROJECT: testbook\nCHAPTER: 10\nMANIFEST: x\n" + "a" * 16000,
        },
    })
    r.assert_(code == 2, "T9 prompt 过长拦截", f"exit={code}")

    # T10: prompt 含 frontmatter → 拦截
    fm_prompt = "---\nname: test\ndescription: foo\n---\nbody " + "x" * 100
    code, _, err = run_hook(PRE_HOOK, {
        "tool_name": "Agent",
        "tool_input": {
            "description": "some task",
            "prompt": fm_prompt,
        },
    })
    r.assert_(code == 2, "T10 prompt 含 frontmatter 拦截", f"exit={code}, err={err[:200]}")


# ============ PostToolUse 测试 ============

def test_post_hook(r: TestResult):
    print("\n--- PostToolUse Hook 测试 ---")

    # T11: 非 Write/Edit 工具放行
    code, _, _ = run_hook(POST_HOOK, {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    r.assert_(code == 0, "T11 非 Write/Edit 工具放行", f"exit={code}")

    # T12: Write 无活跃 plan → 放行
    code, _, err = run_hook(POST_HOOK, {
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/some_random_file_xyz.txt", "content": "abc"},
    })
    r.assert_(code == 0, "T12 Write 无匹配 plan 放行", f"exit={code}, err={err[:200]}")

    # T13: Write 匹配 expected_outputs → 自动 step_complete + exit 0
    # 创建一个临时活跃 plan，含一个 expected_output 指向我们将"写入"的文件
    test_plan_id = _create_test_plan()
    try:
        # 模拟写入匹配文件
        match_file = REPO_ROOT / "core" / "claude-home" / ".plans" / "test_phase2_match.txt"
        match_file.parent.mkdir(parents=True, exist_ok=True)
        match_file.write_text("test", encoding="utf-8")

        code, _, err = run_hook(POST_HOOK, {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(match_file),
                "content": "test",
            },
        })
        r.assert_(code == 0, "T13 Write 匹配后仍 exit 0（不拦截）",
                  f"exit={code}, err={err[:200]}")
        # 检查 stderr 是否含 auto-completed 提示
        has_auto = "auto-completed" in err or "auto-step" in err
        r.assert_(has_auto, "T13.1 stderr 含 auto-completed 提示", f"err={err[:300]}")

        # 验证 plan 中 step 是否被标记完成
        sys.path.insert(0, str(REPO_ROOT / "core" / "scripts"))
        try:
            from plan_tracker import get_plan
            plan = get_plan(test_plan_id)
            step1 = next((s for s in plan["steps"] if s["n"] == 1), None)
            r.assert_(step1 is not None and step1.get("status") == "completed",
                      "T13.2 plan step 已标记 completed",
                      f"step={step1}")
        except Exception as exc:
            r.assert_(False, "T13.2 plan step 已标记 completed",
                      f"无法读取 plan: {exc}")
    finally:
        _cleanup_test_plan(test_plan_id)
        try:
            match_file.unlink(missing_ok=True)
        except Exception:
            pass

    # T14: Write 不匹配任何 plan → 放行
    code, _, err = run_hook(POST_HOOK, {
        "tool_name": "Write",
        "tool_input": {"file_path": "/some/totally/random/unrelated_xyz_zzz.txt",
                       "content": "abc"},
    })
    r.assert_(code == 0, "T14 Write 不匹配任何 plan 仍 exit 0", f"exit={code}")

    # T15: 损坏 JSON 输入 → 放行
    proc = subprocess.run(
        [sys.executable, str(POST_HOOK)],
        input="{not valid json",
        capture_output=True,
        text=True,
        timeout=10,
    )
    r.assert_(proc.returncode == 0, "T15 损坏 JSON 输入放行",
              f"exit={proc.returncode}")


# ============ 测试 plan 创建辅助函数 ============

def _create_test_plan() -> str:
    """直接构造一个最简活跃 plan，写入全局 .plans 目录。返回 plan_id。"""
    from datetime import datetime
    plan_id = f"testphase2_main_save-state_{datetime.now().strftime('%Y%m%dT%H%M%S%f')[:18]}"
    plan_dir = REPO_ROOT / "core" / "claude-home" / ".plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "id": plan_id,
        "command": "save-state",
        "project": "testphase2",
        "chapter": None,
        "key": "main",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": None,
        "abort_reason": None,
        "required_steps": [1],
        "optional_steps": [],
        "steps": [
            {
                "n": 1,
                "name": "test-match-step",
                "description": "测试匹配步骤",
                "required": True,
                "status": "pending",
                "expected_outputs": [".plans/test_phase2_match.txt"],
                "verified_outputs": [],
                "started_at": None,
                "completed_at": None,
                "error": None,
            }
        ],
    }
    (plan_dir / f"{plan_id}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return plan_id


def _cleanup_test_plan(plan_id: str) -> None:
    plan_dir = REPO_ROOT / "core" / "claude-home" / ".plans"
    f = plan_dir / f"{plan_id}.json"
    try:
        f.unlink(missing_ok=True)
    except Exception:
        pass


# ============ 主入口 ============

def main():
    if not PRE_HOOK.exists():
        print(f"[FATAL] 找不到 pretooluse hook: {PRE_HOOK}", file=sys.stderr)
        return 2
    if not POST_HOOK.exists():
        print(f"[FATAL] 找不到 posttooluse hook: {POST_HOOK}", file=sys.stderr)
        return 2
    if not PLAN_TRACKER.exists():
        print(f"[FATAL] 找不到 plan_tracker: {PLAN_TRACKER}", file=sys.stderr)
        return 2

    r = TestResult()
    test_pre_hook(r)
    test_post_hook(r)
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
