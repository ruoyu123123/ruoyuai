"""character_arc_update 的 cluster-native 唯一写入合同。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import character_arc_update as module


def run_update(data: dict, cluster: str = "cluster_002"):
    with tempfile.TemporaryDirectory() as temp:
        project = Path(temp)
        db = project / "_数据库"
        db.mkdir()
        path = db / "character_arc_state.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, RUOYUAI_CLUSTER_STATE_INTERNAL="1", PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "character_arc_update.py"), str(project), "--cluster", cluster],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        receipt_path = db / ".wal" / "cluster_002_character_arc_update.json"
        receipt = (json.loads(receipt_path.read_text(encoding="utf-8"))
                   if receipt_path.exists() else None)
        return result, json.loads(path.read_text(encoding="utf-8")), receipt


def test_stage_for_cluster_is_exact():
    stages = {"cluster_001": "lie", "cluster_002": "lie_cracking"}
    assert module.stage_for_cluster(stages, "cluster_002") == "lie_cracking"
    assert module.stage_for_cluster(stages, "cluster_003") is None


def test_updates_current_stage_at_cluster():
    result, after, receipt = run_update({"characters": {"陆衍": {
        "stages_by_cluster": {"cluster_001": "lie", "cluster_002": "lie_cracking"}
    }}})
    assert result.returncode == 0, result.stderr
    assert after["characters"]["陆衍"]["current_stage_at_cluster"] == "cluster_002:lie_cracking"
    assert receipt["completed"] is True
    assert receipt["updated_characters"] == 1


def test_old_shapes_fail_hard():
    for old in ({"arcs": {}}, {"characters": []}, {"characters": "bad"}):
        result, _, receipt = run_update(old)
        assert result.returncode == 2
        assert receipt is None


def test_unmapped_cluster_is_noop():
    original = {"characters": {"陆衍": {"stages_by_cluster": {"cluster_001": "lie"}}}}
    result, after, receipt = run_update(original)
    assert result.returncode == 0
    assert after == original
    assert receipt["updated_characters"] == 0
