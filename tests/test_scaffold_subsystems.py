"""test_scaffold_subsystems.py — 子系统脚手架框架测试（2026-06-01 根治契约债）

验证：
1. subsystem_skeletons.json 完整（34 canonical + skeletons 齐全 + 都有 schema_version/_schema）
2. scaffold emit 生成 34 个合法 json 骨架
3. ★契约耦合：scaffold 骨架输出必过 db_schema_validate.py（0 error）——防 scaffold 与 validator 再次漂移
4. scaffold verify 能检出缺失/损坏（流程缺步补全）
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "core" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load_mod(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skeletons_file_complete():
    scaf = _load_mod("scaffold_subsystems")
    canonical, skeletons = scaf._load_skeletons()
    assert len(canonical) == 34, f"canonical 应 34 个，实际 {len(canonical)}"
    for name in canonical:
        assert name in skeletons, f"skeletons 缺 {name}"
        obj = skeletons[name]
        assert isinstance(obj, dict), f"{name} 骨架应是 dict"
        assert "schema_version" in obj or "_schema" in obj, f"{name} 骨架缺 schema_version/_schema"


def test_emit_creates_34_valid_json():
    scaf = _load_mod("scaffold_subsystems")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        canonical, _ = scaf._load_skeletons()
        for name in canonical:
            p = db / f"{name}.json"
            assert p.exists(), f"emit 后缺 {name}.json"
            json.loads(p.read_text(encoding="utf-8"))  # 合法性


def test_emit_story_summary_is_runtime_cluster_payload():
    scaf = _load_mod("scaffold_subsystems")
    reader = _load_mod("cluster_summary_reader")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        value = json.loads((db / "故事块摘要.json").read_text(encoding="utf-8"))
        assert set(value) == {"schema_version", "clusters", "volume_summaries"}
        assert reader.load_summary(db) == value


def test_verify_rejects_non_runtime_story_summary_shape():
    scaf = _load_mod("scaffold_subsystems")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        path = db / "故事块摘要.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["_doc"] = "模板元信息不得进入运行时账本"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        assert scaf.cmd_verify(["--db-dir", str(db)]) == 2


def test_emit_skeletons_pass_db_schema_validate():
    """★契约耦合：scaffold 骨架必过 db_schema_validate（0 error），防再次漂移。"""
    scaf = _load_mod("scaffold_subsystems")
    dbv = _load_mod("db_schema_validate")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        errors = []
        for name, rules in dbv.SCHEMA_RULES.items():
            errs, _warns = dbv.validate_file(db / f"{name}.json", rules)
            errors.extend(errs)
        assert not errors, f"scaffold 骨架未过 validator: {errors}"


def test_verify_detects_missing_and_broken():
    scaf = _load_mod("scaffold_subsystems")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        # 全齐 → 0
        assert scaf.cmd_verify(["--db-dir", str(db)]) == 0
        # 删一个 → 2
        (db / "人物卡.json").unlink()
        assert scaf.cmd_verify(["--db-dir", str(db)]) == 2
        # 写坏一个 → 2
        scaf.cmd_emit(["--db-dir", str(db)])  # 补回
        (db / "世界观.json").write_text("{ broken json", encoding="utf-8")
        assert scaf.cmd_verify(["--db-dir", str(db)]) == 2


def test_emit_idempotent_no_overwrite():
    """已存在文件 emit 不覆盖（保护已填内容），--force 才覆盖。"""
    scaf = _load_mod("scaffold_subsystems")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        db.mkdir(parents=True)
        # 预置一个有内容的人物卡
        (db / "人物卡.json").write_text(json.dumps({"schema_version": "v27", "characters": [{"id": "x"}]}, ensure_ascii=False), encoding="utf-8")
        scaf.cmd_emit(["--db-dir", str(db)])
        d = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
        assert d["characters"] == [{"id": "x"}], "emit 不应覆盖已存在文件"
        # --force 覆盖为空骨架
        scaf.cmd_emit(["--db-dir", str(db), "--force"])
        d2 = json.loads((db / "人物卡.json").read_text(encoding="utf-8"))
        assert d2["characters"] == [], "--force 应覆盖为空骨架"
