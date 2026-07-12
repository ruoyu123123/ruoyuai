# -*- coding: utf-8 -*-
"""test_telemetry_producers.py — SYS-5 遥测断点 producer 单测（2026-06-27）

确定性·零依赖·零 LLM/零联网。两个独立 advisory/shadow 遥测断点：
  ① narrative_debt：故事块摘要.json 的 cluster 记录不带 foreshadow 流水字段（CLUSTER_FIELDS
     闭集），planted/paid 债务唯一权威来源是 伏笔表.json 按 setup_cluster 归集。
  ② throughline_progress 全 DORMANT：throughline（本块推进了哪几条
     叙事线 OS/MC/IC/RS）是叙事分析=梳理，由 novel-archivist 读正文抽取（writer 自报属
     A 类违规·已删）。archivist 产 archive.throughline_progress → apply_archive 落
     事件簇.clusters[].throughline_progress → cluster_summary_builder 注入 cluster 账本 →
     cross_cluster_throughline_balance_aggregate 消费（archive 单一来源·非 writer 自报）。

cliffhanger_resonance_next 悬念衔接遥测是 cluster 级信号：由
cross_cluster_continuity_aggregate.scan_cliffhanger_resonance 用账本
ending_type/ending_line 与下一 cluster 终稿直接计算（该模块自身单测覆盖，不在本文件）。

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

import cluster_lookup as cl  # noqa: E402
import cluster_summary_builder as csb  # noqa: E402
import cluster_summary_store as store  # noqa: E402
import continuity_keywords as ckw  # noqa: E402
import cross_cluster_narrative_debt_ledger_aggregate as debt  # noqa: E402
from cluster_summary_fixtures import cluster_record, write_cluster_summary  # noqa: E402

_DEBT_TARGET = _SCRIPTS / "cross_cluster_narrative_debt_ledger_aggregate.py"


def _utf8_env(**extra):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update(extra)
    return env


# ════════════════════════════════════════════════════════════════════
# continuity_keywords.extract_keywords — cliffhanger 关键词共享单一来源
# ════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════
# ① narrative_debt — 伏笔表.json 是 planted/paid 债务的唯一权威来源
# ════════════════════════════════════════════════════════════════════

def _write_foreshadow_table(db: Path, promises):
    (db / "伏笔表.json").write_text(json.dumps({
        "schema_version": "v27", "promises": promises,
        "deadlines": [], "pledges": [], "secrets": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_load_foreshadow_ledger_groups_by_setup_cluster():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        db.mkdir(parents=True)
        _write_foreshadow_table(db, [
            {"id": "fs_001", "setup_cluster": "cluster_001", "status": "consumed"},
            {"id": "fs_002", "setup_cluster": "cluster_001", "status": "open"},
            {"id": "fs_003", "setup_cluster": "2", "status": "suspended"},  # 归一 → cluster_002·suspended=未回收
        ])
        idx = debt._load_foreshadow_ledger(root)
        assert idx["cluster_001"]["planted"] == {"fs_001", "fs_002"}
        assert idx["cluster_001"]["paid"] == {"fs_001"}        # 仅 status==consumed（open/suspended=未回收）
        assert "cluster_002" in idx and idx["cluster_002"]["planted"] == {"fs_003"}


def test_cluster_planted_paid_reads_from_foreshadow_index():
    fb = {"cluster_001": {"planted": {"fs_001", "fs_002"}, "paid": {"fs_001"}}}
    c = {"cluster_id": "cluster_001"}
    planted, paid = debt._cluster_planted_paid(c, fb)
    assert planted == {"fs_001", "fs_002"} and paid == {"fs_001"}


def test_book_ledger_reads_foreshadow_index_total_planted():
    clusters = [
        {"cluster_id": "cluster_001"},
        {"cluster_id": "cluster_002"},
    ]
    fb = {
        "cluster_001": {"planted": {"a", "b"}, "paid": {"a"}},
        "cluster_002": {"planted": {"c"}, "paid": set()},
    }
    book = debt.compute_book_ledger(clusters, fb)
    assert book["total_planted"] == 3
    assert book["total_paid"] == 1


def test_cli_reads_foreshadow_table_clears_book_mortgage_false_positive():
    """端到端 CLI：5 个 cluster + 伏笔表（cluster_001 即有 promise）
    → total_planted>0 且不误报 DEBT_BOOK_MORTGAGE_ABSENT。shadow 模式恒 exit 0（不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        write_cluster_summary(root, [cluster_record(f"cluster_{i:03d}") for i in range(1, 6)])
        _write_foreshadow_table(db, [
            {"id": "fs_001", "setup_cluster": "cluster_001", "status": "consumed"},
            {"id": "fs_002", "setup_cluster": "cluster_001", "status": "open"},
            {"id": "fs_003", "setup_cluster": "cluster_003", "status": "open"},
        ])
        env = _utf8_env(NARRATIVE_DEBT_MODE="shadow", CLUSTER_MODE="1")
        r = subprocess.run([sys.executable, str(_DEBT_TARGET), str(root)],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        assert r.returncode == 0, r.stderr            # shadow 永不阻断
        assert "Traceback" not in (r.stderr or "")
        snap = json.loads((db / ".cross_cluster_scan" / "narrative_debt_snapshot.json")
                          .read_text(encoding="utf-8"))
        assert snap["book"]["total_planted"] >= 3     # 读伏笔表（非恒 0）
        assert debt.CODE_BOOK_MORTGAGE not in snap["advisory_codes"]


def test_cli_no_foreshadow_table_no_crash():
    """无伏笔表.json → 不崩（total_planted=0 仍可能报 mortgage，但 shadow 不阻断）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        write_cluster_summary(root, [cluster_record(f"cluster_{i:03d}") for i in range(1, 4)])
        env = _utf8_env(NARRATIVE_DEBT_MODE="shadow", CLUSTER_MODE="1")
        r = subprocess.run([sys.executable, str(_DEBT_TARGET), str(root)],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        assert r.returncode == 0
        assert "Traceback" not in (r.stderr or "")


# ════════════════════════════════════════════════════════════════════
# ② throughline_progress — archivist 读正文抽取（writer 不自报）
#    archive.throughline_progress → apply_archive → 事件簇.clusters[] → builder → aggregator
# ════════════════════════════════════════════════════════════════════

def test_archivist_extracts_throughline_not_writer():
    """🔴 2026-06-28 不降级收尾：throughline（OS/MC/IC/RS）由 novel-archivist 读正文抽取，
    **writer 不自报**（A 类违规·属"writer 报 state"）。archivist agent 定义须指示 4 线，
    gen_writer 须**不含** throughline 自报指令（架构纠正回归锁）。"""
    arch = (_ROOT / ".claude" / "agents" / "novel-archivist.md").read_text(encoding="utf-8")
    assert "throughline_progress" in arch
    for key in ("OS", "MC", "IC", "RS"):
        assert key in arch, f"archivist 定义缺 throughline 键 {key}"
    # writer 不再自报 throughline：gen_writer 里 throughline_progress 只能出现在「已删」注释行，
    # 绝不在 writer prompt 字符串/自查项里当指令（架构纠正回归锁·允许注释留痕说明它已移走）。
    gw = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    tp_lines = [ln for ln in gw.splitlines() if "throughline_progress" in ln]
    assert tp_lines, "应保留一条注释说明 throughline 已移给 archivist"
    for ln in tp_lines:
        assert ln.lstrip().startswith("#"), \
            f"gen_writer 不应在 prompt 指令里出现 throughline_progress（仅允许注释）: {ln.strip()}"


def test_apply_archive_writes_throughline_to_event_cluster():
    """🔴 archivist 产 archive.throughline_progress → apply_archive 落
    事件簇.clusters[].throughline_progress（cluster_summary_builder 读此处的源）。"""
    import apply_archive as aa  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        (db / ".wal").mkdir(parents=True)
        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 2]}]
        }, ensure_ascii=False), encoding="utf-8")
        (db / ".wal" / "cluster_001_archive.json").write_text(json.dumps({
            "cluster_id": "cluster_001",
            "characters": [{"id": "C_X", "name": "X", "tier": "core", "new": True,
                            "first_cluster": "cluster_001"}],
            "throughline_progress": {"OS": True, "MC": True, "IC": False, "RS": False},
        }, ensure_ascii=False), encoding="utf-8")
        assert aa.main([str(root), "--cluster", "001"]) == 0
        ec = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        assert ec["clusters"][0]["throughline_progress"] == {
            "OS": True, "MC": True, "IC": False, "RS": False}


def test_gen_writer_prompt_instructs_ending_self_report():
    """ending_type/ending_line 自报（cliffhanger 衔接遥测·创作自评 applied_style · advisory 不强制）。"""
    src = (_SCRIPTS / "gen_writer.py").read_text(encoding="utf-8")
    assert "ending_type" in src and "ending_line" in src


def test_throughline_progress_flows_archive_to_aggregator():
    """端到端锁消费路径（archive 单一来源）：archive.throughline_progress → apply_archive →
    事件簇.clusters[].throughline_progress → cluster_summary_builder 注入 cluster 账本 →
    throughline_balance distribution 非全 0（DORMANT 解除）。"""
    import apply_archive as aa  # noqa: PLC0415
    cluster_id = "cluster_001"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "_数据库"
        draft_dir = root / "章节" / f"{cluster_id}_draft"
        (db / ".wal").mkdir(parents=True)
        (db / ".audit").mkdir()
        (db / ".judge_reports").mkdir()
        draft_dir.mkdir(parents=True)

        (db / "事件簇.json").write_text(json.dumps({
            "clusters": [{"cluster_id": cluster_id, "title": "T"}]
        }, ensure_ascii=False), encoding="utf-8")
        # archivist 产 archive（含 throughline）→ apply_archive 落 事件簇
        (db / ".wal" / f"{cluster_id}_archive.json").write_text(json.dumps({
            "cluster_id": cluster_id,
            "characters": [{"id": "C_X", "name": "X", "tier": "core", "new": True,
                            "first_cluster": cluster_id}],
            "throughline_progress": {"OS": True, "MC": True, "IC": False, "RS": False},
        }, ensure_ascii=False), encoding="utf-8")
        assert aa.main([str(root), "--cluster", "001"]) == 0

        # cluster_summary_builder 的其余必需产物（writer 正文不自报 throughline）
        (draft_dir / f"{cluster_id}_draft.txt").write_text(
            "正文内容若干。\n" * 30, encoding="utf-8")
        (draft_dir / f"{cluster_id}_changes.json").write_text(
            json.dumps({"self_eval": {}}, ensure_ascii=False), encoding="utf-8")
        (db / "地图.json").write_text(
            json.dumps({"locations": []}, ensure_ascii=False), encoding="utf-8")
        (db / "主角压力档.json").write_text(
            json.dumps({"stress_log": []}, ensure_ascii=False), encoding="utf-8")
        (db / ".wal" / f"{cluster_id}_summary.json").write_text(json.dumps({
            "cluster_id": cluster_id, "title": "T", "summary": "OS/MC 推进的一个故事块。",
            "scene_summaries": [], "key_details": [], "emotion": {}, "anchor_delivery": {},
        }, ensure_ascii=False), encoding="utf-8")
        (db / ".audit" / f"{cluster_id}_audit.json").write_text(
            json.dumps({"cluster_id": cluster_id, "verdict": "pass"}, ensure_ascii=False),
            encoding="utf-8")
        (db / ".wal" / f"{cluster_id}_state_delta.json").write_text(
            json.dumps({"cluster_id": cluster_id}, ensure_ascii=False), encoding="utf-8")
        (db / ".wal" / f"{cluster_id}_entity_stats.json").write_text(
            json.dumps({"cluster_id": cluster_id, "known_entities": []}, ensure_ascii=False),
            encoding="utf-8")
        (db / ".judge_reports" / f"{cluster_id}_writer-truth-check.json").write_text(
            json.dumps({"cluster_id": cluster_id, "verdict": "pass"}, ensure_ascii=False),
            encoding="utf-8")
        (db / ".wal" / f"{cluster_id}_judge_reports_rollup.json").write_text(
            json.dumps({"cluster_id": cluster_id}, ensure_ascii=False), encoding="utf-8")
        store.initialize_summary(root)

        csb.build_cluster_summary(root, cluster_id)
        from cluster_summary_reader import load_summary
        rec = next(c for c in load_summary(root)["clusters"]
                   if cl.normalize_cluster_id(c["cluster_id"]) == cluster_id)
        # 账本承载 cluster 级 throughline（注入自 事件簇·archive 源）
        assert rec["throughline_progress"]["OS"] is True
        assert rec["throughline_progress"]["MC"] is True

        # 运行 throughline_balance → distribution 非全 0
        target = _SCRIPTS / "cross_cluster_throughline_balance_aggregate.py"
        r = subprocess.run([sys.executable, str(target), str(root), "--last-n", "5"],
                           capture_output=True, text=True, env=_utf8_env(), encoding="utf-8")
        assert "Traceback" not in (r.stderr or "")
        report = sorted((db / ".cross_cluster_scan").glob("throughline_balance_*.json"))[-1]
        dist = json.loads(report.read_text(encoding="utf-8"))["distribution"]
        assert dist["OS"] > 0 and dist["MC"] > 0, "throughline 不应全 DORMANT"
