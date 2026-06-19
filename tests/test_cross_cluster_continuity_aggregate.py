#!/usr/bin/env python3
"""cross_cluster_continuity_aggregate.py 专属确定性回归测试（零 LLM / 零联网）。

补 test_continuity_protagonist.py 的盲区——后者只钉 extract_keywords / get_protagonist /
_build_aliases / scan_object_continuity（去硬编码主角名+物件名）。本文件钉**尚未覆盖**的
4 维衔接核心算法 + 主流程退出码：

- scan_cliffhanger_resonance        —— 维度 1 正文路径（关键词重叠分 / DCAS exempt / 缺字段哨兵）
- scan_cliffhanger_resonance_ledger —— 维度 1 账本路径（取预算分 / exempt / 缺字段）
- scan_time_gap                     —— 维度 2 周日期跳跃（≥2 天才报 / 缺 changes）
- read_emotion / scan_emotion_gap   —— 维度 4 情绪断层（diff≥5 才 detected / 账本优先 WAL 回退）
- find_chapter_dirs / read_chapter_text / read_changes / load_json —— IO 探测
- main()                            —— CLI 端到端（报告落盘 + pairwise + 退出码 0/1）

main() 含 sys.exit，走 subprocess 跑真 CLI（参照 test_cross_cluster_fate_drift_aggregate）。
铁律：真 import 真调用被测函数，绝不 mock 被测逻辑。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import cross_cluster_continuity_aggregate as cc  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_continuity_aggregate.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_chapter(proj: Path, ch: int, text: str, changes: dict | None = None,
                   pre_opening: bool = False) -> Path:
    """造一个 第NNN章 目录：正文 txt + _changes.json（+ 可选 .pre_opening 标记）。"""
    d = proj / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{ch:03d}章.txt").write_text(text, encoding="utf-8")
    if changes is not None:
        (d / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")
    if pre_opening:
        (d / ".pre_opening.txt").write_text("x", encoding="utf-8")
    return d


def _write_wal_summary(proj: Path, ch: int, emotion_value):
    wal = proj / "_数据库" / ".wal"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / f"第{ch:03d}章_summary.json").write_text(
        json.dumps({"emotion": {"value": emotion_value}}, ensure_ascii=False),
        encoding="utf-8")


def _run_cli(proj: Path, *args):
    """跑真 CLI（默认逐章 glob 路径，无 CLUSTER_MODE env → 账本路径不触发）。

    子进程 stdout 走 Windows 控制台编码，errors='replace' 容错；断言只锚 returncode +
    报告文件（报告显式 utf-8 落盘，是确定性权威输出）。
    """
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _latest_report(proj: Path) -> dict:
    out_dir = proj / "_数据库" / ".cross_chapter_scan"
    reports = sorted(out_dir.glob("continuity_*.json"))
    assert reports, "未生成 continuity 报告"
    return json.loads(reports[-1].read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# load_json / IO 探测
# ══════════════════════════════════════════════════════════════════════════
def test_load_json_missing_and_malformed_return_default():
    """缺文件 → default；坏 JSON → default（不抛）；合法 → 解析。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        assert cc.load_json(p / "nope.json", default="X") == "X"
        bad = p / "bad.json"
        bad.write_text("{not json,,,", encoding="utf-8")
        assert cc.load_json(bad, default=None) is None
        good = p / "good.json"
        good.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert cc.load_json(good) == {"a": 1}


def test_find_chapter_dirs_sorted_and_filtered():
    """只认 第N章 目录，按章号升序；非章目录被忽略。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_chapter(proj, 3, "三")
        _write_chapter(proj, 1, "一")
        _write_chapter(proj, 12, "十二")
        (proj / "章节" / "草稿区").mkdir(parents=True)  # 噪声目录
        dirs = cc.find_chapter_dirs(proj)
        assert [ch for ch, _ in dirs] == [1, 3, 12], f"应升序且过滤非章目录: {dirs}"


def test_read_chapter_text_and_changes():
    """正文/changes 读取。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        ch_dir = _write_chapter(proj, 5, "正文内容五",
                                changes={"self_eval": {"k": "v"}})
        assert cc.read_chapter_text(ch_dir, 5) == "正文内容五"
        assert cc.read_changes(ch_dir, 5) == {"self_eval": {"k": "v"}}
        ch_dir2 = _write_chapter(proj, 6, "六", changes=None)
        assert cc.read_changes(ch_dir2, 6) is None


