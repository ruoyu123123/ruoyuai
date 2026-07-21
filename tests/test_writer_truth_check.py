"""writer_truth_check 的 cluster-only 创作自评核对测试。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import writer_truth_check as wtc  # noqa: E402


# ============ 纯函数：开头 type 识别 ============

def test_identify_opening_type_dialogue():
    body = '“你来晚了。”他说。\n后面还有内容。'
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


def test_identify_ending_type_action_void_extended_verbs():
    """回归锁（2026-07-18 真机 cluster_002 实战撞坑）：动作留白判定的动词白名单
    只有 放/推/拉/按/举/抬/蹲/站/走/坐/看/闭/睁/握/垂，不含"写/刻/落"这类同语义
    的收尾动词——导致「一并刻进纸里。」这种动作持续未收束的真实动作留白被误判成
    场景硬收，writer_truth_check 误报 lie。四轮独立 reading-reflector 都判定这类
    收尾是合法动作留白，问题出在分类器动词表覆盖不全，不是 writer 撒谎。"""
    body = "笔尖重新落回本子，接着往下写，一笔一划，写得很慢，像是要把今天这一整天，连同那句还没说出口的警告，一并刻进纸里。"
    assert wtc.identify_ending_type(body) == "动作留白"


def test_identify_ending_type_verb_in_subordinate_clause_not_action_void():
    """回归锁（2026-07-19 真机 cluster_004 实战撞坑）：动作留白动词表里的字
    可能出现在句中的从属/比较分句里（如"和他自己走进这栋楼"），而非句子真正的
    收尾谓语——真正的收尾谓语在后一个逗号分句"是同一批日子"。旧正则允许动词
    与句末标点之间跨逗号匹配 15 字内任意字符，导致这种嵌入式动词被误判成动作
    留白。动词与句末标点之间不得跨逗号/分号，才能只抓真正紧邻句末的收束动作。"""
    body = "撕页发生的那个窗口，逼近得几乎和他自己走进这栋楼，是同一批日子。"
    assert wtc.identify_ending_type(body) != "动作留白"
    assert wtc.identify_ending_type(body) == "场景硬收"


# ============ 纯函数：行抽取 ============

def test_extract_first_line_skips_blanks():
    body = "\n\n  真正的第一句。  \n第二句。"
    assert wtc.extract_first_line(body) == "真正的第一句。"


def test_extract_last_line_uses_raw_body_last_nonblank():
    """extract_last_line 取原始 body 最后一个非空行（不剥标题）。"""
    body = "第一行\n中间\n  最后一行  \n\n   \n"
    assert wtc.extract_last_line(body) == "最后一行"


def test_extract_line_empty_body_returns_empty():
    assert wtc.extract_first_line("") == ""
    assert wtc.extract_last_line("   \n  \n") == ""


def _write_cluster(project_root: Path, cluster: str, body: str, changes: dict) -> None:
    directory = project_root / "章节" / f"{cluster}_draft"
    directory.mkdir(parents=True)
    (directory / f"{cluster}_draft.txt").write_text(body, encoding="utf-8")
    (directory / f"{cluster}_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False), encoding="utf-8",
    )


def test_truth_check_missing_cluster_body_fails():
    with tempfile.TemporaryDirectory() as d:
        try:
            wtc.truth_check_cluster(Path(d), "cluster_007")
        except wtc.WriterTruthError as exc:
            assert "cluster_007_draft.txt" in str(exc)
        else:
            raise AssertionError("缺 cluster 正文必须失败")


def test_truth_check_honest_writer_no_lies():
    """writer 只申报允许的 ending_type，且与 cluster 正文一致。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = '“你终于来了。”老人开口。\n他递过一只铜铃，铃身刻着古怪的纹路。\n“拿着它，别回头。”'
        changes = {
            "self_eval": {"waivers": [], "applied_style": {"ending_type": "对话悬念"}},
        }
        _write_cluster(tmp, "cluster_001", body, changes)
        rep = wtc.truth_check_cluster(tmp, "001")
        findings = rep["specific_findings"]

        assert rep["verdict"] == "pass" and rep["lie_count"] == 0
        assert rep["lies_detected"] == []
        assert findings["ending_type_match"] is True


def test_truth_check_detects_lying_ending_type():
    """申报的 ending_type 与正文不符 → 记一条确定性差异。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = "他走进空荡荡的房间。\n“里面到底有什么？”"
        changes = {
            "self_eval": {
                "waivers": [],
                "applied_style": {"ending_type": "场景硬收"},
            },
        }
        _write_cluster(tmp, "cluster_002", body, changes)
        rep = wtc.truth_check_cluster(tmp, "cluster_002")

        assert rep["verdict"] == "fail" and rep["lie_count"] == 1
        lie = rep["lies_detected"][0]
        assert lie["field"] == "applied_style.ending_type"
        assert lie["declared"] == "场景硬收"
        assert lie["actual"] == "对话悬念"


def test_truth_check_undeclared_fields_match_is_none():
    """writer 未申报 ending_type 时不产生撒谎。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = "风很大，吹得旗子哗哗作响，远处传来一声闷雷滚过天际。"
        _write_cluster(tmp, "cluster_003", body, {"self_eval": {"waivers": []}})
        rep = wtc.truth_check_cluster(tmp, 3)
        findings = rep["specific_findings"]

        assert findings["ending_type_match"] is None
        assert rep["lie_count"] == 0
        # 独立提取仍然产出（不依赖申报）
        assert findings["detected_opening_type"]
        assert findings["detected_ending_type"]


def test_main_writes_fixed_cluster_report_and_blocks_lies():
    with tempfile.TemporaryDirectory() as d:
        project = Path(d)
        _write_cluster(
            project, "cluster_001", "风吹过门缝。",
            {"self_eval": {"waivers": []}},
        )
        assert wtc.main([str(project), "--cluster", "1"]) == 0
        report = (
            project / "_数据库" / ".judge_reports"
            / "cluster_001_writer-truth-check.json"
        )
        assert report.is_file()
        value = json.loads(report.read_text(encoding="utf-8"))
        assert value["cluster_id"] == "cluster_001" and value["verdict"] == "pass"
