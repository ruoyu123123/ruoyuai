"""migrate_data_model_v2.py 专属回归测试 — 零依赖范式（标准库 only）。

被测脚本是 v1→v2 cluster-centric 数据迁移工具：把散落在 12 个 JSON 里的「章号」
字段（setup_ch / first_appear_ch / obtained_ch / current_time.chapter …）反查成
「cluster 号」。它是数据完整性脚本，关键不变量：

  1. ch→cluster **必须走 cluster_lookup 真反查**（第 7 章 ≠ cluster_007），反查失败才
     按章号近似并打 `_cluster_inferred` 标记 —— 钉死系统性头号 bug 不静默回退。
  2. **自动 backup**（迁移前整目录拷贝）+ 成功落 `.migration_v2_done.flag` + 失败落
     `.migration_v2_partial.flag`（防半迁移被二次 SKIP 丢数据）。
  3. **dry-run 零副作用**（不动文件 / 不写任何 flag / 不 backup）。
  4. **rollback 用 backup 原地还原** + 清 done flag。
  5. 已迁移（done flag 在）→ SKIP 不重复改。

本文件聚焦上述不变量（之前**无任何专属测试 + 无真实间接覆盖** —— 仅
test_save_state_audit.py / test_foreshadowing_handoff_scanner_audit.py 的 docstring
顺带提名，未 import/调用）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import migrate_data_model_v2 as mod  # noqa: E402

_TARGET = _SCRIPTS / "migrate_data_model_v2.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目 / 写 _数据库 文件 / 跑真 CLI
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write(proj: Path, name: str, data) -> None:
    (proj / "_数据库" / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(proj: Path, name: str):
    return json.loads((proj / "_数据库" / name).read_text(encoding="utf-8"))


def _write_event_clusters(proj: Path, clusters: list) -> None:
    """事件簇.json 是 ch→cluster 反查的权威源（含 chapter_range）。"""
    _write(proj, "事件簇.json", {"clusters": clusters})


def _run_cli(proj: Path, *args):
    """跑真 CLI（真 argparse + 真 backup + 真 flag 落盘）。

    子进程 stdout/stderr 走 Windows 控制台编码（非 UTF-8），errors='replace' 容错；
    断言只锚 returncode + 落盘文件（显式 utf-8 写，是确定性权威输出）。
    """
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _reset_warn_log():
    """_WARN_LOG 是模块级全局，in-process 测之间需清，防串扰。"""
    mod._WARN_LOG.clear()


# ══════════════════════════════════════════════════════════════════════════
# _ch_to_cluster —— 头号系统性 bug 防护（章号 ≠ cluster 号）
# ══════════════════════════════════════════════════════════════════════════
def test_ch_to_cluster_real_lookup_not_mechanical():
    """核心不变量：第 7 章在 cluster_002 的 range[4,7] → 反查必须得 cluster_002，
    绝不是机械拼的 cluster_007；且 inferred=False（真反查成功不打近似标记）。"""
    _reset_warn_log()
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_event_clusters(proj, [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ])
        cid, inferred = mod._ch_to_cluster(proj, 7, mod._WARN_LOG)
        assert cid == "cluster_002", cid          # 头号 bug 防护点
        assert inferred is False, inferred
        assert mod._WARN_LOG == [], mod._WARN_LOG  # 真反查成功不入 warn


def test_ch_to_cluster_fallback_marks_inferred_and_warns():
    """反查不到（无 chapter_range）→ 按章号近似 normalize_cluster_id + inferred=True +
    写入 warn_log（绝不静默）。第 5 章近似为 cluster_005。"""
    _reset_warn_log()
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 故意不写 chapter_range → 反查链全空
        _write_event_clusters(proj, [{"cluster_id": "cluster_001"}])
        cid, inferred = mod._ch_to_cluster(proj, 5, mod._WARN_LOG)
        assert cid == "cluster_005", cid
        assert inferred is True, inferred
        assert len(mod._WARN_LOG) == 1, mod._WARN_LOG
        assert "ch=5" in mod._WARN_LOG[0]


def test_ch_to_cluster_non_int_passthrough_no_infer():
    """非 int（已是 cluster 串 / None）→ 原样归一，不触发 inferred、不入 warn。"""
    _reset_warn_log()
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_event_clusters(proj, [])
        assert mod._ch_to_cluster(proj, "cluster_3", mod._WARN_LOG) == ("cluster_003", False)
        assert mod._ch_to_cluster(proj, None, mod._WARN_LOG) == (None, False)
        assert mod._WARN_LOG == [], mod._WARN_LOG


# ══════════════════════════════════════════════════════════════════════════
# migrate_progress —— chapter_plan → cluster_blueprint（核心 schema 转换）
# ══════════════════════════════════════════════════════════════════════════
def test_migrate_progress_chapter_plan_to_blueprint():
    """chapter_plan(list) 按 cluster 分组成 cluster_blueprint(dict)，每项 ch → scene_index
    (cluster 内 0-based) + _legacy_ch 留档；total_chapters 改名；words_per_chapter 删除。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "进度.json", {
            "total_chapters": 100,
            "words_per_chapter": 3500,
            "chapter_plan": [
                {"ch": 1, "cluster": "cluster_001", "title": "A"},
                {"ch": 2, "cluster": "cluster_001", "title": "B"},
                {"ch": 3, "cluster": "cluster_002", "title": "C"},
            ],
        })
        mod.migrate_progress(proj, dry_run=False)
        out = _read(proj, "进度.json")
        bp = out["cluster_blueprint"]
        assert set(bp.keys()) == {"cluster_001", "cluster_002"}, bp
        sb1 = bp["cluster_001"]["scene_storyboard"]
        assert [s["scene_index"] for s in sb1] == [0, 1], sb1  # cluster 内 0-based 重排
        assert [s["_legacy_ch"] for s in sb1] == [1, 2], sb1   # 原章号留档
        assert "ch" not in sb1[0], sb1[0]                       # 旧 ch 字段已弹出
        assert out["cluster_002"] if False else bp["cluster_002"]["scene_storyboard"][0]["scene_index"] == 0
        # total_chapters → total_chapters_estimate（不再当死锁目标）
        assert out["total_chapters_estimate"] == 100
        assert "total_chapters" not in out
        # words_per_chapter 删除（v27 freestyle 不锁字数）
        assert "words_per_chapter" not in out