# ══════════════════════════════════════════════════════════════════════════
# 维度 1: cliffhanger resonance（正文 + 账本）
# ══════════════════════════════════════════════════════════════════════════
def test_cliffhanger_resonance_overlap_score():
    """ending_line 关键词在后章首段重叠 → 正分；纯算重叠比。

    🔴 锁真实分词契约：extract_keywords 的 [一-鿿]{2,} 贪婪吞掉**整段连续中文**为单 token，
    故只有「同一段连续中文 run」在前后章一致才算命中——ending 词需被标点隔成独立 run 才能
    在后章首段以同形 run 复现（这是 cliffhanger 评分粗粒度的根因）。
    """
    with tempfile.TemporaryDirectory() as d:
        ch_dir = _write_chapter(_mk_project(Path(d)), 2, "占位")  # 仅借目录
        prev = {"self_eval": {"applied_style": {
            "ending_type": "钩子", "ending_line": "建木枝，深渊"}}}
        # 后章首段以同形独立 run 回应 ending 关键词
        next_text = "建木枝，又一次断裂。深渊，仍在脚下张开。"
        r = cc.scan_cliffhanger_resonance(prev, next_text, ch_dir)
        assert r["score"] > 0.0, f"应有正重叠: {r}"
        assert "建木枝" in r["overlap_keywords"] and "深渊" in r["overlap_keywords"]


def test_cliffhanger_resonance_exempt_and_missing():
    """悬念断章 → exempt 满分；缺 ending_line / 缺 prev_changes → -1 哨兵。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        plain_dir = _write_chapter(proj, 3, "y")
        prev2 = {"self_eval": {"applied_style": {"ending_type": "悬念断章", "ending_line": "x"}}}
        assert cc.scan_cliffhanger_resonance(prev2, "无关", plain_dir).get("exempt") is True
        # 缺 prev_changes → -1
        assert cc.scan_cliffhanger_resonance({}, "任意", plain_dir)["score"] == -1
        # 有 changes 但无 ending_line → -1
        prev3 = {"self_eval": {"applied_style": {"ending_type": "钩子", "ending_line": ""}}}
        assert cc.scan_cliffhanger_resonance(prev3, "任意", plain_dir)["score"] == -1


def test_cliffhanger_resonance_ledger_path():
    """账本预算了 cliffhanger_resonance_next → 直接取用 round 后的分；缺字段 → -1；exempt 优先。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        plain_dir = _write_chapter(proj, 2, "x")
        rec = {"cliffhanger_resonance_next": 0.876, "ending_type": "钩子", "ending_line": "断木"}
        r = cc.scan_cliffhanger_resonance_ledger(rec, plain_dir)
        assert r["score"] == 0.88, f"应 round(0.876,2)=0.88: {r}"
        # 缺预算字段 → -1
        assert cc.scan_cliffhanger_resonance_ledger({}, plain_dir)["score"] == -1
        # 悬念断章 → exempt 满分
        rec2 = {"ending_type": "悬念断章"}
        assert cc.scan_cliffhanger_resonance_ledger(rec2, plain_dir)["score"] == 1.0


# ══════════════════════════════════════════════════════════════════════════
# 维度 2: 时间跳跃
# ══════════════════════════════════════════════════════════════════════════
def test_scan_time_gap_detects_weekday_jump():
    """周一 → 周四 = 3 天跳跃（≥2）→ detected。"""
    prev = {"factual": {"time_advance": {"key_events": ["周一清晨出发"]}}}
    nxt = {"factual": {"time_advance": {"key_events": ["周四傍晚归来"]}}}
    r = cc.scan_time_gap(prev, nxt)
    assert r["detected"] is True and r["gap_days"] == 3, f"周一→周四=3天: {r}"


def test_scan_time_gap_small_jump_and_missing():
    """周一→周二=1 天（<2）不报；缺 changes 直接 not detected。"""
    prev = {"factual": {"time_advance": {"period": "周一"}}}
    nxt = {"factual": {"time_advance": {"period": "周二"}}}
    assert cc.scan_time_gap(prev, nxt)["detected"] is False, "1 天跳跃不该报"
    # 缺一侧 changes → not detected
    assert cc.scan_time_gap({}, nxt)["detected"] is False
    assert cc.scan_time_gap(prev, {})["detected"] is False


# ══════════════════════════════════════════════════════════════════════════
# 维度 4: 情绪断层
# ══════════════════════════════════════════════════════════════════════════
def test_read_emotion_from_wal_summary():
    """read_emotion 从 .wal/第NNN章_summary.json 取 emotion.value；缺文件 → None。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_wal_summary(proj, 4, 7)
        assert cc.read_emotion(proj, 4) == 7
        assert cc.read_emotion(proj, 99) is None  # 无 summary


def test_read_emotion_scalar_form_no_crash():
    """🔴 2026-06-17 回归：emotion 为裸标量(int/float)时 read_emotion 不崩、返回标量值。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        wal = proj / "_数据库" / ".wal"
        wal.mkdir(parents=True, exist_ok=True)
        (wal / "第005章_summary.json").write_text(
            json.dumps({"emotion": 6}, ensure_ascii=False), encoding="utf-8")
        assert cc.read_emotion(proj, 5) == 6  # 标量直接返回·不崩


