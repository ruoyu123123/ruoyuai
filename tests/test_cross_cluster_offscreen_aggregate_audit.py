"""cross_cluster_offscreen_aggregate 健壮性回归（2026-06-16 · triage worth_fixing 4 修）。

守护「幕后行动落地检查」的 legacy（逐章·磁盘）分支——它是**唯一的运行时路径**
（账本 offscreen 字段无任何 producer → ledger_has_field('offscreen') 恒 False，
账本分支是死代码）。该分支读 writer 自由产出的 _changes.json self_eval +
manifest.active_offscreen_actions + 人物卡 offscreen.actions，全是弱 LLM 不受 schema
约束的输入。triage 抓出 4 处 legacy 分支缺守卫（账本孪生分支早已守）：

  · L211/L216  active_offscreen_actions==null / self_eval==null
                → dict 默认只在「键缺失」生效、「键存在但值 null」不生效
                → .get 链 AttributeError（'NoneType' has no attribute 'get'）；
  · L225-226   expected_chars/executed_chars 推导无 isinstance(.,dict) 守卫
                → executed 里混入裸串/None 元素 → AttributeError；
  · L237/L283  action 切片无 `or ''` 兜底 → {'action': null} → None[:60] TypeError；
  · L243       legacy check-2 `for ex in executed` 无 isinstance 守卫
                → 同 L225 数据源的纵深补防。

修复 = 把 legacy 分支与账本分支（L102-104 / L114 / L119-121）拉齐：缺守卫的输入
不再让整个 advisory scanner 崩成 [CRASH]，而是优雅跳过 / 兜底。北极星⑤：纯
确定性数据投影的健壮性，不碰创作判断、不升 hard_gate。

测试零依赖：tempfile 建假项目 + monkeypatch IS_CLUSTER_MODE=False 强制走 legacy
分支，直接调 main()（捕 SystemExit），断言「不抛 AttributeError/TypeError」+
报告文件落地 + per_chapter 计数正确。__main__ 跑全部 test_*。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_offscreen_aggregate as cca  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 沙箱：假项目（章节/第001章/{.txt,_changes.json} + _数据库/.manifest + 人物卡）
# ════════════════════════════════════════════════════════════════

def _build_project(tmp: Path, *, manifest: dict, changes: dict,
                   chars: dict | None = None, body: str = "正文内容。\n") -> Path:
    """造一本只有 ch1 的假书，写 manifest + _changes + 人物卡。返回 project_root。"""
    proj = tmp / "proj"
    ch_dir = proj / "章节" / "第001章"
    ch_dir.mkdir(parents=True)
    (ch_dir / "第001章.txt").write_text(body, encoding="utf-8")
    (ch_dir / "第001章_changes.json").write_text(
        json.dumps(changes, ensure_ascii=False), encoding="utf-8")

    mani_dir = proj / "_数据库" / ".manifest"
    mani_dir.mkdir(parents=True)
    (mani_dir / "ch_001.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps(chars if chars is not None else {"characters": []},
                   ensure_ascii=False), encoding="utf-8")
    return proj


def _run_legacy_main(proj: Path) -> int:
    """monkeypatch IS_CLUSTER_MODE=False 强制 legacy 分支，跑 main() 捕 SystemExit。

    返回退出码。若 main() 抛非 SystemExit（AttributeError/TypeError 等）= scanner 崩 = bug 未修，
    异常向上抛由调用测试 fail。
    """
    old_flag = cca.IS_CLUSTER_MODE
    old_argv = sys.argv[:]
    cca.IS_CLUSTER_MODE = False
    sys.argv = ["cross_cluster_offscreen_aggregate.py", str(proj), "--last-n", "5"]
    try:
        cca.main()
        return 0  # main 正常未必 sys.exit（理论上总会），兜底
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        cca.IS_CLUSTER_MODE = old_flag
        sys.argv = old_argv


def _latest_report(proj: Path) -> dict:
    """读最近一次 offscreen 报告 JSON（断言 scanner 真跑完、产出落盘）。"""
    scan_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(scan_dir.glob("offscreen_*.json"))
    assert reports, f"scanner 未产出报告（很可能中途崩溃）: {scan_dir}"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════════
# Bug 1（L211/L216）：active_offscreen_actions==null / self_eval==null
# ════════════════════════════════════════════════════════════════

def test_null_manifest_actions_and_null_self_eval_no_crash():
    """manifest.active_offscreen_actions=null + _changes.self_eval=null（键存在值为 null）
    → 修前 .get 链 AttributeError；修后 `or {}` / `or []` 兜底，scanner 不崩、零 finding。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={"active_offscreen_actions": None},   # 键存在但 null
            changes={"self_eval": None},                    # 键存在但 null
        )
        code = _run_legacy_main(proj)  # 不抛异常即过 bug1
        assert code == 0, f"无 finding 应 exit 0，实得 {code}"
        rep = _latest_report(proj)
        assert rep["findings"] == []
        assert rep["per_chapter"]["1"]["expected_count"] == 0
        assert rep["per_chapter"]["1"]["executed_count"] == 0


