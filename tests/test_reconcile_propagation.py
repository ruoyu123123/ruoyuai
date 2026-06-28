# -*- coding: utf-8 -*-
"""reconcile.py propagation-check 传播债务校验回归网（🔴 2026-06-27 W5）。

根因（completeness critic 揪出的零约束裸奔环节）：/reconcile 改设定后旧值是否真正传播到
受影响章节此前无机器校验——patch 失败/跳过/strategy=D 时旧值残留正文/changes（propagation
debt）无人察觉 → 下次写作 build_manifest 仍读旧描述穿帮。本测试锁 reconcile.propagation_check
确定性行为：扫受影响章节·统计 before 旧值残留·列『未传播章节』·绝不改正文·永不阻断（advisory）。

覆盖：
  · 残留 > 0 → 列入 un_propagated（含 changes.json 残留计入）
  · 残留 = 0 → propagated（旧值已清·传播完成）
  · 无 before 旧值 → skipped(no_before_value)·不误标
  · 缺 change.json/radius.json → FATAL exit 2
  · 北极星⑤：永不改正文（advisory）· 有债务仍返回 0（不阻断）

零依赖范式（__main__ 自跑）。
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import reconcile as rc  # noqa: E402


# ============ 夹具 ============
def _setup(td: str, *, before: str, after: str, target: str,
           chapters: dict, changes: dict | None = None,
           write_change: bool = True, write_radius: bool = True) -> tuple[Path, Path, SimpleNamespace]:
    """造临时项目：章节 txt + 可选 changes.json + workspace 的 change/radius。

    chapters: {chapter_name: body_text}（如 {"第001章": "他是合同律师"}）
    changes:  {chapter_name: changes_json_dict}（可选·写 第NNN章_changes.json）
    返回 (project_root, workspace, args)。
    """
    project_root = Path(td)
    ws = project_root / "_数据库" / ".reconcile" / "test"
    ws.mkdir(parents=True, exist_ok=True)

    affected = []
    for name, body in chapters.items():
        cdir = project_root / "章节" / name
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{name}.txt").write_text(body, encoding="utf-8")
        if changes and name in changes:
            (cdir / f"{name}_changes.json").write_text(
                json.dumps(changes[name], ensure_ascii=False), encoding="utf-8")
        affected.append({
            "chapter_path": f"章节/{name}/{name}.txt",
            "chapter_name": name,
            "impact_level": "high",
            "matched_lines": [1],
            "sample": body[:50],
        })

    if write_change:
        (ws / "change.json").write_text(json.dumps({
            "schema_version": 1, "reconcile_id": "test",
            "target": target, "before": before, "after": after,
        }, ensure_ascii=False), encoding="utf-8")
    if write_radius:
        (ws / "radius.json").write_text(json.dumps({
            "schema_version": 1, "reconcile_id": "test",
            "affected_chapters": affected,
        }, ensure_ascii=False), encoding="utf-8")

    return project_root, ws, SimpleNamespace(reconcile_id="test")


def _debt(ws: Path) -> dict:
    return json.loads((ws / "propagation_debt.json").read_text(encoding="utf-8"))


# ============ 残留检出 ============
def test_residual_in_body_listed_as_un_propagated():
    """正文残留 before 旧值 → 列入 un_propagated。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={"第001章": "魏无咎是个合同律师，专钻漏洞。"})
        rc_code = rc.propagation_check(pr, ws, args)
        assert rc_code == 0  # advisory·永不阻断
        d = _debt(ws)
        assert d["un_propagated_count"] == 1
        assert d["un_propagated_chapters"][0]["chapter_name"] == "第001章"
        assert d["un_propagated_chapters"][0]["body_residual"] == 1


def test_propagated_when_old_value_cleared():
    """旧值已清（只剩 after 新值）→ propagated·un_propagated 空。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={"第001章": "魏无咎是个破产清算师，专钻漏洞。"})
        rc.propagation_check(pr, ws, args)
        d = _debt(ws)
        assert d["un_propagated_count"] == 0
        assert d["propagated_count"] == 1
        assert "传播完成" in d["advisory"]


def test_changes_json_residual_counts():
    """before 旧值只在 changes.json 残留（正文已清）→ 仍计 un_propagated。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(
            td, before="合同律师", after="破产清算师", target="魏无咎",
            chapters={"第001章": "魏无咎在天庭交易所拆解漏洞。"},  # 正文无旧值
            changes={"第001章": {"character_changes": [{"who": "魏无咎", "note": "合同律师身份"}]}})
        rc.propagation_check(pr, ws, args)
        d = _debt(ws)
        assert d["un_propagated_count"] == 1
        rec = d["un_propagated_chapters"][0]
        assert rec["body_residual"] == 0
        assert rec["changes_residual"] >= 1


