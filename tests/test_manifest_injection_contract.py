#!/usr/bin/env python3
"""C15 BUILD-MANIFEST-INJECTION-CONTRACT 金标准测试（2026-06-27）。

根因：build_manifest 注入空子系统（active cluster 但 brief 全空）此前只追加字符串 warning，
preflight passed=len(fatal)==0 不拦 → writer 拿空约束写偏。

本测试钉死 C15 三件：
 1. preflight() 契约校验：cluster 声明 active（status∈_EVENT_CLUSTER_ACTIVE_STATUSES）但
    scope_summary + scene_storyboard 双空 → fatal（穿帮层）+ 修复 hint。
 2. _collect_event_cluster_context() mode 细分：内容非空→on · active 却 brief 空→contract_violation。
 3. 空注入 [RUNTIME] 指纹（build_manifest.py::empty_injection::<field>）与 self_heal --ingest
    复发计数（≥3 recurring）对齐。

🔴 北极星 fluid 回归锁（验收关键）：早期 ripple_rules 空 / cluster_002+ 涌现未跑 / 新角色 /
   本章 characters 空 / 源文件缺失 —— 全部保持 warning，绝不误升 fatal。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import build_manifest as bm  # noqa: E402
import self_heal_engine as she  # noqa: E402


# ============ fixture helpers ============

def _write_db(proj: Path, name: str, obj) -> None:
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / f"{name}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_project(tmp: Path, *, clusters, characters=None, progress=None,
                  ripple=None, name="c15_book") -> Path:
    proj = tmp / name
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    _write_db(proj, "进度", progress if progress is not None else {})
    _write_db(proj, "人物卡", {"characters": characters or []})
    _write_db(proj, "事件簇", {"clusters": clusters})
    if ripple is not None:
        _write_db(proj, "涟漪规则", ripple)
    return proj


_FILLED_SCENE = {
    "ch": 1, "title": "强冲突开场",
    "characters": ["江条款"],
    "key_events": ["主角踏入副本，发现规则会还价"],
    "scene_type": ["悬疑"],
    "goal": "in_medias_res 强冲突开场",
}


# ============ (a) 契约校验：active 但 brief 空 → fatal ============

def test_a_active_but_empty_brief_is_fatal_with_hint():
    """status active + scope_summary/scene_storyboard 双空 → preflight passed=False
    且 fatal 含「注入契约破损」+ 修复 hint（cluster_choice_apply）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), clusters=[{
            "cluster_id": "cluster_001", "status": "in_progress",
            "chapter_range": [1, 4], "scope_summary": "", "scene_storyboard": [],
        }])
        pf = bm.DatabaseScanner(proj, 1).preflight()
        assert pf["passed"] is False, "active 却 brief 空必须 fatal 不放行"
        joined = "\n".join(pf["fatal"])
        assert "注入契约破损" in joined, f"fatal 应含契约破损语义：{pf['fatal']}"
        assert "cluster_choice_apply" in joined, f"fatal 应含修复 hint：{pf['fatal']}"


def test_a_cli_exits_2_with_repair_hint():
    """CLI 全链路：active 却 brief 空 → 进程 exit 2 + stderr 含修复 hint。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), clusters=[{
            "cluster_id": "cluster_001", "status": "in_progress",
            "chapter_range": [1, 4], "scope_summary": "", "scene_storyboard": [],
        }])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run(
            [sys.executable, str(_ROOT / "core" / "scripts" / "build_manifest.py"),
             str(proj), "1"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        assert r.returncode == 2, f"contract violation 应 exit 2，得 {r.returncode}\n{r.stderr}"
        assert "cluster_choice_apply" in r.stderr, f"stderr 应含修复 hint：{r.stderr}"
        assert "build_manifest.py::empty_injection::event_cluster_context" in r.stderr, \
            f"stderr 应含空注入指纹：{r.stderr}"


# ============ (b) fluid 回归锁：active + brief 已填 + ripple 空 + 无 emergence → 不误拦 ============

def test_b_fluid_regression_lock_filled_brief_passes():
    """🔴 验收关键：cluster active + scene_storyboard 已填，即便 涟漪规则 空 + 无 emergence 文件，
    preflight 也 passed=True，绝不误升 contract fatal（fluid 涌现回归锁）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(
            Path(tmp),
            clusters=[{
                "cluster_id": "cluster_001", "status": "in_progress",
                "chapter_range": [1, 3], "scope_summary": "主角进入副本摸索规则",
                "scene_storyboard": [_FILLED_SCENE],
            }],
            characters=[{"id": "江条款", "name": "江条款", "role": "主角"}],
            ripple={"rules": []},  # 早期 ripple 空
        )
        pf = bm.DatabaseScanner(proj, 1).preflight()
        assert pf["passed"] is True, f"已填 brief 不应被拦：{pf['fatal']}"
        assert not any("注入契约破损" in f for f in pf["fatal"]), \
            f"已填 brief 绝不能触发契约破损 fatal：{pf['fatal']}"


