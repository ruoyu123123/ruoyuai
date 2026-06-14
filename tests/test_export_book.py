"""export_book 回归测试（缺漏修复批次1 · P0-1）— 零依赖范式。

守护点（对应缺漏报告结论）：
  1. 基本拼接：章号升序 + 「第N章 标题」行（title 读 _changes.json·空只写「第N章」）+ 章间空行
  2. 缺章跳过：目录在 txt 缺 → WARN 跳过不中断，其余章正常导出
  3. CHANGES 剥离：正文混入 ---CHANGES--- 标记段 → 导出文件里绝不出现
  4. 无章节：exit 1（main）/ export_book 返回 None
  5. P1-2 接线：孤儿 pending_tail 存在 → stderr 醒目警告但仍继续导出
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import export_book as eb
import chapter_io as cio


# ============ 夹具 ============

def _mk_chapter(root: Path, ch: int, body: str, title: str = None):
    """写 章节/第NNN章/第NNN章.txt（+ 可选 _changes.json 带 title 顶层字段）。"""
    cio.write_body(root, ch, body)
    if title is not None:
        cp = cio.changes_path(root, ch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(
            {"schema_version": "v2.cluster", "chapter": ch, "title": title,
             "factual": {}, "self_eval": {}},
            ensure_ascii=False), encoding="utf-8")


def _mk_pending_tail(root: Path, key: str, body: str):
    d = root / "章节" / f"cluster_{key}_draft"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"cluster_{key}_pending_tail.txt").write_text(body, encoding="utf-8")


def _read_export(report: dict) -> str:
    return Path(report["out_path"]).read_text(encoding="utf-8")


# ============ 1. 基本拼接 + 标题 ============

def test_basic_concat_titles_and_order():
    """章号升序拼接·有 title 写「第N章 标题」·无 title 只写「第N章」·章间空行·默认输出路径。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "测试书"
        root.mkdir()
        # 故意乱序建章（3 → 1 → 2），导出必须按章号升序
        _mk_chapter(root, 3, "第三章正文甲。\n\n第三章正文乙。")           # 无 changes → 只写「第3章」
        _mk_chapter(root, 1, "第一章正文。", title="开局一座山")
        _mk_chapter(root, 2, "第二章正文。", title="")                    # title 空串 → 只写「第2章」
        report = eb.export_book(root)
        assert report is not None
        assert report["chapters"] == 3
        assert report["chapter_list"] == [1, 2, 3]
        # 默认输出路径：<项目>/exports/<书名>_全文_<章数>章.txt
        out = Path(report["out_path"])
        assert out.parent.name == "exports"
        assert out.name == "测试书_全文_3章.txt"
        text = _read_export(report)
        # 标题行格式
        assert "第1章 开局一座山\n\n第一章正文。" in text
        assert "第2章\n\n第二章正文。" in text
        assert "第3章\n\n第三章正文甲。" in text
        # 升序：第1章 在 第2章 前，第2章 在 第3章 前
        assert text.index("第1章 ") < text.index("第2章\n") < text.index("第3章\n")
        # 章间空行（上一章末 + 空行 + 下一章标题）
        assert "第一章正文。\n\n第2章" in text


def test_body_own_title_line_deduped():
    """正文自带「第N章 …」标题行 → 摘出不双写；changes 无 title 时用正文行标题兜底。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        # splitter 历史布局：正文首行就是标题行
        _mk_chapter(root, 1, "第001章 雪夜来客\n\n门被推开了。")
        report = eb.export_book(root)
        text = _read_export(report)
        assert "第1章 雪夜来客\n\n门被推开了。" in text
        # 原 zero-pad 标题行不应残留（双标题）
        assert "第001章 雪夜来客" not in text


# ============ 2. 缺章跳过 ============

def test_missing_chapter_skipped_with_warn():
    """第2章 目录在但 txt 缺 → stderr WARN + 跳过，第1/3章 正常导出不中断。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。", title="一")
        _mk_chapter(root, 3, "三章正文。", title="三")
        # 第2章 只有目录没有 txt（splitter 中断 / 人工误删场景）
        (root / "章节" / "第002章").mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        with redirect_stderr(buf):
            report = eb.export_book(root)
        assert report is not None
        assert report["chapters"] == 2
        assert report["chapter_list"] == [1, 3]
        err = buf.getvalue()
        assert "[WARN]" in err and "第2章" in err, f"应有缺章 WARN，实际 stderr: {err!r}"
        text = _read_export(report)
        assert "第1章 一" in text and "第3章 三" in text
        assert "第2章" not in text


# ============ 3. CHANGES 剥离 ============

