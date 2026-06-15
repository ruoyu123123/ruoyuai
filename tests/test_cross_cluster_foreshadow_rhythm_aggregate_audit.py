#!/usr/bin/env python3
"""cross_cluster_foreshadow_rhythm_aggregate.py 审计回归测试

锁定 2026-06 两处修复（triage_worth_fixing.json L57 / L126）：

  [L57 · schema bug] 旧代码 `fss = data.get("foreshadowings", []) or data.get("items", [])`
    读的是不存在的键。全部真实书伏笔表.json 是 v27 schema（顶层 promises/deadlines/
    pledges/secrets，无 foreshadowings/items）→ fss 恒空 → [SKIP] 伏笔表为空 → 整组
    伏笔跨章节奏检测器（超期/缺铺垫/过度铺垫/从未启动）在所有真实书 100% 死代码。
    修复：读 promises 真键 + per-item 适配 v27 字段（setup_cluster→章号经 cluster_lookup、
    resolved(bool)→paid、due_by_cluster/due_by_ch_offset→绝对章号、账本 foreshadow_reinforced 聚合）。

  [L126 · due_by 语义分歧] 旧代码把 due_by 当『相对 initiated 的偏移』
    （cur_ch > initiated + due_by）。全系统其余消费者（build_manifest L236 /
    cross_cluster_will_learn L114 / db_schema_validate 999 哨兵）一致把 due_by 当
    【绝对章号】。修复后 OVERDUE 与 NO_REINFORCEMENT 守卫都按绝对章号判定。

覆盖：
  ① promises 真键被读取（修复前恒 [SKIP] 伏笔表为空 → 0 finding）
  ② setup_cluster(int) → initiated_at_ch 经 cluster_id_to_range 解析为绝对章号
  ③ setup_cluster("cluster_00X" 字符串形态) 同样解析
  ④ resolved=true 的 promise 被跳过（不报 NO_REINFORCEMENT）
  ⑤ due_by 作【绝对章号】触发 OVERDUE（旧 initiated+due_by 语义此处不该触发）
  ⑥ due_by_ch_offset(相对) → 归一为绝对章号后正确触发 OVERDUE
  ⑦ 账本 foreshadow_reinforced 聚合 → 压住 NO_REINFORCEMENT（cluster 模式增量补强）
  ⑧ 兼容旧 foreshadowings 键不回归
  ⑨ 模块可 import（cluster_summary_reader / cluster_lookup 链路完好）

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照
test_cross_cluster_declarative_data_aggregate_audit.py）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCRIPT = _SCRIPTS / "cross_cluster_foreshadow_rhythm_aggregate.py"

# Windows 下子进程管道 stdout 默认 locale 编码（GBK）——强制子进程 UTF-8 输出。
_ENV_BASE = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(proj: Path, cluster_mode=True, timeout=120):
    """跑 scanner。cluster_mode=True 时透传 CLUSTER_MODE=1（模拟调度器真实环境）。"""
    env = dict(_ENV_BASE)
    if cluster_mode:
        env["CLUSTER_MODE"] = "1"
    else:
        env.pop("CLUSTER_MODE", None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(proj)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )


def _new_proj() -> tuple[Path, Path, "tempfile.TemporaryDirectory"]:
    td = tempfile.TemporaryDirectory(prefix="fsr_audit_")
    proj = Path(td.name) / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    return proj, db, td


def _write_event_clusters(db: Path, clusters: list[dict]):
    """事件簇.json 供 cluster_lookup.cluster_id_to_range 解析 setup_cluster→章范围。"""
    (db / "事件簇.json").write_text(json.dumps({
        "schema_version": "v2.cluster", "clusters": clusters,
    }, ensure_ascii=False), encoding="utf-8")


def _write_summary(db: Path, clusters: list[dict]):
    """故事块摘要.json 供 cluster 模式定 cur_ch + 账本字段（planted/paid/reinforced）。"""
    (db / "故事块摘要.json").write_text(json.dumps({
        "schema_version": "v2.cluster", "clusters": clusters,
    }, ensure_ascii=False), encoding="utf-8")


def _write_fs(db: Path, payload: dict):
    (db / "伏笔表.json").write_text(json.dumps(payload, ensure_ascii=False),
                                    encoding="utf-8")


def _last_report(db: Path) -> dict:
    files = sorted((db / ".cross_chapter_scan").glob("foreshadow_rhythm_*.json"))
    assert files, "未生成报告 JSON"
    return json.loads(files[-1].read_text(encoding="utf-8"))


# ============ ① promises 真键被读取（核心 schema 修复） ============
def test_v27_promises_key_is_read():
    """v27 伏笔表（顶层 promises，无 foreshadowings/items）：修复前 fss 恒空 →
    [SKIP] 伏笔表为空 → 0 finding；修复后必须真正读到 promises 并产出 finding。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_fs(db, {"schema_version": "v27", "promises": [
            {"id": "fs_a", "setup_cluster": 1, "resolved": False, "due_by": None},
        ], "deadlines": [], "pledges": [], "secrets": []})
        r = _run(proj)
        assert "[SKIP]" not in r.stdout, f"仍走 [SKIP]（promises 未被读到）：\n{r.stdout[-400:]}"
        rep = _last_report(db)
        assert rep["total_foreshadowings"] == 1, \
            f"应读到 1 条 promise，实得 {rep['total_foreshadowings']}"
        # ch1 设置后 6 章无 reinforced、due_by=null → NO_REINFORCEMENT
        assert any(f["code"] == "FORESHADOW_NO_REINFORCEMENT" for f in rep["findings"]), \
            f"未产出预期 finding：{[f['code'] for f in rep['findings']]}"
    finally:
        td.cleanup()


