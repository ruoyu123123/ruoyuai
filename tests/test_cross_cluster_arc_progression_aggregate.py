import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_cluster_arc_progression_aggregate as module


def test_cluster_arc_detects_jump_regress_and_stagnation():
    data = {"characters": {
        "甲": {"stages_by_cluster": {"cluster_001": "lie", "cluster_002": "all_is_lost"}},
        "乙": {"stages_by_cluster": {"cluster_001": "truth_realized", "cluster_002": "lie"}},
        "丙": {"stages_by_cluster": {"cluster_001": "debate", "cluster_004": "debate"}},
    }}
    codes = {row["code"] for row in module.scan(data)}
    assert codes == {"STAGE_JUMP", "STAGE_REGRESS", "STAGE_STAGNATION"}


def test_old_arc_shape_is_rejected():
    try:
        module.scan({"arcs": {}})
    except ValueError as exc:
        assert "characters" in str(exc)
    else:
        raise AssertionError("旧 arcs 形态应拒绝")
