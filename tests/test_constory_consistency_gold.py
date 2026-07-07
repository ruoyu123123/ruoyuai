# -*- coding: utf-8 -*-
"""ConStory-Bench 五类一致性维度 · 金 fixture 回归锁（P2/P3 移植 · 2026-07-07）

出处：research/open_source_writing_systems.md —— 把 ConStory-Bench 的 benchmark 维度
翻译成本地**确定性金 fixture**（含已知矛盾的中文 cluster 草稿片段 + 最小数据库 JSON），
断言**现有** scanner/validator 的真实检出力。不引运行时依赖、不加 hard_gate、不改 core/。

已有先例：tests/test_consistency_19_subtypes_blindspot.py（绝对时间/恒定数值 →
locked_fact_cross_scene_scanner）。本文件仿该模式扩其余类别。

五类覆盖结论（每类 ≥1 fixture · 捞不到的显式 skip 记录盲区，不硬造假绿）：
  1. entity（实体一致性）   → character_identity_anchor_scanner（active 模式）真检出
  2. temporal（时间一致性） → locked_fact_cross_scene_scanner 恒定单位「天」opt-in 真检出；
                              草稿内部时序倒错（无锁定事实锚）= 盲区 skip
  3. causality（事件因果）  → validate_chapter.check_knowledge_leak → FUTURE_KNOWLEDGE_LEAK 真检出
  4. location（地点连续性） → focalizer_perception_bounds_scanner 规则③空间不在场标志词子集真检出；
                              无标志词的同场景位置瞬移矛盾 = 盲区 skip
  5. contradiction（明文互斥陈述）→ 描述类互斥（生死/亲缘）确定性层无覆盖 = 盲区 skip
                              （locked_fact_cross_scene 只做恒定数值对账；docstring 里的
                              「描述类」承诺代码从未落地——盲区锁钉死现状，将来补上会跑红提醒更新）

北极星纪律：
  · 不新增任何 hard_gate code（制度锁 test_hard_gate_membership_unchanged）
  · advisory scanner 只用 env 三态开关切 active 测检出，不改默认档位
  · 盲区测试 = 先 assert「现有检测器确实 0 检出」再 skip —— 假如将来有人补了覆盖，
    assert 跑红逼着把盲区锁升级成真阳性断言（诚实缺口清单 > 假绿）

跑：py -m pytest tests/test_constory_consistency_gold.py -q
"""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import audit_hub  # noqa: E402
import character_identity_anchor_scanner as cia  # noqa: E402
import focalizer_perception_bounds_scanner as fpb  # noqa: E402
import locked_fact_cross_scene_scanner as lf  # noqa: E402
import validate_chapter as vc  # noqa: E402


# ───────────────────────── 脚手架 ─────────────────────────

@contextmanager
def _env(**kv):
    """临时设 env（scanner 的 _mode() 在 scan() 调用时读取）·退出后恢复原值。"""
    old = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mk_project(characters=None, clusters=None, units=None) -> Path:
    """搭最小项目：_数据库/人物卡.json + 可选 事件簇.json + 可选 locked_fact_units.json。"""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "_数据库"
    db.mkdir(parents=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters or []}, ensure_ascii=False),
        encoding="utf-8")
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8")
    if units is not None:
        (db / "locked_fact_units.json").write_text(
            json.dumps({"invariant_units": units}, ensure_ascii=False),
            encoding="utf-8")
    return tmp


def _write_draft(project: Path, text: str) -> Path:
    p = project / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# 通用中文填充段（把草稿撑过 scanner 的 MIN_CJK=500 门槛；
