"""audit_dashboard.py 确定性回归测试（零 LLM / 零联网 / 仅标准库）。

被测脚本是一个纯聚合面板 CLI：读 _数据库/.audit、.judge_reports、
.cross_chapter_scan、写作经验.json，汇总成统一面板打印，并按「最新跨章扫描
有无 warning」决定退出码（warning→1 / 否则 0；_数据库 缺失→2）。

钉死两块纯确定性逻辑：
- `load_json`（纯函数，无 sys.exit）：缺文件→default / 坏 JSON→default / 合法→parsed。
  直接 import 真调用。
- `main` CLI 的退出码 + grade 推算 + 跨章 warning 聚合 + cluster/章号混合 key。
  `main` 内含 sys.exit，故走 subprocess 跑真 CLI（真 argparse + 真文件读取 +
  真退出码），断言退出码 / stdout —— 全程不 mock 被测逻辑。

注：子进程 stdout 走 Windows 控制台编码（非 UTF-8），用 errors='replace' 容错解码；
退出码是确定性权威信号，断言以它为主，stdout 仅做关键字软断言。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import audit_dashboard as mod  # noqa: E402

_TARGET = _SCRIPTS / "audit_dashboard.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目目录 / 各类报告文件
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_audit(proj: Path, name: str, payload: dict) -> None:
    d = proj / "_数据库" / ".audit"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_judge(proj: Path, name: str, payload: dict) -> None:
    d = proj / "_数据库" / ".judge_reports"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_cross_scan(proj: Path, name: str, payload: dict) -> None:
    d = proj / "_数据库" / ".cross_chapter_scan"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_exp(proj: Path, payload: dict) -> None:
    (proj / "_数据库" / "写作经验.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_cli(proj: Path, *args):
    """跑真 CLI，返回 CompletedProcess（stdout/stderr 已容错解码为 str）。

    🔴 实测发现（见 test_cli_gbk_console_crashes_on_emoji）：本脚本面板打印含 emoji
    （📊📖🔍…），在 Windows 默认 GBK 控制台会 UnicodeEncodeError 崩溃。为隔离「核心
    确定性逻辑（grade/退出码/聚合）」与「控制台编码脆弱性」，这里给子进程注入
    PYTHONIOENCODING=utf-8（等同 CI/orchestrator 的运行上下文），使逻辑可被稳定断言。
    """
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


# ══════════════════════════════════════════════════════════════════════════
# load_json —— 纯函数（直接 import 真调用）
# ══════════════════════════════════════════════════════════════════════════
def test_load_json_missing_file_returns_default():
    """文件不存在 → 返回 default（不抛）。"""
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        assert mod.load_json(missing) is None
        sentinel = {"x": 1}
        assert mod.load_json(missing, sentinel) is sentinel


def test_load_json_valid_parses():
    """合法 JSON → 解析成对象。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ok.json"
        p.write_text(json.dumps({"a": [1, 2], "中文": True}, ensure_ascii=False),
                     encoding="utf-8")
        got = mod.load_json(p)
        assert got == {"a": [1, 2], "中文": True}


def test_load_json_malformed_returns_default():
    """坏 JSON → 吞 JSONDecodeError 返回 default（默认 None；显式 default 优先）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text("{ this is not json ", encoding="utf-8")
        assert mod.load_json(p) is None
        assert mod.load_json(p, {"fallback": 1}) == {"fallback": 1}


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 退出码 / FATAL / grade / 聚合
# ══════════════════════════════════════════════════════════════════════════
def test_cli_missing_db_dir_exits_2():
    """_数据库 不存在 → [FATAL] stderr + exit 2（致命）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空书"  # 不建 _数据库
        proj.mkdir()
        p = _run_cli(proj)
        assert p.returncode == 2, f"stdout={p.stdout} stderr={p.stderr}"
        assert "FATAL" in p.stderr


def test_cli_empty_project_exits_0():
    """只有空 _数据库（无任何报告）→ 0 项 warning → exit 0，正常打印面板。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        assert "audit_dashboard" in p.stdout


def test_cli_cross_scan_warning_exits_1():
    """最新跨章扫描含 warning>0 → exit 1（核心退出码契约）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_cross_scan(proj, "voice_drift_20260601.json", {
            "scan_ts": "20260601",
            "summary": {"total": 3, "warning": 2, "advisory": 1},
        })
        p = _run_cli(proj)
        assert p.returncode == 1, f"stdout={p.stdout} stderr={p.stderr}"