def test_migrate_progress_normalizes_list_blueprint_sc1():
    """SC-1：已有 cluster_blueprint 但是 list 形态（城南实测 bug）→ 归一成规范 dict。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "进度.json", {
            "cluster_blueprint": [
                {"ch": 1, "cluster": "cluster_001"},
                {"ch": 2, "cluster": "cluster_001"},
                {"ch": 6, "cluster": "cluster_002"},
            ],
        })
        mod.migrate_progress(proj, dry_run=False)
        bp = _read(proj, "进度.json")["cluster_blueprint"]
        assert isinstance(bp, dict), type(bp)
        assert set(bp.keys()) == {"cluster_001", "cluster_002"}, bp
        assert bp["cluster_001"]["chapter_range"] == [1, 2], bp["cluster_001"]


def test_migrate_progress_dry_run_no_write():
    """dry-run：返回 changes 但**不落盘**（文件内容原样）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        original = {"total_chapters": 50, "chapter_plan": [{"ch": 1, "cluster": "cluster_001"}]}
        _write(proj, "进度.json", original)
        changes = mod.migrate_progress(proj, dry_run=True)
        assert changes, changes                       # 有侦测到改动
        after = _read(proj, "进度.json")
        assert after == original, after               # 但磁盘未被改


# ══════════════════════════════════════════════════════════════════════════
# migrate_chapter_summary —— 章纲摘要.json → 故事块摘要.json（rename + schema）
# ══════════════════════════════════════════════════════════════════════════
def test_migrate_chapter_summary_rename_and_unlink_old():
    """旧 章纲摘要.json 改名为 故事块摘要.json（chapters → clusters）+ 删旧文件。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "章纲摘要.json", {"chapters": [{"n": 1}, {"n": 2}]})
        mod.migrate_chapter_summary(proj, dry_run=False)
        assert not (proj / "_数据库" / "章纲摘要.json").exists()      # 旧文件删
        new = _read(proj, "故事块摘要.json")
        assert "clusters" in new and "chapters" not in new, new      # schema 改
        assert new["clusters"] == [{"n": 1}, {"n": 2}], new          # 数据不丢


def test_migrate_chapter_summary_idempotent_when_already_renamed():
    """已是新文件名（无旧文件）→ 返回 'already renamed' 不重复操作（幂等）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "故事块摘要.json", {"clusters": []})
        changes = mod.migrate_chapter_summary(proj, dry_run=False)
        assert changes == ["already renamed"], changes


# ══════════════════════════════════════════════════════════════════════════
# migrate_foreshadowing —— due_by 阈值语义 + _ch → _cluster
# ══════════════════════════════════════════════════════════════════════════
def test_migrate_foreshadowing_due_by_threshold_semantics():
    """伏笔 due_by < 100 → 反查成 due_by_cluster；due_by >= 100 视为「无截止」→ None。
    且 setup_ch 迁移成 setup_cluster + _legacy 留档。"""
    _reset_warn_log()
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_event_clusters(proj, [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ])
        _write(proj, "伏笔表.json", {
            "promises": [
                {"id": "p1", "setup_ch": 5, "due_by": 7},     # 5→c2, 7→c2
                {"id": "p2", "setup_ch": 1, "due_by": 999},   # due_by>=100 → None
            ],
        })
        mod.migrate_foreshadowing(proj, dry_run=False)
        promises = _read(proj, "伏笔表.json")["promises"]
        p1, p2 = promises[0], promises[1]
        assert p1["setup_cluster"] == "cluster_002", p1
        assert p1["_legacy_setup_ch"] == 5, p1
        assert "setup_ch" not in p1, p1
        assert p1["due_by_cluster"] == "cluster_002", p1
        assert p2["setup_cluster"] == "cluster_001", p2
        assert p2["due_by_cluster"] is None, p2               # >=100 阈值 → 无截止