def test_self_eval_key_present_offscreen_field_null():
    """self_eval 在但 offscreen_actions_executed=null → executed 须兜成 []，不崩。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={},  # 键缺失（dict 默认本就生效，对照组）
            changes={"self_eval": {"offscreen_actions_executed": None}},
        )
        code = _run_legacy_main(proj)
        assert code == 0
        assert _latest_report(proj)["per_chapter"]["1"]["executed_count"] == 0


# ════════════════════════════════════════════════════════════════
# Bug 2（L225-226）+ Bug 4（L243）：executed/expected 含非 dict 元素
# ════════════════════════════════════════════════════════════════

def test_executed_contains_non_dict_elements_no_crash():
    """writer self_eval 数组混入裸串 / null 元素：
       · L225-226 expected_chars/executed_chars 推导 → 修前 AttributeError；
       · L243 check-2 `for ex in executed` → 修前 ex.get 再崩。
       修后两处 isinstance 守卫跳过坏元素，只对合法 dict 计数。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={"active_offscreen_actions": [
                {"character": "老周", "action": "暗中调表"},
                "我是一个不该出现的裸串",   # 非 dict → 必须被 isinstance 跳过
                None,
            ]},
            changes={"self_eval": {"offscreen_actions_executed": [
                {"character": "老周", "evidence": "正文里老周拨动了座钟指针作证据。",
                 "completed_fully": True},
                "坏元素裸串",   # 非 dict
                None,
            ]}},
            chars={"characters": [{"name": "老周", "id": "laozhou",
                                   "name_aliases": ["周叔"]}]},
            body="老周拨动了座钟指针。\n",  # 含 alias「老周」→ 不触发 EXECUTION_LIE
        )
        code = _run_legacy_main(proj)
        rep = _latest_report(proj)
        # 计数只算合法 dict（各 1 条），裸串/None 被守卫剔除
        assert rep["per_chapter"]["1"]["expected_count"] == 3  # len 不过滤（原行为）
        assert rep["per_chapter"]["1"]["executed_count"] == 3
        assert rep["per_chapter"]["1"]["expected_chars"] == ["老周"]
        assert rep["per_chapter"]["1"]["executed_chars"] == ["老周"]
        # 合法条 evidence 充足 + alias 在正文 → 无 EVIDENCE_THIN / EXECUTION_LIE
        codes = {f["code"] for f in rep["findings"]}
        assert "OFFSCREEN_EVIDENCE_THIN" not in codes
        assert "OFFSCREEN_EXECUTION_LIE" not in codes
        assert code in (0, 1, 2)  # 关键是没崩；本例预期无 finding → 0


