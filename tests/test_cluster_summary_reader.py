"""cluster_summary_reader 回归测试 — 锁账本（故事块摘要.json）消费侧确定性逻辑。

被测：core/scripts/cluster_summary_reader.py（纯 JSON/文件逻辑·无 LLM/网络）。

现有间接覆盖只在 builder 测试里 round-trip 调过 load_summary、在 aggregator 测试里
monkeypatch is_cluster_mode/get_chapter_records，从未直接锁 reader 自身的：
  · _db_dir 路径解析（_数据库 名/拼 _数据库）
  · load_summary 损坏/缺失 → 空骨架回退
  · _cluster_sort_key 三级排序（chapter_range[0] > cluster_end_ch > id 抽数字）
  · get_clusters 过滤（candidate / 缺 chapters+chapter_range / 缺 cluster_id）+ last_n
  · get_chapter_records 拍平（脏 ch_key 跳过 / 非 dict 跳过 / 按 ch 升序）
  · ledger_has_field 阈值 + 空值（None/[]/{}）不计
  · is_cluster_mode / current_cluster_id 读环境变量

本组测试聚焦这些核心确定性分支，不与现有测试重复。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cluster_summary_reader as csr


def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write_summary(db: Path, summary: dict):
    (db / csr.SUMMARY_FILENAME).write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )


# ───────────────────────── _db_dir / load_summary ─────────────────────────

def test_db_dir_accepts_project_root_and_db_dir():
    """_db_dir：传项目根 → 拼 _数据库；传 _数据库 本身 → 原样返回。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        # 传项目根
        assert csr._db_dir(tmp) == db
        # 传 _数据库 目录本身（root.name == "_数据库" 分支）
        assert csr._db_dir(db) == db


