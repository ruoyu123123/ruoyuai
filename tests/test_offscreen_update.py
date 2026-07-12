"""cluster offscreen 状态更新合同。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import offscreen_update as module  # noqa: E402


def _write_project(root: Path, executed: list[dict], *, done: bool = False) -> Path:
    db = root / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    cards = {
        "characters": [{
            "id": "C_LIN",
            "name": "林默",
            "offscreen": {"actions": [
                {"action": "潜入档案室", "done": done},
                {"action": "联络线人", "done": False},
            ]},
        }],
    }
    cards_path = db / "人物卡.json"
    cards_path.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    draft = root / "章节" / "cluster_001_draft"
    draft.mkdir(parents=True)
    (draft / "cluster_001_changes.json").write_text(
        json.dumps({"self_eval": {"offscreen_actions_executed": executed}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return cards_path


def test_apply_marks_done_at_cluster_and_writes_receipt(tmp_path):
    cards_path = _write_project(tmp_path, [{
        "character": "C_LIN",
        "action_index": 0,
        "completed_fully": True,
        "evidence": "正文中已完成",
    }])
    receipt = module.apply(tmp_path, "cluster_001")
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    action = cards["characters"][0]["offscreen"]["actions"][0]
    assert action["done"] is True
    assert action["_done_at_cluster"] == "cluster_001"
    assert receipt["applied"] == 1
    saved = json.loads(
        (tmp_path / "_数据库" / ".wal" / "cluster_001_offscreen_update.json")
        .read_text(encoding="utf-8")
    )
    assert saved == receipt


def test_apply_is_idempotent(tmp_path):
    cards_path = _write_project(tmp_path, [{
        "character": "林默", "action_index": 0, "completed_fully": True,
    }], done=True)
    receipt = module.apply(tmp_path, "cluster_001")
    action = json.loads(cards_path.read_text(encoding="utf-8"))["characters"][0]["offscreen"]["actions"][0]
    assert action["done"] is True
    assert receipt["applied"] == 0
    assert receipt["already_done"] == 1


def test_incomplete_action_remains_pending(tmp_path):
    cards_path = _write_project(tmp_path, [{
        "character": "林默", "action_index": 1, "completed_fully": False,
    }])
    receipt = module.apply(tmp_path, "cluster_001")
    action = json.loads(cards_path.read_text(encoding="utf-8"))["characters"][0]["offscreen"]["actions"][1]
    assert action["done"] is False
    assert receipt["pending"] == 1


@pytest.mark.parametrize(
    "execution",
    [
        {"character": "UNKNOWN", "action_index": 0, "completed_fully": True},
        {"character": "林默", "action_index": 99, "completed_fully": True},
        {"character": "林默", "completed_fully": True},
        {"character": "林默", "action_index": 0, "completed_fully": "yes"},
    ],
)
def test_invalid_references_fail_before_write(tmp_path, execution):
    cards_path = _write_project(tmp_path, [execution])
    before = cards_path.read_bytes()
    with pytest.raises(module.OffscreenContractError):
        module.apply(tmp_path, "cluster_001")
    assert cards_path.read_bytes() == before
    assert not (tmp_path / "_数据库" / ".wal" / "cluster_001_offscreen_update.json").exists()


def test_dry_run_does_not_write_state_or_receipt(tmp_path):
    cards_path = _write_project(tmp_path, [{
        "character": "林默", "action_index": 0, "completed_fully": True,
    }])
    before = cards_path.read_bytes()
    receipt = module.apply(tmp_path, "cluster_001", dry_run=True)
    assert receipt["applied"] == 1
    assert cards_path.read_bytes() == before
    assert not (tmp_path / "_数据库" / ".wal" / "cluster_001_offscreen_update.json").exists()


def test_cli_requires_cluster_artifact(tmp_path):
    env = dict(os.environ, RUOYUAI_CLUSTER_STATE_INTERNAL="1")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "offscreen_update.py"), str(tmp_path),
         "--cluster", "cluster_001"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert result.returncode == 2
    assert "[FATAL]" in result.stderr