def test_changes_markers_stripped():
    """正文混入 ---CHANGES--- / ---CHANGES_FACTUAL--- 标记段 → 导出绝不含机器数据。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, '正文一。\n\n---CHANGES---\n{"facts_locked": ["秘密A"]}', title="一")
        _mk_chapter(root, 2, '正文二。\n\n---CHANGES_FACTUAL---\n{"x": 1}\n---CHANGES_SELF_EVAL---\n{"y": 2}')
        report = eb.export_book(root)
        text = _read_export(report)
        assert "正文一。" in text and "正文二。" in text
        assert "---CHANGES" not in text
        assert "facts_locked" not in text and "秘密A" not in text


# ============ 改编资料包集成（一人公司·喂 IP 后端·2026-06-15） ============

def test_adaptation_kit_default_off():
    """默认 with_adaptation_kit=False → 不产改编资料包（零回归）+ report.adaptation_kit=None。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "测试书"
        root.mkdir()
        _mk_chapter(root, 1, "正文。")
        report = eb.export_book(root)
        assert report["adaptation_kit"] is None
        assert not (root / "改编资料包").exists()


def test_adaptation_kit_flag_produces_kit():
    """with_adaptation_kit=True + 有人物卡 → 产改编资料包 + report.adaptation_kit 含产出。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "测试书"
        root.mkdir()
        _mk_chapter(root, 1, "正文。")
        dbdir = root / "_数据库"
        dbdir.mkdir(parents=True, exist_ok=True)
        (dbdir / "人物卡.json").write_text(json.dumps(
            {"characters": [{"id": "A", "name": "甲", "role": "主角", "arc": "成长"}]},
            ensure_ascii=False), encoding="utf-8")
        report = eb.export_book(root, with_adaptation_kit=True)
        assert report["adaptation_kit"] is not None
        assert "人物小传.md" in report["adaptation_kit"]["written"]
        assert (root / "改编资料包" / "人物小传.md").exists()


# ============ 4. 无章节 exit 1 ============

def test_no_chapters_exit_1():
    """无任何章节：export_book 返回 None，main exit 1；路径不存在 exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "空书"
        root.mkdir()
        buf = io.StringIO()
        with redirect_stderr(buf):
            assert eb.export_book(root) is None
        assert "[FATAL]" in buf.getvalue()
        # main 退出码：1 无章节
        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(root)])
            raise AssertionError("main 应 sys.exit")
        except SystemExit as e:
            assert e.code == 1
        # main 退出码：2 路径不存在
        try:
            with redirect_stderr(io.StringIO()):
                eb.main([str(Path(d) / "不存在")])
            raise AssertionError("main 应 sys.exit")
        except SystemExit as e:
            assert e.code == 2


def test_main_exit_0_and_out_override():
    """main 成功 exit 0 · --out 自定义输出路径（父目录自动建）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "正文。", title="一")
        out = Path(d) / "自定义" / "全文.txt"
        try:
            eb.main([str(root), "--out", str(out)])
            raise AssertionError("main 应 sys.exit")
        except SystemExit as e:
            assert e.code == 0
        assert out.is_file()
        assert "第1章 一" in out.read_text(encoding="utf-8")


# ============ 5. P1-2 孤儿 pending_tail 警告但仍导出 ============

def test_orphan_pending_tail_warns_but_export_continues():
    """全书最后 cluster 残留 pending_tail 孤儿 → stderr 醒目警告（含字数）但导出照常完成。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "一章正文。", title="一")
        _mk_chapter(root, 2, "二章正文。", title="二")
        # 末 cluster 的 pending_tail 无后继可拼 → 孤儿（finalize_book #3/#4 核心场景）
        tail = "她拿起牛皮纸信封。\n\n" + ("照片边缘已经发黄。" * 60)
        _mk_pending_tail(root, "001", tail)
        buf = io.StringIO()
        with redirect_stderr(buf):
            report = eb.export_book(root)
        assert report is not None and report["chapters"] == 2  # 仍继续导出
        assert report["orphan_pending_tails"] == 1
        err = buf.getvalue()
        assert "pending_tail" in err and "未入章" in err, f"应有孤儿警告，实际: {err!r}"
        assert "cluster_001" in err
        # draft 目录的中间产物绝不能混进导出
        text = _read_export(report)
        assert "牛皮纸信封" not in text


def test_archived_and_draft_dirs_excluded():
    """_archived_v1 归档章 / cluster_*_draft 草稿不参与导出（凿窍纪实地布局）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "书"
        root.mkdir()
        _mk_chapter(root, 1, "正典正文。", title="一")
        # 归档区同章号旧稿（实地验证：凿窍纪 章节/_archived_v1/第001章/…）
        arch = root / "章节" / "_archived_v1" / "第002章"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "第002章.txt").write_text("归档旧稿不该出现。", encoding="utf-8")
        # draft 目录里的同名文件
        dr = root / "章节" / "cluster_001_draft"
        dr.mkdir(parents=True, exist_ok=True)
        (dr / "第003章.txt").write_text("草稿中间产物不该出现。", encoding="utf-8")
        report = eb.export_book(root)
        assert report["chapters"] == 1
        text = _read_export(report)
        assert "正典正文。" in text
        assert "归档旧稿" not in text and "草稿中间产物" not in text


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
