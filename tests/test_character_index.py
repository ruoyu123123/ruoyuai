"""character_index 的 cluster-native 合同测试。"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import character_index as ci  # noqa: E402


def _write_cluster(project: Path, cid: str, body: str) -> Path:
    directory = project / "章节" / f"{cid}_draft"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{cid}_draft.txt"
    path.write_text(body, encoding="utf-8")
    return path


def _write_cards(project: Path, cards: list) -> None:
    db = project / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": cards}, ensure_ascii=False), encoding="utf-8"
    )


def test_load_json_reads_utf8_and_rejects_malformed():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        good = root / "good.json"
        good.write_text('{"中文": 1}', encoding="utf-8")
        assert ci.load_json(good) == {"中文": 1}
        missing = root / "missing.json"
        assert ci.load_json(missing, {"explicit": True}) == {"explicit": True}
        bad = root / "bad.json"
        bad.write_text("{", encoding="utf-8")
        try:
            ci.load_json(bad)
        except ValueError as exc:
            assert "JSON 无法读取" in str(exc)
        else:
            raise AssertionError("坏 JSON 必须报错")


def test_find_cluster_files_only_accepts_canonical_drafts():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        p2 = _write_cluster(root, "cluster_002", "二")
        p1 = _write_cluster(root, "cluster_001", "一")
        physical = root / "章节" / "第001章"
        physical.mkdir(parents=True)
        (physical / "第001章.txt").write_text("不得扫描", encoding="utf-8")
        assert ci.find_cluster_files(root) == [
            ("cluster_001", p1),
            ("cluster_002", p2),
        ]


def test_find_cluster_files_rejects_noncanonical_id():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "章节" / "cluster_1_draft"
        path.mkdir(parents=True)
        (path / "cluster_1_draft.txt").write_text("正文", encoding="utf-8")
        try:
            ci.find_cluster_files(root)
        except ValueError as exc:
            assert "非规范 id" in str(exc)
        else:
            raise AssertionError("非规范 cluster 路径必须报错")


def test_scan_cluster_absent():
    assert ci.scan_cluster_for_character("这里没有目标角色。", "克莱") == {"appears": False}


def test_scan_cluster_mentions_dialogue_and_context():
    result = ci.scan_cluster_for_character(
        "克莱走进房间。克莱说“你来了吗”。窗外下雨。", "克莱"
    )
    assert result["appears"] is True
    assert result["mention_count"] == 2
    assert result["dialogue_samples"] == ["你来了吗"]
    assert result["first_context"].startswith("克莱走进房间")


def test_scan_cluster_dialogue_fallback_and_sample_cap():
    body = (
        "阿珂沉默了很久很久很久很久之后才开口“第一”。"
        "阿珂停了一会“第二”。阿珂又想了想“第三”。阿珂最后说“第四”。"
    )
    result = ci.scan_cluster_for_character(body, "阿珂")
    assert result["appears"] is True
    assert len(result["dialogue_samples"]) == 3
    assert result["dialogue_count_estimate"] >= 3


def test_build_index_aggregates_clusters():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_cards(root, [{
            "id": "C_CLAY",
            "name": "克莱",
            "role": "主角",
            "first_appearance_cluster": "cluster_001",
        }])
        _write_cluster(root, "cluster_001", "克莱醒来。克莱说“开始”。")
        _write_cluster(root, "cluster_002", "这里没有目标角色。")
        _write_cluster(root, "cluster_003", "克莱回来。克莱说“结束”。")
        index = ci.build_index(root)
        assert index["snapshot_at_cluster"] == "cluster_003"
        record = index["characters"]["克莱"]
        assert record["first_appearance_cluster_actual"] == "cluster_001"
        assert record["last_appearance_cluster_actual"] == "cluster_003"
        assert record["total_clusters_appeared"] == 2
        assert [row["cluster_id"] for row in record["appearances"]] == [
            "cluster_001", "cluster_003"
        ]
        assert "declaration_warning" not in record


def test_build_index_reports_declaration_mismatch():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_cards(root, [{
            "id": "C_VIOLET",
            "name": "薇尔莉特",
            "first_appearance_cluster": "cluster_001",
        }])
        _write_cluster(root, "cluster_001", "空无一人的开场。")
        _write_cluster(root, "cluster_002", "薇尔莉特终于登场。")
        record = ci.build_index(root)["characters"]["薇尔莉特"]
        assert record["first_appearance_cluster_actual"] == "cluster_002"
        assert "cluster_002" in record["declaration_warning"]


def test_build_index_registered_character_never_appears():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_cards(root, [{"id": "C_GHOST", "name": "幽灵角色"}])
        _write_cluster(root, "cluster_001", "正文没有目标名字。")
        record = ci.build_index(root)["characters"]["幽灵角色"]
        assert record["total_clusters_appeared"] == 0
        assert record["first_appearance_cluster_actual"] is None
        assert record["last_appearance_cluster_actual"] is None
        assert record["appearances"] == []


def test_build_index_requires_people_database():
    with tempfile.TemporaryDirectory() as directory:
        try:
            ci.build_index(Path(directory))
        except ValueError as exc:
            assert "人物卡.json.characters" in str(exc)
        else:
            raise AssertionError("缺人物卡必须报错")


def test_main_write_and_query(capsys):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _write_cards(root, [{"id": "C_A", "name": "阿甲"}])
        _write_cluster(root, "cluster_001", "阿甲出现。")
        assert ci.main([str(root), "--write"]) == 0
        index_path = root / "_数据库" / "character_index.json"
        assert index_path.exists()
        assert ci.main([str(root), "--query", "阿甲"]) == 0
        output = capsys.readouterr().out
        assert "cluster_001" in output
