"""写作经验.json 原子写回归测试 — 守护 [#6] 多写者半截损坏 bug。

cluster-save-state step7-9 有 ≥5 个写者集中 RMW 同一 _数据库/写作经验.json。
旧实现 learning_loop.save_experience / skill_evolver.save_json 用裸 write_text：
任一进程 subprocess timeout 被 kill 写到一半 → 留半截 JSON →
下个读者 json.JSONDecodeError 兜底成空 {} → 整库 success/failure_patterns 静默清空。

修复：两处裸 write_text 改用 atomic_json.atomic_write_json（tmp 唯一名 + fsync + os.replace 原子落盘）。
这组测试钉死「写盘走原子路径、目标永不残留半截、不留游离 tmp」，**不碰任何学习逻辑**。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import atomic_json  # noqa: E402
import learning_loop  # noqa: E402
import skill_evolver  # noqa: E402


def _no_stray_tmp_or_corrupt(db_dir: Path, target: Path):
    """目标是合法 JSON，且目录里没有残留 .tmp（原子写应清理本次 tmp）。"""
    # 目标可被解析（没留半截）
    json.loads(target.read_text(encoding="utf-8"))
    strays = [p.name for p in db_dir.iterdir() if p.name.endswith(".tmp")]
    assert not strays, f"残留游离 tmp 文件: {strays}"


def test_save_experience_uses_atomic_json():
    """save_experience 必须经由 atomic_json 落盘（不再裸 write_text）。"""
    assert learning_loop.atomic_json is atomic_json


def test_skill_evolver_save_json_uses_atomic_json():
    """skill_evolver.save_json（evolve/promote 都调）必须经由 atomic_json 落盘。"""
    assert skill_evolver.atomic_json is atomic_json


def test_save_experience_roundtrip_valid_json():
    """save_experience → load_experience 数据完整往返，目标为合法 JSON、无游离 tmp。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        data = learning_loop._empty_experience()
        data["failure_patterns"].append({
            "id": "fp1", "category": "rhythm", "trigger": "x",
            "technique": "y", "confidence": 0.7,
        })
        data["success_patterns"].append({"id": "sp1", "confidence": 0.9})
        p = learning_loop.save_experience(root, data)
        assert p.is_file()
        _no_stray_tmp_or_corrupt(p.parent, p)
        reloaded = learning_loop.load_experience(root)
        assert len(reloaded["failure_patterns"]) == 1
        assert reloaded["failure_patterns"][0]["confidence"] == 0.7
        assert len(reloaded["success_patterns"]) == 1


def test_skill_evolver_save_json_roundtrip_valid_json():
    """skill_evolver.save_json 写出合法 JSON、可往返、无游离 tmp。"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        target = db / "写作经验.json"
        payload = {"success_patterns": [{"id": "a", "version": 2}], "failure_patterns": []}
        skill_evolver.save_json(target, payload)
        _no_stray_tmp_or_corrupt(db, target)
        assert skill_evolver.load_json(target) == payload


def test_overwrite_does_not_leave_partial_on_repeated_writes():
    """反复覆盖写（模拟多写者顺序 RMW）后目标始终合法、内容是最后一次的。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(6):  # step7-9 的 ≥5 写者顺序写
            data = learning_loop._empty_experience()
            data["preferences"].append({"preference": f"p{i}", "confidence": 0.5})
            learning_loop.save_experience(root, data)
        p = learning_loop._experience_path(root)
        _no_stray_tmp_or_corrupt(p.parent, p)
        final = learning_loop.load_experience(root)
        assert final["preferences"] == [{"preference": "p5", "confidence": 0.5}]
