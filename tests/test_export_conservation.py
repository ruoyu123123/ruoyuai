"""全书导出守恒回归网（🔴 2026-06-27 W5 · completeness critic 揪出的裸奔环节）。

export 是「最终交付物」——章节缺失/重复/顺序错乱/跨 cluster 字数丢失此前完全无人守
（silently 失败）。本网钉死 export_book.check_book_integrity 的结构完整性自检：

  ① 连续性：1..max 无缺号（缺章 = hard 数据丢失）
  ① 重复：同章号 2+ 正典源文件（hard）
  ② 升序（恒 True · 哨兵）
  ③ 字数守恒：sum(各章 header+body CJK) == 拼接全书 CJK（diff 必须 0 · 标题/CHANGES 剥离后）
  · coverage：cluster range 声明但未导出 → advisory（不 hard）
  · strict：缺章 → main exit 2；默认 → exit 0（顾问制 · 绝不阻断导出·对齐 export.md「exit 始终 0」）

绝不断言内容质量/风格/叙事——export 是格式层（北极星④）。零依赖范式（__main__ 自跑）。
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import export_book as eb  # noqa: E402
import chapter_io as cio  # noqa: E402


# ============ 夹具 ============

def _mk_chapter(root: Path, ch: int, body: str, title: str = None):
    cio.write_body(root, ch, body)
    if title is not None:
        cp = cio.changes_path(root, ch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"chapter": ch, "title": title}, ensure_ascii=False),
                      encoding="utf-8")


def _mk_event_clusters(root: Path, ranges):
    """写 _数据库/事件簇.json 的 clusters[].chapter_range（驱动 coverage 交叉核对）。"""
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    clusters = [{"cluster_id": f"cluster_{i+1:03d}", "chapter_range": list(r)}
                for i, r in enumerate(ranges)]
    (db / "事件簇.json").write_text(json.dumps({"clusters": clusters}, ensure_ascii=False),
                                    encoding="utf-8")


# ============ 1. 健康全书：verdict ok + 字数守恒 diff 0 ============

def test_contiguous_book_verdict_ok_and_conservation_exact():
    """连续 1..N + 无重复 → verdict ok；字数守恒 diff 恒为 0（含标题行/正文分项）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "守恒书"
        root.mkdir()
        _mk_chapter(root, 1, "第一章正文内容甲乙丙。", title="开局")
        _mk_chapter(root, 2, "第二章正文内容丁戊己。", title="转折")
        _mk_chapter(root, 3, "第三章正文内容庚辛壬。", title="")
        report = eb.export_book(root)
        intg = report["integrity"]
        assert intg["verdict"] == "ok", intg
        assert intg["continuity"]["missing"] == []
        assert intg["continuity"]["duplicates"] == {}
        assert intg["continuity"]["ascending"] is True
        wc = intg["word_conservation"]
        assert wc["ok"] and wc["diff"] == 0, wc
        # 守恒分项：full == 正文 + 标题（换行分隔符 0 CJK）
        assert wc["full_cjk"] == wc["body_content_cjk"] + wc["title_overhead_cjk"], wc
        # full_cjk 与 report.total_cjk 一致
        assert wc["full_cjk"] == report["total_cjk"]


def test_conservation_holds_after_changes_and_title_stripped():
    """正文混 CHANGES 段 + 自带标题行 → 剥离/去重后字数守恒仍 diff 0（不把机器数据计进正文）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1,
                    '第001章 雪夜\n\n门被推开了寒风灌进来。\n\n---CHANGES---\n{"facts_locked": ["秘密甲乙丙"]}')
        report = eb.export_book(root)
        wc = report["integrity"]["word_conservation"]
        assert wc["diff"] == 0, wc
        text = Path(report["out_path"]).read_text(encoding="utf-8")
        # CHANGES 段 CJK 绝不计入正文，也绝不出现在导出
        assert "秘密甲乙丙" not in text and "facts_locked" not in text
        assert "门被推开了寒风灌进来" in text


# ============ 2. 缺章 = hard（数据丢失）============

def test_missing_chapter_is_hard_default_warn_exit0():
    """章 1/2/4（缺 3）→ verdict hard·missing=[3]·默认 stderr [WARN] + 导出照常（exit 0 契约）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。", title="一")
        _mk_chapter(root, 2, "二章正文。", title="二")
        _mk_chapter(root, 4, "四章正文。", title="四")
        buf = io.StringIO()
        with redirect_stderr(buf):
            report = eb.export_book(root)  # 默认 strict=False
        intg = report["integrity"]
        assert intg["verdict"] == "hard", intg
        assert intg["continuity"]["missing"] == [3], intg
        assert any(i["code"] == "CHAPTER_MISSING" and i["level"] == "hard"
                   for i in intg["issues"]), intg
        err = buf.getvalue()
        assert "[WARN]" in err and "缺失章节 [3]" in err, err
        assert "[FATAL]" not in err, "默认 advisory 不应打 [FATAL]（防污染 runtime_monitor）"
        # 导出照常完成（顾问非法官）
        assert report["chapters"] == 3 and report["chapter_list"] == [1, 2, 4]