def test_scan_emotion_gap_threshold_and_ledger_priority():
    """diff≥5 才 detected；账本 emotion_value 优先于 WAL；任一缺 → not detected。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_wal_summary(proj, 1, 2)
        _write_wal_summary(proj, 2, 8)  # WAL diff = 6 ≥5
        r = cc.scan_emotion_gap(proj, 1, 2)
        assert r["detected"] is True and r["diff"] == 6, f"WAL 路径 diff=6: {r}"
        # diff = 4 < 5 → not detected
        _write_wal_summary(proj, 3, 6)
        assert cc.scan_emotion_gap(proj, 1, 3)["detected"] is False, "diff=4 不该报"
        # 账本优先：ledger 给 e1=1,e2=9 → diff=8（覆盖 WAL 的 2/8）
        ledger = {1: {"emotion_value": 1}, 2: {"emotion_value": 9}}
        r2 = cc.scan_emotion_gap(proj, 1, 2, ledger_by_ch=ledger)
        assert r2["detected"] is True and r2["diff"] == 8, f"账本优先 diff=8: {r2}"
        # 缺 summary → not detected
        assert cc.scan_emotion_gap(proj, 1, 50)["detected"] is False


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 真 argparse + 真报告落盘 + 退出码
# ══════════════════════════════════════════════════════════════════════════
def test_cli_fewer_than_two_chapters_exits_0():
    """章节 <2 → [OK] 无衔接可扫 → exit 0，不生成报告。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_chapter(proj, 1, "唯一一章")
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        assert not (proj / "_数据库" / ".cross_chapter_scan").exists()


def test_cli_missing_project_dir_exits_2():
    """项目目录不存在 → [FATAL] → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        ghost = Path(d) / "不存在的项目"
        p = _run_cli(ghost)
        assert p.returncode == 2, f"stdout={p.stdout} stderr={p.stderr}"


def test_cli_clean_continuity_exits_0_with_report():
    """两章衔接健康（cliffhanger 回应 + 无时间/情绪断层）→ 0 findings → exit 0 + 报告落盘。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_chapter(proj, 1, "建木枝，在他手中断裂。深渊，在脚下张开。",
                       changes={"self_eval": {"applied_style": {
                           "ending_type": "钩子", "ending_line": "建木枝，深渊"}},
                           "factual": {"time_advance": {"period": "周一"}}})
        _write_chapter(proj, 2, "建木枝，又一次断裂。深渊，吞下了他的犹豫。",
                       changes={"self_eval": {"applied_style": {}},
                                "factual": {"time_advance": {"period": "周一"}}})
        p = _run_cli(proj)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        report = _latest_report(proj)
        assert report["scan_type"] == "continuity"
        assert report["chapters_scanned"] == [1, 2]
        assert len(report["pairwise"]) == 1
        assert report["summary"]["warning"] == 0


def test_cli_time_jump_warning_branch():
    """周一→周四时间跳跃 + 后章无过渡说明 → TIME_JUMP_UNEXPLAINED。

    🔴 锁真实分级契约：该 finding source 里 severity="warning"（gate_level="advisory"）——
    severity=warning 即触发 main 末尾的 exit 1（退出码看 severity 非 gate_level）。
    cliffhanger 用 pre_opening exempt 隔离出纯时间跳跃 finding。
    """
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        _write_chapter(proj, 1, "第一章正文。",
                       changes={"self_eval": {"applied_style": {
                           "ending_type": "钩子", "ending_line": "占位"}},
                           "factual": {"time_advance": {"key_events": ["周一出发"]}}})
        _write_chapter(proj, 2, "第二章正文，毫无关联。",
                       changes={"self_eval": {"applied_style": {}},
                                "factual": {"time_advance": {
                                    "key_events": ["周四抵达"], "plot_nodes": []}}},
                       pre_opening=True)
        p = _run_cli(proj)
        report = _latest_report(proj)
        codes = [f["code"] for f in report["findings"]]
        assert "TIME_JUMP_UNEXPLAINED" in codes, f"应报时间跳跃: {report['findings']}"
        tj = next(f for f in report["findings"] if f["code"] == "TIME_JUMP_UNEXPLAINED")
        assert tj["severity"] == "warning" and tj["gate_level"] == "advisory"
        assert tj["metric"]["gap_days"] == 3
        # severity=warning → exit 1
        assert p.returncode == 1, f"stdout={p.stdout}"


def test_cli_object_continuity_warning_exits_1():
    """关键物件 ch1 获得后长期未提及（gap≥5）→ OBJECT_CONTINUITY_BROKEN warning → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d))
        # ch1 转移「凿窍刀」给主角，之后所有章绝不提及任何别名 → gap 累积
        _write_chapter(proj, 1, "他握住了那柄武器。",
                       changes={"self_eval": {"applied_style": {}},
                                "factual": {"item_transfers": [{"item": "凿窍刀"}]}})
        for ch in range(2, 8):
            _write_chapter(proj, ch, f"第{ch}章与那柄武器无关的日常。",
                           changes={"self_eval": {"applied_style": {}}, "factual": {}})
        p = _run_cli(proj)
        report = _latest_report(proj)
        obj = [f for f in report["findings"] if f["code"] == "OBJECT_CONTINUITY_BROKEN"]
        assert obj, f"应报物件断层: {report['findings']}"
        assert obj[0]["severity"] == "warning", f"gap≥5 应 warning: {obj[0]}"
        assert obj[0]["metric"]["item"] == "凿窍刀"
        # 有 warning → exit 1
        assert p.returncode == 1, f"stdout={p.stdout}"


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
