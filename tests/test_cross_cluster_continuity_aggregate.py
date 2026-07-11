#!/usr/bin/env python3
"""cross_cluster_continuity_aggregate.py 专属确定性回归测试（零 LLM / 零联网）。

覆盖 4 维衔接核心算法、NN bridge 和主流程退出码：

- scan_cliffhanger_resonance        —— 维度 1 正文路径（关键词重叠分 / exempt / 缺字段哨兵）
- scan_cliffhanger_resonance_ledger —— 维度 1 账本路径（取预算分 / exempt / 缺字段）
- scan_time_gap                     —— 维度 2 周日期跳跃（≥2 天才报 / 缺 changes）
- read_emotion / scan_emotion_gap   —— 维度 4 情绪断层（diff≥5 才 detected / 账本优先 WAL 回退）
- find_chapter_dirs / read_chapter_text / read_changes / load_json —— IO 探测
- main()                            —— CLI 端到端（报告落盘 + pairwise + 退出码 0/1）

main() 含 sys.exit，走 subprocess 跑真 CLI（参照 test_cross_cluster_fate_drift_aggregate）。
铁律：真 import 真调用被测函数，绝不 mock 被测逻辑。

NN 连贯性模型覆盖：
- 维度 1/2 新增 model_result 参数的函数级测试——直接传字典做依赖注入，不 mock 任何逻辑
  （model_result 本身就是显式设计的注入点，None=零回归回退路径）
- 维度 3（物件持续性）评估后不模型化，只加 source="heuristic" 字段，补一条回归锁
- _load_coherence_bridge / _predict_pairs_safe / _valid_pair_result / _batch_model_results
  桥接管道单测
- main() 批量预算完整链路：用 mock.patch("nn_coherence_bridge.enabled"/"predict_pairs", ...)
  替换外部 NN 模型后端；被测逻辑本身保持真实调用
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

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


# ══════════════════════════════════════════════════════════════════════════
# 2026-07-01 NN 连贯性模型接入（scanner-NN 升级批）
#
# 维度 1（cliffhanger）+ 维度 2（时间跳跃过渡）新增 model_result 参数：优先模型分/判定，
# None（模型未启用/不可用）→ 逐字节回退上面已钉死的确定性逻辑（零回归）。model_result 是
# 显式依赖注入点，下面大多数测试直接传字典构造，不需要 mock 任何模块。
# 维度 3（物件持续性）评估结论：本质是「实体是否被提及」的存在性核对，非「衔接自然度」，
# coherence 模型不适配，保留确定性别名匹配，只补 source="heuristic" 字段。
# ══════════════════════════════════════════════════════════════════════════

def test_cliffhanger_resonance_model_result_overrides_keyword_overlap():
    """model_result 命中 → 直接用模型分，跳过关键词重叠计算；overlap_keywords 清空。"""
    with tempfile.TemporaryDirectory() as d:
        ch_dir = _write_chapter(_mk_project(Path(d)), 2, "占位")
        prev = {"self_eval": {"applied_style": {"ending_type": "钩子", "ending_line": "建木枝，深渊"}}}
        r = cc.scan_cliffhanger_resonance(
            prev, "任意后章文本（有无重叠都不影响，因为走模型分支）", ch_dir,
            model_result={"coherence_score": 0.81, "is_coherent": True, "source": "model"})
        assert r["score"] == 0.81
        assert r["source"] == "model"
        assert r["overlap_keywords"] == []


def test_cliffhanger_resonance_model_result_none_matches_keyword_path():
    """model_result=None（显式或省略）必须与 test_cliffhanger_resonance_overlap_score 已钉死
    的关键词重叠回归测试逐字节同分（零回归·复用同一组 ending_line/next_text 避免另造场景引入偏差）。"""
    with tempfile.TemporaryDirectory() as d:
        ch_dir = _write_chapter(_mk_project(Path(d)), 2, "占位")
        prev = {"self_eval": {"applied_style": {"ending_type": "钩子", "ending_line": "建木枝，深渊"}}}
        next_text = "建木枝，又一次断裂。深渊，仍在脚下张开。"
        r_default = cc.scan_cliffhanger_resonance(prev, next_text, ch_dir)
        r_explicit_none = cc.scan_cliffhanger_resonance(prev, next_text, ch_dir, model_result=None)
        assert r_default == r_explicit_none
        assert r_default["source"] == "heuristic"
        assert r_default["score"] > 0.0


def test_cliffhanger_resonance_exempt_ignores_model_result():
    """悬念断章 exempt 分支不消费 model_result（物理承接天然 OK，无需模型判断）。"""
    with tempfile.TemporaryDirectory() as d:
        ch_dir = _write_chapter(_mk_project(Path(d)), 3, "y")
        prev = {"self_eval": {"applied_style": {"ending_type": "悬念断章", "ending_line": "x"}}}
        r = cc.scan_cliffhanger_resonance(
            prev, "无关", ch_dir,
            model_result={"coherence_score": 0.02, "is_coherent": False, "source": "model"})
        assert r["exempt"] is True
        assert r["score"] == 1.0
        assert r["source"] == "heuristic"


def test_cliffhanger_resonance_missing_fields_ignore_model_result():
    """缺 prev_changes / 缺 ending_line 两个哨兵分支都先于 model_result 判断短路（不消费模型）。"""
    with tempfile.TemporaryDirectory() as d:
        ch_dir = _write_chapter(_mk_project(Path(d)), 3, "y")
        fake_model = {"coherence_score": 0.9, "is_coherent": True, "source": "model"}
        assert cc.scan_cliffhanger_resonance({}, "任意", ch_dir, model_result=fake_model)["score"] == -1
        prev = {"self_eval": {"applied_style": {"ending_type": "钩子", "ending_line": ""}}}
        r = cc.scan_cliffhanger_resonance(prev, "任意", ch_dir, model_result=fake_model)
        assert r["score"] == -1
        assert r["source"] == "heuristic"


def test_cliffhanger_resonance_ledger_model_result_overrides():
    """ledger 路径 model_result 命中 → 覆盖账本预算分。"""
    with tempfile.TemporaryDirectory() as d:
        plain_dir = _write_chapter(_mk_project(Path(d)), 2, "x")
        rec = {"cliffhanger_resonance_next": 0.05, "ending_type": "钩子", "ending_line": "断木"}
        r = cc.scan_cliffhanger_resonance_ledger(
            rec, plain_dir,
            model_result={"coherence_score": 0.93, "is_coherent": True, "source": "model"})
        assert r["score"] == 0.93
        assert r["source"] == "model"


def test_cliffhanger_resonance_ledger_model_result_none_matches_original():
    """ledger 路径 model_result=None 必须与 test_cliffhanger_resonance_ledger_path 已钉死的
    账本预算分回归测试逐字节同分（零回归）。"""
    with tempfile.TemporaryDirectory() as d:
        plain_dir = _write_chapter(_mk_project(Path(d)), 2, "x")
        rec = {"cliffhanger_resonance_next": 0.876, "ending_type": "钩子", "ending_line": "断木"}
        r_default = cc.scan_cliffhanger_resonance_ledger(rec, plain_dir)
        r_explicit_none = cc.scan_cliffhanger_resonance_ledger(rec, plain_dir, model_result=None)
        assert r_default == r_explicit_none
        assert r_default["score"] == 0.88
        assert r_default["source"] == "heuristic"


def test_cliffhanger_resonance_ledger_exempt_ignores_model_result():
    with tempfile.TemporaryDirectory() as d:
        plain_dir = _write_chapter(_mk_project(Path(d)), 2, "x")
        rec2 = {"ending_type": "悬念断章"}
        r = cc.scan_cliffhanger_resonance_ledger(
            rec2, plain_dir,
            model_result={"coherence_score": 0.01, "is_coherent": False, "source": "model"})
        assert r["score"] == 1.0
        assert r["source"] == "heuristic"


def test_check_time_transition_heuristic_matches_original_inline_logic():
    """model_result=None → 与改前 main() 内联关键词判断逻辑（["过渡","周末","回忆","醒来","睡了"]）
    完全一致（零回归）。"""
    assert cc.check_time_transition(["普通剧情推进，没有任何说明"]) == \
        {"has_transition": False, "source": "heuristic"}
    assert cc.check_time_transition(["周末他去看海"]) == {"has_transition": True, "source": "heuristic"}
    assert cc.check_time_transition(["回忆起过去种种"]) == {"has_transition": True, "source": "heuristic"}
    assert cc.check_time_transition([]) == {"has_transition": False, "source": "heuristic"}


def test_check_time_transition_model_result_overrides_keyword_logic():
    """model_result 命中 → 用模型 is_coherent 判定，不再看 plot_nodes 关键词命中。"""
    # 无关键词但模型判连贯 → True（纠正 heuristic 会漏判的同义过渡表述）
    r1 = cc.check_time_transition(
        ["普通剧情推进，没有任何说明"],
        model_result={"coherence_score": 0.9, "is_coherent": True, "source": "model"})
    assert r1 == {"has_transition": True, "source": "model"}
    # 有关键词但模型判不连贯 → False
    r2 = cc.check_time_transition(
        ["周末他去看海"],
        model_result={"coherence_score": 0.1, "is_coherent": False, "source": "model"})
    assert r2 == {"has_transition": False, "source": "model"}


def test_check_time_transition_model_result_missing_is_coherent_falls_back_to_score():
    """model_result 缺 is_coherent 字段（上游产出不完整）→ 按 coherence_score≥0.5 兜底判定。"""
    hi = cc.check_time_transition(["无关内容"], model_result={"coherence_score": 0.7})
    lo = cc.check_time_transition(["无关内容"], model_result={"coherence_score": 0.3})
    assert hi["has_transition"] is True
    assert lo["has_transition"] is False


def test_object_continuity_source_field_always_heuristic():
    """2026-07-01 评估结论：物件持续性存在性核对不适配 coherence 模型，不模型化——恒 source=heuristic。"""
    all_changes = {1: {"factual": {"item_transfers": [{"item": "青龙剑"}]}}, 2: {}, 3: {}, 4: {}}
    all_texts = {1: "他获得了青龙剑", 2: "今天天气不错", 3: "他去了集市", 4: "他继续赶路"}
    findings = cc.scan_object_continuity(all_changes, all_texts, 4)
    assert len(findings) == 1
    assert findings[0]["item"] == "青龙剑"
    assert findings[0]["gap"] == 3
    assert findings[0]["source"] == "heuristic"


def test_object_continuity_source_heuristic_even_with_nn_env_on(monkeypatch):
    """即使全局开了 RUOYU_NN_COHERENCE，本维度也不应被模型接管（评估结论：不适配子任务·不强套）。"""
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    all_changes = {1: {"factual": {"item_transfers": [{"item": "青龙剑"}]}}, 2: {}, 3: {}, 4: {}}
    all_texts = {1: "他获得了青龙剑", 2: "今天天气不错", 3: "他去了集市", 4: "他继续赶路"}
    findings = cc.scan_object_continuity(all_changes, all_texts, 4)
    assert len(findings) == 1
    assert findings[0]["source"] == "heuristic"


def test_codes_not_in_hard_gate():
    """本文件 3 个 finding code 必须恒 advisory（不可硬闸）——与 audit_hub 单一来源对齐。"""
    import audit_hub  # noqa: E402（sys.path 已在文件顶部注入 _SCRIPTS）
    for code in ("CLIFFHANGER_NOT_RESONATED", "TIME_JUMP_UNEXPLAINED", "OBJECT_CONTINUITY_BROKEN"):
        assert code not in audit_hub.HARD_GATE_CODES


# ──────────────────────────────────────────────────────────────────────────
# NN 桥接管道（_load_coherence_bridge / _predict_pairs_safe / _valid_pair_result /
# _batch_model_results）。前两组测试直传纯函数元组做依赖注入（零 mock）；
# _load_coherence_bridge 的两个分支测试真实模块可用性（真实场景验证，非 mock 被测逻辑）。
# ──────────────────────────────────────────────────────────────────────────

def test_load_coherence_bridge_real_module_disabled_by_default():
    """真实 nn_coherence_bridge 模块可正常导入；RUOYU_NN_COHERENCE 未设 → enabled()=False。"""
    bridge = cc._load_coherence_bridge()
    assert bridge is not None
    predict_pairs, enabled = bridge
    assert callable(predict_pairs) and callable(enabled)
    assert enabled() is False


def test_load_coherence_bridge_none_when_unimportable(monkeypatch):
    """nn_coherence_bridge 不可导入（模块缺失/被禁）→ None（调用方回退确定性逻辑）。"""
    monkeypatch.setitem(sys.modules, "nn_coherence_bridge", None)
    assert cc._load_coherence_bridge() is None


def test_predict_pairs_safe_handles_exception():
    def boom(pairs):
        raise RuntimeError("boom")
    assert cc._predict_pairs_safe((boom, lambda: True), [("a", "b")]) == [None]


def test_predict_pairs_safe_handles_length_mismatch():
    bridge = (lambda pairs: [None], lambda: True)  # 只回 1 个但传 2 个
    assert cc._predict_pairs_safe(bridge, [("a", "b"), ("c", "d")]) == [None, None]


def test_predict_pairs_safe_empty_pairs_no_call():
    calls = []
    def predict_pairs(pairs):
        calls.append(pairs)
        return []
    assert cc._predict_pairs_safe((predict_pairs, lambda: True), []) == []
    assert calls == []


def test_valid_pair_result_rejects_malformed():
    assert cc._valid_pair_result(None) is None
    assert cc._valid_pair_result("not a dict") is None
    assert cc._valid_pair_result({"coherence_score": "not a number"}) is None
    assert cc._valid_pair_result({"coherence_score": True}) is None  # bool 排除
    good = {"coherence_score": 0.5, "is_coherent": True, "source": "model"}
    assert cc._valid_pair_result(good) == good


def test_batch_model_results_maps_back_correctly():
    bridge = (lambda pairs: [{"coherence_score": 0.9, "is_coherent": True, "source": "model"}
                             for _ in pairs],
              lambda: True)
    results = cc._batch_model_results(bridge, [None, ("a", "b"), None, ("c", "d")])
    assert results[0] is None and results[2] is None
    assert results[1]["coherence_score"] == 0.9 and results[3]["coherence_score"] == 0.9


def test_batch_model_results_none_bridge():
    assert cc._batch_model_results(None, [("a", "b")]) == [None]


def test_batch_model_results_no_candidates_skips_call():
    """全 None 候选 → 不应发起任何 predict_pairs 调用（摊薄 subprocess 开销的核心保证）。"""
    calls = []
    def predict_pairs(pairs):
        calls.append(pairs)
        return []
    results = cc._batch_model_results((predict_pairs, lambda: True), [None, None])
    assert results == [None, None]
    assert calls == []


# ──────────────────────────────────────────────────────────────────────────
# main() 批量预算完整链路——用 mock.patch("nn_coherence_bridge.enabled"/"predict_pairs", ...)
# 替换外部 NN 模型后端（与 test_coherence_scanner.py::TestScanner 同款既有范式）。main() 含
# sys.exit 且需要控制模型返回值 → 只能 in-process 调用（父进程 monkeypatch 对 _run_cli 的
# subprocess 子进程无效），与上方纯 subprocess 路径的 baseline 测试互补。
# ──────────────────────────────────────────────────────────────────────────

def _run_main_inprocess(proj: Path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prog", str(proj)])
    with pytest.raises(SystemExit):
        cc.main()


def test_main_inprocess_model_suppresses_cliffhanger_false_positive(tmp_path, monkeypatch):
    """raw 路径：关键词零重叠本应触发 CLIFFHANGER_NOT_RESONATED，模型判连贯 → 抑制假阳性。"""
    monkeypatch.delenv("CLUSTER_MODE", raising=False)
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    proj = _mk_project(tmp_path)
    _write_chapter(proj, 1, "建木枝，在他手中断裂。深渊，在脚下张开。" * 3,
                   changes={"self_eval": {"applied_style": {
                       "ending_type": "钩子", "ending_line": "建木枝，深渊"}}})
    _write_chapter(proj, 2, "完全无关的内容，没有任何字面重叠。" * 3, changes={})
    with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
         mock.patch("nn_coherence_bridge.predict_pairs",
                    return_value=[{"coherence_score": 0.9, "is_coherent": True, "source": "model"}]):
        _run_main_inprocess(proj, monkeypatch)
    report = _latest_report(proj)
    codes = {f["code"] for f in report["findings"]}
    assert "CLIFFHANGER_NOT_RESONATED" not in codes
    assert report["pairwise"][0]["cliffhanger_source"] == "model"
    assert report["pairwise"][0]["cliffhanger_score"] == 0.9


def test_main_inprocess_model_enabled_but_none_matches_disabled_baseline(tmp_path, monkeypatch):
    """模型 enabled 但 predict_pairs 全返回 None（真实场景：subprocess 失败/ckpt 缺）→
    必须与完全不设 NN 环境变量的 baseline 逐字节一致（零回归的核心断言）。"""
    def _build(root):
        proj = _mk_project(root)
        _write_chapter(proj, 1, "建木枝，在他手中断裂。深渊，在脚下张开。" * 3,
                       changes={"self_eval": {"applied_style": {
                           "ending_type": "钩子", "ending_line": "建木枝，深渊"}},
                           "factual": {"time_advance": {"key_events": ["周一出发"]}}})
        _write_chapter(proj, 2, "完全无关的内容，没有任何字面重叠。" * 3,
                       changes={"self_eval": {"applied_style": {}},
                                "factual": {"time_advance": {"key_events": ["周四抵达"],
                                                              "plot_nodes": []}}})
        return proj

    monkeypatch.delenv("CLUSTER_MODE", raising=False)
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    proj_with_env = _build(tmp_path / "with_env")
    with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
         mock.patch("nn_coherence_bridge.predict_pairs", return_value=[None]):
        _run_main_inprocess(proj_with_env, monkeypatch)
    report_with_env = _latest_report(proj_with_env)

    monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
    proj_baseline = _build(tmp_path / "baseline")
    _run_main_inprocess(proj_baseline, monkeypatch)
    report_baseline = _latest_report(proj_baseline)

    def _strip_ts(r):
        r = dict(r)
        r.pop("scan_ts", None)
        return r
    assert _strip_ts(report_with_env) == _strip_ts(report_baseline)


def test_main_inprocess_time_gap_model_suppresses(tmp_path, monkeypatch):
    """时间跳跃维度：plot_nodes 无过渡关键词本应触发 TIME_JUMP_UNEXPLAINED，模型判连贯 → 抑制。
    ending_type 设悬念断章隔离掉 cliffhanger 维度，纯测时间跳跃这一维。"""
    monkeypatch.delenv("CLUSTER_MODE", raising=False)
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    proj = _mk_project(tmp_path)
    _write_chapter(proj, 1, "第一章正文内容" * 20,
                   changes={"self_eval": {"applied_style": {"ending_type": "悬念断章", "ending_line": "x"}},
                            "factual": {"time_advance": {"key_events": ["周一出发"]}}})
    _write_chapter(proj, 2, "第二章正文内容" * 20,
                   changes={"self_eval": {"applied_style": {}},
                            "factual": {"time_advance": {"key_events": ["周四抵达"], "plot_nodes": []}}})
    with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
         mock.patch("nn_coherence_bridge.predict_pairs",
                    return_value=[{"coherence_score": 0.9, "is_coherent": True, "source": "model"}]):
        _run_main_inprocess(proj, monkeypatch)
    report = _latest_report(proj)
    codes = {f["code"] for f in report["findings"]}
    assert "TIME_JUMP_UNEXPLAINED" not in codes
    assert report["pairwise"][0]["time_transition_source"] == "model"


def _mk_ledger_project(tmp_path, resonance_next: float = 0.0) -> Path:
    proj = _mk_project(tmp_path)
    _write_chapter(proj, 1, "第一章正文" * 30)
    _write_chapter(proj, 2, "第二章正文" * 30)
    ledger = {
        "schema_version": "v2.cluster",
        "clusters": [{
            "cluster_id": "cluster_001",
            "title": "测试 cluster",
            "chapter_range": [1, 2],
            "chapters": {
                "1": {"ending_type": "钩子", "ending_line": "他望着深渊陷入沉默",
                      "cliffhanger_resonance_next": resonance_next},
                "2": {},
            },
        }],
    }
    (proj / "_数据库" / "故事块摘要.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return proj


def test_main_inprocess_ledger_baseline_uses_precomputed_score(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MODE", "1")
    monkeypatch.delenv("RUOYU_NN_COHERENCE", raising=False)
    proj = _mk_ledger_project(tmp_path, resonance_next=0.0)
    _run_main_inprocess(proj, monkeypatch)
    report = _latest_report(proj)
    assert report["pairwise"][0]["cliffhanger_source"] == "heuristic"
    assert report["pairwise"][0]["cliffhanger_score"] == 0.0
    codes = {f["code"] for f in report["findings"]}
    assert "CLIFFHANGER_NOT_RESONATED" in codes


def test_main_inprocess_ledger_model_overrides_precomputed_score(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MODE", "1")
    monkeypatch.setenv("RUOYU_NN_COHERENCE", "1")
    proj = _mk_ledger_project(tmp_path, resonance_next=0.0)
    with mock.patch("nn_coherence_bridge.enabled", return_value=True), \
         mock.patch("nn_coherence_bridge.predict_pairs",
                    return_value=[{"coherence_score": 0.95, "is_coherent": True, "source": "model"}]):
        _run_main_inprocess(proj, monkeypatch)
    report = _latest_report(proj)
    assert report["pairwise"][0]["cliffhanger_source"] == "model"
    assert report["pairwise"][0]["cliffhanger_score"] == 0.95
    codes = {f["code"] for f in report["findings"]}
    assert "CLIFFHANGER_NOT_RESONATED" not in codes


if __name__ == "__main__":
    import inspect

    def _needs_pytest_fixture(fn) -> bool:
        """裸跑（非 pytest）只能无参调用；需要 monkeypatch/tmp_path 等 fixture 的用例跳过
        （与本文件单测同款判定逻辑，避免误报"假失败"）。"""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                return True
        return False

    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            fn = globals()[nm]
            if _needs_pytest_fixture(fn):
                print(f"  [SKIP] {nm}（需 pytest fixture，跑 pytest 覆盖）")
                continue
            try:
                fn()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
