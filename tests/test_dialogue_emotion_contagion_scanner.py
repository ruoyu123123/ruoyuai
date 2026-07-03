# -*- coding: utf-8 -*-
"""dialogue_emotion_contagion_scanner R19 W8 Batch-X·P1 情感传染+Gottman·回归。

确定性·零依赖。覆盖 off/短稿/场景数不足/_max_lagged_corr/_is_conflict_scene/
_detect_gottman_sequence/compute_dialogue_contagion_signature/作者档 override/
shadow vs active/CLI/hard_gate registry 守卫。

🔴 2026-07-01 NN情绪VAD集成回归：_nn_batch_vad(model_vad|None) + scan()/
compute_dialogue_contagion_signature 的 vad_source 归因·核心断言=NN 开启但桥
未命中时输出必须与 NN 完全关闭时逐字节一致(零回归)。
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
import dialogue_emotion_contagion_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "dialogue_emotion_contagion_scanner.py"
_ENV = "DIALOGUE_CONTAGION_MODE"


def _set_mode(m):
    if m is None:
        os.environ.pop(_ENV, None)
    else:
        os.environ[_ENV] = m


def _write_draft(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(names=None, baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if names:
        (proj / "_数据库" / "角色池.json").write_text(
            json.dumps({"emerged": [{"name": n} for n in names]},
                       ensure_ascii=False), encoding="utf-8")
    if baseline:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps({"quantitative": {"dialogue_contagion_signature": baseline}},
                       ensure_ascii=False), encoding="utf-8")
    return proj


def _dialogue_draft():
    """两个场景·小王 vs 李雷 双人主导对话。"""
    s1 = ("夜色无边，灯火阑珊。" * 30 + "\n"
          + "“你怎么来了？”小王问道。" * 4 + "\n"
          + "“我有话要说。”李雷答道。" * 4)
    s2 = ("第二天清晨。" * 30 + "\n"
          + "“说吧。”小王说道。" * 4 + "\n"
          + "“我恨你。”李雷说道。" * 4)
    return s1 + "\n\n" + s2


def _conflict_scene_text():
    """冲突场景·含 4 阶段标志词。"""
    return ("两人吵架不止·相互翻脸·“你总是这样指责我！”小王怒道。"
            "“哼，蔑视谁呢。”李雷讥讽道。"
            "“不是我的错，你才是！”小王反驳。"
            "李雷转身不说话，沉默以对。" * 6)


def test_off_skeleton():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("off")
        r = mod.scan(_write_draft(_dialogue_draft()))
        assert r["mode"] == "off"
    finally:
        _set_mode(bak)


def test_short_draft_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        r = mod.scan(_write_draft("短。"))
        assert "短" in r.get("note", "")
    finally:
        _set_mode(bak)


def test_too_few_scenes_skip():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        # 单段长稿 → 切完场景数 < MIN_SCENES
        r = mod.scan(_write_draft("一" * 900))
        assert "场景数" in r.get("note", "") or r["verdict"] == "PASS"
    finally:
        _set_mode(bak)


def test_max_lagged_corr_aligned():
    xs = [1, 2, 3, 4, 5, 6]
    ys = [1, 2, 3, 4, 5, 6]
    r = mod._max_lagged_corr(xs, ys)
    assert r is not None
    assert abs(r) > 0.9


def test_max_lagged_corr_too_short():
    assert mod._max_lagged_corr([1], [1]) is None


def test_is_conflict_scene_detects():
    txt = "他们吵架翻脸怒目相对·激烈争执·彼此怒恨。"
    assert mod._is_conflict_scene(txt)


def test_is_conflict_scene_neutral():
    assert not mod._is_conflict_scene("阳光明媚·散步聊天。")


def test_detect_gottman_sequence_order():
    txt = "你总是这样·哼·不是我·沉默以对·懒得回。"
    seq = mod._detect_gottman_sequence(txt)
    stages = [s for s, _ in seq]
    assert "criticism" in stages
    assert "contempt" in stages
    assert "stonewalling" in stages


def test_detect_gottman_sequence_empty():
    seq = mod._detect_gottman_sequence("天气真好。")
    assert seq == []


def test_load_characters_no_project():
    assert mod._load_characters(None) == set()


def test_load_characters_from_project():
    proj = _mk_project(names=["小王", "李雷"])
    s = mod._load_characters(proj)
    assert "小王" in s
    assert "李雷" in s


def test_compute_signature_empty_paths():
    sig = mod.compute_dialogue_contagion_signature([])
    assert sig["sync_window_baseline"]["sample_n"] == 0
    assert sig["gottman_cascade_n"] == 0


def test_compute_signature_with_data():
    p = _write_draft(_dialogue_draft() + "\n\n" + _conflict_scene_text())
    sig = mod.compute_dialogue_contagion_signature(
        [str(p)], names={"小王", "李雷"})
    assert "sync_window_baseline" in sig
    assert "gottman_cascade_freq" in sig


def test_shadow_mode_no_violations():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        proj = _mk_project(names=["小王", "李雷"])
        r = mod.scan(_write_draft(_dialogue_draft()), project_root=proj)
        assert r["violations"] == []
    finally:
        _set_mode(bak)


def test_cli_runs():
    bak = os.environ.get(_ENV)
    try:
        _set_mode("shadow")
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(_write_draft(_dialogue_draft()))],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
            env={**os.environ, _ENV: "shadow", "PYTHONIOENCODING": "utf-8"})
        assert r.returncode in (0, 1)
        assert "scanner" in r.stdout
    finally:
        _set_mode(bak)


def test_code_not_in_hard_gate():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert "DIALOGUE_CONTAGION_ABNORMAL" not in set(data.get("hard_gate_codes", []))


def test_registry_entry_new_true():
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    entry = data["scanners"].get("dialogue_emotion_contagion_scanner")
    assert entry is not None
    assert entry.get("_new") is True
    assert "DIALOGUE_CONTAGION_ABNORMAL" in entry.get("issues_emitted", [])


# ═══════════════ 🔴 2026-07-01 NN情绪VAD集成回归 ═══════════════

def _big_dialogue_draft():
    """两个场景(第一场景独立越过 1500 CJK flush 阈值)·小王/李雷双人主导对话·
    对白含 CVAW/NRC 词典真命中词(喜欢/恼怒)·总量越过 MIN_CJK(800)供 scan() 走完整 sync 流程。"""
    s1 = ("夜色无边，灯火阑珊，寂静无人。" * 150 + "\n"
          + "“我很喜欢你。”小王说道。" * 6 + "\n"
          + "“我十分恼怒。”李雷说道。" * 6)
    s2 = ("第二天清晨，阳光洒满窗台。" * 30 + "\n"
          + "“我很喜欢你。”小王说道。" * 6 + "\n"
          + "“我十分恼怒。”李雷说道。" * 6)
    return s1 + "\n\n" + s2


def test_nn_batch_vad_off_by_default(monkeypatch):
    """env 未开(默认) → _nn_batch_vad 全 None·不碰 FeatureStore/nn_vad_bridge。"""
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    assert mod._nn_batch_vad(["随便写点什么", "再来一句"]) == [None, None]


def test_nn_batch_vad_empty_input():
    assert mod._nn_batch_vad([]) == []


def test_nn_batch_vad_model_source_when_enabled(monkeypatch):
    """env 开 + 桥命中模型 → 批量返回 (V,A,D)·D 缺省补 0.5。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(
        predict_batch=lambda texts: [
            {"valence": 0.81, "arousal": 0.4, "dominance": None, "source": "model"}
            for _ in texts
        ]
    )
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    result = mod._nn_batch_vad(["文本一", "文本二"])
    assert result == [(0.81, 0.4, 0.5), (0.81, 0.4, 0.5)]