# 刻意避开 时空标志词/自体不可见词/POV 心理动词 等一切检测词典，保证是纯噪声底料）
_FILLER = (
    "殿外的风卷着沙尘掠过廊柱，灯影在墙上摇成一片碎金。案几上的茶早凉了，"
    "杯沿积着薄薄一圈灰。廊下的铜铃哑了多年，绳结里还缠着去岁的红布条。\n\n"
    "青砖缝里钻出半指高的野草，被来往的靴底踩得伏贴。墙角堆着没启封的酒坛，"
    "泥封上的朱印早已辨不出字样。梁上悬着的灯笼骨架空空荡荡，糊纸只剩一角。\n\n"
    "院里的老槐树掉了满地碎叶，扫帚斜倚在门边，没人去动。井台的辘轳绳换了新麻，"
    "打上来的水带着土腥气。灶间的烟囱歪了半边，砖缝里塞着枯黄的茅草。\n\n"
    "门槛被磨出一道浅浅的凹痕，漆皮翻卷。窗纸上有指头戳出的旧洞，糊了又破。"
    "檐角的瓦当缺了两枚，雨水顺着断口滴出一线深色的痕，一路蜿蜒到阶下。\n\n"
)


# ═══════════════ 0. 制度锁：hard_gate 清单零变动（北极星纪律）═══════════════

def test_hard_gate_membership_unchanged():
    """本 fixture 套件只验证既有检出力：既有 hard 码仍 hard，advisory 码绝不升格。"""
    hg = audit_hub.HARD_GATE_CODES
    # 既有 hard_gate（对齐 STRUCTURE.md §11 / CLAUDE.md 19 码清单）
    assert "LOCKED_FACT_CROSS_SCENE_CONFLICT" in hg
    assert "FUTURE_KNOWLEDGE_LEAK" in hg
    # 本套件涉及的 advisory 码一律不得进 hard_gate（各 scanner docstring 明文承诺）
    for code in ("CHARACTER_IDENTITY_ANCHOR_DRIFT",
                 "FOCALIZER_PERCEPTION_OUT_OF_BOUNDS",
                 "TEMPORAL_GROUNDING_THIN",
                 "ANACHRONY_ORDER_THIN",
                 "POV_HEAD_HOPPING"):
        assert code not in hg, f"{code} 误入 HARD_GATE_CODES"
        assert audit_hub._gate_level_for(code, "error") == "advisory", code


# ═══════════════ 1. entity 实体一致性：同一角色属性跨场景冲突 ═══════════════
# 检测器：character_identity_anchor_scanner（identity_anchors 稳定锚 · active 模式）

_ENTITY_CARD = [{"name": "顾长风", "role": "主角",
                 "identity_anchors": {"hair_color": "黑发"}}]


def test_entity_identity_anchor_conflict_detected():
    """人物卡锚定「黑发」，第二场景写成「银发」→ CHARACTER_IDENTITY_ANCHOR_DRIFT 检出。"""
    proj = _mk_project(characters=_ENTITY_CARD)
    draft = _write_draft(proj, (
        "顾长风束着一头黑发立在阶前，玄色大氅被风掀起一角。\n\n\n"
        "顾长风抬手拢了拢鬓边的银发，转身走进大殿深处。\n\n" + _FILLER
    ))
    with _env(CHARACTER_IDENTITY_ANCHOR_MODE="active"):
        r = cia.scan(draft, proj)
    assert r["mode"] == "active", r
    assert r["drift_count"] >= 1, r
    assert r["verdict"] == "FAIL_MINOR", r
    v = r["violations"][0]
    assert v["code"] == "CHARACTER_IDENTITY_ANCHOR_DRIFT", v
    assert v["character"] == "顾长风", v
    assert v["anchor_type"] == "hair_color", v
    assert v["observed"] == "银发", v
    assert v["gate_level"] == "advisory", v  # 北极星⑤：外貌漂移是 advisory 非法官


def test_entity_identity_anchor_consistent_pass():
    """负例：全程黑发一致 → 0 drift（fixture 真能红绿翻转，非恒真）。"""
    proj = _mk_project(characters=_ENTITY_CARD)
    draft = _write_draft(proj, (
        "顾长风束着一头黑发立在阶前。\n\n\n"
        "顾长风的黑发被雨水打湿，贴在颈侧。\n\n" + _FILLER
    ))
    with _env(CHARACTER_IDENTITY_ANCHOR_MODE="active"):
        r = cia.scan(draft, proj)
    assert r.get("drift_count", 0) == 0, r
    assert r["verdict"] == "PASS", r
    assert r["violations"] == [], r


