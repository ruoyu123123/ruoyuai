# -*- coding: utf-8 -*-
"""test_telemetry_producers.py — SYS-5 三遥测断点 producer 补全单测（2026-06-27）

确定性·零依赖·零 LLM/零联网。三独立 advisory/shadow 遥测断点的 producer 修复：
  ① cliffhanger_resonance_next：cluster_summary_builder 确定性预算（原恒缺 → 账本无字段
     → continuity scanner 回退恒 -1）。修：builder 第二遍算前章 ending ∩ 下一章 head 重叠，
     复用 continuity_keywords 单一来源（两边分可比），mega-token 补 2-gram 恢复可信度。
  ② narrative_debt total_planted=0：cluster 摘要无 foreshadow 流水 → 误报
     DEBT_BOOK_MORTGAGE_ABSENT。修：回退伏笔表.json 按 setup_cluster 归集 planted/paid。
  ③ throughline_progress 全 DORMANT：gen_writer 不让 writer 自报 → 账本恒空。修：
     CHANGES 自查项增可选 throughline_progress 字段（OS/MC/IC/RS）。

全程 advisory/shadow：永不 exit 非 0 阻断、永不升 hard_gate（见各 CLI/读路径断言）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import chapter_io as cio  # noqa: E402
import cluster_lookup as cl  # noqa: E402
import cluster_summary_builder as csb  # noqa: E402
import continuity_keywords as ckw  # noqa: E402
import cross_cluster_continuity_aggregate as cont  # noqa: E402
import cross_cluster_narrative_debt_ledger_aggregate as debt  # noqa: E402

_DEBT_TARGET = _SCRIPTS / "cross_cluster_narrative_debt_ledger_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


# ════════════════════════════════════════════════════════════════════
# ① cliffhanger_resonance_next — 共享关键词单一来源 + 确定性预算
# ════════════════════════════════════════════════════════════════════

def test_extract_keywords_single_source_shared_by_both_sides():
    """producer（builder）与 consumer（continuity scanner）必须用同一个 extract_keywords
    对象——否则两边 cliffhanger 分不可比（SYS-5 ① 核心不变量）。"""
    assert ckw.extract_keywords is cont.extract_keywords
    assert csb._cliff_keywords is ckw.extract_keywords


def test_extract_keywords_bigram_recovers_punctuation_sparse_overlap():
    """无内部标点的长中文句被正则吞成 mega-token → 逐字几乎不可能匹配。补 2-gram 后
    实质词可跨文本命中（恢复遥测可信度·回归锁 mega-token 修复）。"""
    ending = "会议彻底陷入了僵局"          # 无标点 → 旧版本仅 1 个 9 字 mega-token
    head = "新的会议又开始了，气氛依旧僵局。"
    ek = ckw.extract_keywords(ending)
    hk = ckw.extract_keywords(head)
    # mega-token 仍在，但 2-gram「会议」「僵局」也在 → 与 head 有真重叠
    assert "会议" in ek and "僵局" in ek
    assert ek & hk, "bigram 切分后应能与含相同实质词的文本重叠（非恒 0）"


def test_extract_keywords_stopword_and_protagonist_filter_preserved():
    """bigram 增强不破坏 stop / 主角名过滤（含 bigram 也过 stop）。"""
    kw = ckw.extract_keywords("他的房子很大", protagonist=None)
    assert "他的" not in kw          # stop 词（含作为 bigram 出现）被过滤
    kw2 = ckw.extract_keywords("重黎 建木", protagonist="重黎")
    assert "重黎" not in kw2 and "建木" in kw2


def test_compute_cliffhanger_resonance_real_overlap_and_boundary():
    """前章 ending 在下一章 head 出现 → score>0；本 cluster 末章无下一章 → -1（no-signal）。"""
    chapters = {
        "1": {"ending_line": "他握紧了那柄断剑，转身离去"},
        "2": {"ending_line": "夜色降临，万籁俱寂"},
    }
    bodies = {
        1: "占位正文。",
        2: "那柄断剑还插在地上，断剑的寒光刺眼。" + "无关内容。" * 40,
    }
    csb._compute_cliffhanger_resonance(chapters, bodies, lo=1, hi=2, protagonist=None)
    assert chapters["1"]["cliffhanger_resonance_next"] > 0      # 「断剑」跨章命中
    assert chapters["2"]["cliffhanger_resonance_next"] == -1.0  # 末章 no-signal skip（非 0% 误报）


def test_compute_cliffhanger_resonance_no_overlap_is_zero_not_minus_one():
    """有 ending 关键词但下一章 head 完全无关 → 0.0（真算出的低分），区别于 -1（无信号）。"""
    chapters = {"1": {"ending_line": "苍鹰掠过雪峰"}, "2": {"ending_line": "x"}}
    bodies = {1: "占位。", 2: "完全无关的市集喧闹与买卖。" * 30}
    csb._compute_cliffhanger_resonance(chapters, bodies, lo=1, hi=2, protagonist=None)
    assert chapters["1"]["cliffhanger_resonance_next"] == 0.0


def test_compute_cliffhanger_resonance_empty_ending_is_minus_one():
    """ending_line 为空/无关键词 → -1（不可算 → no-signal，非 0%）。"""
    chapters = {"1": {"ending_line": ""}, "2": {}}
    bodies = {1: "占位。", 2: "正文内容。" * 30}
    csb._compute_cliffhanger_resonance(chapters, bodies, lo=1, hi=2, protagonist=None)
    assert chapters["1"]["cliffhanger_resonance_next"] == -1.0


def test_builder_writes_cliffhanger_field_end_to_end():
    """端到端：build_cluster_summary 后账本每章（除末章）含 cliffhanger_resonance_next，
    且不全是 -1（断点修复证据）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "title": "T", "chapter_range": [1, 3]}]
        }, ensure_ascii=False), encoding="utf-8")
        # ch1 结尾的实质词在 ch2 开头复现 → 可算出 >0
        cio.write_body(root, 1, ("青云城外风沙弥漫。\n" * 25) + "他盯着那枚黑色令牌，久久不语")
        cio.write_body(root, 2, "黑色令牌的纹路在月下泛光，令牌的来历成谜。" + ("城中喧嚣。\n" * 25))
        cio.write_body(root, 3, "翌日清晨，故事继续。\n" * 26)
        for ch in range(1, 4):
            cio.write_changes(root, ch, {"factual": {}, "self_eval": {}})
        res = csb.build_cluster_summary(root, "cluster_001")
        assert res["ok"] is True
        from cluster_summary_reader import load_summary
        rec = next(c for c in load_summary(root)["clusters"]
                   if cl.normalize_cluster_id(c["cluster_id"]) == "cluster_001")
        chs = rec["chapters"]
        assert "cliffhanger_resonance_next" in chs["1"]
        assert chs["3"]["cliffhanger_resonance_next"] == -1.0   # 末章
        scores = [chs[str(c)]["cliffhanger_resonance_next"] for c in (1, 2, 3)]
        assert not all(s == -1 for s in scores), "断点修复：cliffhanger 不应恒 -1"


