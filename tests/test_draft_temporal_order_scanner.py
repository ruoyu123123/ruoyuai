# -*- coding: utf-8 -*-
"""draft_temporal_order_scanner 单测 — 草稿内部时序倒错盲区补齐（2026-07-07）

盲区出处：tests/test_constory_consistency_gold.py::test_temporal_order_reversal_blindspot
（ConStory temporal 类·scene1=第九天、scene2=第五天 互相打架·确定性层此前 0 检出）。
本套件用同款金 fixture 句子验证新 scanner 真能捞到，并锁死六重防误报豁免
（narrative_mode 门控 / 闪回豁免 / 锚点稀疏不判 + 时段序仅同日 / 引语掩蔽 /
时段复合词+时长量词 / 时段链场景跨度上限）。

2026-07-07 金标准校准放量：10 作者 × 10 chunk（连续4章·均匀取样·linear 声明）
首轮 6/100 误报 → 加豁免④⑤⑥ → 复跑 0/100 零误报 → 默认 shadow→active。

北极星⑤纪律：DRAFT_TEMPORAL_ORDER_REVERSED 永远 advisory（时序自由是叙事手法，
scanner 只捞无标记的意外倒错）——本套件不断言 audit_hub 接线（由主代理统一做）。

跑：py -m pytest tests/test_draft_temporal_order_scanner.py -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import draft_temporal_order_scanner as dto  # noqa: E402

# ── ConStory 金 fixture 同款句子（test_constory_consistency_gold.py 盲区原文）──
_S_DAY9 = "阿禾在谷中已经熬到第九天，溪水开始发苦。"
_S_DAY5 = "阿禾被困在山谷里，这才是第五天，干粮还剩半块。"
# 补第三个锚点场景（过锚点稀疏地板·不参与倒错对）
_S_DAY3 = "阿禾入谷的第三天，她在崖壁下搭起了窝棚。"
_S_DAY10 = "第十天，谷口传来了铃铛声，阿禾攥紧了石刀。"
_S_FLASHBACK_DAY5 = "阿禾想起被困第五天的时候，干粮还剩半块，她没敢生火。"

# 时段序 fixture（刻意不含任何天数锚/闪回标志词）
_S_MORNING = "清晨，阿禾沿着溪边捡回一捆枯枝。"
_S_DUSK = "黄昏，她把窝棚的缝隙塞满了干草。"
_S_FORENOON = "上午，谷口的雾还没散，她数着剩下的干粮。"
_S_NEXTDAY_FORENOON = "次日上午，谷口的雾还没散，她数着剩下的干粮。"


def _write_draft(tmp_path: Path, *scenes: str, name: str = "draft.txt") -> Path:
    p = tmp_path / name
    p.write_text("\n\n".join(scenes) + "\n", encoding="utf-8")
    return p


def _mk_project(tmp_path: Path, clusters) -> Path:
    db = tmp_path / "proj" / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "proj"


# ═══════════ 1. 正例：ConStory 金 fixture 同款倒错真检出（active）═══════════

def test_gold_constory_day_reversal_detected_active(tmp_path, monkeypatch):
    """第九天场景后接第五天场景、无闪回标志 → DRAFT_TEMPORAL_ORDER_REVERSED 检出。
    scene1/scene2 = ConStory 盲区 fixture 原句（金标准）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft)
    assert r["mode"] == "active", r
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 1, r
    assert r["verdict"] == "FAIL_MINOR", r
    v = r["violations"][0]
    assert v["code"] == "DRAFT_TEMPORAL_ORDER_REVERSED", v
    assert v["kind"] == "absolute_day_reversal", v
    assert v["scene_index_pair"] == [1, 2], v
    assert v["anchor_pair"] == ["第九天", "第五天"], v
    assert "无闪回标志" in v["reason"], v
    assert v["gate_level"] == "advisory", v  # 北极星⑤：时序自由是叙事手法·永不 hard_gate
    assert r["gate_level"] == "advisory", r


