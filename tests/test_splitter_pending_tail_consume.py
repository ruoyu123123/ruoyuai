#!/usr/bin/env python3
"""chapter_splitter 跨 cluster pending_tail **消费端** prepend 测试（2026-06-17 · /loop 自主硬化）。

v27 pending_tail 机制有两半：① 生产端（末段 < 单章下限 → 退 pending_tail·test_chapter_splitter
已测「< lo → 0 章」）② **消费端（下个 cluster 拼接：--previous-pending-tail 把上 cluster 的
pending_tail prepend 到草稿头部联合切·previous_pending_tail_consumed_cjk 记账）——此前未测**。

锁住消费端：splitter 收 --previous-pending-tail → 真把它拼到草稿头 → 联合切 → WAL 记
consumed_cjk == pending_tail 的 CJK · 拼接内容出现在首章开头（接龙不丢内容）。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPLITTER = _ROOT / "core" / "scripts" / "chapter_splitter.py"


def _cjk(s: str) -> int:
    return sum(1 for ch in s if "一" <= ch <= "鿿")


def _run_splitter(proj: Path, *extra):
    p = subprocess.run(
        [sys.executable, str(_SPLITTER), str(proj), "--mode", "ecas_freestyle",
         "--cluster-id", "cluster_002", "--cluster-start-ch", "2",
         "--draft", str(proj / "章节" / "cluster_002_draft" / "cluster_002_draft.txt"),
         *extra],
        capture_output=True, cwd=str(_ROOT),
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return subprocess.CompletedProcess(
        p.args, p.returncode,
        p.stdout.decode("utf-8", "replace") if p.stdout else "",
        p.stderr.decode("utf-8", "replace") if p.stderr else "")


def _seed(tmp: Path, draft_cjk_units: int):
    proj = tmp / "拼接书"
    (proj / "_数据库" / ".wal").mkdir(parents=True)
    dd = proj / "章节" / "cluster_002_draft"
    dd.mkdir(parents=True)
    # 草稿（distinctive 本块标记）
    draft = "本块正文内容" * draft_cjk_units      # 6 CJK/单元
    (dd / "cluster_002_draft.txt").write_text(draft, encoding="utf-8")
    return proj, draft


def _wal(proj: Path) -> dict:
    w = proj / "_数据库" / ".wal" / "splitter_cluster_002_decisions.json"
    assert w.exists(), "splitter 未产 cluster_002 WAL"
    return json.loads(w.read_text(encoding="utf-8"))


def test_pending_tail_prepended_and_consumed():
    """--previous-pending-tail → 拼到草稿头·联合切·WAL 记 consumed_cjk == pending_tail CJK。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, draft = _seed(tmp, 1400)                # 草稿 ~8400 CJK
        # 上 cluster pending_tail（distinctive 前缀标记）
        pt_text = "上块悬念尾" * 400                   # 2000 CJK · 5 CJK/单元
        pt_file = tmp / "prev_pending_tail.txt"
        pt_file.write_text(pt_text, encoding="utf-8")

        r = _run_splitter(proj, "--previous-pending-tail", str(pt_file))
        assert r.returncode == 0, f"splitter 应成功·stderr={r.stderr[:200]}"
        wal = _wal(proj)
        # WAL 记账：消费了上 cluster 的 pending_tail CJK
        consumed = wal.get("previous_pending_tail_consumed_cjk", 0)
        assert consumed == _cjk(pt_text), \
            f"consumed_cjk 应 == pending_tail CJK({_cjk(pt_text)})·实际 {consumed}"
        # 联合切：总切出内容 = 草稿 + pending_tail（章数据涨）
        assert wal.get("chapters_split", 0) >= 2, \
            f"草稿+尾巴联合应切 ≥2 章·实际 {wal.get('chapters_split')}"
        # 接龙不丢：拼接的 pending_tail 内容出现在首章开头
        first_ch = proj / "章节" / "第002章" / "第002章.txt"
        assert first_ch.exists(), "首章 第002章 未写出"
        head = first_ch.read_text(encoding="utf-8")[:60]
        assert "上块悬念尾" in head, f"上 cluster pending_tail 未 prepend 到首章开头·首章头: {head!r}"


def test_no_pending_tail_consumes_zero():
    """无 --previous-pending-tail → consumed_cjk == 0（不凭空拼接）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, draft = _seed(tmp, 1400)
        r = _run_splitter(proj)                       # 不传 previous-pending-tail
        assert r.returncode == 0, f"stderr={r.stderr[:200]}"
        wal = _wal(proj)
        assert wal.get("previous_pending_tail_consumed_cjk", 0) == 0, \
            "无 pending_tail 时不该消费"
        first_ch = proj / "章节" / "第002章" / "第002章.txt"
        head = first_ch.read_text(encoding="utf-8")[:60]
        assert "上块悬念尾" not in head, "不该凭空出现上块尾巴"


def test_missing_pending_tail_file_warns_not_crash():
    """--previous-pending-tail 指向不存在文件 → WARN 不崩（鲁棒·退化成无尾巴切）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, draft = _seed(tmp, 1400)
        r = _run_splitter(proj, "--previous-pending-tail", str(tmp / "不存在.txt"))
        assert r.returncode == 0, f"缺文件应 WARN 不崩·stderr={r.stderr[:200]}"
        wal = _wal(proj)
        assert wal.get("previous_pending_tail_consumed_cjk", 0) == 0


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  [FAIL] {nm}: {e}")
            traceback.print_exc()
    sys.exit(1 if fails else 0)