# ════════════════════════════════════════════════════════════════════
# ② narrative_debt — 伏笔表.json 回退（消除 total_planted 恒 0 误报）
# ════════════════════════════════════════════════════════════════════

def _write_foreshadow_table(db: Path, promises):
    (db / "伏笔表.json").write_text(json.dumps({
        "schema_version": "v27", "promises": promises,
        "deadlines": [], "pledges": [], "secrets": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_load_foreshadow_table_fallback_groups_by_setup_cluster():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        _write_foreshadow_table(db, [
            {"id": "fs_001", "setup_cluster": "cluster_001", "resolved": True},
            {"id": "fs_002", "setup_cluster": "cluster_001", "resolved": False},
            {"id": "fs_003", "setup_cluster": "2", "resolved": False},  # 归一 → cluster_002
        ])
        idx = debt._load_foreshadow_table_fallback(root)
        assert idx["cluster_001"]["planted"] == {"fs_001", "fs_002"}
        assert idx["cluster_001"]["paid"] == {"fs_001"}        # 仅 resolved
        assert "cluster_002" in idx and idx["cluster_002"]["planted"] == {"fs_003"}


def test_cluster_planted_paid_uses_fallback_when_summary_empty():
    fb = {"cluster_001": {"planted": {"fs_001", "fs_002"}, "paid": {"fs_001"}}}
    c = {"cluster_id": "cluster_001"}  # 摘要无 foreshadow 流水
    planted, paid = debt._cluster_planted_paid(c, fb)
    assert planted == {"fs_001", "fs_002"} and paid == {"fs_001"}


def test_cluster_planted_paid_native_takes_precedence_over_fallback():
    fb = {"cluster_001": {"planted": {"X"}, "paid": {"X"}}}
    c = {"cluster_id": "cluster_001", "foreshadow_planted": ["native_1"]}
    planted, paid = debt._cluster_planted_paid(c, fb)
    assert planted == {"native_1"} and "X" not in planted  # 有原生流水 → 不回退


def test_book_ledger_fallback_eliminates_total_planted_zero():
    clusters = [
        {"cluster_id": "cluster_001", "chapters": {"1": {}}},
        {"cluster_id": "cluster_002", "chapters": {"5": {}}},
    ]
    fb = {
        "cluster_001": {"planted": {"a", "b"}, "paid": {"a"}},
        "cluster_002": {"planted": {"c"}, "paid": set()},
    }
    book = debt.compute_book_ledger(clusters, fb)
    assert book["total_planted"] == 3   # 旧版本读摘要恒 0
    assert book["total_paid"] == 1


def test_cli_fallback_clears_book_mortgage_false_positive():
    """端到端 CLI：5 个无 foreshadow 流水的 cluster + 伏笔表（cluster_001 即有 promise）
    → total_planted>0 且不再误报 DEBT_BOOK_MORTGAGE_ABSENT。shadow 模式恒 exit 0（不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        clusters = [{"cluster_id": f"cluster_{i:03d}", "title": f"c{i}",
                     "chapter_range": [i, i], "cluster_end_ch": i, "status": "done",
                     "chapters": {str(i): {}}} for i in range(1, 6)]
        (db / "故事块摘要.json").write_text(json.dumps(
            {"schema_version": "v2.cluster", "clusters": clusters},
            ensure_ascii=False), encoding="utf-8")
        _write_foreshadow_table(db, [
            {"id": "fs_001", "setup_cluster": "cluster_001", "resolved": True},
            {"id": "fs_002", "setup_cluster": "cluster_001", "resolved": False},
            {"id": "fs_003", "setup_cluster": "cluster_003", "resolved": False},
        ])
        env = _utf8_env(NARRATIVE_DEBT_MODE="shadow", CLUSTER_MODE="1")
        r = subprocess.run([sys.executable, str(_DEBT_TARGET), str(root)],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        assert r.returncode == 0, r.stderr            # shadow 永不阻断
        assert "Traceback" not in (r.stderr or "")
        snap = json.loads((db / ".cross_chapter_scan" / "narrative_debt_snapshot.json")
                          .read_text(encoding="utf-8"))
        assert snap["book"]["total_planted"] >= 3     # 读伏笔表（非恒 0）
        assert debt.CODE_BOOK_MORTGAGE not in snap["advisory_codes"]


def test_cli_no_fallback_when_table_absent_no_crash():
    """无伏笔表.json → 不回退、不崩（fb_used=False，total_planted=0 仍可能报 mortgage，但 shadow 不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        clusters = [{"cluster_id": f"cluster_{i:03d}", "chapter_range": [i, i],
                     "cluster_end_ch": i, "status": "done", "chapters": {str(i): {}}}
                    for i in range(1, 4)]
        (db / "故事块摘要.json").write_text(json.dumps(
            {"schema_version": "v2.cluster", "clusters": clusters},
            ensure_ascii=False), encoding="utf-8")
        env = _utf8_env(NARRATIVE_DEBT_MODE="shadow", CLUSTER_MODE="1")
        r = subprocess.run([sys.executable, str(_DEBT_TARGET), str(root)],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        assert r.returncode == 0
        assert "Traceback" not in (r.stderr or "")


# ════════════════════════════════════════════════════════════════════
# ③ throughline_progress — gen_writer 自报字段（producer 补 + 消费路径锁）
# ════════════════════════════════════════════════════════════════════

def test_gen_writer_prompt_instructs_throughline_self_report():
    """gen_writer 自查项必须含 throughline_progress 自报指令（OS/MC/IC/RS 与
    throughline_balance 消费 key 对齐），否则 writer 永不产 → 账本恒空 → 全 DORMANT。"""
    src = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "throughline_progress" in src
    for key in ("OS", "MC", "IC", "RS"):
        assert f'"{key}"' in src or key in src


def test_gen_writer_prompt_instructs_ending_self_report():
    """ending_type/ending_line 自报（cliffhanger 衔接遥测 · advisory 不强制）。"""
    src = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "ending_type" in src and "ending_line" in src


def test_throughline_progress_flows_builder_to_aggregator():
    """端到端锁消费路径：changes.factual.throughline_progress → builder 账本 →
    throughline_balance distribution 非全 0（DORMANT 解除证据）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "title": "T", "chapter_range": [1, 2]}]
        }, ensure_ascii=False), encoding="utf-8")
        for ch in (1, 2):
            cio.write_body(root, ch, "正文内容若干。\n" * 30)
            cio.write_changes(root, ch, {
                "factual": {"throughline_progress": {"OS": True, "MC": True, "IC": False, "RS": False}},
                "self_eval": {},
            })
        csb.build_cluster_summary(root, "cluster_001")
        from cluster_summary_reader import load_summary
        rec = next(c for c in load_summary(root)["clusters"]
                   if cl.normalize_cluster_id(c["cluster_id"]) == "cluster_001")
        # 账本逐章保留 throughline_progress
        assert rec["chapters"]["1"]["throughline_progress"]["OS"] is True
        # 运行 throughline_balance（cluster 模式）→ distribution 非全 0
        target = _SCRIPTS / "cross_cluster_throughline_balance_aggregate.py"
        env = _utf8_env(CLUSTER_MODE="1", CLUSTER_ID="cluster_001")
        r = subprocess.run([sys.executable, str(target), str(root), "--last-n", "5"],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        assert "Traceback" not in (r.stderr or "")
        report = sorted((db / ".cross_chapter_scan").glob("throughline_balance_*.json"))[-1]
        dist = json.loads(report.read_text(encoding="utf-8"))["distribution"]
        assert dist["OS"] > 0 and dist["MC"] > 0, "throughline 不应全 DORMANT"
