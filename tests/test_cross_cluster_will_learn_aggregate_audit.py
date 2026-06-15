#!/usr/bin/env python3
"""cross_cluster_will_learn_aggregate.py 审计修复回归测试

锚定 triage_worth_fixing.json 中本文件的两条 bug（L107 + L109·同一处契约脱节的两半）：
  · L109：will_learn 锚字段旧读 due_by/by_ch（无 producer 写）→ 应改读权威 schema
          learn_at_cluster（cluster ID 字符串）经 cluster_lookup.cluster_id_to_range 反查末章作 due_by。
  · L107：item_id/content 旧读 id/name/content/description（真实条目无此 4 键）→ 应兜底 fact/what。
  根因：producer↔consumer 契约脱节。真实 will_learn 条目用 {fact, learn_at_cluster, how}
  （凿窍纪人物卡.json 实证 + build_manifest._collect_will_learn_due L2416 / cross_cluster_declarative
  / validate_chapter 三处兄弟消费者一致）。旧代码 due_by 恒 0 → L110 守卫对每条 continue →
  两个检测（WILL_LEARN_OVERDUE / WILL_LEARN_NEVER_HINTED）全静默 no-op、scanner 恒 exit 0。

回归目标：
  ① import 不崩（新增 import cluster_lookup 不破坏模块）。
  ② 用权威 schema（learn_at_cluster + fact）喂数据 → 检测真正可达（OVERDUE 触发·exit 2），
     证明 due_by 锚已从 learn_at_cluster 经 cluster_lookup 反查得到（修复生效）。
  ③ 向后兼容：旧字段 id/due_by 仍能触发检测（or 兜底保留）。
  ④ graceful：learn_at_cluster=null（未涌现）→ due_by=0 → 该条跳过·不误报·exit 0
     （与兄弟消费者一致·不破坏北极星⑤）。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照 test_cross_cluster_contract）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCANNER = _SCRIPTS / "cross_cluster_will_learn_aggregate.py"

sys.path.insert(0, str(_SCRIPTS))
import cross_cluster_will_learn_aggregate as wl_mod  # noqa: E402

# Windows 子进程管道默认 GBK → 强制 UTF-8（同 test_cross_cluster_contract 范式）
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
# 本 scanner 的 cluster 化分支依赖账本字段；这里用磁盘模式（不设 CLUSTER_MODE）
# → cur_ch 由 章节/第*章 目录末章解析，路径稳定可控。
_ENV.pop("CLUSTER_MODE", None)


def _run(proj, timeout=120):
    return subprocess.run(
        [sys.executable, str(_SCANNER), str(proj)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _mk_project(td, will_learn, clusters, last_ch):
    """造最小项目：人物卡.json(will_learn) + 事件簇.json(cluster→章范围) + 章节目录到 last_ch。"""
    proj = Path(td) / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(json.dumps({
        "characters": [{"name": "重黎", "knowledge": {"will_learn": will_learn}}]
    }, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps({
        "schema_version": "v2.cluster", "clusters": clusters,
    }, ensure_ascii=False), encoding="utf-8")
    # 磁盘模式锚点：建 last_ch 个章节目录 + 正文（正文不含 content_kws → 避免 NEVER_HINTED 噪声干扰）
    for ch in range(1, last_ch + 1):
        cdir = proj / "章节" / f"第{ch:03d}章"
        cdir.mkdir(parents=True)
        (cdir / f"第{ch:03d}章.txt").write_text("无关正文。" * 50, encoding="utf-8")
    return proj


def _findings(proj):
    """读最新落盘报告的 findings（scanner 把报告写进 _数据库/.cross_chapter_scan/）。"""
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("will_learn_*.json")) if scan_dir.exists() else []
    if not reports:
        return []
    obj = json.loads(reports[-1].read_text(encoding="utf-8"))
    return obj.get("findings", [])


# ============ ① import 不崩 + cluster_lookup 已接入命名空间 ============
def test_import_ok_and_cluster_lookup_wired():
    """模块 import 成功（新增 import cluster_lookup 不破坏）；
    且 cluster_lookup 真接入模块命名空间（锚反查依赖它）。"""
    assert hasattr(wl_mod, "cluster_lookup"), "未 import cluster_lookup（L109 锚反查依赖）"
    assert hasattr(wl_mod.cluster_lookup, "cluster_id_to_range"), \
        "cluster_lookup 缺 cluster_id_to_range（锚反查 API）"
    assert hasattr(wl_mod, "main"), "scanner 应有 main 入口"


# ============ ② 权威 schema 检测可达（核心回归：修前恒 no-op）============
def test_authoritative_schema_overdue_detected():
    """用权威 schema {fact, learn_at_cluster, how}：learn_at_cluster=cluster_001(ch1-3)，
    cur_ch=10（远过 ch3）→ WILL_LEARN_OVERDUE 必触发 → exit 2。
    修前：due_by 读 due_by/by_ch=0 → L110 continue → 0 finding → exit 0（静默 no-op）。
    本断言钉住修复：due_by 由 learn_at_cluster 经 cluster_lookup 反查末章得到。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td,
            will_learn=[{
                "fact": "五色石是女娲补天剩石",
                "learn_at_cluster": "cluster_001",
                "how": "卷四揭底",
            }],
            clusters=[{"cluster_id": "cluster_001", "title": "开端",
                       "chapter_range": [1, 3]}],
            last_ch=10,
        )
        r = _run(proj)
        assert r.returncode == 2, \
            f"权威 schema 应触发 OVERDUE → exit 2，实得 {r.returncode}（修前 no-op exit 0）: {r.stdout[-300:]} {r.stderr[-300:]}"
        assert "Traceback" not in r.stderr, f"stderr 出现 Traceback:\n{r.stderr[-600:]}"
        fs = _findings(proj)
        overdue = [f for f in fs if f.get("code") == "WILL_LEARN_OVERDUE"]
        assert overdue, f"未产出 WILL_LEARN_OVERDUE finding（检测仍 no-op）: {fs}"
        f0 = overdue[0]
        # due_by 应为 cluster_001 末章 ch3（来自 chapter_range[1]，非旧 due_by/by_ch=0）
        assert f0.get("due_by") == 3, f"due_by 应反查为末章 3，实得 {f0.get('due_by')}"
        # item_id 应从 fact 兜底（修前 id/name 取不到 → 空串 → 被 L110 跳过）
        assert f0.get("item_id"), f"item_id 应从 fact 兜底非空，实得 {f0.get('item_id')!r}"
        # content 应来自 fact
        assert "五色石" in (f0.get("content") or ""), f"content 应取自 fact，实得 {f0.get('content')!r}"