def test_cli_cross_scan_only_advisory_exits_0():
    """跨章扫描只有 advisory（warning=0）→ 不致命 → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_cross_scan(proj, "drift_20260601.json", {
            "scan_ts": "20260601",
            "summary": {"total": 2, "warning": 0, "advisory": 2},
        })
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"


def test_cli_audit_grade_pass_and_fail_and_cluster_key():
    """grade 推算三分支 + cluster/章号混合 key 都能跑通不崩、exit 0：
    - verdict in pass-family + error=0 → A
    - verdict in pass-family + error>0 → A/B
    - verdict 非 pass-family（needs_agent）→ C+
    - cluster_<key> 报告与 ch_<n> 报告并存（混合 int/str key 排序）
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # ch1: pass, 无 error → A
        _write_audit(proj, "ch_001_audit.json", {
            "verdict": "pass", "summary": {"error": 0, "warning": 0, "waived": 0}})
        # ch2: pass-family(waived), 有 error → A/B
        _write_audit(proj, "ch_002_audit.json", {
            "verdict": "waived", "summary": {"error": 1, "warning": 0, "waived": 1}})
        # ch3: needs_agent → C+
        _write_audit(proj, "ch_003_audit.json", {
            "verdict": "needs_agent", "summary": {"error": 2, "warning": 1, "waived": 0}})
        # cluster key 报告
        _write_audit(proj, "cluster_001_audit.json", {
            "verdict": "pass", "summary": {"error": 0, "warning": 0, "waived": 0}})
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        # 共 4 条 audit 报告进入矩阵（3 章 + 1 cluster）
        assert "共 4 条" in p.stdout, p.stdout
        # grade 标签都出现过
        assert "A/B" in p.stdout
        assert "C+" in p.stdout


def test_cli_recurrence_and_judge_aggregation():
    """复发追踪 Top + judge_reports 计数进入元健康度，且不影响 exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_audit(proj, "ch_001_audit.json", {
            "verdict": "pass", "summary": {"error": 0}})
        _write_judge(proj, "ch_001_writer.json", {"overall_grade": "A"})
        _write_judge(proj, "ch_001_foreshadower.json", {"overall_grade": "B"})
        _write_exp(proj, {
            "_recurrence_tracker": {
                "STYLE_单段超长": {"count": 6, "chapters": [1, 2, 3]},
                "FORESHADOWING_NOT_PAID": {"count": 2, "chapters": [1]},
            },
            "tool_calibration_suggestions": [
                {"code": "STYLE_单段超长", "waived_count": 5,
                 "suggestion_type": "raise_threshold", "scene_type_hint": "action"},
            ],
        })
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        # 高复发 code (count>=4) 计 1；judge_reports 共 2 份
        assert "STYLE_单段超长" in p.stdout
        assert "总 judge_reports: 2" in p.stdout


def test_cli_malformed_audit_file_does_not_crash():
    """损坏的 audit JSON → load_json 吞错返回 {} → grade '?'，整体仍 exit 0 不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        ad = proj / "_数据库" / ".audit"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "ch_001_audit.json").write_text("{ broken json", encoding="utf-8")
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        # 坏文件被 load_json 兜底为 {} → 进入矩阵但 grade='?'
        assert "共 1 条" in p.stdout, p.stdout


def test_cli_gbk_console_crashes_on_emoji():
    """实测文档化：面板含 emoji，子进程 stdout 编码为 GBK（PYTHONIOENCODING=gbk）时
    UnicodeEncodeError 崩溃（returncode!=0 且 stderr 现 UnicodeEncodeError）。

    这是真实环境脆弱性（Windows 默认控制台 = GBK）。其余测试通过 _run_cli 注入
    PYTHONIOENCODING=utf-8 隔离此问题，专测核心确定性逻辑。本测试反向钉死脆弱性，
    避免被静默掩盖；若将来脚本改为 reconfigure utf-8 / errors=replace 自愈，本断言
    需同步调整（届时说明脆弱性已修）。
    """
    import os
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"
        p = subprocess.run(
            [sys.executable, str(_TARGET), str(proj)],
            capture_output=True, cwd=str(_ROOT), env=env)
        err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
        # _数据库 存在 → 不会走 FATAL(exit2)；进入面板打印 emoji → GBK 编码崩
        assert p.returncode != 0, f"GBK 控制台本应崩溃 但 returncode={p.returncode}"
        assert "UnicodeEncodeError" in err, f"未见编码崩溃 stderr={err}"


# ──────────────────────────────────────────────────────────────────────────
# 独立运行入口（零依赖 self-runner）
# ──────────────────────────────────────────────────────────────────────────
def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[audit_dashboard] {passed} passed / {failed} failed / "
          f"{passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