def test_missing_chapter_strict_exit2():
    """--strict + 缺章 → main exit 2（hard 数据丢失）·stderr [FATAL]。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。")
        _mk_chapter(root, 3, "三章正文。")  # 缺 2
        buf = io.StringIO()
        code = None
        try:
            with redirect_stderr(buf):
                eb.main([str(root), "--strict"])
            raise AssertionError("main 应 sys.exit")
        except SystemExit as e:
            code = e.code
        assert code == 2, f"--strict 缺章应 exit 2·实际 {code}"
        assert "[FATAL]" in buf.getvalue()


def test_missing_chapter_default_exit0():
    """同样缺章·无 --strict → main exit 0（export.md「exit 始终 0」顾问制契约不回归）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。")
        _mk_chapter(root, 3, "三章正文。")
        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(root)])
            raise AssertionError("main 应 sys.exit")
        except SystemExit as e:
            assert e.code == 0, f"默认应 exit 0·实际 {e.code}"


# ============ 3. 重复章号 = hard ============

def test_duplicate_chapter_source_is_hard():
    """同章号 2 个正典源文件（章节/第002章 + 章节/卷一/第002章）→ verdict hard·duplicates。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。")
        _mk_chapter(root, 2, "二章正文标准位。")
        # 同章号第二份（非 _ 前缀、非 _draft → 合法正典区 → 真重复歧义）
        dup_dir = root / "章节" / "卷一" / "第002章"
        dup_dir.mkdir(parents=True, exist_ok=True)
        (dup_dir / "第002章.txt").write_text("二章正文重复源。", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stderr(buf):
            report = eb.export_book(root)
        intg = report["integrity"]
        assert intg["verdict"] == "hard", intg
        assert 2 in intg["continuity"]["duplicates"], intg["continuity"]
        assert any(i["code"] == "CHAPTER_DUPLICATE" for i in intg["issues"])
        assert "重复章节 [2]" in buf.getvalue()


def test_archived_and_draft_not_counted_as_duplicate():
    """_archived_v1 / *_draft 里的同章号文件不算重复（非正典区·排除）→ verdict ok。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "正典一章。")
        # 归档区 + 草稿区各放一份同章号——都应被排除，不触发 duplicate
        for sub in ("_archived_v1/第001章", "cluster_001_draft"):
            dd = root / "章节" / sub
            dd.mkdir(parents=True, exist_ok=True)
            (dd / "第001章.txt").write_text("非正典副本。", encoding="utf-8")
        report = eb.export_book(root)
        assert report["integrity"]["verdict"] == "ok", report["integrity"]
        assert report["integrity"]["continuity"]["duplicates"] == {}


# ============ 4. coverage 交叉核对 = advisory（不 hard）============

def test_cluster_range_uncovered_is_advisory_only():
    """cluster range 声明 [1,3] 但只导出 1/2（range 末章未写）→ advisory·非 hard·导出照常。

    注意：导出 1/2 连续无内部缺号（max=2）→ 不触发 continuity hard；
    coverage 发现 range 声明的 ch3 未导出 → 仅 advisory（fluid 未写常态）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章。")
        _mk_chapter(root, 2, "二章。")
        _mk_event_clusters(root, [(1, 3)])  # cluster 声明覆盖到 ch3
        buf = io.StringIO()
        with redirect_stderr(buf):
            report = eb.export_book(root)
        intg = report["integrity"]
        assert intg["verdict"] == "advisory", intg
        assert intg["coverage"]["available"] is True
        assert intg["coverage"]["uncovered"] == [3], intg["coverage"]
        assert not intg["continuity"]["missing"], "1/2 连续·不该报 continuity 缺章"
        assert "[FATAL]" not in buf.getvalue()
        assert "cluster 覆盖缺口" in buf.getvalue()


def test_coverage_unavailable_when_no_event_clusters():
    """无 事件簇.json → coverage.available=False·跳过·不崩·不影响 verdict。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章。")
        _mk_chapter(root, 2, "二章。")
        report = eb.export_book(root)
        assert report["integrity"]["coverage"]["available"] is False
        assert report["integrity"]["verdict"] == "ok"


# ============ 5. 自检不崩 + 报告段存在（鲁棒性）============

def test_integrity_section_always_present_in_report():
    """任意导出 report 都带 integrity 段（最终交付物守护点·不再 silently 失败）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "唯一一章。")
        report = eb.export_book(root)
        assert "integrity" in report
        intg = report["integrity"]
        for k in ("verdict", "ok", "continuity", "coverage", "word_conservation", "issues"):
            assert k in intg, f"integrity 缺字段 {k}"


def test_scan_chapter_sources_dedupes_physical_files():
    """scan_chapter_sources 按 resolve 去重（root 基址 rglob 重复扫 章节/ 下文件）→
    标准布局每章只 1 个源·不误判重复。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章。")
        _mk_chapter(root, 2, "二章。")
        mm = eb.scan_chapter_sources(root)
        assert set(mm.keys()) == {1, 2}
        assert all(len(v) == 1 for v in mm.values()), mm


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
