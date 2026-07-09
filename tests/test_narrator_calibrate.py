"""narrator_calibrate.py 聚焦回归测试（零 LLM / 零联网 / 纯确定性逻辑）。

补现有间接覆盖之外的核心算法盲区：
- 既有覆盖（tests/test_save_state_feedback_loop.py）：infer_outcome_from_changes 的
  writer 申报优先 / heuristic 回退 / 非法值忽略 + calibrate 的 outcome_source 标注。
- 既有覆盖（tests/test_engine_schema_compat.py）：narrator_view 三套 schema + beats 标 done。

本文件钉死尚未覆盖的：
1. `_infer_outcome_heuristic` —— 负面关键词阈值、facts_locked/locked_facts 双读兜底、
   fate/foreshadow→win、neutral 分级 intensity。
2. `evaluate_phase` —— rising→climax→cooldown→steady→rising 状态机四条迁移边 + 守门条件。
3. `calibrate` —— adaptation_factor 滑窗（setback_count / win_streak / loss_streak）、
   narrator_recommendation 三分支（setback/win/auto）+ _urgent 退出语义、log 按 ch 去重幂等、
   缺 叙事节拍器.json 的 error 返回。
4. `main` CLI —— 退出码 0/1（健康 vs urgent）、缺文件 SKIP exit 0。
"""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import narrator_calibrate as nc  # noqa: E402

_TARGET = _SCRIPTS / "narrator_calibrate.py"
_INTERNAL_ENV_NAME = "RUOYUAI_CLUSTER_STATE_INTERNAL"


def _fake_vad(predict_batch_fn):
    """临时装 RUOYU_NN_VAD=1 + 假 nn_vad_bridge 模块，返回还原函数（无 monkeypatch 依赖）。"""
    old_env = os.environ.get("RUOYU_NN_VAD")
    old_mod = sys.modules.get("nn_vad_bridge")
    os.environ["RUOYU_NN_VAD"] = "1"
    sys.modules["nn_vad_bridge"] = types.SimpleNamespace(predict_batch=predict_batch_fn)

    def _restore():
        if old_env is None:
            os.environ.pop("RUOYU_NN_VAD", None)
        else:
            os.environ["RUOYU_NN_VAD"] = old_env
        if old_mod is None:
            sys.modules.pop("nn_vad_bridge", None)
        else:
            sys.modules["nn_vad_bridge"] = old_mod
    return _restore


# ──────────────────────────────────────────────────────────────────────────
# 脚手架
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path, pacer: dict) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "叙事节拍器.json").write_text(json.dumps(pacer, ensure_ascii=False),
                                        encoding="utf-8")
    return tmp


def _write_changes(root: Path, ch: int, payload: dict) -> None:
    ch_dir = root / "章节" / f"第{ch:03d}章"
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / f"第{ch:03d}章_changes.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_pacer(root: Path) -> dict:
    return json.loads((root / "_数据库" / "叙事节拍器.json").read_text(encoding="utf-8"))


def _log(*outcomes) -> list:
    """构造 chapter_outcome_log（outcome,intensity 元组或纯 outcome 字符串）。"""
    out = []
    for i, o in enumerate(outcomes, start=1):
        if isinstance(o, tuple):
            outcome, intensity = o
        else:
            outcome, intensity = o, 4
        out.append({"ch": i, "outcome": outcome, "intensity": intensity})
    return out


# ──────────────────────────────────────────────────────────────────────────
# 1. _infer_outcome_heuristic —— 启发式分支 + intensity
# ──────────────────────────────────────────────────────────────────────────
def test_heuristic_negative_keywords_trigger_setback():
    """负面关键词命中 >=2 → setback，intensity = min(8, 3+hits)。"""
    factual = {
        # locked_facts 含 "受伤"+"死亡"+"暴露" = 3 命中
        "locked_facts": ["主角受伤被发现", "盟友死亡"],
    }
    outcome, intensity = nc._infer_outcome_heuristic(factual)
    assert outcome == "setback"
    # "受伤"/"被发现"/"死亡" 三个负面词命中 → 3+3=6
    assert intensity == 6


def test_heuristic_negative_intensity_capped_at_8():
    """大量负面词 → intensity 封顶 8。"""
    factual = {
        "locked_facts": ["失败 受伤 死亡 暴露 被发现 被打 败退 崩溃 失控 受重创"],
    }
    outcome, intensity = nc._infer_outcome_heuristic(factual)
    assert outcome == "setback"
    assert intensity == 8  # min(8, 3+10) 封顶


