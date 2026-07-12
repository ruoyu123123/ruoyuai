#!/usr/bin/env python3
"""cross_cluster_declarative_data_aggregate.py 审计回归测试

覆盖三档退出码契约（warning=2 / advisory=1 / 健康=0）与三类到期未消费检测：
  EVENT_OVERDUE (warning)：事件表.pending_events 到期
    （scheduled_cluster/due_cluster 命中已完成 cluster）但仍未转 triggered。
  SECRET_OVERDUE (warning)：伏笔表.secrets 的 reveal_at_cluster 命中已完成 cluster
    但 status 仍 hidden。
  WILL_LEARN_NOT_TRIGGERED (advisory)：人物卡 will_learn 的 learn_at_cluster 命中
    已完成 cluster 但 fact 未进 knows。

"已完成 cluster" 由 cluster_state_sources.iter_completed_clusters 从
故事块摘要.json 读出的 cluster_id 集合判定，不依赖物理章节目录或章号比较。

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]（照
test_cross_cluster_contract.py）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
_SCRIPT = _SCRIPTS / "cross_cluster_declarative_data_aggregate.py"

sys.path.insert(0, str(_ROOT / "tests"))
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402

# Windows 下子进程管道 stdout 默认 locale 编码（GBK）——强制子进程 UTF-8 输出。
_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def _run(proj: Path, timeout=120):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(proj)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=timeout,
    )


def _new_proj() -> tuple[Path, Path, "tempfile.TemporaryDirectory"]:
    td = tempfile.TemporaryDirectory(prefix="decl_audit_")
    proj = Path(td.name) / "proj"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    return proj, db, td


# ============ ① warning → exit 2 ============
def test_warning_finding_exits_2():
    """SECRET_OVERDUE 是 warning 码：reveal_at_cluster 命中已完成 cluster_001
    且 status 仍 hidden → exit 2（真 warning 不可被调度器当 advisory 静默丢弃）。"""
    proj, db, td = _new_proj()
    try:
        write_cluster_summary(proj, [cluster_record("cluster_001")])
        (db / "伏笔表.json").write_text(json.dumps({
            "secrets": [{"id": "s1", "reveal_at_cluster": "cluster_001",
                         "status": "hidden", "secret": "藏着的真相"}],
        }, ensure_ascii=False), encoding="utf-8")
        r = _run(proj)
        assert "[WARNING]" in r.stdout and "SECRET_OVERDUE" in r.stdout, \
            f"未触发 SECRET_OVERDUE warning：\n{r.stdout[-500:]}"
        assert r.returncode == 2, \
            f"warning 发现应 exit 2，实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ② advisory-only → exit 1 ============
def test_advisory_only_finding_exits_1():
    """WILL_LEARN_NOT_TRIGGERED 是 advisory 码：learn_at_cluster 命中已完成
    cluster_001 但 fact 未进 knows、且无任何 warning → exit 1。"""
    proj, db, td = _new_proj()
    try:
        write_cluster_summary(proj, [cluster_record("cluster_001")])
        (db / "人物卡.json").write_text(json.dumps({
            "characters": [{"id": "c1", "name": "陆参", "knowledge": {
                "knows": [],
                "will_learn": [{"fact": "太子的真实身份", "learn_at_cluster": "cluster_001"}],
            }}],
        }, ensure_ascii=False), encoding="utf-8")
        r = _run(proj)
        assert "[ADVISORY]" in r.stdout and "WILL_LEARN_NOT_TRIGGERED" in r.stdout, \
            f"未触发 WILL_LEARN_NOT_TRIGGERED advisory：\n{r.stdout[-500:]}"
        assert "[WARNING]" not in r.stdout, \
            f"该 fixture 不应有 warning（会污染 exit 码断言）：\n{r.stdout[-500:]}"
        assert r.returncode == 1, \
            f"advisory-only 应 exit 1，实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ③ 无发现 → exit 0 ============
def test_no_finding_exits_0():
    """空项目（无到期项）→ 无 finding → exit 0。"""
    proj, _db, td = _new_proj()
    try:
        r = _run(proj)
        assert "=== 发现 0 项" in r.stdout, f"应零发现：\n{r.stdout[-400:]}"
        assert r.returncode == 0, \
            f"健康应 exit 0，实得 {r.returncode}：\n{r.stdout[-400:]}"
    finally:
        td.cleanup()


# ============ ④ 源码层三档守卫（防静默回归两档） ============
def test_source_has_three_tier_exit_codes():
    """直接审源码：三档退出码契约（warning=2 / advisory=1 / 健康=0）必须存在，
    防止未来 diff 把 warning 档悄悄并回 advisory 档（exit 1 会被调度器当 advisory
    静默丢弃，正是本文件锁定的历史 bug 形态）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "return 2" in src, "缺 warning 档退出码 2（疑回归两档）"
    assert 'summary"]["warning"]' in src, "warning 档判定条件缺失"
    assert 'summary"]["advisory"]' in src, "advisory 档判定条件缺失"
    assert "0 健康 / 1 advisory / 2 warning" in src, "docstring 退出码契约文案被改"


# ============ ⑤ 模块可 import ============
def test_module_imports():
    """模块独立 import 不崩（依赖 cluster_state_sources / cluster_lookup 链路完好）。"""
    sys.path.insert(0, str(_SCRIPTS))
    import importlib
    mod = importlib.import_module("cross_cluster_declarative_data_aggregate")
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