# ═══════════ 2. 负例：顺序正常 0 检出（fixture 真能红绿翻转，非恒真）═══════════

def test_ordered_days_no_violation(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY5, _S_DAY9)
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r


# ═══════════ 3. 豁免①：narrative_mode 门控 ═══════════

def test_in_medias_res_cluster_skips_entirely(tmp_path, monkeypatch):
    """事件簇.json 声明 in_medias_res（黄金三章倒叙）→ 整体 skip，倒错文本也不报。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    proj = _mk_project(tmp_path, [
        {"cluster_id": "cluster_001", "narrative_mode": "in_medias_res"}])
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft, proj, "cluster_001")
    assert r["narrative_mode"] == "in_medias_res", r
    assert "skipped" in r.get("note", ""), r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r
    assert "reversal_count" not in r, r  # 门控在检测前返回，压根不排序


def test_cluster_001_undeclared_defaults_in_medias_res_skip(tmp_path, monkeypatch):
    """clusters[0] 未声明 narrative_mode → 系统默认 in_medias_res（对齐 build_manifest
    口径）→ skip。cluster id 从 draft 路径 cluster_001_draft 推导（无 --cluster）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    proj = _mk_project(tmp_path, [{"cluster_id": "cluster_001"},
                                  {"cluster_id": "cluster_002"}])
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5,
                         name="cluster_001_draft.txt")
    r = dto.scan(draft, proj)
    assert r["cluster_id"] == "cluster_001", r
    assert r["narrative_mode"] == "in_medias_res", r
    assert r["violations"] == [], r


def test_linear_cluster_scans_normally(tmp_path, monkeypatch):
    """cluster_002（默认 linear）→ 正常扫描，倒错照报（门控不误伤顺叙块）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    proj = _mk_project(tmp_path, [{"cluster_id": "cluster_001"},
                                  {"cluster_id": "cluster_002"}])
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft, proj, "cluster_002")
    assert r["narrative_mode"] == "linear", r
    assert r["reversal_count"] == 1, r
    assert r["verdict"] == "FAIL_MINOR", r


# ═══════════ 4. 豁免②：闪回场景整体不参与排序 ═══════════

def test_flashback_scene_exempt(tmp_path, monkeypatch):
    """第九天后出现「想起…第五天」闪回场景 → 该场景退出排序，0 检出；
    时间线对闪回透明（第十天接第九天不受影响）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_FLASHBACK_DAY5, _S_DAY10)
    r = dto.scan(draft)
    assert r["flashback_scene_count"] == 1, r
    assert r["anchored_scene_count"] == 3, r  # 闪回场景不计锚点场景
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r


# ═══════════ 5. 豁免③a：锚点稀疏不判（<3 锚点场景）═══════════

def test_sparse_anchors_not_judged(tmp_path, monkeypatch):
    """恰好是 ConStory 盲区 fixture 的两场景原文：只有 2 个锚点场景 →
    孤锚对可能是合法省叙/换线，不判（note 记录）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_DAY9, _S_DAY5)
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 2, r
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r
    assert "not judged" in r.get("note", ""), r
    assert r["verdict"] == "PASS", r


# ═══════════ 6. 时段序：同日倒错检出 / 跨日介入不误判 ═══════════

def test_time_of_day_same_day_reversal_detected(tmp_path, monkeypatch):
    """清晨→黄昏→上午（全程无跨日锚）→ 同日链内时段回退 = 倒错。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_MORNING, _S_DUSK, _S_FORENOON)
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 1, r
    v = r["violations"][0]
    assert v["code"] == "DRAFT_TEMPORAL_ORDER_REVERSED", v
    assert v["kind"] == "time_of_day_reversal", v
    assert v["scene_index_pair"] == [1, 2], v
    assert v["anchor_pair"] == ["黄昏", "上午"], v
    assert v["gate_level"] == "advisory", v


