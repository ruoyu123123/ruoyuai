"""save_state 的 required cluster 伏笔状态子步骤测试。"""

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import save_state as ss  # noqa: E402


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _project(tmp: Path, *, promises=None, planned=None, scores=None) -> Path:
    db = tmp / "_数据库"
    _write_json(db / "伏笔表.json", {
        "promises": list(promises or []),
        "deadlines": [],
        "pledges": [],
        "secrets": [],
    })
    _write_json(db / "事件簇.json", {"clusters": [{
        "cluster_id": "cluster_001",
        "foreshadowing_to_plant": list(planned or []),
    }]})
    if scores is not None:
        _write_json(db / ".judge_reports" / "cluster_001_foreshadower.json", {
            "judge_id": "foreshadower",
            "cluster_id": "cluster_001",
            "specific_findings": {"payoff_scores": scores},
        })
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    return tmp


def _read_fs(project: Path) -> dict:
    return json.loads((project / "_数据库" / "伏笔表.json").read_text(encoding="utf-8"))


def _receipt(project: Path) -> dict:
    return json.loads((project / "_数据库" / ".wal" /
                       "cluster_001_foreshadow_state_receipt.json").read_text(encoding="utf-8"))


def test_register_brief_foreshadowings():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(Path(directory), planned=[
            {"id": "FS_014", "tier": 2, "desc": "墙上的旧照片", "payoff_scope": "volume"},
            {"id": "FS_015", "description": "瘸腿男孩的失踪"},
        ])
        result = ss._register_brief_foreshadowings(project, "cluster_001")
        assert result == {"planned": 2, "registered": 2}
        by_id = {row["id"]: row for row in _read_fs(project)["promises"]}
        assert by_id["FS_014"]["setup_cluster"] == "cluster_001"
        assert by_id["FS_014"]["status"] == "open"
        assert by_id["FS_014"]["owner"] == "writer"
        assert by_id["FS_014"]["payoff_scope"] == "volume"


def test_register_is_idempotent_and_preserves_existing_terminal_state():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(
            Path(directory),
            promises=[{"id": "FS_014", "status": "consumed", "_source": "manual"}],
            planned=[{"id": "FS_014", "desc": "x"}, {"id": "FS_016", "desc": "y"}],
        )
        assert ss._register_brief_foreshadowings(project, "cluster_001")["registered"] == 1
        assert ss._register_brief_foreshadowings(project, "cluster_001")["registered"] == 0
        rows = _read_fs(project)["promises"]
        assert [row["id"] for row in rows].count("FS_014") == 1
        fs014 = next(row for row in rows if row["id"] == "FS_014")
        assert fs014["status"] == "consumed" and fs014["_source"] == "manual"


def test_register_rejects_noncanonical_brief_id_field():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(Path(directory), planned=[{"fs_id": "FS_OLD", "desc": "x"}])
        try:
            ss._register_brief_foreshadowings(project, "cluster_001")
        except RuntimeError as exc:
            assert "稳定 id" in str(exc)
        else:
            raise AssertionError("brief 必须使用 id")


def test_terminal_payoff_consumes_at_cluster():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(
            Path(directory),
            promises=[{"id": "FS_006", "setup_cluster": "cluster_001", "status": "open"}],
            scores=[{
                "fs_id": "FS_006", "score": 5, "terminal": True,
                "verdict": "paid_terminal", "reason": "登记册正式成立",
            }],
        )
        result = ss._apply_foreshadower_payoffs(project, "cluster_001")
        assert result["terminal_applied"] == 1
        promise = _read_fs(project)["promises"][0]
        assert promise["status"] == "consumed"
        assert promise["consumed_at_cluster"] == "cluster_001"
        assert "consumed_at_ch" not in promise


def test_progressive_payoff_is_cluster_idempotent():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(
            Path(directory),
            promises=[{"id": "FS_007", "status": "open"}],
            scores=[{
                "fs_id": "FS_007", "score": 4, "terminal": False,
                "verdict": "paid_progressive", "reason": "线索进一步显形",
            }],
        )
        assert ss._apply_foreshadower_payoffs(project, "cluster_001")["progressive_recorded"] == 1
        assert ss._apply_foreshadower_payoffs(project, "cluster_001")["progressive_recorded"] == 0
        promise = _read_fs(project)["promises"][0]
        assert promise["status"] == "open"
        assert promise["payoff_progress"] == ["cluster_001"]


def test_zero_score_does_not_change_state():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(
            Path(directory),
            promises=[{"id": "FS_004", "status": "open"}],
            scores=[{
                "fs_id": "FS_004", "score": 0, "terminal": True,
                "verdict": "paid_terminal", "reason": "无正文痕迹",
            }],
        )
        result = ss._apply_foreshadower_payoffs(project, "cluster_001")
        assert result["terminal_applied"] == 0
        assert _read_fs(project)["promises"][0]["status"] == "open"


def test_required_command_writes_completion_receipt():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(
            Path(directory),
            planned=[{"id": "FS_010", "desc": "铜铃"}],
            scores=[{
                "fs_id": "FS_010", "score": 5, "terminal": True,
                "verdict": "paid_terminal", "evidence": "铜铃在火中裂开",
            }],
        )
        assert ss.cmd_apply_foreshadow_state(project, "001") == 0
        receipt = _receipt(project)
        assert receipt["contract"] == "cluster_foreshadow_state"
        assert receipt["completed"] is True
        assert receipt["brief_registered"] == 1
        assert receipt["terminal_applied"] == 1
        assert receipt["rejection_count"] == 0


def test_required_command_accepts_explicit_empty_result():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(Path(directory), scores=[])
        assert ss.cmd_apply_foreshadow_state(project, "cluster_001") == 0
        receipt = _receipt(project)
        assert receipt["brief_planned"] == 0
        assert receipt["payoff_checked"] == 0
        assert receipt["completed"] is True


def test_required_command_missing_report_hard_fails_without_receipt():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(Path(directory), planned=[{"id": "FS_011", "desc": "门锁"}])
        assert ss.cmd_apply_foreshadow_state(project, "cluster_001") == 2
        assert not (project / "_数据库" / ".wal" /
                    "cluster_001_foreshadow_state_receipt.json").exists()
        assert _read_fs(project)["promises"] == []


def test_missing_payoff_target_is_rejected_and_receipted():
    with tempfile.TemporaryDirectory() as directory:
        project = _project(Path(directory), scores=[{
            "fs_id": "FS_UNKNOWN", "score": 3, "terminal": True,
            "verdict": "paid_terminal", "evidence": "陌生线索闭合",
        }])
        assert ss.cmd_apply_foreshadow_state(project, "cluster_001") == 0
        assert _receipt(project)["rejection_count"] == 1