def test_nn_batch_vad_bridge_miss_returns_none_list(monkeypatch):
    """env 开但桥未命中(全 None) → _nn_batch_vad 也全 None(调用方回退词典)。"""
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
    assert mod._nn_batch_vad(["文本一", "文本二"]) == [None, None]


def test_scan_model_unavailable_matches_lexicon_baseline(monkeypatch):
    """🔴 零回归核心断言：NN 开启但桥返回 None → scan() 完整输出须与 NN 完全关闭时逐字节一致。"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        draft = _write_draft(_big_dialogue_draft())
        monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
        baseline = mod.scan(draft)
        assert baseline["sync_results"], "fixture 应至少产出 1 组同步分析(否则本测试无意义)"

        monkeypatch.setenv("RUOYU_NN_VAD", "1")
        fake_bridge = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
        with_model_unavailable = mod.scan(draft)
        assert with_model_unavailable == baseline
    finally:
        _set_mode(bak)


def test_scan_model_vad_source_when_enabled(monkeypatch):
    """env 开 + 桥命中模型 → sync_results/metrics 的 vad_source 标 model_vad。"""
    bak = os.environ.get(_ENV)
    try:
        _set_mode("active")
        draft = _write_draft(_big_dialogue_draft())
        monkeypatch.setenv("RUOYU_NN_VAD", "1")
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts: [
                {"valence": 0.2 if "喜欢" in t else 0.8, "arousal": 0.5,
                 "dominance": None, "source": "model"}
                for t in texts
            ]
        )
        monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_bridge)
        r = mod.scan(draft)
        assert r["sync_results"], "应至少算出一组双人同步分析"
        assert all(s["vad_source"] == "model_vad" for s in r["sync_results"])
        assert r["metrics"]["vad_source_summary"]["model_vad"] == len(r["sync_results"])
        assert r["metrics"]["vad_source_summary"]["lexicon_fallback"] == 0
    finally:
        _set_mode(bak)


def test_compute_signature_vad_source_model_and_fallback(monkeypatch):
    """compute_dialogue_contagion_signature 的 vad_source：默认词典·桥未命中零回归·桥命中模型。"""
    p = _write_draft(_big_dialogue_draft())
    monkeypatch.delenv("RUOYU_NN_VAD", raising=False)
    baseline = mod.compute_dialogue_contagion_signature([str(p)], names={"小王", "李雷"})
    assert baseline["vad_source"] == "lexicon_fallback"
    assert baseline["sync_window_baseline"]["sample_n"] > 0

    fake_none = types.SimpleNamespace(predict_batch=lambda texts: [None for _ in texts])
    monkeypatch.setenv("RUOYU_NN_VAD", "1")
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_none)
    sig_none = mod.compute_dialogue_contagion_signature([str(p)], names={"小王", "李雷"})
    assert sig_none == baseline  # 零回归：桥不可用时逐字节一致

    fake_model = types.SimpleNamespace(predict_batch=lambda texts: [
        {"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "model"} for _ in texts])
    monkeypatch.setitem(sys.modules, "nn_vad_bridge", fake_model)
    sig_model = mod.compute_dialogue_contagion_signature([str(p)], names={"小王", "李雷"})
    assert sig_model["vad_source"] == "model_vad"


def test_code_not_in_hard_gate_after_nn_integration():
    """确认 NN 集成后 ISSUE_CODE 仍不在 hard_gate 清单(北极星⑤守卫)。"""
    reg = _SCRIPTS / "scanner_registry.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert mod.ISSUE_CODE not in set(data.get("hard_gate_codes", []))