# ============ ③ 向后兼容：旧字段 id/due_by 仍工作 ============
def test_legacy_due_by_still_works():
    """旧 schema {id, content, due_by}：due_by=3 绝对章号，cur_ch=10 → OVERDUE 触发。
    证明 fix 保留了 wl.get('due_by') or wl.get('by_ch') 的 or 兜底（未破坏旧数据）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td,
            will_learn=[{"id": "old_item", "content": "旧式认知", "due_by": 3}],
            clusters=[{"cluster_id": "cluster_001", "title": "开端",
                       "chapter_range": [1, 3]}],
            last_ch=10,
        )
        r = _run(proj)
        assert r.returncode == 2, \
            f"旧字段 due_by 应仍触发 OVERDUE → exit 2，实得 {r.returncode}: {r.stdout[-300:]}"
        fs = _findings(proj)
        overdue = [f for f in fs if f.get("code") == "WILL_LEARN_OVERDUE"]
        assert overdue and overdue[0].get("due_by") == 3, f"旧字段 due_by 路径回归: {fs}"
        assert overdue[0].get("item_id") == "old_item", \
            f"旧 id 应仍被 wl.get('id') 优先取到，实得 {overdue[0].get('item_id')!r}"


# ============ ④ graceful：learn_at_cluster=null → 跳过不误报 ============
def test_null_learn_at_cluster_skipped_cleanly():
    """learn_at_cluster=null（未涌现·凿窍纪真实态）+ 无 due_by → due_by=0 → L110 跳过。
    应 0 finding · exit 0 · 无 Traceback（与兄弟消费者 build_manifest 一致·不误报·守北极星⑤）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _mk_project(
            td,
            will_learn=[{
                "fact": "某未定锚的认知",
                "learn_at_cluster": None,
                "how": "待定",
            }],
            clusters=[{"cluster_id": "cluster_001", "title": "开端",
                       "chapter_range": [1, 3]}],
            last_ch=10,
        )
        r = _run(proj)
        assert r.returncode == 0, \
            f"learn_at_cluster=null 应跳过 → exit 0（无可观测损害·不误报），实得 {r.returncode}: {r.stdout[-300:]} {r.stderr[-300:]}"
        assert "Traceback" not in r.stderr, f"stderr 出现 Traceback:\n{r.stderr[-600:]}"
        assert not _findings(proj), f"null 锚不应产 finding（避免假阳性）: {_findings(proj)}"


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