def test_heuristic_fate_event_triggers_win():
    """fate_count>=1（无负面词）→ win，intensity=min(7,3+fate+foreshadow)。"""
    factual = {"fate_events_triggered": [{"id": "V1_ME_001"}]}
    outcome, intensity = nc._infer_outcome_heuristic(factual)
    assert outcome == "win"
    assert intensity == 4  # 3 + 1 fate + 0 foreshadow


def test_heuristic_foreshadow_two_triggers_win():
    """foreshadowing_paid >=2（无 fate）→ win。"""
    factual = {"foreshadowing_paid": ["伏笔A", "伏笔B"]}
    outcome, intensity = nc._infer_outcome_heuristic(factual)
    assert outcome == "win"
    assert intensity == 5  # 3 + 0 + 2


def test_heuristic_single_foreshadow_neutral_intensity3():
    """单条 foreshadow（不足 2，无 fate）→ neutral intensity 3。"""
    factual = {"foreshadowing_paid": ["伏笔A"]}
    outcome, intensity = nc._infer_outcome_heuristic(factual)
    assert outcome == "neutral"
    assert intensity == 3


def test_heuristic_empty_factual_neutral():
    """空 factual → neutral（fate>=0 分支命中 intensity 3）。"""
    outcome, intensity = nc._infer_outcome_heuristic({})
    assert outcome == "neutral"
    assert intensity == 3  # foreshadow_paid>=1 假；fate_count>=0 真 → 3


def test_heuristic_model_hit_flags_setback_below_keyword_threshold():
    """RUOYU_NN_VAD=1 + 假模型命中负面 valence → setback，即便关键词命中数<2（纯词典判不出）。
    假模型按内容区分（含'困境'→低valence，其余→高valence），证明真走了模型路径。"""
    def _fake(items):
        return [{"valence": 0.15, "arousal": 0.6, "dominance": None, "source": "model"}
                if "困境" in t else
                {"valence": 0.85, "arousal": 0.3, "dominance": None, "source": "model"}
                for t in items]
    restore = _fake_vad(_fake)
    try:
        factual = {"locked_facts": ["主角遭遇了困境"]}  # 0 个原关键词命中
        outcome, intensity = nc._infer_outcome_heuristic(factual)
        assert outcome == "setback", f"模型 valence<0.5 应判 setback，得 {outcome}"
        assert intensity == 3, f"negative_hits=0 → min(8,3+0)=3，得 {intensity}"

        # 对照：换一段模型判正向文本 → 不应被误判 setback（走 win/neutral 分支）
        factual2 = {"foreshadowing_paid": ["伏笔A", "伏笔B"]}
        outcome2, _ = nc._infer_outcome_heuristic(factual2)
        assert outcome2 == "win", f"正向 valence 不该被强判 setback，得 {outcome2}"
    finally:
        restore()


def test_heuristic_model_unavailable_zero_regression():
    """RUOYU_NN_VAD=1 但 predict_batch 全 None（模型不可用）→ 与默认(env off)关键词路径逐位一致。"""
    cases = [
        {"locked_facts": ["主角受伤被发现", "盟友死亡"]},
        {"fate_events_triggered": [{"id": "V1_ME_001"}]},
        {"foreshadowing_paid": ["伏笔A"]},
        {},
    ]
    baselines = [nc._infer_outcome_heuristic(f) for f in cases]

    restore = _fake_vad(lambda items: [None for _ in items])
    try:
        for factual, baseline in zip(cases, baselines):
            got = nc._infer_outcome_heuristic(factual)
            assert got == baseline, f"模型不可用应与默认路径一致：{factual} → {got} != {baseline}"
    finally:
        restore()


# ──────────────────────────────────────────────────────────────────────────
# 2. evaluate_phase —— 压力 phase 状态机
# ──────────────────────────────────────────────────────────────────────────
def test_phase_rising_to_climax_on_win_streak():
    """rising：连续 3 win 且 intensity 累计 >=12 → climax（since_ch 重置为当前 ch）。"""
    log = _log(("win", 4), ("win", 4), ("win", 4))  # 3 连胜，累计 12
    new_phase, since = nc.evaluate_phase("cassandra", log, "rising", 1, ch=3)
    assert new_phase == "climax"
    assert since == 3


