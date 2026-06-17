"""character_lazy_spawn 回归测试 — 涌现角色 lazy spawn 处理（纯逻辑 + CLI 契约·零 LLM）。

钉死：
- load_json / save_json 文件 IO 容错 + UTF-8 中文往返
- _resolve_cluster 章号→真实 cluster_id 反查（命中=not inferred / 未命中=fallback+inferred）
  —— 守护「章号 ≠ cluster 号」头号 bug：不许静默回退 f"cluster_{ch:03d}"
- main() CLI 端到端：扶正 emerged（角色池 + 人物卡骨架）/ 进 extras / 去重 / dry-run 不落盘 / 无新角色早退
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import character_lazy_spawn as cls  # noqa: E402


# ---------------------------------------------------------------------------
# 造测试项目脚手架
# ---------------------------------------------------------------------------

def _mk_project(tmp: Path, clusters=None) -> Path:
    """造最小项目：_数据库/事件簇.json（供章号反查）。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if clusters is not None:
        (db / "事件簇.json").write_text(
            json.dumps({"clusters": clusters}, ensure_ascii=False),
            encoding="utf-8",
        )
    return tmp


def _write_changes(project: Path, ch: int, characters: list) -> Path:
    """造 章节/第NNN章/第NNN章_changes.json，把角色塞进 factual.new_entities.characters。"""
    cdir = project / "章节" / f"第{ch:03d}章"
    cdir.mkdir(parents=True, exist_ok=True)
    p = cdir / f"第{ch:03d}章_changes.json"
    p.write_text(
        json.dumps(
            {"factual": {"new_entities": {"characters": characters}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def _run_main(project: Path, ch: int, dry_run: bool = False):
    """以子进程跑 main()（main 调 sys.exit，子进程隔离）。返回 CompletedProcess。

    Windows 默认控制台编码非 UTF-8（GBK），子进程打印中文会让父进程 utf-8 解码崩。
    强制 PYTHONIOENCODING=utf-8 + errors=replace 兜底，保证 r.stdout 总是 str 可断言。
    """
    cmd = [sys.executable, str(_SCRIPTS / "character_lazy_spawn.py"), str(project), str(ch)]
    if dry_run:
        cmd.append("--dry-run")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env
    )


# ---------------------------------------------------------------------------
# load_json / save_json
# ---------------------------------------------------------------------------

def test_load_json_missing_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "nope.json"
        sentinel = {"x": 1}
        assert cls.load_json(p, sentinel) is sentinel
        assert cls.load_json(p) is None  # 默认 default=None


def test_load_json_malformed_returns_default():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.json"
        p.write_text("{ not valid json", encoding="utf-8")
        assert cls.load_json(p, {"fallback": True}) == {"fallback": True}


def test_save_json_roundtrip_utf8():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "out.json"
        p.parent.mkdir(parents=True)
        data = {"name": "张三", "role": "反派", "nested": [1, "二", {"k": "值"}]}
        cls.save_json(p, data)
        # ensure_ascii=False → 中文不转义，磁盘里能直接读到汉字
        raw = p.read_text(encoding="utf-8")
        assert "张三" in raw and "反派" in raw
        assert cls.load_json(p) == data


# ---------------------------------------------------------------------------
# _resolve_cluster —— 章号 ≠ cluster 号头号 bug 防护
# ---------------------------------------------------------------------------

def test_resolve_cluster_hit_not_inferred():
    """ch 落在 cluster_002 的 range[4,7] → 返回 cluster_002 且 inferred=False（绝不是 cluster_007）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ])
        cid, inferred = cls._resolve_cluster(proj, 7)
        assert cid == "cluster_002"
        assert inferred is False


def test_resolve_cluster_miss_falls_back_inferred():
    """反查不到 → fallback 到 normalize_cluster_id(ch) 且 inferred=True（标不可信）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        ])
        cid, inferred = cls._resolve_cluster(proj, 99)
        assert cid == "cluster_099"  # normalize_cluster_id(99)
        assert inferred is True


def test_resolve_cluster_no_event_file_inferred():
    """连 事件簇.json 都没有 → 必然 fallback + inferred=True。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), clusters=None)
        cid, inferred = cls._resolve_cluster(proj, 12)
        assert cid == "cluster_012"
        assert inferred is True


# ---------------------------------------------------------------------------
# main() 端到端 CLI 契约
# ---------------------------------------------------------------------------

def test_main_no_new_chars_early_exit():
    """changes 里没有新角色 → exit 0 + 提示无 spawn，不创建角色池/人物卡。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}])
        _write_changes(proj, 1, [])
        r = _run_main(proj, 1)
        assert r.returncode == 0, r.stderr
        assert "无新角色 spawn" in r.stdout
        assert not (proj / "_数据库" / "角色池.json").exists()


