"""writer_truth_check 回归测试 —— 钉死 writer 自评真实性检测的确定性纯逻辑。

被测脚本 core/scripts/writer_truth_check.py 的非 LLM 部分：
  · identify_opening_type / identify_ending_type —— 正则规则识别开头/结尾 type（顺序敏感）
  · _strip_chapter_title / extract_first_line / extract_last_line —— 正文行抽取
  · load_json —— 缺失/坏 JSON 回退 default
  · truth_check_chapter —— 端到端：缺文件报 error、申报 vs 独立提取对比、anchors 撒谎检测

零依赖：只用标准库 + tempfile 临时项目目录，绝不写真项目。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_io as cio  # noqa: E402
import writer_truth_check as wtc  # noqa: E402


# ============ 纯函数：开头 type 识别 ============

def test_identify_opening_type_strips_title_then_dialogue():
    """首行是 '第NNN章 标题' 应被剥离，紧接对话引号 → 纯对话开场。"""
    body = '第012章 风起\n“你来晚了。”他说。\n后面还有内容。'
    assert wtc.identify_opening_type(body) == "纯对话开场"


def test_identify_opening_type_onomatopoeia():
    """拟声词 + 破折号开场（在对话规则之前命中）→ 拟声定格。"""
    body = "砰——\n门被踹开了。"
    assert wtc.identify_opening_type(body) == "拟声定格"


def test_identify_opening_type_fallback_scene():
    """既非对话又非拟声/锚点的普通叙述 → 兜底场景型。"""
    body = "院子里堆满了落叶，墙角的水缸结了一层薄冰。"
    assert wtc.identify_opening_type(body) == "场景型"


# ============ 纯函数：结尾 type 识别 ============

def test_identify_ending_type_dialogue_suspense():
    """结尾以问号收 → 对话悬念（先于场景硬收命中）。"""
    body = "他盯着那扇门看了很久。\n“里面到底有什么？”"
    assert wtc.identify_ending_type(body) == "对话悬念"


def test_identify_ending_type_fallback_hard_close():
    """普通陈述句收尾、未命中前置规则 → 场景硬收兜底。"""
    # 末行较长(>12 字避开独立短句)、无问号/引号/留白动作收束、无 ——/：信息炸弹
    body = "天色一点点暗下来，整条街渐渐沉入一片浓得化不开的灰蒙蒙暮色当中"
    assert wtc.identify_ending_type(body) == "场景硬收"


# ============ 纯函数：行抽取 ============

def test_extract_first_line_skips_title_and_blanks():
    body = "第003章 序\n\n\n  真正的第一句。  \n第二句。"
    assert wtc.extract_first_line(body) == "真正的第一句。"


def test_extract_last_line_uses_raw_body_last_nonblank():
    """extract_last_line 取原始 body 最后一个非空行（不剥标题）。"""
    body = "第一行\n中间\n  最后一行  \n\n   \n"
    assert wtc.extract_last_line(body) == "最后一行"


def test_extract_line_empty_body_returns_empty():
    assert wtc.extract_first_line("") == ""
    assert wtc.extract_last_line("   \n  \n") == ""


# ============ load_json：缺失 / 坏 JSON 回退 ============

def test_load_json_missing_and_corrupt_return_default():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        missing = tmp / "nope.json"
        assert wtc.load_json(missing, default={"x": 1}) == {"x": 1}

        bad = tmp / "bad.json"
        bad.write_text("{not json,", encoding="utf-8")
        assert wtc.load_json(bad, default=[]) == []

        good = tmp / "good.json"
        good.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        assert wtc.load_json(good) == {"k": "v"}


# ============ 端到端 truth_check_chapter ============

def _write_chapter(project_root: Path, ch: int, body: str, changes: dict):
    """用被测脚本依赖的 chapter_io 真实落盘正文 + _changes.json。"""
    cio.write_body(project_root, ch, body)
    cio.write_changes(project_root, ch, changes)


def test_truth_check_missing_chapter_returns_error():
    with tempfile.TemporaryDirectory() as d:
        rep = wtc.truth_check_chapter(Path(d), 7)
        assert rep["ch"] == 7
        assert "error" in rep


def test_truth_check_honest_writer_no_lies():
    """writer 自评与正文一致 → 0 撒谎、type 匹配、line 匹配。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = '第001章 起\n“你终于来了。”老人开口。\n他递过一只铜铃，铃身刻着古怪的纹路。\n“拿着它，别回头。”'
        first = "“你终于来了。”老人开口。"
        last = "“拿着它，别回头。”"
        changes = {
            "factual": {},
            "self_eval": {
                "applied_style": {
                    "opening_type": "纯对话开场",
                    "opening_line": first,
                    "ending_type": "对话悬念",
                    "ending_line": last,
                    "anchors_hit": ["铜铃", "纹路"],
                }
            },
        }
        _write_chapter(tmp, 1, body, changes)
        rep = wtc.truth_check_chapter(tmp, 1)

        assert rep["lie_count"] == 0
        assert rep["lies_detected"] == []
        assert rep["opening_type_match"] is True
        assert rep["ending_type_match"] is True
        assert rep["opening_line_match"] is True
        assert rep["ending_line_match"] is True
        # anchors 全部 in_body
        assert all(a["in_body"] for a in rep["anchors_truth"])


def test_truth_check_detects_lying_anchor():
    """申报的 anchor 不在正文 → 记一条 anchors_hit 撒谎。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = "第002章\n他走进空荡荡的房间，地上只有一只旧鞋。"
        changes = {
            "factual": {},
            "self_eval": {
                "applied_style": {
                    "anchors_hit": ["旧鞋", "并不存在的龙纹剑"],
                }
            },
        }
        _write_chapter(tmp, 2, body, changes)
        rep = wtc.truth_check_chapter(tmp, 2)

        assert rep["lie_count"] == 1
        lie = rep["lies_detected"][0]
        assert lie["field"] == "anchors_hit"
        assert lie["missing"] == ["并不存在的龙纹剑"]
        # 真实存在的锚点标 in_body=True，伪造的标 False
        truth = {a["anchor"]: a["in_body"] for a in rep["anchors_truth"]}
        assert truth["旧鞋"] is True
        assert truth["并不存在的龙纹剑"] is False


def test_truth_check_undeclared_fields_match_is_none():
    """writer 没申报 opening_line/ending_line/type → 对应 match 字段为 None（不算撒谎）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = "第003章\n风很大，吹得旗子哗哗作响，远处传来一声闷雷滚过天际。"
        # 完全空的 self_eval/applied_style
        _write_chapter(tmp, 3, body, {"factual": {}, "self_eval": {}})
        rep = wtc.truth_check_chapter(tmp, 3)

        assert rep["opening_line_match"] is None
        assert rep["ending_line_match"] is None
        assert rep["opening_type_match"] is None
        assert rep["ending_type_match"] is None
        assert rep["lie_count"] == 0
        # 独立提取仍然产出（不依赖申报）
        assert rep["detected_opening_type"]
        assert rep["detected_ending_type"]
