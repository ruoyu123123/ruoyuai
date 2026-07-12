import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import relationship_evaluator as module


def project(root: Path, revealed=None) -> Path:
    db = root / "_数据库"
    (db / ".wal").mkdir(parents=True)
    (db / "群像档.json").write_text(json.dumps({"characters": {"阿青": {"heart_events": [{
        "event_id": "HE_001", "consumed": False, "trigger_at": {"trust": 2}, "reveal": "身世"
    }]}}}, ensure_ascii=False), encoding="utf-8")
    (db / "关系.json").write_text(json.dumps({"relationships": [{
        "from": "主角", "to": "阿青", "trust": 3
    }]}), encoding="utf-8")
    (db / "人物卡.json").write_text(json.dumps({"characters": [{"name": "主角", "role": "主角"}]}), encoding="utf-8")
    delta = {"cluster_id": "cluster_002", "heart_events_revealed": revealed or []}
    (db / ".wal/cluster_002_state_delta.json").write_text(json.dumps(delta, ensure_ascii=False), encoding="utf-8")
    return root


def test_revealed_event_consumed_at_cluster():
    with tempfile.TemporaryDirectory() as temp:
        root = project(Path(temp), [{"event_id": "HE_001", "evidence": "旧信件"}])
        result = module.evaluate(root, "cluster_002")
        assert result["newly_consumed_count"] == 1
        ensemble = json.loads((root / "_数据库/群像档.json").read_text(encoding="utf-8"))
        event = ensemble["characters"]["阿青"]["heart_events"][0]
        assert event["consumed_at_cluster"] == "cluster_002"
        assert result["pending_reveals_count"] == 0


def test_unrevealed_satisfied_event_is_pending():
    with tempfile.TemporaryDirectory() as temp:
        result = module.evaluate(project(Path(temp)), "cluster_002")
        assert result["pending_reveals_count"] == 1


def test_missing_state_delta_fails():
    with tempfile.TemporaryDirectory() as temp:
        root = project(Path(temp))
        (root / "_数据库/.wal/cluster_002_state_delta.json").unlink()
        try:
            module.evaluate(root, "cluster_002")
        except ValueError as exc:
            assert "state delta" in str(exc)
        else:
            raise AssertionError("缺 state delta 应失败")