def test_main_promote_emerged_and_extras():
    """_propose_emerged=true → emerged_characters + 人物卡骨架；false → extras。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}])
        _write_changes(proj, 3, [
            {"name": "李四", "role": "导师", "_propose_emerged": True},
            {"name": "路人甲", "role": "群演", "_propose_emerged": False},
        ])
        r = _run_main(proj, 3)
        assert r.returncode == 0, r.stderr

        pool = cls.load_json(proj / "_数据库" / "角色池.json")
        emerged_ids = {c["id"] for c in pool["emerged_characters"]}
        extras_ids = {c["id"] for c in pool["extras"]}
        assert emerged_ids == {"李四"}
        assert extras_ids == {"路人甲"}
        # emerged 记录带 spawn 章号
        li = next(c for c in pool["emerged_characters"] if c["id"] == "李四")
        assert li["spawned_at_ch"] == 3 and li["tier"] == "emerged"

        cards = cls.load_json(proj / "_数据库" / "人物卡.json")
        names = {c["name"] for c in cards["characters"]}
        assert "李四" in names          # emerged 才建骨架卡
        assert "路人甲" not in names    # extras 不建卡
        card = next(c for c in cards["characters"] if c["name"] == "李四")
        assert card["_lazy_spawned"] is True
        # ch3 落在 cluster_001 range[1,5] → first_appear_cluster 是真实反查值，非 cluster_003
        assert card["first_appear_cluster"] == "cluster_001"
        assert "_cluster_inferred" not in card  # 命中权威源 → 不标 inferred


def test_main_dedup_against_existing_pool():
    """已存在的角色（按 id/name）不重复添加。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}])
        # 预置角色池：王五 已在 core_characters
        pool_path = proj / "_数据库" / "角色池.json"
        cls.save_json(pool_path, {
            "core_characters": [{"id": "王五", "role": "主角"}],
            "emerged_characters": [],
            "extras": [],
        })
        _write_changes(proj, 2, [
            {"name": "王五", "role": "主角", "_propose_emerged": True},   # 重复 → 跳过
            {"name": "赵六", "role": "配角", "_propose_emerged": False},  # 新 → 进 extras
        ])
        r = _run_main(proj, 2)
        assert r.returncode == 0, r.stderr

        pool = cls.load_json(pool_path)
        assert pool["emerged_characters"] == []          # 王五未被重复扶正
        assert {c["id"] for c in pool["extras"]} == {"赵六"}
        assert len(pool["core_characters"]) == 1          # 原有不变


def test_main_dry_run_does_not_write():
    """--dry-run → 只打印不落盘，角色池/人物卡都不被创建。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}])
        _write_changes(proj, 4, [
            {"name": "孙七", "role": "神秘人", "_propose_emerged": True},
        ])
        r = _run_main(proj, 4, dry_run=True)
        assert r.returncode == 0, r.stderr
        assert "DRY-RUN" in r.stdout
        assert "孙七" in r.stdout
        # 落盘文件不应被创建
        assert not (proj / "_数据库" / "角色池.json").exists()
        assert not (proj / "_数据库" / "人物卡.json").exists()


def test_main_missing_name_skipped():
    """缺 name/id 的角色条目被跳过，不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"cluster_id": "cluster_001", "chapter_range": [1, 5]}])
        _write_changes(proj, 1, [
            {"role": "无名氏", "_propose_emerged": True},   # 无 name/id → 跳过
            {"name": "周八", "role": "线人", "_propose_emerged": False},
        ])
        r = _run_main(proj, 1)
        assert r.returncode == 0, r.stderr
        pool = cls.load_json(proj / "_数据库" / "角色池.json")
        assert pool["emerged_characters"] == []
        assert {c["id"] for c in pool["extras"]} == {"周八"}
