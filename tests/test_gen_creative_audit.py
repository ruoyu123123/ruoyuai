"""gen_creative 健壮性回归（2026-06-16 · triage worth_fixing 2 修）。

triage 抓出 gen_creative.py 两处确定性 IO/结构契约缺口（均不碰创作判断·北极星⑤）：

  · L749-752 outline_card 分支：只校验 `--skeleton` 非空，随后裸
    `json.loads(Path(args.skeleton).read_text())`——文件缺失→FileNotFoundError
    裸 traceback exit 1；内容非合法 JSON→JSONDecodeError 裸 traceback。
    姊妹 volume_arc(L495-501) 早有 Path.exists()+try/except OSError/JSONDecodeError
    优雅降级。修复 = 加存在性检查 + try/except，破损输入 → 干净 exit 2（不抛裸
    traceback·对 GUI 非技术用户友好），与 volume_arc 范式对齐。

  · L459-460 _emit_volume_arc_to_db：`[{**me, "status": me.get(...)} for me in
    data.get("major_events", [])]` 对每个元素无条件 `{**me}`/`me.get(...)`。
    gen-model 软约束（仅 responseMimeType·无 responseSchema）下可合法吐出
    ['ME1','ME2'] 或含 null → `{**'str'}`/`{**None}` → TypeError: object is not
    a mapping，且该 emit 在 3 次 parse 重试循环之后无 try/except 调用 → 直接崩
    建书单点调用。下游 cluster_emergence_engine.py:349 对同一非 dict ME 已有
    `if not isinstance(me, dict): continue` 守卫（2026-05-29 复审修复 L8）——证明
    此失败模式已知、消费端已补而生产端漏。修复 = 加 isinstance(me, dict) 过滤。

测试零依赖：tempfile 建临时目录 + 直接调 main()（捕 SystemExit）/直接调
_emit_volume_arc_to_db 纯函数。outline_card happy-path 走 --dry-run 不碰网络。
__main__ 跑全部 test_*。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import gen_creative as gc  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 沙箱 helper：跑 main() 捕 SystemExit / 非 SystemExit 异常向上抛（= bug 未修）
# ════════════════════════════════════════════════════════════════

def _run_main(argv: list[str]) -> int | None:
    """以给定 argv 跑 main()，捕 SystemExit 返回退出码；正常返回返 None。

    若 main() 抛非 SystemExit（FileNotFoundError/JSONDecodeError/TypeError 等）
    = 未优雅降级 = bug 未修，异常向上抛由调用测试 fail。
    """
    old_argv = sys.argv[:]
    sys.argv = argv
    try:
        gc.main()
        return None
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old_argv


# ════════════════════════════════════════════════════════════════
# Bug 1（L749-752）：outline_card --skeleton IO 健壮性
# ════════════════════════════════════════════════════════════════

def test_outline_card_missing_skeleton_flag_exit2():
    """不传 --skeleton → 干净 exit 2（前置契约·确认守卫仍在）。"""
    code = _run_main(["gen_creative.py", "--mode", "outline_card", "--count", "2"])
    assert code == 2, f"缺 --skeleton 应 exit 2，实得 {code}"


def test_outline_card_nonexistent_file_exit2_no_crash():
    """--skeleton 指向不存在的文件 → 修前 FileNotFoundError 裸 traceback exit 1；
    修后 Path.exists() 拦截 → 干净 exit 2，不抛 FileNotFoundError。"""
    with tempfile.TemporaryDirectory() as td:
        ghost = str(Path(td) / "不存在的骨架.json")
        # 若修未生效 → main() 在 json.loads(...read_text()) 抛 FileNotFoundError（非 SystemExit）
        # → _run_main 不捕 → 本测试因未捕异常而 fail（正是回归保护点）
        code = _run_main(["gen_creative.py", "--mode", "outline_card",
                          "--skeleton", ghost, "--count", "2"])
        assert code == 2, f"骨架文件不存在应 exit 2，实得 {code}"


def test_outline_card_malformed_json_exit2_no_crash():
    """--skeleton 文件内容非合法 JSON → 修前 json.JSONDecodeError 裸 traceback；
    修后 try/except 捕 → 干净 exit 2。"""
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "坏骨架.json"
        bad.write_text("{ 这不是合法 JSON ,,, ", encoding="utf-8")
        code = _run_main(["gen_creative.py", "--mode", "outline_card",
                          "--skeleton", str(bad), "--count", "2"])
        assert code == 2, f"骨架 JSON 破损应 exit 2，实得 {code}"


def test_outline_card_valid_skeleton_dry_run_ok():
    """合法骨架 + --dry-run → main() 正常返回（不抛异常·不调网络），
    确认修复未改坏 happy-path。dry-run 末尾 bare return → 退出码 None。"""
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "骨架.json"
        good.write_text(json.dumps(
            {"cards": [{"card_id": "A", "path_id": "p1"},
                       {"card_id": "B", "path_id": "p2"}]},
            ensure_ascii=False), encoding="utf-8")
        # --dry-run：解析骨架 → 组 prompt → 打印 → bare return（不调 gen-model）
        code = _run_main(["gen_creative.py", "--mode", "outline_card",
                          "--skeleton", str(good), "--count", "2", "--dry-run"])
        # dry-run 分支以 bare `return` 结束 main()（非 sys.exit）→ None
        assert code is None, f"合法骨架 dry-run 应正常返回(None)，实得 {code}"


# ════════════════════════════════════════════════════════════════
# Bug 2（L459-460）：_emit_volume_arc_to_db major_events isinstance 守卫
# ════════════════════════════════════════════════════════════════

def _emit(tmp: Path, data: dict) -> dict:
    """调 _emit_volume_arc_to_db 落盘并读回 大势卡.json（断言不崩 + 内容正确）。"""
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    p_major, p_cluster = gc._emit_volume_arc_to_db(proj, data, rhythm="", framework="")
    assert p_major.exists() and p_cluster.exists(), "大势卡/事件簇 未落盘"
    return json.loads(p_major.read_text(encoding="utf-8"))


def test_emit_volume_arc_filters_non_dict_major_events():
    """major_events 含裸串 / None / 整数（gen-model 软约束下合法可达）：
    修前 `{**'str'}` TypeError: object is not a mapping → 崩建书单点调用；
    修后 isinstance(me, dict) 过滤掉坏元素，只投影合法 dict ME。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {"final_image": "末法最后一人"},
            "volumes": [{"vol": 1, "title": "卷一"}],
            "major_events": [
                {"id": "ME-V1-01", "volume": 1, "summary": "开局"},
                "我是一个不该出现的裸串 ME",   # 非 dict → 必须被过滤
                None,                          # 非 dict → 必须被过滤
                42,                            # 非 dict → 必须被过滤
                {"id": "ME-V1-02", "volume": 1, "status": "active"},
            ],
            "cluster_001": {"scope_summary": "倒叙开场"},
        }
        major = _emit(Path(td), data)   # 不抛 TypeError 即过 bug2
        mes = major["major_events"]
        # 只剩 2 条合法 dict（裸串/None/int 被剔除）
        assert len(mes) == 2, f"非 dict ME 未被过滤，实得 {len(mes)} 条: {mes}"
        ids = {m["id"] for m in mes}
        assert ids == {"ME-V1-01", "ME-V1-02"}
        # 缺 status 的补 pending；自带 status 的保留
        by_id = {m["id"]: m for m in mes}
        assert by_id["ME-V1-01"]["status"] == "pending"
        assert by_id["ME-V1-02"]["status"] == "active"


