"""world_evolution_apply_card.py 回归测试 — 守护「走向卡选定 → apply_minor_event」入口契约。

被测脚本是一个 CLI：读 _数据库/.wal/第<N>章_fate_cards.json → 按 label 取选定卡 →
取 ripple_match → 在世界文件存在时调 world_evolution_engine.apply_minor_event。

这组测试钉死它的**确定性分支与退出码契约**（不碰 LLM、不联网）：
  exit 1  卡片文件不存在
  exit 2  JSON 损坏 / label 在卡片里不存在
  exit 0  ripple_match 为空（纯叙事跳过）/ 世界文件缺失（未启用世界演化）/ 正常触发涟漪

测试方式：patch sys.argv 驱动真实 main()，捕获 SystemExit 与 stdout，断言退出码 + 关键输出。
happy-path 用真实最小 世界状态.json + 涟漪规则.json 让 apply_minor_event 真跑（确定性数值 delta）。
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import world_evolution_apply_card as mod


# ---------- fixtures ----------

def _mk_project(tmp: Path) -> Path:
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    return tmp


def _write_cards(tmp: Path, ch: int, cards: list):
    p = tmp / "_数据库" / ".wal" / f"第{ch:03d}章_fate_cards.json"
    p.write_text(json.dumps({"cards": cards}, ensure_ascii=False), encoding="utf-8")


def _write_world_and_rules(tmp: Path):
    """最小但真实：一条 minor_event canonical 规则 + 可命中的 factions_state 数值，
    让 apply_minor_event 真跑出一条 delta applied_log。"""
    db = tmp / "_数据库"
    world = {
        "current_ch": 0,
        "factions_state": {"皇朝": {"威望": 50}},
    }
    rules = {
        "ripple_rules": [
            {
                "id": "RR_TEST_01",
                "trigger_type": "minor_event",
                "trigger_match": "皇朝威望受损",
                "ripples": [
                    {"target": "factions_state.皇朝.威望", "delta": -10,
                     "reason": "测试规则命中"}
                ],
            }
        ]
    }
    (db / "世界状态.json").write_text(json.dumps(world, ensure_ascii=False), encoding="utf-8")
    (db / "涟漪规则.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")


def _run(project: Path, ch: int, label: str):
    """驱动真实 main()，返回 (exit_code, stdout_text)。"""
    argv = ["world_evolution_apply_card.py", str(project), str(ch), label]
    old = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    code = None
    try:
        with redirect_stdout(buf):
            mod.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old
    return code, buf.getvalue()


# ---------- tests ----------

def test_missing_cards_file_exits_1():
    """卡片文件不存在 → exit 1（契约：找不到走向卡硬失败）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        code, out = _run(tmp, 5, "A")
        assert code == 1, f"expected exit 1, got {code}; out={out!r}"
        assert "走向卡文件不存在" in out


def test_corrupt_cards_json_exits_2():
    """卡片 JSON 损坏 → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        p = tmp / "_数据库" / ".wal" / "第005章_fate_cards.json"
        p.write_text("{ this is not valid json", encoding="utf-8")
        code, out = _run(tmp, 5, "A")
        assert code == 2, f"expected exit 2, got {code}; out={out!r}"
        assert "JSON 解析失败" in out


def test_unknown_label_exits_2():
    """请求的 label 在卡片里不存在 → exit 2，并回显可选 label 列表。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _write_cards(tmp, 5, [
            {"label": "A", "title": "甲卡", "ripple_match": ""},
            {"label": "B", "title": "乙卡", "ripple_match": ""},
        ])
        code, out = _run(tmp, 5, "C")
        assert code == 2, f"expected exit 2, got {code}; out={out!r}"
        assert "标签 C" in out
        # 回显可选项里应能看到现有 label
        assert "'A'" in out and "'B'" in out


def test_empty_ripple_match_skips_exit_0():
    """选定卡 ripple_match 为空 → 纯叙事推进，跳过涟漪，exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _write_cards(tmp, 5, [
            {"label": "A", "title": "纯叙事卡", "ripple_match": "   "},  # 空白也算空
            {"label": "B", "title": "别的卡", "ripple_match": "xxx"},
        ])
        code, out = _run(tmp, 5, "A")
        assert code == 0, f"expected exit 0, got {code}; out={out!r}"
        assert "纯叙事卡" in out          # 选中卡标题被打印
        assert "SKIP" in out and "ripple_match 为空" in out


def test_world_files_missing_skips_exit_0():
    """卡有 ripple_match 但项目未启用世界演化（缺 世界状态/涟漪规则）→ exit 0 跳过。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _write_cards(tmp, 5, [
            {"label": "A", "title": "有涟漪卡", "ripple_match": "皇朝威望受损"},
        ])
        # 故意不写 世界状态.json / 涟漪规则.json
        code, out = _run(tmp, 5, "A")
        assert code == 0, f"expected exit 0, got {code}; out={out!r}"
        assert "未启用世界演化" in out


def test_happy_path_applies_minor_event_and_mutates_world():
    """全链路 happy path：卡有 ripple_match + 世界文件齐全 → 真调 apply_minor_event，
    命中规则、应用数值 delta、世界状态文件被写回（50 → 40），exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _write_cards(tmp, 5, [
            {"label": "B", "title": "皇朝失势", "ripple_match": "皇朝威望受损"},
        ])
        _write_world_and_rules(tmp)

        code, out = _run(tmp, 5, "B")
        assert code == 0, f"expected exit 0, got {code}; out={out!r}"
        assert "皇朝失势" in out
        assert "apply_minor_event" in out
        # 规则被匹配上（matched_rules 非空 → 不应出现未匹配 WARN）
        assert "RR_TEST_01" in out
        assert "WARN" not in out

        # 世界状态被真实写回：威望 50 - 10 = 40
        world_after = json.loads(
            (tmp / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        assert world_after["factions_state"]["皇朝"]["威望"] == 40
        # 触发被记进 world_ticks_log
        log = world_after.get("world_ticks_log", [])
        assert any(e.get("trigger_type") == "minor_event" for e in log)


def test_ripple_match_no_rule_match_warns_but_exit_0():
    """卡有 ripple_match 且世界文件齐全，但没有规则能匹配该触发词 →
    apply_minor_event 返回空 matched_rules → 打印 WARN，仍 exit 0（顾问非门禁）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _write_cards(tmp, 5, [
            {"label": "A", "title": "无规则覆盖卡", "ripple_match": "完全没有规则覆盖的触发词"},
        ])
        _write_world_and_rules(tmp)  # 只有匹配「皇朝威望受损」的规则

        code, out = _run(tmp, 5, "A")
        assert code == 0, f"expected exit 0, got {code}; out={out!r}"
        assert "WARN" in out
        assert "未匹配任何" in out

        # 世界状态未被任何 delta 改动（威望仍 50）
        world_after = json.loads(
            (tmp / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        assert world_after["factions_state"]["皇朝"]["威望"] == 50