def test_phase_rising_stays_when_intensity_low():
    """rising：3 win 但 intensity 累计 <12 → 不触发 climax。"""
    log = _log(("win", 3), ("win", 3), ("win", 3))  # 累计 9 < 12
    new_phase, since = nc.evaluate_phase("cassandra", log, "rising", 1, ch=3)
    assert new_phase == "rising"
    assert since == 1  # 不变


def test_phase_climax_to_cooldown():
    """climax：持续 >=1 章 → cooldown。"""
    log = _log("win", "win", "win")
    # since_change_ch=3, ch=4 → chs_since = 4-3+1 = 2 >= 1
    new_phase, since = nc.evaluate_phase("cassandra", log, "climax", 3, ch=4)
    assert new_phase == "cooldown"
    assert since == 4


def test_phase_cooldown_to_steady_after_3ch():
    """cooldown：持续 >=3 章 → steady；不足 3 章保持 cooldown。"""
    log = _log("neutral", "neutral", "neutral", "neutral", "neutral")
    # 不足：since=4, ch=5 → chs_since=2 <3 保持
    p1, s1 = nc.evaluate_phase("cassandra", log, "cooldown", 4, ch=5)
    assert p1 == "cooldown" and s1 == 4
    # 足够：since=4, ch=6 → chs_since=3 → steady
    p2, s2 = nc.evaluate_phase("cassandra", log, "cooldown", 4, ch=6)
    assert p2 == "steady" and s2 == 6


def test_phase_steady_to_rising_on_setback_run():
    """steady：连续 2 setback → rising。"""
    log = _log("neutral", "neutral", "neutral", "setback", "setback")
    new_phase, since = nc.evaluate_phase("cassandra", log, "steady", 1, ch=5)
    assert new_phase == "rising"
    assert since == 5


def test_phase_steady_to_rising_on_timeout():
    """steady：无 setback 但 chs_since >=5 → 也转 rising（长间歇推进）。"""
    log = _log("win", "neutral", "win", "neutral", "win")  # 无连续 setback
    # since=1, ch=5 → chs_since = 5-1+1 = 5 >=5
    new_phase, since = nc.evaluate_phase("cassandra", log, "steady", 1, ch=5)
    assert new_phase == "rising"
    assert since == 5


# ──────────────────────────────────────────────────────────────────────────
# 3. calibrate —— 滑窗 + 推荐 + 去重 + error
# ──────────────────────────────────────────────────────────────────────────
def _pacer_with_log(log: list) -> dict:
    return {
        "storyteller_profile": "cassandra",
        "current_pressure_phase": "rising",
        "since_phase_change_ch": 1,
        "chapter_outcome_log": list(log),
        "adaptation_factor": {"recent_n_chapters": 10,
                              "expected_setback_per_n_ch": 4,
                              "tolerance_window": 2},
    }


def test_calibrate_recommends_setback_when_too_few():
    """近窗 setback 远低于期望 → 推荐 setback + urgent。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 历史全 win（0 setback），expected=4 tol=2 → 0+2 < 4 → 推 setback
        _mk_project(tmp, _pacer_with_log(_log("win", "win", "win", "win")))
        _write_changes(tmp, 5, {"factual": {}, "self_eval": {}})
        r = nc.calibrate(tmp, 5)
        assert r["next_recommendation"]["next_chapter_target_outcome"] == "setback"
        assert r["next_recommendation"]["next_chapter_intensity_target"] == "high"
        assert r["_urgent"] is True
        # 滑窗统计：当前章 neutral，历史 4 win → win_streak=0（末章 neutral 打断）
        af = r["adaptation_factor"]
        assert af["current_setback_count_in_window"] == 0


def test_calibrate_recommends_win_when_too_many_setbacks():
    """近窗 setback 远高于期望 → 推荐 win + urgent。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 历史 6 setback，expected=4 tol=2 → 7-2 > 4 → 推 win（含当前章 setback）
        hist = _log("setback", "setback", "setback", "setback", "setback", "setback")
        _mk_project(tmp, _pacer_with_log(hist))
        # 当前章 ch7 也是 setback（负面词触发）
        _write_changes(tmp, 7, {"factual": {"locked_facts": ["主角受伤 据点暴露被发现"]},
                                "self_eval": {}})
        r = nc.calibrate(tmp, 7)
        assert r["outcome_inferred"] == "setback"
        assert r["next_recommendation"]["next_chapter_target_outcome"] == "win"
        assert r["_urgent"] is True
        af = r["adaptation_factor"]
        assert af["current_setback_count_in_window"] == 7
        assert af["current_loss_streak"] >= 1


