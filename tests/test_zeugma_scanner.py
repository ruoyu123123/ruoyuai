# -*- coding: utf-8 -*-
"""zeugma_scanner R22 W10 Batch-FF · P1 · 拈连单格 advisory"""
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
import zeugma_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "zeugma_scanner.py"


def _set_mode(m):
    if m is None:
        os.environ.pop("ZEUGMA_MODE", None)
    else:
        os.environ["ZEUGMA_MODE"] = m


def _write(text):
    d = Path(tempfile.mkdtemp())
    p = d / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    if baseline is not None:
        (proj / "_数据库" / "作者风格.json").write_text(
            json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    return proj


# 拈连密集（"飞鸟" common + "飞阿Q" unusual）
_ZEUGMA_RICH = ("他飞鸟过山岗。然后飞阿Q远去无形。" * 60)

# 拈连缺席（平凡叙述）
_THIN = ("他写了一封信。她看了一会儿。桌上是茶。" * 80)


def test_off_returns_skeleton():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("off")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        assert out["mode"] == "off" and out["verdict"] == "PASS"
        assert "zeugma_count" not in out
    finally:
        _set_mode(bak)


def test_shadow_default_no_violation():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("shadow")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        assert out["mode"] == "shadow"
        assert out["violations"] == []
    finally:
        _set_mode(bak)


def test_active_zeugma_detected():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ZEUGMA_DETECTED" in codes
        assert out["zeugma_count"] >= 1
    finally:
        _set_mode(bak)


def test_active_thin_no_detection():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_THIN), _mk_project())
        # 无拈连命中
        assert out["zeugma_count"] == 0
    finally:
        _set_mode(bak)


def test_under_baseline_flagged_when_comedy_author():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        baseline = {"zeugma_per_kcj": 0.6}  # 搞笑流作者
        out = mod.scan(_write(_THIN), _mk_project(baseline=baseline))
        codes = {v["code"] for v in out.get("violations", [])}
        # 搞笑流但 0 命中 → UNDER
        assert "ZEUGMA_UNDER_BASELINE" in codes
    finally:
        _set_mode(bak)


def test_baseline_dict_format():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        baseline = {"zeugma_per_kcj": {"mean": 0.4, "std": 0.1}}
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project(baseline=baseline))
        assert out["baseline_source"] == "author_profile"
        assert out["baseline"] == 0.4
    finally:
        _set_mode(bak)


def test_short_draft_skipped():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write("他飞鸟。"), _mk_project())
        assert out.get("note") == "草稿太短·跳过"
    finally:
        _set_mode(bak)


def test_read_failure_returns_note():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(str(Path(tempfile.mkdtemp()) / "nope.txt"))
        assert "草稿读取失败" in out.get("note", "")
    finally:
        _set_mode(bak)


def test_mode_invalid_falls_back_shadow():
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("bogus")
        assert mod._mode() == "shadow"
    finally:
        _set_mode(bak)


def test_lexicon_placeholder_flag():
    lex = mod.load_lexicon()
    # 占位词典 _placeholder=true（确保 advisory 不被当真词典）
    assert lex.get("_placeholder") is True


def test_strip_changes_marker():
    s = "正文飞鸟过山岗。\n---CHANGES---\nyada"
    out = mod._strip_changes(s)
    assert "yada" not in out


def test_detect_pairs_zero_when_only_common():
    text = "他飞鸟过去。然后飞鸟回来了。" * 40  # 全是 common·非拈连
    pairs = mod.detect_zeugma_pairs(text, mod.load_lexicon())
    assert pairs == [] or all(p["obj1"] != p["obj2"] for p in pairs)


# ============ 🔴 2026-07-01 真模型(surprisal_gpt2) 补充证据回归 ============
# 词典判断(zeugma_count/per_kcjk/flags/violations)是主结论·真模型只附加 surprisal_evidence
# 字段，绝不覆盖/替换主结论——以下测试同时验证"附加证据正确"与"主判断零回归"两件事。
#
# 注：_ZEUGMA_RICH 句子极短(9 字/句)·搭配窗口(±6字上下文)会吃掉整句·没有"其余部分"可
# 当基线(这是保守设计的预期行为：宁可不附证据也不瞎凑基线)。用更贴近真实章节句长的
# 长句夹具单独测"证据确实能被正常附加"这条路径。

_ZEUGMA_LONG_SENT = (
    "那天下午他心情不错悠然地飞鸟儿掠过山岗看着天空发呆。"
    "可是没想到接下来的剧情急转直下他忽然发疯般地飞阿Q的灵魂远远地抛向天际无影无踪。"
) * 20