# ══════════════════════════════════════════════════════════════════════════
# 全链路 CLI —— backup / done flag / dry-run 零副作用 / rollback / SKIP
# ══════════════════════════════════════════════════════════════════════════
def test_cli_full_run_backups_and_writes_done_flag():
    """真 CLI 跑通：退出码 0 + 自动 backup 整个 _数据库 + 落 done flag（不落 partial）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "进度.json", {"total_chapters": 10,
                                    "chapter_plan": [{"ch": 1, "cluster": "cluster_001"}]})
        _write_event_clusters(proj, [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}])
        r = _run_cli(proj)
        assert r.returncode == 0, (r.returncode, r.stderr)
        # 自动 backup 目录存在且是真拷贝（含原始 进度.json 内容）
        baks = list(proj.glob("_数据库.bak.*"))
        assert len(baks) == 1, baks
        bak_prog = json.loads((baks[0] / "进度.json").read_text(encoding="utf-8"))
        assert bak_prog["total_chapters"] == 10, bak_prog       # backup 保留迁移前原貌
        # done flag 落 + partial flag 不落
        assert (proj / "_数据库" / ".migration_v2_done.flag").exists()
        assert not (proj / "_数据库" / ".migration_v2_partial.flag").exists()


def test_cli_dry_run_zero_side_effects():
    """dry-run：退出码 0 + 不 backup + 不写任何 flag + 源文件零改动。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        original = {"total_chapters": 10, "chapter_plan": [{"ch": 1, "cluster": "cluster_001"}]}
        _write(proj, "进度.json", original)
        r = _run_cli(proj, "--dry-run")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert list(proj.glob("_数据库.bak.*")) == []           # 无 backup
        assert not (proj / "_数据库" / ".migration_v2_done.flag").exists()
        assert not (proj / "_数据库" / ".migration_v2_partial.flag").exists()
        assert _read(proj, "进度.json") == original, "dry-run 改了源文件"


def test_cli_skips_when_done_flag_present():
    """done flag 已在 → SKIP（退出码 0 + 不再 backup + 不改源文件）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        original = {"total_chapters": 10, "chapter_plan": [{"ch": 1, "cluster": "cluster_001"}]}
        _write(proj, "进度.json", original)
        (proj / "_数据库" / ".migration_v2_done.flag").write_text(
            "migrated earlier", encoding="utf-8")
        r = _run_cli(proj)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert "SKIP" in r.stdout, r.stdout
        assert list(proj.glob("_数据库.bak.*")) == []           # 不重复 backup
        assert _read(proj, "进度.json") == original, "SKIP 仍改了源文件"


def test_cli_rollback_restores_from_backup():
    """rollback：用 backup 原地还原 _数据库 + 清 done flag。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # 造一个 backup（迁移前原貌）+ 一个被改坏的当前 _数据库 + done flag
        _write(proj, "进度.json", {"corrupted": True})
        bak = proj / "_数据库.bak.20260528_120000"
        bak.mkdir()
        (bak / "进度.json").write_text(
            json.dumps({"total_chapters": 42}, ensure_ascii=False), encoding="utf-8")
        (proj / "_数据库" / ".migration_v2_done.flag").write_text("done", encoding="utf-8")
        r = _run_cli(proj, "--rollback", "_数据库.bak.20260528_120000")
        assert r.returncode == 0, (r.returncode, r.stderr)
        restored = _read(proj, "进度.json")
        assert restored == {"total_chapters": 42}, restored     # 还原成 backup 原貌
        assert not (proj / "_数据库" / ".migration_v2_done.flag").exists()  # done flag 清


def test_cli_rollback_missing_backup_exits_2():
    """rollback 指向不存在的 backup → 退出码 2（FATAL 预检拒绝，不动数据）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write(proj, "进度.json", {"keep": "me"})
        r = _run_cli(proj, "--rollback", "_数据库.bak.NOPE")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert _read(proj, "进度.json") == {"keep": "me"}, "预检失败仍动了数据"


def test_cli_missing_project_exits_2():
    """项目目录不存在 → 退出码 2（FATAL 预检）。"""
    with tempfile.TemporaryDirectory() as d:
        ghost = Path(d) / "不存在的项目"
        r = _run_cli(ghost)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