def test_non_dict_executed_does_not_block_legit_findings():
    """坏元素与合法条混存时，合法条的 advisory（evidence 太短）仍照常产出（守卫只跳坏元素不吞合法）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={},
            changes={"self_eval": {"offscreen_actions_executed": [
                42,  # 非 dict
                {"character": "林晚秋", "evidence": "短", "completed_fully": False},
            ]}},
            chars={"characters": [{"name": "林晚秋", "id": "lin"}]},
        )
        code = _run_legacy_main(proj)
        rep = _latest_report(proj)
        codes = [f["code"] for f in rep["findings"]]
        assert "OFFSCREEN_EVIDENCE_THIN" in codes, f"合法条 advisory 被吞: {codes}"
        assert code == 1  # 仅 advisory → exit 1


# ════════════════════════════════════════════════════════════════
# Bug 3（L237 / L283）：action==null → 切片 None[:60] TypeError
# ════════════════════════════════════════════════════════════════

def test_expected_action_null_no_crash_l237():
    """manifest action=null + executed 空 → 触发 OFFSCREEN_ACTIONS_NOT_EXECUTED 分支，
    其 expected_actions 推导切 action[:60]。修前 None[:60] TypeError，修后 `or ''` 兜底。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={"active_offscreen_actions": [
                {"character": "顾沉", "action": None},  # 键在值 null
            ]},
            changes={"self_eval": {"offscreen_actions_executed": []}},  # 空 → 触发检查 1
            chars={"characters": [{"name": "顾沉", "id": "gu"}]},
        )
        code = _run_legacy_main(proj)
        rep = _latest_report(proj)
        nf = [f for f in rep["findings"] if f["code"] == "OFFSCREEN_ACTIONS_NOT_EXECUTED"]
        assert nf, "应报 OFFSCREEN_ACTIONS_NOT_EXECUTED"
        # action 被兜成空串（不是 None），切片不崩
        assert nf[0]["expected_actions"][0]["action"] == ""
        assert code == 2  # warning → exit 2


def test_backlog_action_null_no_crash_l283():
    """人物卡 offscreen.actions[].action=null + ch_range 已过期未 done → backlog 分支切 action[:60]。
    修前 None[:60] TypeError，修后 `or ''` 兜底。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={},
            changes={"self_eval": {"offscreen_actions_executed": []}},
            chars={"characters": [{
                "name": "反派甲", "id": "villain", "role": "反派",
                "offscreen": {"actions": [
                    {"action": None, "ch_range": [0, 0], "done": False},  # action null + 已过期
                ]},
            }]},
        )
        # 唯一章是 ch1 → ch_range[1]=0 < 1 且未 done → 进 backlog；走到 act.get('action','')[:60]
        code = _run_legacy_main(proj)
        rep = _latest_report(proj)
        bk = [f for f in rep["findings"] if f["code"] == "OFFSCREEN_BACKLOG"]
        assert bk, "应报 OFFSCREEN_BACKLOG"
        assert bk[0]["backlog"][0]["action"] == ""  # None 兜成空串，切片未崩
        assert code == 1  # backlog 为 advisory → exit 1


# ════════════════════════════════════════════════════════════════
# 控制组：happy-path 未被改坏（守卫不改变正常语义）
# ════════════════════════════════════════════════════════════════

def test_happy_path_clean_no_findings():
    """全合法输入 + alias 命中正文 → 零 finding、exit 0（确认 4 处守卫不误伤正常路径）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _build_project(
            Path(td),
            manifest={"active_offscreen_actions": [
                {"character": "老周", "action": "暗中布局"},
            ]},
            changes={"self_eval": {"offscreen_actions_executed": [
                {"character": "老周",
                 "evidence": "正文中老周在后院布下了暗哨，读者可见其行动痕迹。",
                 "completed_fully": True},
            ]}},
            chars={"characters": [{"name": "老周", "id": "laozhou"}]},
            body="老周在后院布下了暗哨。\n",
        )
        code = _run_legacy_main(proj)
        rep = _latest_report(proj)
        assert rep["findings"] == [], f"happy-path 不应有 finding: {rep['findings']}"
        assert code == 0


def test_module_imports():
    """模块可 import（4 修后无语法/引用错）。"""
    assert hasattr(cca, "main")
    assert hasattr(cca, "_scan_from_ledger")


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