def test_surprisal_evidence_attached_when_model_enabled(monkeypatch):
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")

        def fake_predict(texts, ids=None):
            half = len(texts) // 2
            return ([{"mean_surprisal": 9.0, "source": "model"}] * half
                    + [{"mean_surprisal": 3.0, "source": "model"}] * (len(texts) - half))

        fake_bridge = types.SimpleNamespace(predict_batch=fake_predict)
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)

        out = mod.scan(_write(_ZEUGMA_LONG_SENT), _mk_project())
        # 主判断(词典)照常触发·不受 evidence 影响
        assert out["zeugma_count"] >= 1
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ZEUGMA_DETECTED" in codes
        # 补充证据已附加：窗口(9.0) / 基线(3.0) = 3.0 > 1.3 阈值 → is_spike
        evidenced = [s for s in out["samples"] if s.get("surprisal_evidence")]
        assert evidenced, "至少一条 sample 应带 surprisal_evidence"
        ev = evidenced[0]["surprisal_evidence"]
        assert ev["source"] == "model"
        assert ev["is_spike"] is True
        assert ev["ratio"] == 3.0
    finally:
        _set_mode(bak)


def test_surprisal_evidence_none_when_model_unavailable():
    """模型未开启(默认) → surprisal_evidence 全 None·主判断(count/violations)不受影响。"""
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        out = mod.scan(_write(_ZEUGMA_LONG_SENT), _mk_project())
        assert out["zeugma_count"] >= 1
        codes = {v["code"] for v in out.get("violations", [])}
        assert "ZEUGMA_DETECTED" in codes
        for s in out["samples"]:
            assert s.get("surprisal_evidence") is None
    finally:
        _set_mode(bak)


def test_surprisal_evidence_none_when_bridge_returns_none(monkeypatch):
    """RUOYU_NN_SURPRISAL=1 但 bridge 返回全 None → surprisal_evidence 仍全 None(不崩·不误判)。"""
    bak = os.environ.get("ZEUGMA_MODE")
    try:
        _set_mode("active")
        monkeypatch.setenv("RUOYU_NN_SURPRISAL", "1")
        fake_bridge = types.SimpleNamespace(
            predict_batch=lambda texts, ids=None: [None for _ in texts])
        monkeypatch.setitem(sys.modules, "nn_surprisal_bridge", fake_bridge)
        out = mod.scan(_write(_ZEUGMA_RICH), _mk_project())
        assert out["zeugma_count"] >= 1
        for s in out["samples"]:
            assert s.get("surprisal_evidence") is None
    finally:
        _set_mode(bak)


def test_collocation_span_locates_verb_obj():
    sentence = "然后飞阿Q远去无形"
    span = mod._collocation_span(sentence, "飞", "阿Q", 4)
    assert span is not None
    start, end = span
    hit = sentence[start:end]
    assert "飞" in hit
    assert "阿Q" in hit


def test_collocation_span_none_when_not_found():
    assert mod._collocation_span("完全无关的句子在此处", "飞", "阿Q", 4) is None


def test_attach_surprisal_evidence_does_not_mutate_dict_matching_fields():
    """附加证据只加 key·不改 detect_zeugma_pairs 已产出的核心字段(verb/obj1/obj2 等)。"""
    lexicon = mod.load_lexicon()
    pairs = mod.detect_zeugma_pairs(_ZEUGMA_RICH, lexicon)
    assert pairs, "夹具应能检出拈连候选"
    core_before = [{"verb": p["verb"], "obj1": p["obj1"], "obj2": p["obj2"],
                   "sent1_idx": p["sent1_idx"], "sent2_idx": p["sent2_idx"]} for p in pairs]
    pairs = mod._attach_surprisal_evidence(pairs, lexicon.get("_match_policy"))
    core_after = [{"verb": p["verb"], "obj1": p["obj1"], "obj2": p["obj2"],
                  "sent1_idx": p["sent1_idx"], "sent2_idx": p["sent2_idx"]} for p in pairs]
    assert core_before == core_after
    assert all("surprisal_evidence" in p for p in pairs)


def test_attach_surprisal_evidence_empty_pairs_noop():
    assert mod._attach_surprisal_evidence([], {}) == []


def _run_cli(draft_path, project=None, mode="active"):
    cmd = [sys.executable, str(_TARGET), str(draft_path)]
    if project:
        cmd += ["--project", str(project)]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ZEUGMA_MODE": mode, "PYTHONIOENCODING": "utf-8"})


def test_main_cli_returns_json():
    p = _write(_ZEUGMA_RICH)
    r = _run_cli(p, _mk_project())
    assert r.returncode in (0, 1), r.stderr
    rep = json.loads(r.stdout)
    assert rep["scanner"] == "zeugma"


def test_main_cli_thin_no_warning():
    p = _write(_THIN)
    r = _run_cli(p, _mk_project(), mode="shadow")
    rep = json.loads(r.stdout)
    # shadow 模式不上报
    assert rep.get("warning") is None
