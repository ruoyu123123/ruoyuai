"""manifest_compress 回归测试 — LLMLingua 风格 manifest 压缩（纯函数·零 LLM）。

钉死 compress() 的确定性行为 + main() 的 CLI 契约：
  · 删 _doc/_note 等开发者注释字段，保留 _critical_summary/_id 等 LLM 要用的字段
  · 删空值（None/[]/{}/""），含「压缩后变空」二次过滤
  · 列表 > 10 截断 + _truncated_at 标记
  · 字符串 > 200 截断 + (+N chars) 后缀
  · main() 读 ch_NNN.json → 写 ch_NNN_compressed.json，缺文件 exit 2
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import manifest_compress as mc  # noqa: E402


# ---------- compress(): 字段删除 ----------

def test_drops_developer_note_fields():
    """DROP_FIELDS_DEEP 里的开发者注释字段被删除，普通字段保留。"""
    out = mc.compress({
        "_doc": "给开发者看的说明",
        "_note": "另一条注释",
        "_writer_hint": "hint",
        "name": "保留我",
        "value": 42,
    })
    assert "_doc" not in out
    assert "_note" not in out
    assert "_writer_hint" not in out
    assert out["name"] == "保留我"
    assert out["value"] == 42


def test_keeps_llm_facing_underscore_fields():
    """KEEP_UNDERSCORE_FIELDS 里的 _ 字段（LLM 要用）必须保留。
    v28 系统整改：_critical_summary/_cache_layout 已移入 DROP_TOP_LEVEL_KEYS（仅 Claude agent 用）。
    """
    out = mc.compress({
        "_id": "ch_001",
        "_schema": "v1",
        "_priority": "high",
        "_doc": "应被删",
    })
    assert out["_id"] == "ch_001"
    assert out["_schema"] == "v1"
    assert out["_priority"] == "high"
    assert "_doc" not in out


# ---------- compress(): 空值删除 ----------

def test_drops_empty_values():
    """None / [] / {} / "" 在结果中被删除，0 / False 这类非空值保留。"""
    out = mc.compress({
        "none_v": None,
        "empty_list": [],
        "empty_dict": {},
        "empty_str": "",
        "zero": 0,
        "false_v": False,
        "kept": "x",
    })
    assert "none_v" not in out
    assert "empty_list" not in out
    assert "empty_dict" not in out
    assert "empty_str" not in out
    # 0 / False 不是 None/[]/{}/"" — 必须保留（不能被空值规则误删）
    assert out["zero"] == 0
    assert out["false_v"] is False
    assert out["kept"] == "x"


def test_drops_value_that_becomes_empty_after_recursion():
    """二次过滤：嵌套 dict 里全是注释字段 → 压缩后变 {} → 整个 key 被删。"""
    out = mc.compress({
        "all_notes": {"_doc": "a", "_note": "b"},      # 压缩后变 {}
        "has_real": {"_doc": "x", "real": 1},          # 压缩后留 {real:1}
    })
    assert "all_notes" not in out
    assert out["has_real"] == {"real": 1}


# ---------- compress(): 列表截断 ----------

def test_list_truncation_over_max():
    """列表长度 > MAX_LIST_LEN(10) → 截到 10 + 追加 _truncated_at 标记。"""
    long_list = list(range(25))
    out = mc.compress({"items": long_list})
    items = out["items"]
    # 10 个保留项 + 1 个截断标记
    assert len(items) == mc.MAX_LIST_LEN + 1
    assert items[:mc.MAX_LIST_LEN] == list(range(mc.MAX_LIST_LEN))
    marker = items[-1]
    assert marker == {"_truncated_at": 25, "_kept": mc.MAX_LIST_LEN}


def test_list_at_or_under_max_not_truncated():
    """列表 == MAX_LIST_LEN 时不截断、不加标记（边界）。"""
    exact = list(range(mc.MAX_LIST_LEN))
    out = mc.compress({"items": exact})
    assert out["items"] == exact
    assert len(out["items"]) == mc.MAX_LIST_LEN


# ---------- compress(): 字符串截断 ----------

def test_string_truncation_over_max():
    """字符串 > MAX_STR_LEN(200) → 截断 + (+N chars) 后缀；短串原样。"""
    long_str = "a" * 250
    out = mc.compress({"text": long_str, "short": "ok"})
    truncated = out["text"]
    assert truncated.startswith("a" * mc.MAX_STR_LEN)
    assert truncated.endswith(f"...(+{250 - mc.MAX_STR_LEN} chars)")
    assert truncated == "a" * mc.MAX_STR_LEN + "...(+50 chars)"
    # 短串不动
    assert out["short"] == "ok"


# ---------- compress(): 顶层非 dict + 嵌套递归 ----------

def test_top_level_list_and_nested_recursion():
    """顶层 list / 深层嵌套 dict 都正确递归压缩。"""
    obj = [
        {"_doc": "drop", "keep": 1},
        {"nested": {"_note": "drop", "deep": [{"_reason": "x", "v": 2}]}},
    ]
    out = mc.compress(obj)
    assert out[0] == {"keep": 1}
    assert out[1] == {"nested": {"deep": [{"v": 2}]}}


def test_empty_dict_input_returns_empty_dict():
    """空 dict / 空 list 输入：边界不崩，原样返回空容器。"""
    assert mc.compress({}) == {}
    assert mc.compress([]) == []
    # 标量原样透传
    assert mc.compress(7) == 7
    assert mc.compress("short") == "short"


# ---------- main(): CLI 契约 ----------

def _run_main(project: Path, ch: int) -> int:
    """以指定 argv 跑 main()，捕获 SystemExit code。"""
    old_argv = sys.argv
    sys.argv = ["manifest_compress.py", str(project), str(ch)]
    try:
        mc.main()
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    finally:
        sys.argv = old_argv


def test_main_writes_compressed_file_and_exits_0():
    """happy path：读 ch_001.json → 写 ch_001_compressed.json，exit 0，注释字段被剥离。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        mdir = proj / "_数据库" / ".manifest"
        mdir.mkdir(parents=True)
        src = mdir / "ch_001.json"
        src.write_text(json.dumps({
            "_doc": "开发者注释应被删",
            "_critical_summary": "顶层元数据应被删",
            "title": "第一章",
            "empty": "",
        }, ensure_ascii=False), encoding="utf-8")

        code = _run_main(proj, 1)
        assert code == 0
        out = mdir / "ch_001_compressed.json"
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "_doc" not in data
        assert "empty" not in data
        # v28: _critical_summary 已移入 DROP_TOP_LEVEL_KEYS（仅 Claude agent 用）
        assert "_critical_summary" not in data
        assert data["title"] == "第一章"


def test_main_missing_manifest_exits_2():
    """缺 manifest 文件 → exit 2（非 0），不写任何输出。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库" / ".manifest").mkdir(parents=True)
        code = _run_main(proj, 99)
        assert code == 2
        assert not (proj / "_数据库" / ".manifest" / "ch_099_compressed.json").exists()