# ============ ② setup_cluster(int) → 绝对 initiated_at_ch ============
def test_setup_cluster_int_resolves_to_abs_chapter():
    """setup_cluster=2（int）→ cluster_002 起始章（事件簇 range [4,6] → initiated=4）。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "status": "done", "chapter_range": [4, 10]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "status": "done", "chapter_range": [4, 10]}])
        _write_fs(db, {"promises": [
            {"id": "fs_b", "setup_cluster": 2, "resolved": False, "due_by": None}]})
        r = _run(proj)
        rep = _last_report(db)
        f = next((x for x in rep["findings"] if x["id"] == "fs_b"), None)
        assert f is not None, f"fs_b 未产 finding：{rep['findings']}"
        assert f.get("initiated_at_ch") == 4, \
            f"setup_cluster=2 应解析为 ch4，实得 {f.get('initiated_at_ch')}"
    finally:
        td.cleanup()


# ============ ③ setup_cluster 字符串形态同样解析 ============
def test_setup_cluster_string_form_resolves():
    """setup_cluster='cluster_001' 字符串形态（凿窍纪真实数据里两种混用）→ ch1。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_fs(db, {"promises": [
            {"id": "fs_c", "setup_cluster": "cluster_001", "resolved": False,
             "due_by": None}]})
        r = _run(proj)
        rep = _last_report(db)
        f = next((x for x in rep["findings"] if x["id"] == "fs_c"), None)
        assert f is not None and f.get("initiated_at_ch") == 1, \
            f"setup_cluster='cluster_001' 应解析为 ch1，实得 {f and f.get('initiated_at_ch')}"
    finally:
        td.cleanup()


# ============ ④ resolved=true 被跳过 ============
def test_resolved_promise_skipped():
    """resolved=true（v27 取代 paid_at_ch）→ 视为已回收，跳过，不报 NO_REINFORCEMENT。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_fs(db, {"promises": [
            {"id": "fs_done", "setup_cluster": 1, "resolved": True, "due_by": None}]})
        r = _run(proj)
        rep = _last_report(db)
        assert not any(x["id"] == "fs_done" for x in rep["findings"]), \
            f"resolved=true 的伏笔不应产 finding：{rep['findings']}"
    finally:
        td.cleanup()


# ============ ⑤ due_by 作【绝对章号】触发 OVERDUE（L126 核心修复） ============
def test_due_by_is_absolute_chapter_overdue():
    """initiated=ch1、due_by=3（绝对章号『第3章前回收』）、cur_ch=7、未 resolved
    → 超期 7-3=4 章 → OVERDUE。
    旧『相对』语义会算成 cur_ch>1+3=4 才报且 overdue=3，章号与超期量都错。
    本断言锁绝对语义：overdue_by == cur_ch - due_by == 4。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_fs(db, {"promises": [
            {"id": "fs_due", "setup_cluster": 1, "resolved": False, "due_by": 3}]})
        r = _run(proj)
        rep = _last_report(db)
        f = next((x for x in rep["findings"]
                  if x["id"] == "fs_due" and x["code"] == "FORESHADOW_OVERDUE"), None)
        assert f is not None, f"应触发 FORESHADOW_OVERDUE：{[x['code'] for x in rep['findings']]}"
        assert f["overdue_by"] == 4, \
            f"绝对语义 overdue 应=cur_ch-due_by=7-3=4，实得 {f['overdue_by']}（疑回归 initiated+due_by 相对语义）"
    finally:
        td.cleanup()