def test_cross_day_anchor_intervening_no_false_positive(tmp_path, monkeypatch):
    """清晨→黄昏→「次日」上午：跨日锚介入 → 时段链重置，不误判。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(tmp_path, _S_MORNING, _S_DUSK, _S_NEXTDAY_FORENOON)
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r


def test_implicit_midnight_rollover_no_false_positive(tmp_path, monkeypatch):
    """深夜→清晨 = 隐式跨日翻页（网文常漏写「翌日」）→ 重置链不判（防误报守卫）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(
        tmp_path,
        "黄昏，她把窝棚的缝隙塞满了干草。",
        "深夜，谷里起了风，她缩在窝棚最里侧。",
        "清晨，阿禾沿着溪边捡回一捆枯枝。")
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r


# ═══════════ 6b. 豁免④⑤⑥：金标准校准新增（2026-07-07·真作者误报根因回归锁）═══════════

def test_quoted_speech_time_words_exempt(tmp_path, monkeypatch):
    """豁免④：引语内时间词不进时钟——问候「晚上好」/对话内计划「第五天」
    是话语谈论的时间非叙事时钟（金标准：诡秘/人生长恨误报主根因）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(
        tmp_path,
        _S_DAY3, _S_DAY9,
        "“晚上好，到了第五天你就去谷口等我。”她把石刀塞进阿禾手里。",
        _S_DAY10)
    r = dto.scan(draft)
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r


def test_unclosed_quote_masked_to_line_end(tmp_path, monkeypatch):
    """豁免④：网文多段引语只有开引号无闭引号 → 掩到行尾（第五天不进时钟）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(
        tmp_path,
        _S_DAY3, _S_DAY9,
        "“你听我说，第五天的时候谷口就该有人来了。\n她顿了顿，没再说下去。",
        _S_DAY10)
    r = dto.scan(draft)
    assert r["reversal_count"] == 0, r


def test_tod_compound_word_exempt(tmp_path, monkeypatch):
    """豁免⑤：「黄昏隐士会」（专名）/「下午茶」（名词）后邻汉字不在白名单 →
    不算时点锚（金标准：诡秘之主 ch618/ch1390 误报根因）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    # 抽取级断言：专名/复合词直接不产锚
    assert dto._extract_tod("黄昏隐士会的人堵住了谷口。") is None
    assert dto._extract_tod("到了下午茶点的时辰，她数着剩下的干粮。") is None
    # 白名单尾字仍是合法锚（黄昏时分/深夜里）
    assert dto._extract_tod("黄昏时分，她把窝棚塞满干草。")["text"] == "黄昏"
    # 整链回归：正午场景里混入「黄昏隐士会」不该把链推到 rank4 造成后续误报
    draft = _write_draft(
        tmp_path,
        _S_MORNING,
        "正午，黄昏隐士会的人堵住了谷口。",  # 合法锚=正午(2)·专名黄昏不算
        _S_DUSK)  # 黄昏(4)：清晨→正午→黄昏 正序 0 检出
    r = dto.scan(draft)
    assert r["anchored_scene_count"] == 3, r
    assert r["reversal_count"] == 0, r


def test_tod_duration_prefix_exempt(tmp_path, monkeypatch):
    """豁免⑤：「一上午/半晌午」时长量词非时点（金标准：人生长恨 ch12 误报根因）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    draft = _write_draft(
        tmp_path,
        _S_MORNING,
        "到了中午，她才直起腰。",
        "这一顿她忙活了一上午，胳膊都抬不起来。",  # 「一上午」时长·非回到上午
        _S_DUSK)
    r = dto.scan(draft)
    assert r["reversal_count"] == 0, r


