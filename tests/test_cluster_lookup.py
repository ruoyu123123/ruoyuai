"""cluster_lookup 回归测试 — 守护「头号系统性 bug」：章号 ≠ cluster 号。

一个 cluster 含 2-6 章，第 7 章极可能属于 cluster_002 而非 cluster_007。
这组测试钉死这个语义，防下次重构静默回退到 f"cluster_{ch:03d}"。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cluster_lookup as cl


def test_normalize_cluster_id():
    assert cl.normalize_cluster_id(6) == "cluster_006"
    assert cl.normalize_cluster_id("cluster_6") == "cluster_006"
    assert cl.normalize_cluster_id("cluster_002") == "cluster_002"
    assert cl.normalize_cluster_id("6") == "cluster_006"
    assert cl.normalize_cluster_id(None) is None
    assert cl.normalize_cluster_id(True) is None      # bool 不能被当 int


def test_cluster_num():
    assert cl.cluster_num("cluster_006") == 6
    assert cl.cluster_num(6) == 6
    assert cl.cluster_num(None) is None
    assert cl.cluster_num(True) is None


def _mk_project(tmp: Path, clusters: list) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "事件簇.json").write_text(json.dumps({"clusters": clusters}, ensure_ascii=False),
                                    encoding="utf-8")
    return tmp


def test_ch_to_cluster_id_core_bug():
    """核心：ch7 含在 cluster_002 的 range[4,7] → 必须返回 cluster_002，绝不是 cluster_007。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ])
        assert cl.ch_to_cluster_id(tmp, 7) == "cluster_002"   # 头号 bug 防护点
        assert cl.ch_to_cluster_id(tmp, 1) == "cluster_001"
        assert cl.ch_to_cluster_id(tmp, 5) == "cluster_002"


def test_ch_to_cluster_id_not_found_returns_none():
    """查不到返回 None（禁止伪造 cluster_{ch}）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}])
        assert cl.ch_to_cluster_id(tmp, 99) is None           # 不是 cluster_099


def test_normalize_blueprint_list_form():
    """城南实测 cluster_blueprint 是 list 形态 → 归并成 dict 不崩 + 推出 chapter_range。"""
    bp = [{"cluster": "cluster_001", "ch": 1},
          {"cluster": "cluster_001", "ch": 2},
          {"cluster": "cluster_002", "ch": 3}]
    out = cl.normalize_blueprint({"cluster_blueprint": bp})
    assert out["cluster_001"]["chapter_range"] == [1, 2]
    assert out["cluster_002"]["chapter_range"] == [3, 3]


def test_normalize_blueprint_garbage_returns_empty():
    """脏数据（str/int/None）→ 返回 {} 不崩。"""
    assert cl.normalize_blueprint(None) == {}
    assert cl.normalize_blueprint("garbage") == {}
    assert cl.normalize_blueprint(123) == {}