def test_load_summary_missing_returns_empty_skeleton():
    """文件不存在 → 返回 v2.cluster 空骨架，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)  # 建 _数据库 但不写摘要
        data = csr.load_summary(tmp)
        assert data == {"schema_version": "v2.cluster", "clusters": []}


def test_load_summary_corrupt_json_falls_back():
    """损坏 JSON / 非 dict 顶层 → 回退空骨架（异常被吞）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        # 截断的非法 JSON
        (db / csr.SUMMARY_FILENAME).write_text("{not valid json", encoding="utf-8")
        assert csr.load_summary(tmp) == {"schema_version": "v2.cluster", "clusters": []}
        # 合法 JSON 但顶层是 list（非 dict）→ 也回退
        (db / csr.SUMMARY_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        assert csr.load_summary(tmp) == {"schema_version": "v2.cluster", "clusters": []}


def test_load_summary_reads_valid_payload():
    """合法账本 → 原样读回（dict 顶层透传）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        payload = {"schema_version": "v2.cluster", "clusters": [], "extra": 1}
        _write_summary(db, payload)
        assert csr.load_summary(tmp) == payload


# ───────────────────────── _cluster_sort_key ─────────────────────────

def test_cluster_sort_key_priority_order():
    """排序键三级回退：chapter_range[0] > cluster_end_ch > cluster_id 抽数字。"""
    # chapter_range[0] 优先
    assert csr._cluster_sort_key({"chapter_range": [4, 9], "cluster_end_ch": 99}) == 4
    # 无 chapter_range → cluster_end_ch
    assert csr._cluster_sort_key({"cluster_end_ch": 12}) == 12
    # 两者皆无 → cluster_id 抽数字 ×1000
    assert csr._cluster_sort_key({"cluster_id": "cluster_003"}) == 3000
    # 完全无锚 → 巨大值压末尾
    assert csr._cluster_sort_key({}) == 1_000_000
    # chapter_range 非法（空/首元素非 int）→ 落到下一级
    assert csr._cluster_sort_key({"chapter_range": [], "cluster_end_ch": 7}) == 7
    assert csr._cluster_sort_key({"chapter_range": ["x", 5], "cluster_end_ch": 8}) == 8


# ───────────────────────── get_clusters ─────────────────────────

def test_get_clusters_filters_and_sorts():
    """过滤掉 candidate / 缺 cluster_id / 既无 chapters 又无 chapter_range 的；按 range[0] 升序。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_summary(db, {"clusters": [
            # 乱序输入，期望按 chapter_range[0] 升序回来
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            # candidate 雏形 → 滤掉
            {"cluster_id": "cluster_003", "chapter_range": [8, 9], "status": "candidate"},
            # 缺 cluster_id → 滤掉
            {"chapter_range": [10, 11]},
            # 既无 chapters 又无 chapter_range → 滤掉
            {"cluster_id": "cluster_004"},
            # 只有 chapters（fluid 未切章）→ 保留
            {"cluster_id": "cluster_000", "chapters": {"0": {}}},
            # 非 dict 元素 → 滤掉，不崩
            "garbage",
        ]})
        out = csr.get_clusters(tmp)
        ids = [c["cluster_id"] for c in out]
        # cluster_000 无 chapter_range → 用 cluster_end_ch(无) → id 抽数字 0 → 排最前
        assert ids == ["cluster_000", "cluster_001", "cluster_002"]


def test_get_clusters_last_n():
    """last_n 截最后 N 个；last_n<=0 或 None 不截。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_summary(db, {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 2]},
            {"cluster_id": "cluster_002", "chapter_range": [3, 4]},
            {"cluster_id": "cluster_003", "chapter_range": [5, 6]},
        ]})
        assert [c["cluster_id"] for c in csr.get_clusters(tmp, last_n=2)] == \
            ["cluster_002", "cluster_003"]
        assert len(csr.get_clusters(tmp, last_n=None)) == 3
        # last_n <= 0 不截（实现里 last_n>0 才切片）
        assert len(csr.get_clusters(tmp, last_n=0)) == 3


# ───────────────────────── get_chapter_records ─────────────────────────

def test_get_chapter_records_flatten_and_sort():
    """拍平所有 cluster.chapters → [(ch:int, rec)]，按 ch 升序；脏 key / 非 dict rec 跳过。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_summary(db, {"clusters": [
            {"cluster_id": "cluster_002", "chapter_range": [4, 5],
             "chapters": {"5": {"cjk": 500}, "4": {"cjk": 400}}},
            {"cluster_id": "cluster_001", "chapter_range": [1, 2],
             "chapters": {
                 "2": {"cjk": 200},
                 "1": {"cjk": 100},
                 "x": {"cjk": 999},        # 非整数 key → 跳过
                 "3": "not_a_dict",        # rec 非 dict → 跳过
             }},
        ]})
        recs = csr.get_chapter_records(tmp)
        chs = [ch for ch, _ in recs]
        assert chs == [1, 2, 4, 5]                       # 跨 cluster 拍平 + 全局升序
        by_ch = dict(recs)
        assert by_ch[1] == {"cjk": 100}
        assert by_ch[5] == {"cjk": 500}
        assert 3 not in by_ch                            # 脏数据被剔


def test_get_chapter_records_handles_non_dict_chapters():
    """cluster.chapters 非 dict（list / None）→ 跳过该 cluster，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_summary(db, {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 1], "chapters": ["bad"]},
            {"cluster_id": "cluster_002", "chapter_range": [2, 2], "chapters": {"2": {"ok": 1}}},
        ]})
        recs = csr.get_chapter_records(tmp)
        assert [ch for ch, _ in recs] == [2]


# ───────────────────────── ledger_has_field ─────────────────────────

def test_ledger_has_field_threshold_and_empty_values():
    """字段命中计数 ≥ min_chapters 才 True；空值（None/[]/{}）不计命中。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write_summary(db, {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 4], "chapters": {
                "1": {"summary": "有内容"},          # 命中
                "2": {"summary": ""},                # "" 不在空值黑名单 → 也算命中
                "3": {"summary": None},              # None → 不计
                "4": {"summary": []},                # [] → 不计
            }},
        ]})
        # summary 有效命中：ch1("有内容") + ch2("") = 2
        assert csr.ledger_has_field(tmp, "summary", min_chapters=1) is True
        assert csr.ledger_has_field(tmp, "summary", min_chapters=2) is True
        assert csr.ledger_has_field(tmp, "summary", min_chapters=3) is False
        # 完全不存在的字段 → False
        assert csr.ledger_has_field(tmp, "pattern_metrics", min_chapters=1) is False


# ───────────────────────── env 读取 ─────────────────────────

def test_is_cluster_mode_and_current_cluster_id_env():
    """is_cluster_mode 看 CLUSTER_MODE=="1"；current_cluster_id 透传 CLUSTER_ID。"""
    saved_mode = os.environ.get("CLUSTER_MODE")
    saved_id = os.environ.get("CLUSTER_ID")
    try:
        os.environ["CLUSTER_MODE"] = "1"
        assert csr.is_cluster_mode() is True
        os.environ["CLUSTER_MODE"] = "0"
        assert csr.is_cluster_mode() is False
        os.environ.pop("CLUSTER_MODE", None)
        assert csr.is_cluster_mode() is False           # 未设 → False

        os.environ["CLUSTER_ID"] = "cluster_007"
        assert csr.current_cluster_id() == "cluster_007"
        os.environ.pop("CLUSTER_ID", None)
        assert csr.current_cluster_id() is None
    finally:
        if saved_mode is None:
            os.environ.pop("CLUSTER_MODE", None)
        else:
            os.environ["CLUSTER_MODE"] = saved_mode
        if saved_id is None:
            os.environ.pop("CLUSTER_ID", None)
        else:
            os.environ["CLUSTER_ID"] = saved_id
