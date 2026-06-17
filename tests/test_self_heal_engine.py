"""self_heal_engine.py 核心**确定性算法**回归测试（零 LLM / 零联网）。

与既有 tests/test_self_heal_atomic.py **互补不重叠**：那一组钉死「原子写 / 并发不丢计数 /
offset 增量 / 锁内读-改-写」（bug #5）。本组聚焦尚未被覆盖的 **Analyze+Knowledge 层算法**：

- `_classify` / ACTION_MAP / DEFAULT_ACTION：错误类型 → (根因, 动作, severity) 映射。
- 状态升级阈值机：observed→recurring(≥3)→known(≥5) + confidence 公式 + regression 复活。
- `_normalize_kb`：Tolerant Reader 兜底任意脏输入成合法骨架。
- `_apply_ingest` 纯函数：signature 推导 / 空 sig 跳过 / raw_samples 去重+截顶 /
  offset 截断重置 / regression（resolved 又复发）侦测。
- `cmd_suggest`：精确命中 / error_type 模糊命中取 count 最高 / 未见过按类型兜底 + JSON 形状。
- `cmd_emit_lessons`：只沉淀 known/regression + 写 runtime_lessons.md + 空库跳过。
- `_make_lesson`：Reflexion 自然语言经验拼装。
- `main()` CLI：互斥参数必填 / 各子命令退出码（走真 subprocess + --project-root override）。

全程**真 import 真调用被测函数**，断言真实数值/状态/文件内容/退出码，绝不套套逻辑。
不修改被测脚本、不碰其它测试。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import self_heal_engine as she  # noqa: E402

_TARGET = _SCRIPTS / "self_heal_engine.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _write_incidents(root: Path, incidents):
    inc = she._incidents_path(root)  # 内部 mkdir runtime/
    with open(inc, "w", encoding="utf-8") as f:
        for d in incidents:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return inc


def _inc(sig, error_type="KeyError", ts="2026-05-30T10:00:00+08:00", msg="boom", raw=None):
    script, _, loc = sig.partition("::")
    d = {"signature": sig, "script": script or "x.py", "error_type": error_type,
         "location": loc, "message": msg, "ts": ts}
    if raw is not None:
        d["raw"] = raw
    return d


def _run_cli(root: Path, *args):
    """跑真 CLI（真 argparse + main() 的 sys.exit）。返回 (rc, stdout, stderr)。

    Windows 上子进程默认走 CP936（GBK）写中文 stdout，父侧 encoding='utf-8' 会解码失败拿到
    None。强制 PYTHONIOENCODING=utf-8 让子进程统一 UTF-8 输出，errors='replace' 再兜底，
    保证 stdout/stderr 永远是 str（不影响退出码这个核心断言）。
    """
    cmd = [sys.executable, str(_TARGET), "--project-root", str(root), *args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout, p.stderr


# ──────────────────────────────────────────────────────────────────────────
# 1. _classify / ACTION_MAP — 分类映射的关键不变量
# ──────────────────────────────────────────────────────────────────────────
def test_classify_known_types_map_to_expected_severity():
    """ACTION_MAP 关键条目的 severity 分类必须稳定（adaptive_runner 依此选 Plan）。"""
    expect = {
        "KeyError": "adapt",
        "FileNotFoundError": "missing_step",   # 缺步嫌疑
        "JSONDecodeError": "degrade",           # fail-loud 不覆盖
        "TimeoutError": "retry",
        "ConnectionError": "retry",
        "NameError": "escalate",                # 代码 bug 升人
        "ImportError": "escalate",
        "CRASH": "degrade",
        "FATAL": "escalate",
    }
    for et, sev in expect.items():
        rc, act, got = she._classify(et)
        assert got == sev, f"{et} severity 应为 {sev} 实为 {got}"
        assert rc and act, f"{et} 根因/动作不应为空"


def test_classify_unknown_type_falls_back_to_default_escalate():
    """未知错误类型必走 DEFAULT_ACTION（escalate · 人工诊断），不抛错。"""
    rc, act, sev = she._classify("SomeNeverSeenError")
    assert (rc, act, sev) == she.DEFAULT_ACTION
    assert sev == "escalate"


# ──────────────────────────────────────────────────────────────────────────
# 2. _normalize_kb — Tolerant Reader 兜底
# ──────────────────────────────────────────────────────────────────────────
def test_normalize_kb_repairs_garbage_inputs():
    """非 dict / 缺字段 / patterns 非 dict 都被兜底成合法骨架。"""
    # 非 dict 输入 → 全新空骨架
    for bad in (None, [], "x", 42):
        out = she._normalize_kb(bad)
        assert isinstance(out, dict)
        assert out["patterns"] == {} and out["_total_incidents"] == 0
        assert out["_ingest_offset"] == 0
    # dict 缺字段 → 补齐默认
    out = she._normalize_kb({"patterns": {"a": {"count": 1}}})
    assert out["schema_version"] == "v1.self_heal"
    assert out["_ingest_offset"] == 0 and out["_total_incidents"] == 0
    assert out["patterns"]["a"]["count"] == 1  # 已有内容保留
    # patterns 是 list（脏） → 归一成 dict
    out2 = she._normalize_kb({"patterns": ["junk"]})
    assert out2["patterns"] == {}


# ──────────────────────────────────────────────────────────────────────────
# 3. 状态升级阈值机 + confidence 公式（核心算法 · 既有测试未覆盖）
# ──────────────────────────────────────────────────────────────────────────
def test_status_thresholds_observed_recurring_known():
    """count 1/2 → observed；3/4 → recurring；≥5 → known。阈值精确边界。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 三个指纹分别累计到 2 / 3 / 5 次
        incidents = []
        incidents += [_inc("two.py::KeyError")] * 2
        incidents += [_inc("three.py::KeyError")] * 3
        incidents += [_inc("five.py::KeyError")] * 5
        _write_incidents(root, incidents)
        assert she.cmd_ingest(root) == 0
        pats = she.load_kb(root)["patterns"]
        assert pats["two.py::KeyError"]["status"] == "observed"
        assert pats["three.py::KeyError"]["status"] == "recurring"
        assert pats["five.py::KeyError"]["status"] == "known"


