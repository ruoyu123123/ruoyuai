"""save_state 的 cluster writer self-eval receipt 测试。"""

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402


def _write_changes(root: Path, payload: dict) -> None:
    draft = root / "章节" / "cluster_001_draft"
    draft.mkdir(parents=True, exist_ok=True)
    (draft / "cluster_001_changes.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)


def test_cmd_apply_cluster_changes_validates_self_eval_and_writes_receipt():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_changes(root, {"self_eval": {"waivers": []}})
        assert ss.cmd_apply_cluster_changes(root, "001") == 0
        receipt_path = root / "_数据库" / ".wal" / "cluster_001_apply_cluster.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["cluster_id"] == "cluster_001"
        assert receipt["contract"] == "cluster_writer_self_eval"
        assert receipt["objective_state_applied"] is False
        assert receipt["waiver_count"] == 0


def test_cmd_apply_cluster_changes_rejects_objective_state_payload():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_changes(root, {
            "self_eval": {"waivers": []},
            "factual": {"time_advance": {}},
        })
        assert ss.cmd_apply_cluster_changes(root, "cluster_001") == 2
        assert not (root / "_数据库" / ".wal" /
                    "cluster_001_apply_cluster.json").exists()
