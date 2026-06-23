#!/usr/bin/env python3
"""subplot_progress_update str/dict 走向线兼容测试（G3 e2e 修 · 2026-06-23）

G3 真 API e2e 抓出：subplot_threads.json / 四线脉络.json 的线条目可能是**字符串列表**
（走向线 schema 存成 str），脚本却假设全是 dict 列表 → `line.get("name")` 抛
`'str' object has no attribute 'get'`。advisory 性质虽不阻断主链，但脏报错污染日志。
修后必须对 str / dict 两种 schema 都跑通且不抛异常。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import subplot_progress_update as m   # noqa: E402


def _mkproj(throughlines, threads):
    d = Path(tempfile.mkdtemp())
    db = d / "_数据库"
    db.mkdir(parents=True)
    (db / "四线脉络.json").write_text(
        json.dumps({"throughlines": throughlines}, ensure_ascii=False), encoding="utf-8")
    (db / "subplot_threads.json").write_text(
        json.dumps({"threads": threads}, ensure_ascii=False), encoding="utf-8")
    (db / "故事块摘要.json").write_text(
        json.dumps({"clusters": [
            {"cluster_id": "001", "summary": "主线复仇推进，感情线起步"}]},
            ensure_ascii=False), encoding="utf-8")
    return d


def test_str_list_schema_no_crash():
    """字符串列表 schema：不抛 AttributeError，命中关键词计数正常。"""
    d = _mkproj(throughlines=["主线复仇", "感情线", "支线甲"],
                threads=["主线复仇", "配角线"])
    r = m.update(d, "001")
    # 四线：主线复仇 + 感情线 命中摘要 → 2；支线甲 不命中
    assert r["throughline_updated"] == 2
    # subplot：主线复仇 命中 → 1；配角线 不命中
    assert r["subplot_updated"] == 1
    # str schema 只读不回写：四线脉络仍是字符串列表（schema 未被破坏）
    tl = json.loads((d / "_数据库" / "四线脉络.json").read_text(encoding="utf-8"))
    assert tl["throughlines"] == ["主线复仇", "感情线", "支线甲"]


def test_dict_list_schema_writes_back():
    """dict 列表 schema：命中线条目回写 last_cluster / last_updated。"""
    d = _mkproj(
        throughlines=[{"name": "主线复仇"}, {"name": "无关线"}],
        threads=[{"id": "t1", "name": "主线复仇"}, {"id": "t2", "name": "另一条"}])
    r = m.update(d, "001")
    assert r["throughline_updated"] == 1   # 只有「主线复仇」命中
    assert r["subplot_updated"] == 1
    sub = json.loads((d / "_数据库" / "subplot_threads.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in sub["threads"]}
    assert by_id["t1"].get("last_cluster") == "001"
    assert "last_cluster" not in by_id["t2"]


def test_mixed_list_schema_no_crash():
    """混合 str + dict 列表也不崩（防御性兼容）。"""
    d = _mkproj(
        throughlines=["主线复仇", {"name": "感情线"}],
        threads=["主线复仇", {"id": "t9", "name": "另一条"}])
    r = m.update(d, "001")  # 不抛异常即通过
    assert r["throughline_updated"] == 2  # 两条都命中摘要
    assert r["subplot_updated"] == 1


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