def test_emit_volume_arc_all_dict_major_events_preserved():
    """控制组：全 dict major_events → 全部保留 + status 默认 pending（守卫不误伤正常路径）。"""
    with tempfile.TemporaryDirectory() as td:
        data = {
            "story_destiny": {},
            "volumes": [],
            "major_events": [
                {"id": "ME-A"},
                {"id": "ME-B", "status": "done"},
                {"id": "ME-C"},
            ],
            "cluster_001": {},
        }
        major = _emit(Path(td), data)
        mes = major["major_events"]
        assert len(mes) == 3, f"合法 ME 被误删: {mes}"
        st = {m["id"]: m["status"] for m in mes}
        assert st == {"ME-A": "pending", "ME-B": "done", "ME-C": "pending"}


def test_emit_volume_arc_empty_and_missing_major_events():
    """major_events 缺失 / 为空列表 → 投影空列表，不崩（边界）。"""
    with tempfile.TemporaryDirectory() as td:
        m1 = _emit(Path(td) / "a", {"major_events": []})
        assert m1["major_events"] == []
    with tempfile.TemporaryDirectory() as td:
        m2 = _emit(Path(td) / "b", {})   # 无 major_events 键 → .get 默认 []
        assert m2["major_events"] == []


# ════════════════════════════════════════════════════════════════
# import 守卫
# ════════════════════════════════════════════════════════════════

def test_module_imports():
    """模块可 import（2 修后无语法/引用错）。"""
    assert hasattr(gc, "main")
    assert hasattr(gc, "_emit_volume_arc_to_db")


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
    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
