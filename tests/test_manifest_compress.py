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
import types
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


# ============ 🔴 2026-07-02 真模型(surprisal_gpt2) 信息量优选截断接入回归 ============

_FILLER_SENT = "这是平淡无奇的陈述内容。"
_MARKER_SENT = "这里出现了极其罕见的关键转折信息。"
# 前缀 filler 已超 200 字预算·标记句排在第 240+ 字之后·盲切前 200 字必然切不到它
_LONG_FIELD = (_FILLER_SENT * 20) + _MARKER_SENT + (_FILLER_SENT * 5)


def test_gate_off_byte_identical_to_pre_change_behavior(monkeypatch):
    """门控关（未设置 RUOYU_NN_SURPRISAL，默认状态）→ compress() 对同一输入输出
    逐字节等于改动前的盲切前缀行为（零回归基线）。"""
    monkeypatch.delenv("RUOYU_NN_SURPRISAL", raising=False)
    out = mc.compress({"long_field": _LONG_FIELD, "n": [1, 2, 3], "short": "ok"})
    assert out["long_field"] == (
        _LONG_FIELD[:mc.MAX_STR_LEN] + f"...(+{len(_LONG_FIELD) - mc.MAX_STR_LEN} chars)")
    assert out["n"] == [1, 2, 3]
    assert out["short"] == "ok"


def test_model_selects_high_surprisal_sentences_over_blind_prefix(monkeypatch):
    """门控开 + 桥命中(content-aware：含"罕见"标记词的句子得高分) → 高信息量句优先保留·
    与盲切前 200 字（必然切不到晚于第 240 字才出现的标记句）结果不同。"""
    assert len(_LONG_FIELD) > mc.MAX_STR_LEN
    blind = _LONG_FIELD[:mc.MAX_STR_LEN]
    assert _MARKER_SENT not in blind  # 前提校验：标记句确实在盲切范围之外

    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: [
            {"mean_surprisal": 9.0 if "罕见" in t else 1.0} for t in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)

    out = mc.compress({"long_field": _LONG_FIELD, "short": "ok"})
    compressed = out["long_field"]
    assert "罕见" in compressed
    assert compressed != blind + f"...(+{len(_LONG_FIELD) - mc.MAX_STR_LEN} chars)"
    assert "surprisal精选" in compressed
    assert out["short"] == "ok"


def test_model_gate_on_but_bridge_none_keeps_blind_truncation(monkeypatch):
    """门控开但桥返回全 None → 该字符串回退盲切前缀·与门控关时逐字节一致(零回归)。"""
    baseline = mc.compress({"long_field": _LONG_FIELD})
    monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts, ids=None: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
    out = mc.compress({"long_field": _LONG_FIELD})
    assert out == baseline
    assert out["long_field"] == (
        _LONG_FIELD[:mc.MAX_STR_LEN] + f"...(+{len(_LONG_FIELD) - mc.MAX_STR_LEN} chars)")


def test_collect_long_strings_skips_dropped_fields():
    """_collect_long_strings 与 compress() 过滤逻辑同构·不为会被丢弃的字段(_doc 等)
    内的超限字符串浪费模型调用。"""
    long_doc = "x" * 300
    long_keep = "y" * 300
    collected = mc._collect_long_strings({"_doc": long_doc, "keep": long_keep})
    assert long_keep in collected
    assert long_doc not in collected


def test_select_high_surprisal_picks_highest_scores_within_budget():
    """_select_high_surprisal 按分数降序挑句填满预算·未入选句不出现在结果中。"""
    sentences = ["A" * 50 + "。", "B" * 50 + "。", "C" * 50 + "。",
                 "D" * 50 + "。", "E" * 50 + "。"]
    scores = [1.0, 5.0, 2.0, 9.0, 3.0]
    original = "".join(sentences)
    result = mc._select_high_surprisal(original, sentences, scores)
    assert "D" * 50 in result  # 最高分(9.0)
    assert "B" * 50 in result  # 次高分(5.0)
    assert "C" * 50 not in result
    assert "A" * 50 not in result
    assert "surprisal精选" in result


def test_predict_surprisal_batch_empty_input():
    assert mc._predict_surprisal_batch([]) == []


def test_split_sentences_keeps_punctuation():
    parts = mc._split_sentences("第一句。第二句！第三句？")
    assert len(parts) == 3
    assert parts[0] == "第一句。"