# ═══════════════ 2. temporal 时间一致性：时间锚冲突（恒定单位「天」）═══════════════
# 检测器：locked_fact_cross_scene_scanner + 项目 opt-in invariant_units=["天"]

def test_temporal_anchor_conflict_cross_scene_detected():
    """锁定事实「被困第五天」，跨场景正文写「第九天」→ LOCKED_FACT_CROSS_SCENE_CONFLICT(hard)。"""
    proj = _mk_project(
        characters=[{"name": "阿禾",
                     "locked_facts": [{"fact": "阿禾被困第五天"}]}],
        units=["天"])
    draft = _write_draft(proj, (
        "阿禾被困在山谷里，这是第五天，干粮只剩半块。\n\n\n"
        "阿禾在谷中已经熬到第九天，溪水也开始发苦。\n"
    ))
    r = lf.scan(proj, draft)
    assert r["conflicts_count"] == 1, r
    assert r["code"] == "LOCKED_FACT_CROSS_SCENE_CONFLICT", r
    assert r["gate_level"] == "hard_gate", r
    c = r["conflicts"][0]
    assert c["character"] == "阿禾", c
    assert c["unit"] == "天", c
    assert "九" in c["conflict_value"], c


def test_temporal_anchor_consistent_no_report():
    """负例：两场景都写第五天 → 0 冲突。"""
    proj = _mk_project(
        characters=[{"name": "阿禾",
                     "locked_facts": [{"fact": "阿禾被困第五天"}]}],
        units=["天"])
    draft = _write_draft(proj, (
        "阿禾被困在山谷里，这是第五天。\n\n\n"
        "阿禾数着石壁上的刻痕，还是第五天，救兵没来。\n"
    ))
    r = lf.scan(proj, draft)
    assert r["conflicts_count"] == 0, r


def test_temporal_order_reversal_blindspot():
    """盲区：草稿**内部**时序倒错（scene1=第九天、scene2=第五天，人物卡无数值锚）——
    locked_fact_cross_scene 只做「锁定事实 vs 正文」对账，草稿内部两处时间锚互相打架
    且无锁定事实基准时，确定性层无人捞。语义时序推理已明文交 LLM 判官（scanner docstring）。"""
    proj = _mk_project(
        characters=[{"name": "阿禾",
                     "locked_facts": [{"fact": "阿禾是猎户之女"}]}],  # 无数值 → 数值通路不点火
        units=["天"])
    draft = _write_draft(proj, (
        "阿禾在谷中已经熬到第九天，溪水开始发苦。\n\n\n"
        "阿禾被困在山谷里，这才是第五天，干粮还剩半块。\n"
    ))
    r = lf.scan(proj, draft)
    assert r["conflicts_count"] == 0, ("盲区已被补上？请把本测试升级成真阳性断言", r)
    pytest.skip("现有检测器无此类覆盖: 草稿内部时序倒错（无锁定事实数值锚）确定性层 0 检出；"
                "locked_fact_cross_scene 仅做 fact↔正文恒定数值对账，跨场景时间推算/顺序矛盾"
                "按其 docstring 明文交 LLM 判官（timeline_causality_consistency），"
                "anachrony_order/temporal_grounding 只测锚词密度非矛盾")


# ═══════════════ 3. causality 事件因果：果先于因（未来知识泄露）═══════════════
# 检测器：validate_chapter.check_knowledge_leak → FUTURE_KNOWLEDGE_LEAK（hard_gate 码）

_LEAK_FACT = "地宫钥匙藏在祠堂神像底座下"
_LEAK_CHARS = [{"name": "沈青梧", "role": "主角",
                "knowledge": {"will_learn": [
                    {"fact": _LEAK_FACT, "learn_at_cluster": "cluster_002"}]}}]
_LEAK_CLUSTERS = [{"cluster_id": "cluster_001", "chapter_range": [1, 4]},
                  {"cluster_id": "cluster_002", "chapter_range": [5, 8]}]
_LEAK_MANIFEST = {"active_characters": ["沈青梧"]}