def test_confidence_formula_per_status():
    """confidence 公式：observed=0.3*c；recurring=0.5+0.1*(c-3)；known=0.9。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        incidents = []
        incidents += [_inc("c1.py::KeyError")] * 1      # observed → 0.3
        incidents += [_inc("c4.py::KeyError")] * 4      # recurring c=4 → 0.5+0.1=0.6
        incidents += [_inc("c5.py::KeyError")] * 5      # known → 0.9
        _write_incidents(root, incidents)
        she.cmd_ingest(root)
        pats = she.load_kb(root)["patterns"]
        assert pats["c1.py::KeyError"]["confidence"] == 0.3
        assert pats["c4.py::KeyError"]["confidence"] == 0.6
        assert pats["c5.py::KeyError"]["confidence"] == 0.9
        # known 级必带 Reflexion lesson；observed 级不带
        assert pats["c5.py::KeyError"].get("lesson")
        assert "lesson" not in pats["c1.py::KeyError"]


def test_known_pattern_fills_root_cause_and_action_from_action_map():
    """升级后 root_cause_hint / recommended_action / severity 必从 ACTION_MAP 落库。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_inc("svc.py::TimeoutError", error_type="TimeoutError")] * 5)
        she.cmd_ingest(root)
        pat = she.load_kb(root)["patterns"]["svc.py::TimeoutError"]
        rc, act, sev = she._classify("TimeoutError")
        assert pat["root_cause_hint"] == rc
        assert pat["recommended_action"] == act
        assert pat["severity"] == sev == "retry"


# ──────────────────────────────────────────────────────────────────────────
# 4. regression：resolved 又复发 = 修了又犯（重要信号）
# ──────────────────────────────────────────────────────────────────────────
def test_regression_on_resolved_pattern_recurrence():
    """已 resolved 的指纹再次 ingest → status 转 regression + confidence=0.95 + 记 regression_at。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sig = "reg.py::ValueError"
        _write_incidents(root, [_inc(sig, error_type="ValueError")] * 5)
        she.cmd_ingest(root)
        assert she.cmd_resolve(root, sig) == 0
        assert she.load_kb(root)["patterns"][sig]["status"] == "resolved"
        # 再追加一条同指纹 incident → 复发
        with open(she._incidents_path(root), "a", encoding="utf-8") as f:
            f.write(json.dumps(_inc(sig, error_type="ValueError"), ensure_ascii=False) + "\n")
        she.cmd_ingest(root)
        pat = she.load_kb(root)["patterns"][sig]
        assert pat["status"] == "regression"
        assert pat["confidence"] == 0.95
        assert pat.get("regression_at")


def test_resolved_pattern_count_not_upgraded_without_recurrence():
    """resolved 的 pattern 若没有新 incident，状态保持 resolved（升级循环 skip resolved）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sig = "stay.py::KeyError"
        _write_incidents(root, [_inc(sig)] * 5)
        she.cmd_ingest(root)
        she.cmd_resolve(root, sig)
        # 再 ingest（无新 incident，offset 已到末尾）→ resolved 不被升级覆盖
        she.cmd_ingest(root)
        assert she.load_kb(root)["patterns"][sig]["status"] == "resolved"


