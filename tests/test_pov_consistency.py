"""pov_consistency_scanner 归因回归测试 — 守护 2026-05-30 修 #7 POV 归因缺陷。

旧 bug：detect_pov_signal_holders 用「POV 动词前 30 字任意角色名」归因 → 第三人称限知
叙事中主角观察配角时，配角名离 POV 动词更近 → 配角被误判 dominant_pov、主角归零。
本测试断言：
  · 纯主角视角段 → 全归主角；
  · 主角盯着配角段（配角是宾语位）→ 仍归主角，不归配角；
  · 真 head-hopping 段（两人各自心理）→ 两人都拿到信号。

advisory scanner，目标=降误报。测试只测确定性归因纯函数，不碰 LLM/agent。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import pov_consistency_scanner as pov


PROTAG = "林尘"
SIDE = "王虎"
NAMES = {PROTAG, SIDE, "张姐"}


def test_pure_protagonist_pov_all_to_protag():
    """纯主角视角段：主角名只在开头出现一次，后续靠零指代 + 代词。
    旧版会把所有 POV 动词归到离它们最近的任意名字（这里也只有林尘），
    本测试主要锁「零指代/代词承前 → 归主角」不丢分。"""
    text = (
        "林尘走进昏暗的仓库。"
        "他觉得空气里有股铁锈味。"
        "想到昨夜那通电话，心口又揪了一下。"
        "他意识到自己来晚了。"
    )
    counts = pov.detect_pov_signal_holders(text, NAMES, protagonist=PROTAG)
    # 觉得 / 想到（零指代）/ 意识到 三个 POV 动词全归主角
    assert counts[PROTAG] == 3, counts
    assert counts[SIDE] == 0, counts


def test_protagonist_observing_sidekick_object_position():
    """核心回归：主角盯着配角，配角是凝视动词的宾语，POV 仍属主角。
    旧版会因王虎名离 POV 动词更近 → 误判王虎为 dominant_pov。"""
    text = (
        "林尘盯着王虎，觉得这人眼神不对劲。"
        "他看出王虎在撒谎。"
        "林尘盯着王虎的手，察觉对方在发抖。"
    )
    counts = pov.detect_pov_signal_holders(text, NAMES, protagonist=PROTAG)
    # 觉得 / 看出 / 察觉 全是林尘的心理，王虎只是被观察对象
    assert counts[PROTAG] == 3, counts
    assert counts[SIDE] == 0, counts


def test_explicit_subject_beats_object():
    """显式主语位 vs 宾语位：「王虎盯着林尘，觉得」应归王虎（王虎是主语）。"""
    text = "王虎盯着林尘，觉得这小子不简单。"
    counts = pov.detect_pov_signal_holders(text, NAMES, protagonist=PROTAG)
    assert counts[SIDE] == 1, counts
    assert counts[PROTAG] == 0, counts


def test_real_head_hopping_both_get_signal():
    """真 head-hopping：两人各自有显式主语 + 心理动词 → 两人都拿信号。
    断言 scanner 仍能抓到（改进只降误报，不掩盖真问题）。"""
    text = (
        "林尘觉得局势已经失控。"
        "王虎心想这正是动手的好时机。"
    )
    counts = pov.detect_pov_signal_holders(text, NAMES, protagonist=PROTAG)
    assert counts[PROTAG] == 1, counts
    assert counts[SIDE] == 1, counts


def test_pronoun_carries_forward_to_last_explicit_subject():
    """代词承前：配角作显式主语后，紧跟的「他+POV动词」承前归该配角。"""
    text = "王虎走上前。他觉得有人在跟踪自己。"
    counts = pov.detect_pov_signal_holders(text, NAMES, protagonist=PROTAG)
    # 「他觉得」承前指代王虎（段落最近显式主语），不机械归主角
    assert counts[SIDE] == 1, counts
    assert counts[PROTAG] == 0, counts


def test_no_protagonist_no_clue_attributes_nobody():
    """无主角在场 + 零指代无主语 → 不错报给任何人（保守）。"""
    text = "屋里很安静。觉得时间过得很慢。"
    counts = pov.detect_pov_signal_holders(text, {"赵四", "钱五"}, protagonist=None)
    assert sum(counts.values()) == 0, counts


def test_full_scan_pure_protagonist_no_false_positive(tmp_path=None):
    """端到端：纯主角 + 观察配角的整段，scan() 不应报 NON_PROTAGONIST_SCENE。"""
    import json
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(json.dumps({
        "characters": [
            {"name": PROTAG, "role": "主角"},
            {"name": SIDE, "role": "配角"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    draft = tmp / "draft.txt"
    draft.write_text(
        "林尘走进会议室。"
        "他觉得气氛不对。"
        "林尘盯着王虎，看出对方心虚。"
        "他意识到陷阱就在眼前。",
        encoding="utf-8")
    report = pov.scan(tmp, draft)
    assert "_fatal" not in report, report
    assert report["protagonist"] == PROTAG
    codes = {i["code"] for i in report["issues"]}
    # 主角视角场景不该被误报为非主角 POV，也不该 head-hopping
    assert "POV_NON_PROTAGONIST_SCENE" not in codes, report
    assert "POV_HEAD_HOPPING" not in codes, report
