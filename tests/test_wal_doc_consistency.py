"""`.wal/` 口径回归锁：WAL 不是断点机制，续跑点唯一真相源 = plan_tracker plan JSON。

当前事实：`.wal/` 是各 step 的产物与回执存放区；cluster-save-state step 1 的产物是
`cluster_<key>_schema_validate.json` 确定性校验报告（`db_schema_validate.py --report-out` 落盘），
plan 模板不再用 touch_outputs 造 0 字节占位当 step 产物（历史项目 `.wal/` 里可能遗留
`cluster_<key>_save_state.json` 空标记，不作任何信号）。

本测试锁四件事：
  ① 代码事实：无任何 .py / plan 模板引用 `cluster_<key>_save_state.json`（死标记不得复活）
  ② 代码事实：续跑点由 `wal_recovery._first_resume_step` 从 plan JSON 的 `steps[].status`
     算出「第一个未完成 step」（不是 done_count+1，不读任何 WAL 文件）
  ③ 文档事实：全仓 md **和 py（docstring/注释）**不得再出现「WAL/completed_steps 断点恢复」
     「.wal/ 是崩溃恢复日志」「读 save_state.json 的 status 字段」这类纸面机制表述；
     workspace 下 git-tracked 的说明文件（README 等）同样是系统契约，纳入扫描
  ④ 模板事实：任何 plan 模板 step 的 expected_outputs 不得被其自身 touch_outputs 全覆盖
     （0 字节纯占位自我满足 = step 零真实验收）

零 API；workspace 扫描集合由 `git ls-files` 划定（本仓库即 git repo，git 不可用=环境坏，响亮失败）。
"""
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import wal_recovery as wr  # noqa: E402


# 扫描范围：仓库自有文档与脚本（排开第三方/产出/调研快照）
_SKIP_DIR_PARTS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages",
    "build", "dist",
}


@lru_cache(maxsize=1)
def _workspace_tracked() -> frozenset:
    """workspace 下的系统契约文件集合 = git-tracked 说明文件（README.md 等）。

    用户产出（novels/<书名>/、styles/<书名>/）未被 git 跟踪，天然不在集合里；
    _temp_research/ 调研历史快照即使被跟踪也排除（不是系统契约）。
    """
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z", "--", "workspace"],
        capture_output=True, check=True,
    )
    files = set()
    for rel in out.stdout.decode("utf-8").split("\0"):
        if not rel or "_temp_research" in Path(rel).parts:
            continue
        files.add((_ROOT / rel).resolve())
    return frozenset(files)


def _iter_files(suffix: str):
    for p in _ROOT.rglob(f"*{suffix}"):
        if any(part in _SKIP_DIR_PARTS for part in p.parts):
            continue
        rel = p.relative_to(_ROOT).parts
        if rel and rel[0] == "workspace" and p.resolve() not in _workspace_tracked():
            continue
        yield p


# ============ ① `cluster_<key>_save_state.json` 死标记不得复活 ============

