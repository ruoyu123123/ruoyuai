"""save_state.py 的数据库 JSON 原子写契约测试。

`save_json` 必须走 `atomic_json.atomic_write_json`；替换失败时原文件保持完整，临时文件名
保持唯一，重复写入后仍能解析为合法 UTF-8 JSON。

这组测试钉死「写盘走原子路径、目标永不残留半截、崩溃不毁原文件」，**不碰任何状态保存业务逻辑**。
零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import atomic_json  # noqa: E402
import save_state  # noqa: E402
import save_state_common  # noqa: E402


def _no_stray_tmp_or_corrupt(dirpath: Path, target: Path):
    """目标是合法 JSON，且目录里没有残留 .tmp（原子写应清理本次 tmp）。"""
    json.loads(target.read_text(encoding="utf-8"))  # 解析失败 = 半截损坏
    strays = [p.name for p in dirpath.iterdir() if p.name.endswith(".tmp")]
    assert not strays, f"残留游离 tmp 文件: {strays}"


# ---------------------------------------------------------------- 原子写路径

def test_save_state_imports_atomic_json():
    """共享状态 IO 必须经由 required atomic_json 模块落盘。"""
    assert save_state_common.atomic_json is atomic_json


def test_save_json_routes_through_atomic_write_json():
    """save_json 写入必须实际调用 atomic_json.atomic_write_json（打桩确认调用路径）。"""
    called = {"target": None, "n": 0}
    orig = atomic_json.atomic_write_json

    def _spy(target, data, **kw):
        called["target"] = Path(target)
        called["n"] += 1
        return orig(target, data, **kw)

    atomic_json.atomic_write_json = _spy
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_数据库" / "伏笔表.json"
            save_state.save_json(p, {"promises": [], "中文": "ok"})
    finally:
        atomic_json.atomic_write_json = orig
    assert called["n"] == 1, "save_json 未走 atomic_write_json（绕过了原子路径）"
    assert called["target"] == p


def test_save_json_roundtrip_valid_json_no_stray_tmp():
    """save_json 写出合法 JSON（含中文不转义）、可覆盖重写、无游离 tmp。"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        p = db / "人物卡.json"
        data = {"characters": [{"name": "林潜", "growth_arc": []}]}
        save_state.save_json(p, data)
        _no_stray_tmp_or_corrupt(db, p)
        assert json.loads(p.read_text(encoding="utf-8")) == data
        # 覆盖写第二次（模拟 re-apply 幂等重跑）仍合法、内容是最后一次的
        data2 = {"characters": [{"name": "林潜", "growth_arc": [{"ch": 3, "state": "觉醒"}]}]}
        save_state.save_json(p, data2)
        _no_stray_tmp_or_corrupt(db, p)
        assert json.loads(p.read_text(encoding="utf-8")) == data2


def test_atomic_json_is_required_without_fallback_branch():
    source = Path(save_state_common.__file__).read_text(encoding="utf-8")
    assert "import atomic_json" in source
    assert "except ImportError" not in source
    assert "atomic_json is not None" not in source


# ---------------------------------------------------------------- 崩溃不毁原文件（P1-1 核心语义）

def test_crash_mid_write_preserves_original():
    """模拟进程在写 tmp 阶段被杀（atomic_write_json 抛异常·未到 replace）→ 原文件原样无损。"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        p = db / "伏笔表.json"
        original = {"promises": [{"id": "fs_001", "status": "open"}]}
        save_state.save_json(p, original)

        orig_fn = atomic_json.atomic_write_json

        def _boom(target, data, **kw):
            # 模拟写 tmp 写到一半进程被杀：留半截 tmp、永远到不了 os.replace
            tmp = Path(target).parent / f".{Path(target).name}.crash.tmp"
            tmp.write_text('{"promises": [{"id": "fs_0', encoding="utf-8")
            raise OSError("simulated kill mid-write")

        atomic_json.atomic_write_json = _boom
        try:
            try:
                save_state.save_json(p, {"promises": "GARBAGE"})
                assert False, "应向上抛 OSError（不静默吞）"
            except OSError:
                pass
        finally:
            atomic_json.atomic_write_json = orig_fn
        # 原子语义核心断言：目标文件完好、内容仍是崩溃前的版本（半截只在 tmp 里）
        assert json.loads(p.read_text(encoding="utf-8")) == original


# ---------------------------------------------------------------- 防回归 + 调用方覆盖

def test_no_bare_db_write_text_regression():
    """源码层防回归：save_state 不得再出现「目标文件裸 write_text」写 _数据库 JSON。"""
    import re
    src = (Path(save_state.__file__).read_text(encoding="utf-8")
           + Path(save_state_common.__file__).read_text(encoding="utf-8"))
    # 目标数据库文件不得绕过公共原子写入口。
    assert not re.search(r"(?<![\w.])p\.write_text\(json\.dumps", src), \
        "save_json 回归成裸 write_text"
    # 临时文件名不得固定，避免并发写交错。
    assert 'with_suffix(".json.tmp")' not in src, "回归成固定 tmp 名（并发会交错损坏）"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