# ============ ⑥ due_by_ch_offset(相对) → 归一绝对后触发 ============
def test_due_by_ch_offset_normalizes_then_overdue():
    """v27 due_by_ch_offset 是相对 setup 的偏移：setup_cluster=1(→ch1) + offset=2
    → due_by 绝对章号=3；cur_ch=7 → 超期 4 章 OVERDUE。验证相对偏移先归一为绝对再判定。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_fs(db, {"promises": [
            {"id": "fs_off", "setup_cluster": 1, "resolved": False,
             "due_by": None, "due_by_cluster": None, "due_by_ch_offset": 2}]})
        r = _run(proj)
        rep = _last_report(db)
        f = next((x for x in rep["findings"]
                  if x["id"] == "fs_off" and x["code"] == "FORESHADOW_OVERDUE"), None)
        assert f is not None, f"due_by_ch_offset 应归一为绝对 ch3 并触发 OVERDUE：{rep['findings']}"
        assert f["due_by"] == 3, f"offset 2 + initiated 1 应得 due_by=3，实得 {f['due_by']}"
        assert f["overdue_by"] == 4, f"overdue 应=7-3=4，实得 {f['overdue_by']}"
    finally:
        td.cleanup()


# ============ ⑦ 账本 foreshadow_reinforced 聚合压住 NO_REINFORCEMENT ============
def test_ledger_reinforced_suppresses_no_reinforcement():
    """cluster 账本 foreshadow_reinforced={fid:[ch]} 被聚合进 reinforced → 该伏笔
    len(reinforced)>0 → 不报 NO_REINFORCEMENT（修复 changeB：reinforced 从账本增量补强）。
    对照组 fs_silent 无任何 reinforced → 仍报 NO_REINFORCEMENT。"""
    proj, db, td = _new_proj()
    try:
        _write_event_clusters(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7]}])
        _write_summary(db, [
            {"cluster_id": "cluster_001", "status": "done", "chapter_range": [1, 7],
             "foreshadow_reinforced": {"fs_reinf": [3, 5]}}])
        _write_fs(db, {"promises": [
            {"id": "fs_reinf", "setup_cluster": 1, "resolved": False, "due_by": None},
            {"id": "fs_silent", "setup_cluster": 1, "resolved": False, "due_by": None}]})
        r = _run(proj)
        rep = _last_report(db)
        codes_reinf = [x["code"] for x in rep["findings"] if x["id"] == "fs_reinf"]
        codes_silent = [x["code"] for x in rep["findings"] if x["id"] == "fs_silent"]
        assert "FORESHADOW_NO_REINFORCEMENT" not in codes_reinf, \
            f"账本有 reinforced 的伏笔不应报 NO_REINFORCEMENT：{codes_reinf}"
        assert "FORESHADOW_NO_REINFORCEMENT" in codes_silent, \
            f"对照组（无 reinforced）应报 NO_REINFORCEMENT：{codes_silent}"
    finally:
        td.cleanup()


# ============ ⑧ 兼容旧 foreshadowings 键不回归 ============
def test_legacy_foreshadowings_key_still_works():
    """旧 schema 顶层 foreshadowings + 旧 initiated_at_ch/due_by(相对历史含义已统一为绝对)
    仍能被读到（or 链兜底），不因新增 promises 优先而漏读旧书。"""
    proj, db, td = _new_proj()
    try:
        # 非 cluster 模式：靠物理章节目录定 cur_ch
        for ch in range(1, 8):
            (proj / "章节" / f"第{ch}章").mkdir(parents=True, exist_ok=True)
        _write_fs(db, {"foreshadowings": [
            {"id": "old_fs", "initiated_at_ch": 1, "due_by": 3, "resolved_at_ch": None}]})
        r = _run(proj, cluster_mode=False)
        assert "[SKIP]" not in r.stdout, f"旧 foreshadowings 键应被读到：\n{r.stdout[-400:]}"
        rep = _last_report(db)
        assert rep["total_foreshadowings"] == 1, \
            f"旧键应读到 1 条，实得 {rep['total_foreshadowings']}"
        # initiated=1、due_by=3(绝对)、cur_ch=7 → OVERDUE
        assert any(x["code"] == "FORESHADOW_OVERDUE" for x in rep["findings"]), \
            f"旧键 + due_by 绝对语义应触发 OVERDUE：{[x['code'] for x in rep['findings']]}"
    finally:
        td.cleanup()


# ============ ⑨ 模块可 import ============
def test_module_imports():
    """模块独立 import 不崩（依赖 cluster_summary_reader / cluster_lookup 链路完好）。"""
    sys.path.insert(0, str(_SCRIPTS))
    import importlib
    mod = importlib.import_module("cross_cluster_foreshadow_rhythm_aggregate")
    assert hasattr(mod, "main"), "模块缺 main()"


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