# ──────────────────────────────────────────────────────────────────────────
# 5. _apply_ingest 纯函数细节：signature 推导 / 空 sig / raw 去重截顶 / 截断重置
# ──────────────────────────────────────────────────────────────────────────
def test_signature_derived_from_script_and_type_when_absent():
    """incident 缺 signature 时由 script::error_type 拼出。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inc = she._incidents_path(root)
        with open(inc, "w", encoding="utf-8") as f:
            # 故意不带 signature 字段
            f.write(json.dumps({"script": "gen.py", "error_type": "IndexError",
                                "message": "oops", "ts": "2026-05-30T10:00:00+08:00"},
                               ensure_ascii=False) + "\n")
        she.cmd_ingest(root)
        pats = she.load_kb(root)["patterns"]
        assert "gen.py::IndexError" in pats
        assert pats["gen.py::IndexError"]["count"] == 1


def test_empty_signature_incident_skipped():
    """signature 与 script/error_type 全空 → sig 全是冒号 → 跳过，不计数。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inc = she._incidents_path(root)
        with open(inc, "w", encoding="utf-8") as f:
            # 既无 signature，script/error_type 也空 → "::" → strip(':') 为空 → skip
            f.write(json.dumps({"message": "ghost", "ts": "2026-05-30T10:00:00+08:00"},
                               ensure_ascii=False) + "\n")
            # 紧跟一条合法的，证明 skip 不影响后续
            f.write(json.dumps(_inc("ok.py::KeyError"), ensure_ascii=False) + "\n")
        she.cmd_ingest(root)
        kb = she.load_kb(root)
        assert kb["_total_incidents"] == 1
        assert list(kb["patterns"].keys()) == ["ok.py::KeyError"]


def test_raw_samples_dedup_and_capped():
    """raw_samples 去重 + 截到 MAX_RAW_SAMPLES（3）条，保留最近的。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sig = "raw.py::TypeError"
        incidents = [
            _inc(sig, error_type="TypeError", raw="tb-A"),
            _inc(sig, error_type="TypeError", raw="tb-A"),   # 重复 → 不再添加
            _inc(sig, error_type="TypeError", raw="tb-B"),
            _inc(sig, error_type="TypeError", raw="tb-C"),
            _inc(sig, error_type="TypeError", raw="tb-D"),   # 超 3 条 → 截掉最旧 tb-A
        ]
        _write_incidents(root, incidents)
        she.cmd_ingest(root)
        samples = she.load_kb(root)["patterns"][sig]["raw_samples"]
        assert len(samples) == she.MAX_RAW_SAMPLES == 3
        assert samples == ["tb-B", "tb-C", "tb-D"]  # 去重 A + 截顶保留最近 3


def test_offset_reset_when_incidents_truncated():
    """incidents.jsonl 被轮转/截断（size < offset）→ offset 重置从头读，不漏计。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_inc("t.py::KeyError")] * 4)
        she.cmd_ingest(root)
        assert she.load_kb(root)["patterns"]["t.py::KeyError"]["count"] == 4
        # 用更短的新文件覆盖（模拟轮转）：1 条，size 远小于旧 offset
        _write_incidents(root, [_inc("t.py::KeyError")])
        she.cmd_ingest(root)
        # 截断重置后从头重读这 1 条 → count 累加到 5（旧 4 + 重读 1）
        assert she.load_kb(root)["patterns"]["t.py::KeyError"]["count"] == 5


# ──────────────────────────────────────────────────────────────────────────
# 6. cmd_suggest — 三条路径 + JSON 形状
# ──────────────────────────────────────────────────────────────────────────
def test_suggest_exact_signature_hit():
    """精确指纹命中 → known=True + 带 status/count/severity，退出码 0。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_inc("hit.py::KeyError")] * 5)
        she.cmd_ingest(root)
        rc, out, err = _run_cli(root, "--suggest", "hit.py::KeyError")
        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["known"] is True
        assert obj["signature"] == "hit.py::KeyError"
        assert obj["status"] == "known"
        assert obj["count"] == 5
        assert obj["severity"] == "adapt"


def test_suggest_fuzzy_by_error_type_picks_highest_count():
    """按 error_type 模糊命中时取 count 最高的 pattern。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        incidents = [_inc("low.py::TypeError", error_type="TypeError")] * 2
        incidents += [_inc("high.py::TypeError", error_type="TypeError")] * 6
        _write_incidents(root, incidents)
        she.cmd_ingest(root)
        rc, out, err = _run_cli(root, "--suggest", "TypeError")
        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["known"] is True
        assert obj["signature"] == "high.py::TypeError"
        assert obj["count"] == 6