def test_mixed_partial_propagation():
    """混合：一章残留一章已清 → un=1 propagated=1。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="黑瞳", after="金瞳", target="主角",
                              chapters={
                                  "第001章": "他抬头，黑瞳映着火光。",   # 残留
                                  "第002章": "他抬头，金瞳映着火光。",   # 已清
                              })
        rc.propagation_check(pr, ws, args)
        d = _debt(ws)
        assert d["un_propagated_count"] == 1
        assert d["propagated_count"] == 1
        names = [c["chapter_name"] for c in d["un_propagated_chapters"]]
        assert names == ["第001章"]


# ============ no_before / skip ============
def test_no_before_value_skipped():
    """纯新增设定（无 before 旧值）→ skipped·不误标·返回 0。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="", after="新增侦探身份", target="魏无咎",
                              chapters={"第001章": "魏无咎是个合同律师。"})
        rc_code = rc.propagation_check(pr, ws, args)
        assert rc_code == 0
        d = _debt(ws)
        assert d.get("skipped") == "no_before_value"
        assert d["un_propagated_count"] == 0


# ============ FATAL：缺前序产物 ============
def test_fatal_when_change_missing():
    """缺 change.json → FATAL exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="x", after="y", target="t",
                              chapters={"第001章": "x"}, write_change=False)
        assert rc.propagation_check(pr, ws, args) == 2


def test_fatal_when_radius_missing():
    """缺 radius.json → FATAL exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="x", after="y", target="t",
                              chapters={"第001章": "x"}, write_radius=False)
        assert rc.propagation_check(pr, ws, args) == 2


# ============ 北极星⑤：advisory·绝不改正文 ============
def test_never_edits_chapter_text():
    """🔴 北极星⑤：propagation-check 绝不改正文（只标债务·调和决策权在模型/用户）。"""
    with tempfile.TemporaryDirectory() as td:
        original = "魏无咎是个合同律师，专钻漏洞。"
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={"第001章": original})
        rc.propagation_check(pr, ws, args)
        after = (pr / "章节" / "第001章" / "第001章.txt").read_text(encoding="utf-8")
        assert after == original  # 正文一字未改


def test_returns_zero_even_with_debt():
    """有传播债务仍返回 0（advisory·绝不阻断 plan·配合 `?` 容忍前缀）。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={f"第{i:03d}章": "魏无咎是个合同律师。" for i in range(1, 6)})
        rc_code = rc.propagation_check(pr, ws, args)
        assert rc_code == 0
        d = _debt(ws)
        assert d["un_propagated_count"] == 5
        assert d["gate_level"] == "advisory"


def test_strategy_d_residual_noted_not_blocked():
    """strategy=D（只更新档案不改正文）→ 全章残留属已知债务·仍 advisory 返回 0。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={"第001章": "魏无咎是个合同律师。"})
        # 写一个 strategy=D 的 patch_log
        (ws / "patch_log.json").write_text(json.dumps(
            {"strategy": "D", "dry_run": False}, ensure_ascii=False), encoding="utf-8")
        assert rc.propagation_check(pr, ws, args) == 0
        d = _debt(ws)
        assert d["strategy"] == "D"
        assert d["un_propagated_count"] == 1


# ============ report 汇总 propagation_debt ============
def test_report_includes_propagation_summary():
    """report mode 读 propagation_debt.json → modules.propagation_summary + 顶层 debt_chapters。"""
    with tempfile.TemporaryDirectory() as td:
        pr, ws, args = _setup(td, before="合同律师", after="破产清算师", target="魏无咎",
                              chapters={"第001章": "魏无咎是个合同律师。"})
        rc.propagation_check(pr, ws, args)
        rc.generate_report(pr, ws, args)
        report = json.loads((ws / rc.REPORT_NAME).read_text(encoding="utf-8"))
        ps = report["modules"]["propagation_summary"]
        assert ps["gate_level"] == "advisory"
        assert ps["un_propagated_count"] == 1
        assert report["propagation_debt_chapters"] == ["第001章"]
        # advisory 绝不翻转 verdict（北极星⑤）：无 patch_log → no_op·不因债务变 fail
        assert report["verdict"] == "no_op"


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