def test_causality_future_knowledge_leak_detected():
    """角色第 5 章（cluster_002）才该学到的事实，第 2 章正文就说出口 →
    FUTURE_KNOWLEDGE_LEAK（果先于因：知识出现在获知事件之前）。"""
    proj = _mk_project(characters=_LEAK_CHARS, clusters=_LEAK_CLUSTERS)
    body = ("沈青梧压低声音：地宫钥匙藏在祠堂神像底座下，谁也别声张。\n\n"
            "烛火晃了一下，供桌上的灰被夜风扫出一道浅痕。\n")
    errs = vc.check_knowledge_leak(body, proj, _LEAK_MANIFEST, chapter=2)
    leaks = [e for e in errs if e["code"] == "FUTURE_KNOWLEDGE_LEAK"]
    assert len(leaks) == 1, errs
    assert "沈青梧" in leaks[0]["msg"], leaks[0]
    assert "cluster_002" in leaks[0]["msg"], leaks[0]
    assert leaks[0]["severity"] == "error", leaks[0]


def test_causality_no_leak_after_learning_chapter():
    """负例：同一段正文放在第 5 章（已进入学习 cluster）→ 合法知晓，0 检出。"""
    proj = _mk_project(characters=_LEAK_CHARS, clusters=_LEAK_CLUSTERS)
    body = "沈青梧压低声音：地宫钥匙藏在祠堂神像底座下，谁也别声张。\n"
    errs = vc.check_knowledge_leak(body, proj, _LEAK_MANIFEST, chapter=5)
    assert [e for e in errs if e["code"] == "FUTURE_KNOWLEDGE_LEAK"] == [], errs


def test_causality_no_leak_when_fact_not_spoken():
    """负例：正文提到角色但没泄露事实内容 → 0 检出（bigram 命中不达阈）。"""
    proj = _mk_project(characters=_LEAK_CHARS, clusters=_LEAK_CLUSTERS)
    body = "沈青梧在祠堂外站了半个时辰，始终没有进门。\n"
    errs = vc.check_knowledge_leak(body, proj, _LEAK_MANIFEST, chapter=2)
    assert [e for e in errs if e["code"] == "FUTURE_KNOWLEDGE_LEAK"] == [], errs


# ═══════════════ 4. location 地点连续性 ═══════════════
# 最近覆盖：focalizer_perception_bounds_scanner 规则③「空间不在场」标志词子集（active 模式）
# —— 检测「与此同时/同一时刻 + 远方地点」的聚焦人不在场空间分裂（marker-based 确定性子集）

def test_location_spatial_absence_markers_detected():
    """两处「与此同时，千里之外 / 同一时刻，远处」空间分裂标志 →
    FOCALIZER_PERCEPTION_OUT_OF_BOUNDS 检出（空间不在场 ≥2 处地板）。"""
    text = (
        "顾长风推开殿门，脚下的青砖裂着细纹，香炉里的灰早就冷透了。\n\n"
        "与此同时，千里之外的皇城灯火通明，禁军沿着宫墙一队队巡过。\n\n"
        + _FILLER +
        "同一时刻，远处的烽火台燃起了第三堆狼烟，守卒的喊声被风撕成碎片。\n\n"
        + _FILLER
    )
    assert fpb._cjk_count(text) >= 500, "fixture 底料不足 MIN_CJK，会被『太短跳过』假绿"
    proj = _mk_project(characters=[{"name": "顾长风", "role": "主角"}])
    draft = _write_draft(proj, text)
    with _env(FOCALIZER_PERCEPTION_BOUNDS_MODE="active"):
        r = fpb.scan(draft, proj)
    assert r["spatial_absence_count"] >= 2, r
    assert r["verdict"] == "FAIL_MINOR", r
    assert r["violations"], r
    assert r["violations"][0]["kind"] == "focalizer_perception_out_of_bounds", r
    assert "空间不在场" in r["warning"], r
    assert r["gate_level"] == "advisory", r  # 北极星⑤：多线叙事可豁免


