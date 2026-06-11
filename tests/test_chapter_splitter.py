"""chapter_splitter 回归测试 — 守护 v27 字数钳位算法 + 2026-05-30 动态角色名加强。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_splitter as cs


def test_resolve_rhythm_default():
    assert cs._resolve_rhythm("unknown") == (3000, 4500, 3500)
    assert cs._resolve_rhythm("") == (3000, 4500, 3500)
    assert cs._resolve_rhythm(None) == (3000, 4500, 3500)


def test_chapter_count_too_short_returns_zero():
    """< lo → 0（不切，整段退 pending_tail 等下 cluster 拼）。"""
    assert cs.compute_freestyle_chapter_count(2000, 3000, 4500, 3500) == 0


def test_chapter_count_within_range():
    """关键不变量：每章平均字数必须落在硬范围 [lo, hi]。"""
    for draft in (7000, 10000, 14000, 18000, 23000, 30000):
        n = cs.compute_freestyle_chapter_count(draft, 3000, 4500, 3500)
        assert n >= 1
        per = draft / n
        assert 3000 <= per <= 4500, f"draft={draft} n={n} per_chapter={per:.0f} 越界"


def test_chapter_count_examples():
    assert cs.compute_freestyle_chapter_count(14000, 3000, 4500, 3500) == 4
    assert cs.compute_freestyle_chapter_count(7000, 3000, 4500, 3500) == 2


def test_load_char_names_dynamic():
    """动态加载 人物卡.json（name + 姓名 两种 key）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True)
        (tmp / "_数据库" / "人物卡.json").write_text(
            json.dumps({"characters": [{"name": "陆寒山"}, {"姓名": "老周"}]}, ensure_ascii=False),
            encoding="utf-8")
        names = cs._load_char_names(tmp)
        assert "陆寒山" in names and "老周" in names


def test_load_char_names_missing_or_broken_returns_empty():
    """无文件 / 损坏 JSON → 空表（退化不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        assert cs._load_char_names(Path(d) / "nope") == []
        db = Path(d) / "_数据库"
        db.mkdir(parents=True)
        (db / "人物卡.json").write_text("{坏", encoding="utf-8")
        assert cs._load_char_names(Path(d)) == []


def test_freestyle_tail_overflow_rebalanced():
    """轮次2 全旅程实测回归：等距锚点把余量堆末章超 hi 21% → 4.5 再平衡把最后切点
    逐段后移（倒数第二章不破 hi 为限）。合成 17400 CJK 草稿·切完每章须 ≤4500。"""
    import chapter_splitter as cs
    from pathlib import Path as _P
    import tempfile
    para = "这是一个测试段落，" * 10          # ~90 CJK/段
    text = "\n\n".join([para] * 195)        # ~17500 CJK
    with tempfile.TemporaryDirectory() as tmp:
        r = cs.run_freestyle(_P(tmp), "cluster_001", 1, text,
                             "标准", None, True, narrative_mode="linear")
    assert r["per_chapter_cjk"], "应切出章"
    hi = r["_freestyle_decision_log"]["per_chapter_range"][1]
    assert all(c <= hi for c in r["per_chapter_cjk"]), \
        f"末章上溢未再平衡: {r['per_chapter_cjk']} hi={hi}"