def test_suggest_unknown_query_returns_generic_by_type():
    """从未见过的 query → known=False + 按 _classify 给通用建议，退出码 0。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 空库
        rc, out, err = _run_cli(root, "--suggest", "ConnectionError")
        assert rc == 0
        obj = json.loads(out.strip())
        assert obj["known"] is False
        assert obj["query"] == "ConnectionError"
        # 通用建议来自 ACTION_MAP
        exp_rc, exp_act, exp_sev = she._classify("ConnectionError")
        assert obj["severity"] == exp_sev == "retry"
        assert obj["recommended_action"] == exp_act


# ──────────────────────────────────────────────────────────────────────────
# 7. cmd_emit_lessons — 只沉淀 known/regression
# ──────────────────────────────────────────────────────────────────────────
def test_emit_lessons_only_writes_known_and_regression():
    """emit-lessons 只把 known/regression 级写 runtime_lessons.md，observed 不写。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        incidents = []
        incidents += [_inc("known.py::KeyError")] * 5       # known
        incidents += [_inc("obs.py::IndexError", error_type="IndexError")] * 2  # observed
        _write_incidents(root, incidents)
        she.cmd_ingest(root)
        assert she.cmd_emit_lessons(root) == 0
        out = root / "core" / "claude-home" / "lessons" / "runtime_lessons.md"
        text = out.read_text(encoding="utf-8")
        assert "known.py::KeyError" in text
        assert "obs.py::IndexError" not in text  # observed 级不沉淀


def test_emit_lessons_empty_kb_skips_no_file_overwrite():
    """空库（无 known）→ emit-lessons 跳过，返回 0，不创建/破坏 lessons 文件。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 只有 observed 级
        _write_incidents(root, [_inc("o.py::KeyError")] * 2)
        she.cmd_ingest(root)
        assert she.cmd_emit_lessons(root) == 0
        # 无 known → 不写文件
        out = root / "core" / "claude-home" / "lessons" / "runtime_lessons.md"
        assert not out.exists()


def test_make_lesson_includes_count_and_action():
    """_make_lesson 拼装含次数 + 根因 + 推荐动作（Reflexion 经验）。"""
    pat = {"script": "g.py", "location": "L10", "error_type": "KeyError", "count": 7,
           "root_cause_hint": "上游缺字段", "recommended_action": "Tolerant Reader"}
    lesson = she._make_lesson(pat)
    assert "g.py" in lesson and "L10" in lesson and "KeyError" in lesson
    assert "7" in lesson
    assert "上游缺字段" in lesson and "Tolerant Reader" in lesson


# ──────────────────────────────────────────────────────────────────────────
# 8. main() CLI — 互斥/必填 + 退出码（真 subprocess）
# ──────────────────────────────────────────────────────────────────────────
def test_cli_requires_a_command():
    """无任何子命令 → argparse 互斥组 required → 退出码 2（usage error）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rc, out, err = _run_cli(root)  # 不带 --ingest/--suggest/...
        assert rc == 2, f"应为 argparse usage error (2)，实为 {rc}"


def test_cli_ingest_empty_returns_0():
    """无 incidents.jsonl → ingest 跳过，退出码 0。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rc, out, err = _run_cli(root, "--ingest")
        assert rc == 0
        assert "无 incidents" in out or "跳过" in out


def test_cli_resolve_missing_signature_returns_1():
    """resolve 不存在的指纹 → 退出码 1（main 透传 cmd_resolve 返回值）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_inc("real.py::KeyError")])
        _run_cli(root, "--ingest")
        rc, out, err = _run_cli(root, "--resolve", "ghost.py::Nope")
        assert rc == 1


def test_cli_dashboard_runs_and_lists_status():
    """dashboard 渲染各 status 分组，退出码 0，known 级出现在输出。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_inc("dash.py::KeyError")] * 5)
        _run_cli(root, "--ingest")
        rc, out, err = _run_cli(root, "--dashboard")
        assert rc == 0
        assert "dashboard" in out
        assert "dash.py::KeyError" in out
        assert "known" in out
