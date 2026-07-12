#!/usr/bin/env python3
"""跨模块确定性错误处理与数据完整性回归测试。"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))

import chapter_io as cio          # noqa: E402
import cluster_lookup as cl       # noqa: E402
import save_state                 # noqa: E402
import self_heal_engine           # noqa: E402


def _db(proj):
    d = proj / "_数据库"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── #12 chapter_io.read_changes 对损坏/空 _changes.json 容错（不崩审核管线）──
def test_chapter_io_read_changes_tolerant_on_corrupt():
    for content in ("", "{半截", "not json at all", "{}garbage"):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            cp = cio.changes_path(proj, 1)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(content, encoding="utf-8")
            r = cio.read_changes(proj, 1)   # 原裸 json.loads 在此崩 JSONDecodeError
            assert r == {"self_eval": {"waivers": [], "uncertainty_flags": []}}


# ── #3+cluster_lookup str-form：权威反查认 list + 历史 str "lo-hi" ──
def test_cluster_lookup_str_form_chapter_range():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        _db(proj).joinpath("事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_005", "chapter_range": "10-12"}]}, ensure_ascii=False),
            encoding="utf-8")
        assert cl.cluster_id_to_range(proj, "cluster_005") == [10, 12]   # str 形态被解析
        # 数值归一化：裸数字/短 id 也命中
        assert cl.cluster_id_to_range(proj, "5") == [10, 12]


# ── #3 save_state._get_cluster_chapter_range blueprint 兜底（对齐 evaluators·不再 FATAL）──
def test_save_state_get_range_blueprint_fallback():
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        db = _db(proj)
        # 事件簇 无 chapter_range（fluid v27 未回填）· blueprint 有
        db.joinpath("事件簇.json").write_text(json.dumps({"clusters": [
            {"cluster_id": "cluster_002"}]}, ensure_ascii=False), encoding="utf-8")
        db.joinpath("进度.json").write_text(json.dumps({
            "cluster_blueprint": {"cluster_002": {"chapter_range": [4, 6]}}},
            ensure_ascii=False), encoding="utf-8")
        # git-commit-cluster 用 _get_cluster_chapter_range 取物理章节范围写 commit message；
        # 无 chapter_range 时走 cluster_lookup blueprint 兜底而非返回 []（返回 [] 会让调用方 FATAL exit2）。
        assert save_state._get_cluster_chapter_range(proj, "002") == [4, 5, 6]


# ── #13 self_heal_engine ingest 容忍非字符串 signature（一条毒记录不整批崩）──
def test_self_heal_ingest_nonstring_signature_skips():
    """_apply_ingest(kb, inc_path, stats)：incidents.jsonl 含 int signature 的毒记录·
    原 sig.strip(":") 在 int 上 AttributeError 整批崩·修后强转 str 正常处理。"""
    line = json.dumps({"signature": 12345, "error_type": "KeyError", "script": "x.py"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "incidents.jsonl"
        p.write_text(line + "\n", encoding="utf-8")
        kb, stats = {}, {}
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self_heal_engine._apply_ingest(kb, p, stats)   # 不该抛 AttributeError
    # 毒记录被强转 str 后正常 ingest（非字符串 signature 不再让整批失败）


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