def test_no_script_reads_or_writes_save_state_wal_file():
    """🔴 根因锁：没有任何 .py 读写 `.wal/cluster_<key>_save_state.json`。

    该 0 字节占位已从 plan 模板清除（step 1 产物 = schema_validate 校验报告）。
    一旦有人给它加读写（复活「WAL 断点」纸面机制），本测试炸——
    续跑点必须留在 plan_tracker plan JSON 这一个真相源里。
    """
    pat = re.compile(r"_save_state\.json|save_state\.json")
    offenders = []
    for p in _iter_files(".py"):
        if p.name == Path(__file__).name:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{p.relative_to(_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "没有脚本应读写 cluster_<key>_save_state.json（该 0 字节占位已从 plan 模板清除）。\n"
        "若要做断点恢复，唯一真相源是 plan_tracker 的 plan JSON（steps[].status）。\n命中：\n"
        + "\n".join(offenders)
    )


def test_plan_templates_do_not_reference_save_state_marker():
    """🔴 plan 模板不得再引用 `cluster_{key}_save_state.json`（0 字节占位已清除）。"""
    pat = re.compile(r"cluster_\{key\}_save_state\.json")
    offenders = []
    for p in sorted((_ROOT / "core" / "claude-home" / "plans").glob("*.plan.json")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{p.relative_to(_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "plan 模板引用了已清除的 0 字节占位 cluster_{key}_save_state.json；\n"
        "step 1 的产物是 db_schema_validate --report-out 写的 cluster_{key}_schema_validate.json。\n命中：\n"
        + "\n".join(offenders)
    )


def test_no_plan_step_is_fully_self_satisfied_by_touch_outputs():
    """🔴 0 字节纯占位锁：plan 模板任何 step 的 expected_outputs 不得被自身 touch_outputs 全覆盖。

    plan_tracker._apply_touch_outputs 在 step 验收前把 touch_outputs touch 成空文件——
    若某 step 的 expected_outputs ⊆ 自身 touch_outputs，则该 step 的产物校验被自touch的
    0 字节占位「自我满足」，等于零真实验收（touch_outputs 里的文件必须有真实 writer，
    或至少 expected_outputs 还含其他真产物）。
    """
    import json as _json
    offenders = []
    for p in sorted((_ROOT / "core" / "claude-home" / "plans").glob("*.plan.json")):
        plan = _json.loads(p.read_text(encoding="utf-8"))
        for step in plan.get("steps", []):
            touch = set(step.get("touch_outputs") or [])
            expected = set(step.get("expected_outputs") or [])
            if expected and touch and expected <= touch:
                offenders.append(
                    f"{p.name} step {step.get('n')}({step.get('name')}): "
                    f"expected_outputs 全部被自身 touch_outputs 覆盖 → 零真实验收")
    assert not offenders, (
        "发现 0 字节纯占位自我满足的 plan step（expected_outputs ⊆ touch_outputs）。\n"
        "给该 step 配真实 writer 产物（如 db_schema_validate --report-out）。\n命中：\n"
        + "\n".join(offenders)
    )


def test_completed_steps_is_not_a_real_field_anywhere():
    """`completed_steps` 不是任何运行时 JSON 字段——不得以它为名重建 WAL 断点。

    只允许出现在测试/注释的说明性文字里；禁止出现取值/赋值形态
    （`["completed_steps"]` / `.get("completed_steps")` / `"completed_steps":`）。
    """
    bad = re.compile(r"""\[\s*['"]completed_steps['"]\s*\]"""
                     r"""|\.get\(\s*['"]completed_steps['"]"""
                     r"""|['"]completed_steps['"]\s*:""")
    offenders = []
    for suffix in (".py", ".json"):
        for p in _iter_files(suffix):
            if p.name == Path(__file__).name:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if bad.search(line):
                    offenders.append(f"{p.relative_to(_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "completed_steps 不是真实字段（WAL 断点是纸面机制，已清除）。命中：\n" + "\n".join(offenders)
    )


# ============ ② 续跑点真相源 = plan JSON 的 steps[].status ============

def test_resume_step_comes_from_plan_steps_status_only():
    """`_first_resume_step` 只吃 plan 的 steps[].status，产出第一个未完成 step。"""
    steps = [
        {"n": 1, "status": "completed"},
        {"n": 2, "status": "completed"},
        {"n": 3, "status": "pending"},     # ← 第一个未完成
        {"n": 4, "status": "completed"},   # 非连续完成（done_count+1 会漏掉 step3）
    ]
    assert wr._first_resume_step(steps, 3) == 3, "续跑点必须是第一个未完成 step，不是 done_count+1"


def test_wal_recovery_does_not_read_any_wal_file():
    """wal_recovery 只读 `.plans/` 下的 plan JSON，绝不读 `.wal/` 任何文件。"""
    src = (_ROOT / "core" / "scripts" / "wal_recovery.py").read_text(encoding="utf-8")
    assert ".wal" not in src, "wal_recovery 不得读 .wal/（续跑点唯一真相源是 plan JSON）"
    assert ".plans" in src, "wal_recovery 必须从 .plans/ 读 plan JSON"


# ============ ③ 文档不得复活「WAL 断点恢复」纸面机制 ============

_DOC_FALSE_CLAIMS = [
    # WAL/completed_steps 断点恢复
    re.compile(r"completed_steps[^\n]{0,40}(断点|恢复|续跑)"),
    re.compile(r"(WAL|wal)[^\n]{0,30}(细粒度断点|断点恢复|预写日志|写前日志)"),
    # `.wal/` 被标成崩溃恢复/恢复日志/中断点（真实职责=各 step 产物与回执存放区）。
    # 锚定 `.wal`/大写 WAL，避免误伤 wal_recovery（读 plan JSON 的真实机制）的合法表述。
    re.compile(r"(\.wal|WAL)[^\n]{0,40}(崩溃恢复|恢复日志|崩溃日志|中断点)"),
    re.compile(r"(崩溃恢复|恢复日志|崩溃日志|中断点)[^\n]{0,30}(\.wal|WAL)"),
    # 读 save_state.json 的 status 字段（该字段从不存在）
    re.compile(r"save_state\.json[^\n]{0,40}status"),
    re.compile(r"WAL 状态为\s*[`'\"]?done"),
]


def test_no_doc_claims_wal_breakpoint_recovery():
    """🔴 双口径锁：全仓 md + py 不得再宣称 WAL 是断点/崩溃恢复机制、save_state.json 有 status。

    文档和 docstring 是新会话唯一读到的东西——描述与代码现实不符会把人导向不存在的机制。
    py 一并扫：假口径同样藏在模块 docstring/注释里（plan_tracker/session_memory 实证）。
    """
    offenders = []
    for suffix in (".md", ".py"):
        for p in _iter_files(suffix):
            if p.name == Path(__file__).name:
                continue  # 本文件背景说明按定义引用旧假口径
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for pat in _DOC_FALSE_CLAIMS:
                    if pat.search(line):
                        offenders.append(f"{p.relative_to(_ROOT)}:{i}: {line.strip()[:120]}")
                        break
    assert not offenders, (
        "文档/docstring 复活了「WAL 断点恢复/崩溃恢复日志」纸面机制（代码里不存在）。\n"
        "事实：.wal/ 是各 step 的产物与回执存放区；续跑点唯一真相源 = plan_tracker plan JSON 的 steps[].status。\n"
        "命中：\n" + "\n".join(offenders)
    )


def test_novels_readme_wal_annotation_is_artifact_area():
    """workspace/novels/README.md（git-tracked 系统契约）对 `.wal/` 的标注必须是产物区口径。"""
    txt = (_ROOT / "workspace" / "novels" / "README.md").read_text(encoding="utf-8")
    assert ".wal/" in txt, "README 目录树应保留 .wal/ 条目"
    assert "产物与回执" in txt, "README 必须把 .wal/ 标注为「各 step 的产物与回执存放区」"


def test_plan_tracker_and_session_memory_docstrings_state_wal_truth():
    """plan_tracker/session_memory 模块 docstring 必须正面写 .wal/=产物区（防又被改回断点口径）。"""
    for name in ("plan_tracker.py", "session_memory.py"):
        txt = (_ROOT / "core" / "scripts" / name).read_text(encoding="utf-8")
        assert "产物与回执存放区" in txt, (
            f"core/scripts/{name} docstring 必须写明 .wal/ = 各 step 的产物与回执存放区"
        )
        assert "细粒度断点" not in txt, (
            f"core/scripts/{name} docstring 不得保留「WAL 细粒度断点」旧口径"
        )


def test_claude_md_states_plan_json_is_the_resume_truth_source():
    """CLAUDE.md 必须正面写明续跑点真相源 = plan_tracker plan JSON（防又被改回 WAL 口径）。"""
    for name in ("CLAUDE.md", "AGENTS.md"):
        txt = (_ROOT / name).read_text(encoding="utf-8")
        assert "续跑点的唯一真相源" in txt and "plan JSON" in txt, (
            f"{name} 必须写明「续跑点的唯一真相源 = plan_tracker 的 plan JSON」"
        )
        assert "cluster_<key>_schema_validate.json" in txt, (
            f"{name} 必须写明 step 1 产物 = cluster_<key>_schema_validate.json 确定性校验报告"
        )
        assert "0 字节存在性标记" not in txt, (
            f"{name} 不得保留「cluster_<key>_save_state.json 0 字节存在性标记」旧口径（该占位已清除）"
        )


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            print(f"  [FAIL] {nm}: {e}")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