def test_b_only_scope_summary_filled_passes():
    """边界：scope_summary 非空但 storyboard 空 = brief 已填 = 不空 → 不误拦。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), clusters=[{
            "cluster_id": "cluster_001", "status": "in_progress",
            "chapter_range": [1, 4], "scope_summary": "本块核心：规则会还价",
            "scene_storyboard": [],
        }])
        cl = bm.DatabaseScanner(proj, 1)._event_cluster_by_id("cluster_001")
        assert bm._cluster_brief_empty(cl) is False, "scope_summary 非空即不空"


def test_b_candidate_status_not_active_no_fatal():
    """cluster_002+ 涌现未跑（status 占位非 active）→ 不进契约校验 → 不 fatal。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(
            Path(tmp),
            clusters=[
                {"cluster_id": "cluster_001", "status": "in_progress",
                 "chapter_range": [1, 3], "scope_summary": "第一块",
                 "scene_storyboard": [_FILLED_SCENE]},
                # 候选占位态：scope/storyboard 都空，但 status 非 active → 不该被契约校验拦
                {"cluster_id": "cluster_002", "status": "未涌现",
                 "chapter_range": [4, 6], "scope_summary": "", "scene_storyboard": []},
            ],
            characters=[{"id": "江条款", "name": "江条款", "role": "主角"}],
        )
        pf = bm.DatabaseScanner(proj, 1).preflight()
        assert pf["passed"] is True, f"候选占位块不应触发契约破损：{pf['fatal']}"


# ============ (c) 新角色 → 仍 warning 不 fatal ============

def test_c_new_character_stays_warning_not_fatal():
    """storyboard 角色暂不在人物卡（pre-write 视为新角色）→ warning，passed=True。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(
            Path(tmp),
            clusters=[{
                "cluster_id": "cluster_001", "status": "in_progress",
                "chapter_range": [1, 3], "scope_summary": "主角遇到陌生人",
                "scene_storyboard": [{
                    "ch": 1, "title": "邂逅",
                    "characters": ["陌生访客"], "key_events": ["陌生访客登场"],
                    "scene_type": ["悬疑"], "goal": "引入新角色",
                }],
            }],
            characters=[{"id": "江条款", "name": "江条款", "role": "主角"}],
        )
        pf = bm.DatabaseScanner(proj, 1).preflight()
        assert pf["passed"] is True, f"新角色不该 fatal：{pf['fatal']}"
        assert any("陌生访客" in w and "新角色" in w for w in pf["warning"]), \
            f"新角色应是 warning：{pf['warning']}"


# ============ (d) self_heal ingest 同指纹 ×3 → recurring ============

def test_d_self_heal_ingest_same_fingerprint_thrice_recurring():
    """空注入指纹 build_manifest.py::empty_injection::<field> 与 self_heal --ingest 复发计数对齐：
    同指纹 3 条 → status=recurring（RECURRING_THRESHOLD=3）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runtime = root / "core" / "claude-home" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        sig = "build_manifest.py::empty_injection::event_cluster_context"
        lines = []
        for i in range(3):
            lines.append(json.dumps({
                "kind": "empty_injection", "script": "build_manifest.py",
                "error_type": "empty_injection", "location": "",
                "message": "empty injection event_cluster_context",
                "signature": sig, "ts": f"2026-06-27T0{i}:00:00",
                "raw": "[RUNTIME] empty_injection ...",
            }, ensure_ascii=False))
        (runtime / "incidents.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        rc = she.cmd_ingest(root)
        assert rc == 0
        kb = she.load_kb(root)
        pat = kb["patterns"].get(sig)
        assert pat is not None, f"指纹应入库：{list(kb['patterns'])}"
        assert pat["count"] == 3, f"复发计数应=3，得 {pat['count']}"
        assert pat["status"] == "recurring", f"≥3 应升 recurring，得 {pat['status']}"


# ============ 指纹格式 + collector mode 细分 ============

def test_empty_injection_signal_fingerprint_format():
    """_emit_empty_injection_signal 写 [RUNTIME] 行 + 正确指纹到 stderr·不抛异常。"""
    buf = io.StringIO()
    with redirect_stderr(buf):
        bm._emit_empty_injection_signal("event_cluster_context", "cluster_006", 18)
    out = buf.getvalue()
    assert "[RUNTIME]" in out
    assert "build_manifest.py::empty_injection::event_cluster_context" in out
    assert "cluster=cluster_006" in out and "ch=18" in out


def test_collector_mode_contract_violation_on_empty_active_cluster():
    """active 却 brief 空 → collector 返回 mode=contract_violation（不静默注入空 brief 当 on）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), clusters=[{
            "cluster_id": "cluster_001", "status": "in_progress",
            "chapter_range": [1, 4], "scope_summary": "", "scene_storyboard": [],
        }])
        ctx = bm._collect_event_cluster_context(bm.DatabaseScanner(proj, 1), 1)
        assert ctx.get("mode") == "contract_violation", f"应 contract_violation，得 {ctx.get('mode')}"
        assert ctx.get("cluster_id") == "cluster_001"
        assert "_hint" in ctx


def test_collector_mode_on_filled_active_cluster():
    """active + brief 已填 → collector 照常 mode=on（fluid 回归锁）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), clusters=[{
            "cluster_id": "cluster_001", "status": "in_progress",
            "chapter_range": [1, 3], "scope_summary": "主角进入副本",
            "scene_storyboard": [_FILLED_SCENE],
        }])
        ctx = bm._collect_event_cluster_context(bm.DatabaseScanner(proj, 1), 1)
        assert ctx.get("mode") == "on", f"已填应 mode=on，得 {ctx.get('mode')}"


def test_collector_mode_off_when_no_event_cluster_file():
    """无 事件簇.json → mode=off（世界演化/cluster opt-out 不误判契约破损）。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "no_ec"
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        ctx = bm._collect_event_cluster_context(bm.DatabaseScanner(proj, 1), 1)
        assert ctx.get("mode") == "off"


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