def test_calibrate_auto_when_in_band():
    """近窗 setback 在容差区间内 → target=auto + 非 urgent（健康，exit 0）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # 期望 4，造 4 个 setback（在 [2,6] 内）
        hist = _log("setback", "win", "setback", "win", "setback", "win", "setback")
        _mk_project(tmp, _pacer_with_log(hist))
        _write_changes(tmp, 8, {"factual": {}, "self_eval": {}})  # ch8 neutral
        r = nc.calibrate(tmp, 8)
        assert r["next_recommendation"]["next_chapter_target_outcome"] == "auto"
        assert r["_urgent"] is False
        assert r["adaptation_factor"]["current_setback_count_in_window"] == 4


def test_calibrate_dedup_by_ch_idempotent():
    """同一 ch 二次 calibrate → log 只保留一条（去重幂等，不重复 append）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_project(tmp, _pacer_with_log([]))
        _write_changes(tmp, 3, {"factual": {}, "self_eval": {}})
        nc.calibrate(tmp, 3)
        nc.calibrate(tmp, 3)  # 重放
        pacer = _read_pacer(tmp)
        ch3_entries = [e for e in pacer["chapter_outcome_log"] if e["ch"] == 3]
        assert len(ch3_entries) == 1  # 去重，非 2 条


def test_calibrate_win_streak_counts_from_window_tail():
    """win_streak 从窗口末尾连续 win 计数（末章 win 打断 setback 历史）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        hist = _log("setback", "win", "win")
        _mk_project(tmp, _pacer_with_log(hist))
        # 当前 ch4 = win（fate 触发）
        _write_changes(tmp, 4, {"factual": {"fate_events_triggered": [{"id": "x"}]},
                                "self_eval": {}})
        r = nc.calibrate(tmp, 4)
        assert r["outcome_inferred"] == "win"
        # 末尾连续 win：ch2,ch3,ch4 = 3 连胜
        assert r["adaptation_factor"]["current_win_streak"] == 3
        assert r["adaptation_factor"]["current_loss_streak"] == 0


def test_calibrate_missing_pacer_returns_error():
    """缺 叙事节拍器.json → calibrate 返回 error dict（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)  # 目录在但文件不在
        r = nc.calibrate(tmp, 1)
        assert "error" in r
        assert "叙事节拍器" in r["error"]


# ──────────────────────────────────────────────────────────────────────────
# 4. main CLI —— 退出码语义（subprocess 跑真 CLI）
# ──────────────────────────────────────────────────────────────────────────
def _run_cli(project: Path, ch: int):
    # Windows 控制台默认 cp936 → 子进程打印中文 JSON 必须强制 UTF-8 stdout，
    # 否则父进程按 utf-8 解码 gbk 字节会崩（res.stdout=None）。捕字节自解码兜底。
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        **{_INTERNAL_ENV_NAME: "1"},
    )
    res = subprocess.run(
        [sys.executable, str(_TARGET), str(project), "--ch", str(ch)],
        capture_output=True, env=env)
    res_stdout = res.stdout.decode("utf-8", errors="replace")
    res_stderr = res.stderr.decode("utf-8", errors="replace")
    return res.returncode, res_stdout, res_stderr


def test_cli_exit_0_when_healthy():
    """健康（in-band → 非 urgent）→ exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        hist = _log("setback", "win", "setback", "win", "setback", "win", "setback")
        _mk_project(tmp, _pacer_with_log(hist))
        _write_changes(tmp, 8, {"factual": {}, "self_eval": {}})
        code, out, err = _run_cli(tmp, 8)
        assert code == 0, err
        assert '"_urgent": false' in out


def test_cli_exit_1_when_urgent():
    """urgent（setback 远不足 → 强烈建议下章修正）→ exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_project(tmp, _pacer_with_log(_log("win", "win", "win", "win")))
        _write_changes(tmp, 5, {"factual": {}, "self_eval": {}})
        code, out, err = _run_cli(tmp, 5)
        assert code == 1, err
        assert '"_urgent": true' in out


def test_cli_skip_exit_0_when_no_pacer():
    """无 叙事节拍器.json → main 打印 SKIP 并 exit 0（未启用 storyteller 系统不报错）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        code, out, err = _run_cli(tmp, 1)
        assert code == 0, err
        assert "SKIP" in out
