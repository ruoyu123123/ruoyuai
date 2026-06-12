"""save_state.py 原子写回归测试 — 守护缺漏报告 P1-1「_数据库 JSON 裸 write_text 半截损坏」。

save_state.py 的 save_json 是伏笔表/人物卡/进度/地图/时间线/道具 等 _数据库 JSON 的
唯一写出口。旧实现裸 write_text：进程写一半被杀 → 留半截 JSON → 下个读者
json.JSONDecodeError → load_json 兜底成 default → 整库静默清空（数据损坏面最大的一处）。

修复（2026-06-12 缺漏修复批次1 任务C）：
  - save_json 改走 atomic_json.atomic_write_json（tmp 唯一名 pid+uuid + fsync + os.replace 原子替换）。
  - ImportError 兜底手写唯一名 tmp + os.replace（照 cluster_choice_apply.py:89-96 范式·
    不沿用固定 .json.tmp 反模式）。
  - cmd_ecas_checkpoint 的 final.json（也在 _数据库 下）裸 write_text 同步改走 save_json。

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


def _no_stray_tmp_or_corrupt(dirpath: Path, target: Path):
    """目标是合法 JSON，且目录里没有残留 .tmp（原子写应清理本次 tmp）。"""
    json.loads(target.read_text(encoding="utf-8"))  # 解析失败 = 半截损坏
    strays = [p.name for p in dirpath.iterdir() if p.name.endswith(".tmp")]
    assert not strays, f"残留游离 tmp 文件: {strays}"


# ---------------------------------------------------------------- 原子写路径

def test_save_state_imports_atomic_json():
    """save_state 必须经由 atomic_json 落盘（不再裸 write_text）——模块身份钉死。"""
    assert save_state.atomic_json is atomic_json


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


def test_save_json_fallback_without_atomic_json():
    """atomic_json 不可导入（=None）时兜底路径：手写唯一名 tmp + os.replace 仍正确落盘。"""
    orig = save_state.atomic_json
    save_state.atomic_json = None
    try:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "_数据库"
            p = db / "进度.json"
            save_state.save_json(p, {"completed": 3, "current": 4})
            _no_stray_tmp_or_corrupt(db, p)
            assert json.loads(p.read_text(encoding="utf-8"))["completed"] == 3
            # 兜底路径覆盖写也不残留 tmp
            save_state.save_json(p, {"completed": 4, "current": 5})
            _no_stray_tmp_or_corrupt(db, p)
    finally:
        save_state.atomic_json = orig


# ---------------------------------------------------------------- 崩溃不毁原文件（P1-1 核心语义）

def test_crash_mid_write_preserves_original():
    """模拟进程在写 tmp 阶段被杀（atomic_write_json 抛异常·未到 replace）→ 原文件原样无损。"""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "_数据库"
        p = db / "伏笔表.json"
        original = {"promises": [{"id": "fs_001", "resolved": False}]}
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
    src = Path(save_state.__file__).read_text(encoding="utf-8")
    # 旧反模式 1：save_json 直接 p.write_text(json.dumps(...))（缺漏报告 :60 点名）。
    # 注意词边界：兜底路径的 tmp.write_text(json.dumps 是合法的（写 tmp 非目标），
    # 裸子串 "p.write_text" 会误匹配 "tmp.write_text" 尾部 → 用 \b 钉死变量名恰为 p。
    assert not re.search(r"(?<![\w.])p\.write_text\(json\.dumps", src), \
        "save_json 回归成裸 write_text"
    # 旧反模式 2：cmd_ecas_checkpoint 的 final.json 裸 write_text（原 :754）
    assert "final_path.write_text" not in src, "ecas final.json 回归成裸 write_text"
    # 旧反模式 3：固定 .json.tmp 名（并发交错损坏·见 test_self_heal_atomic 同款断言）
    assert 'with_suffix(".json.tmp")' not in src, "回归成固定 tmp 名（并发会交错损坏）"


def test_ecas_checkpoint_final_json_via_save_json():
    """cmd_ecas_checkpoint 的 final.json 必须经 save_json（原子路径）落盘。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        called = []
        orig = save_state.save_json

        def _spy(p, data):
            called.append(Path(p))
            return orig(p, data)

        save_state.save_json = _spy
        try:
            rc = save_state.cmd_ecas_checkpoint(root, "cluster_001")
        finally:
            save_state.save_json = orig
        # 空项目必 FAIL（draft/事件簇缺失）→ rc=1，但 final.json 仍要写出（汇总落盘不豁免）
        assert rc == 1
        final = root / "_数据库" / ".ecas_checkpoints" / "cluster_001_final.json"
        assert final.is_file(), "final.json 未写出"
        assert final in called, "final.json 未走 save_json 原子路径"
        _no_stray_tmp_or_corrupt(final.parent, final)
        # 内容可解析且结构正确
        result = json.loads(final.read_text(encoding="utf-8"))
        assert result["cluster_id"] == "cluster_001"
        assert result["passed"] is False


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