def test_location_spatial_markers_absent_pass():
    """负例：同样长度、无空间分裂标志 → 0 检出。"""
    text = "顾长风推开殿门，脚下的青砖裂着细纹。\n\n" + _FILLER + _FILLER
    assert fpb._cjk_count(text) >= 500
    proj = _mk_project(characters=[{"name": "顾长风", "role": "主角"}])
    draft = _write_draft(proj, text)
    with _env(FOCALIZER_PERCEPTION_BOUNDS_MODE="active"):
        r = fpb.scan(draft, proj)
    assert r["spatial_absence_count"] == 0, r
    assert r["verdict"] == "PASS", r


def test_location_teleport_blindspot():
    """盲区：同场景内**无标志词**的位置瞬移矛盾（地窖深处 → 下一段已在北境城墙，零过渡）——
    focalizer 规则③只认「与此同时/同一时刻/此刻+远方」语言标志；locked_fact 只对恒定数值；
    pov_consistency 只看 POV 信号归属。三族对纯空间瞬移矛盾均 0 检出。"""
    text = (
        "顾长风盘膝坐在地窖最深处，四壁是湿冷的岩石，头顶只有一线天光。\n\n"
        "下一句话还没说完，顾长风已经站在北境城墙的垛口上，朔风灌进衣领。\n\n"
        + _FILLER + _FILLER
    )
    assert fpb._cjk_count(text) >= 500
    proj = _mk_project(
        characters=[{"name": "顾长风", "role": "主角",
                     "locked_facts": [{"fact": "顾长风身在地窖"}]}])
    draft = _write_draft(proj, text)
    with _env(FOCALIZER_PERCEPTION_BOUNDS_MODE="active"):
        r_f = fpb.scan(draft, proj)
    r_lf = lf.scan(proj, draft)
    assert r_f["spatial_absence_count"] == 0, ("盲区已被补上？请升级成真阳性断言", r_f)
    assert r_f["verdict"] == "PASS", r_f
    assert r_lf["conflicts_count"] == 0, ("盲区已被补上？请升级成真阳性断言", r_lf)
    pytest.skip("现有检测器无此类覆盖: 同场景内无标志词的位置瞬移矛盾（地窖→城墙零过渡）"
                "focalizer/locked_fact/pov 三族均 0 检出；需要空间状态跟踪（角色↔地点绑定"
                "跨句对账），确定性层现无此通路，语义空间连续性留 LLM 判官")


# ═══════════════ 5. contradiction 未解决矛盾：明文互斥陈述 ═══════════════

def test_contradiction_descriptive_mutex_blindspot():
    """盲区：描述类明文互斥（锁定事实「满门尽灭只剩一人」 vs 正文「兄长推门而入」）——
    locked_fact_cross_scene_scanner docstring 承诺『描述类（外貌/出身）矛盾陈述』，
    但 scan() 代码只实现了恒定数值通路，描述类互斥从未落地 → 0 检出。
    （外貌子集由 character_identity_anchor 另行覆盖，见 entity 类；生死/亲缘/身份类互斥
    需要语义蕴含判断，确定性层无覆盖。）"""
    proj = _mk_project(
        characters=[{"name": "沈昭",
                     "locked_facts": [{"fact": "沈家满门尽灭，只剩沈昭一人"}]}])
    draft = _write_draft(proj, (
        "沈昭正在灵前烧纸，火光映着一张没有表情的脸。\n\n\n"
        "沈昭的兄长沈铖推门而入，掸了掸肩上的雪：家里一切安好。\n"
    ))
    r = lf.scan(proj, draft)
    assert r["facts_checked"] == 1, r  # fact 确实被检了——不是没跑，是通路不存在
    assert r["conflicts_count"] == 0, ("盲区已被补上？请升级成真阳性断言", r)
    pytest.skip("现有检测器无此类覆盖: 描述类明文互斥陈述（生死/亲缘/身份）0 检出；"
                "locked_fact_cross_scene 仅有恒定数值对账通路（docstring 的『描述类』"
                "承诺未落地），互斥语义蕴含（只剩一人 ⊥ 兄长在世）需 NLI/LLM 判官")