def test_tod_chain_scene_gap_reset(tmp_path, monkeypatch):
    """豁免⑥：两时段锚相隔场景数 > MAX_TOD_SCENE_GAP → 链过远重置不判
    （金标准：人生长恨相隔 29/30 场景的时段词被强行同日比较）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    fillers = [f"她数到第{'一二三四五'[i]}块石头，又把它放回了原处（场景无锚）。"
               for i in range(4)]
    draft = _write_draft(tmp_path, _S_MORNING, _S_DUSK, *fillers, _S_FORENOON)
    r = dto.scan(draft)  # 黄昏→(隔4个无锚场景)→上午：gap=5>3 重置不判
    assert r["anchored_scene_count"] >= 3, r
    assert r["reversal_count"] == 0, r
    assert r["violations"] == [], r


def test_tod_chain_gap_within_limit_still_judged(tmp_path, monkeypatch):
    """gap ≤ MAX_TOD_SCENE_GAP 的同日回退仍要检出（豁免⑥不误伤近距倒错）。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "active")
    filler = "她数着剩下的干粮，把石刀在掌心翻了个面（场景无锚）。"
    draft = _write_draft(tmp_path, _S_MORNING, _S_DUSK, filler, filler, _S_FORENOON)
    r = dto.scan(draft)  # 黄昏→(隔2个无锚场景)→上午：gap=3≤3 仍判
    assert r["reversal_count"] == 1, r
    assert r["violations"][0]["anchor_pair"] == ["黄昏", "上午"], r


# ═══════════ 6c. 金标准校准证据锁（2026-07-07 放量记载不许被清掉）═══════════

def test_calibration_evidence_recorded_in_source():
    """默认 active 的依据=金标准校准零误报。锁死源码 docstring 记载：
    改默认档/删记载都必须先重做校准。"""
    src = (_ROOT / "core" / "scripts" / "draft_temporal_order_scanner.py").read_text(
        encoding="utf-8")
    assert "金标准10作者100chunk零误报放量·2026-07-07" in src
    assert "默认 active" in src


# ═══════════ 7. 三态 env 门：默认 active 产 violations / shadow 不产 / off 不扫 ═══════════

def test_default_mode_is_active(tmp_path, monkeypatch):
    """未设 env 的默认档 = active（金标准10作者100chunk零误报放量·2026-07-07）。"""
    monkeypatch.delenv("DRAFT_TEMPORAL_ORDER_MODE", raising=False)
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft)
    assert r["mode"] == "active", r  # 默认档 = active
    assert r["reversal_count"] == 1, r
    assert r["violations"] != [], r
    assert r["verdict"] == "FAIL_MINOR", r
    assert r["gate_level"] == "advisory", r  # 放量不改性质：永远 advisory


def test_shadow_mode_no_violations_but_counts(tmp_path, monkeypatch, capsys):
    """shadow（显式设 env）：检出只进 reversal_count + stderr，不产 violations。"""
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "shadow")
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft)
    assert r["mode"] == "shadow", r
    assert r["reversal_count"] == 1, r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r
    assert r["warning"] is None, r
    assert "[SHADOW] draft_temporal_order" in capsys.readouterr().err


def test_off_mode_skips_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAFT_TEMPORAL_ORDER_MODE", "off")
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    r = dto.scan(draft)
    assert r["mode"] == "off", r
    assert r["violations"] == [], r
    assert r["verdict"] == "PASS", r
    assert "scene_count" not in r, r  # off 档提前返回，不读不切


# ═══════════ 8. CLI 契约冒烟（draft_path --project <root> [--cluster]）═══════════

def test_cli_smoke_active_exit1_json(tmp_path):
    proj = _mk_project(tmp_path, [{"cluster_id": "cluster_001"},
                                  {"cluster_id": "cluster_002"}])
    draft = _write_draft(tmp_path, _S_DAY3, _S_DAY9, _S_DAY5)
    env = dict(os.environ)
    env["DRAFT_TEMPORAL_ORDER_MODE"] = "active"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable,
         str(_ROOT / "core" / "scripts" / "draft_temporal_order_scanner.py"),
         str(draft), "--project", str(proj), "--cluster", "cluster_002"],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 1, proc.stderr  # active 检出 → exit 1（与同族 scanner 一致）
    report = json.loads(proc.stdout)
    assert report["code"] == "DRAFT_TEMPORAL_ORDER_REVERSED", report
    assert report["cluster_id"] == "cluster_002", report
    assert report["violations_count"] == 1, report
