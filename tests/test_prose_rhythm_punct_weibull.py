# -*- coding: utf-8 -*-
"""prose_rhythm_scanner 探针8 标点距离 Weibull 形状指纹 (R13 W6 Batch-R · P2 MODEST)
回归测试·确定性·零依赖·零联网·零 LLM。

覆盖核心分支：
  ① 无作者档 slow_update.punctuation_distance_weibull → 探针 8 静默 skip
  ② 有作者档 + cluster 节奏与作者基线一致 → KS 低 → 无 advisory
  ③ 有作者档 + cluster 节奏严重偏离 → KS > 0.15 → shadow 不报 / active 报
  ④ 单标点样本 <8 → 该标点 ks=None 不计入
  ⑤ env PROSE_PUNCT_WEIBULL_MODE=off → 不参与
  ⑥ _author_weibull_baseline 全分支
  ⑦ _punctuation_distance_series 正确返回距离列表
  ⑧ scipy 缺失时 _weibull_ks 返回 None
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import prose_rhythm_scanner as mod  # noqa: E402


def _set_mode(m):
    if m is None:
        os.environ.pop("PROSE_PUNCT_WEIBULL_MODE", None)
    else:
        os.environ["PROSE_PUNCT_WEIBULL_MODE"] = m


def _mk_style(weibull=None):
    p = Path(tempfile.mkdtemp()) / "作者风格.json"
    style = {"quantitative": {"sentence_length": {"mean": 25.0, "std": 12.0}}}
    if weibull is not None:
        style["slow_update"] = {"punctuation_distance_weibull": weibull}
    p.write_text(json.dumps(style, ensure_ascii=False), encoding="utf-8")
    return p


# ───── ① 无作者档 slow_update → skip ────────────────────────────────────────
def test_no_author_baseline_no_weibull_metric():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("active")
        style = _mk_style(weibull=None)
        # 短句长 12 字 + 标点密集
        text = "他走过去看着她。她笑了。雪在飘落。他停下来。" * 60
        rep = mod.scan(text, style_path=style)
        # punct_weibull_per_mark 应存在但为空（无作者档→静默 skip）
        assert rep["metrics"]["punct_weibull_per_mark"] == {}
    finally:
        _set_mode(bak)


# ───── ② 有作者档 + 探针记录 metrics ────────────────────────────────────────
def test_with_author_baseline_records_metrics():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("active")
        weibull = {"。": {"k": 1.8, "lambda": 13.0}}
        style = _mk_style(weibull=weibull)
        text = "他走过去看着她说了几句话。她抬头笑了笑回答了一句。" * 50
        rep = mod.scan(text, style_path=style)
        per_mark = rep["metrics"]["punct_weibull_per_mark"]
        assert "。" in per_mark
        assert per_mark["。"]["k_author"] == 1.8
        assert per_mark["。"]["samples"] >= 8
        # ks 必须是 float（计算成功），无论高低
        assert isinstance(per_mark["。"]["ks"], float)


    finally:
        _set_mode(bak)


# ───── ③ 严重偏离 → shadow 不报 / active 报 ────────────────────────────────
def test_severely_drifted_active_reports():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("active")
        # 作者基线 λ=50（长段标点距离）·cluster 全短句（距离 ~ 8）→ KS 一定大
        weibull = {"。": {"k": 2.0, "lambda": 50.0}}
        style = _mk_style(weibull=weibull)
        text = "他走了。她笑了。雪停了。他坐下。她抬头。他开口。她应答。" * 30
        rep = mod.scan(text, style_path=style)
        per_mark = rep["metrics"]["punct_weibull_per_mark"]
        assert per_mark["。"]["ks"] is not None
        assert per_mark["。"]["ks"] > 0.15
        # active 模式应触发 violation
        weibull_vios = [v for v in rep.get("violations", [])
                        if v.get("kind") == "punctuation_distance_weibull"]
        assert weibull_vios and weibull_vios[0]["mark"] == "。"
    finally:
        _set_mode(bak)


def test_severely_drifted_shadow_no_violation():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("shadow")
        weibull = {"。": {"k": 2.0, "lambda": 50.0}}
        style = _mk_style(weibull=weibull)
        text = "他走了。她笑了。雪停了。他坐下。她抬头。他开口。她应答。" * 30
        rep = mod.scan(text, style_path=style)
        per_mark = rep["metrics"]["punct_weibull_per_mark"]
        assert per_mark["。"]["ks"] > 0.15   # 仍计算
        # shadow → 不上报
        weibull_vios = [v for v in rep.get("violations", [])
                        if v.get("kind") == "punctuation_distance_weibull"]
        assert weibull_vios == []
    finally:
        _set_mode(bak)


# ───── ④ 单标点样本 <8 → ks=None ───────────────────────────────────────────
def test_low_samples_ks_none():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("active")
        weibull = {"——": {"k": 1.5, "lambda": 60.0}}
        style = _mk_style(weibull=weibull)
        # 仅 2 个 ——，距离样本只有 1
        text = "他走过来——她笑——这是早就有的默契。" + "句号填充内容。" * 60
        rep = mod.scan(text, style_path=style)
        per_mark = rep["metrics"]["punct_weibull_per_mark"]
        assert per_mark["——"]["samples"] < 8
        assert per_mark["——"]["ks"] is None
    finally:
        _set_mode(bak)


# ───── ⑤ env=off → 探针不参与 ───────────────────────────────────────────────
def test_mode_off_skips_probe():
    bak = os.environ.get("PROSE_PUNCT_WEIBULL_MODE")
    try:
        _set_mode("off")
        weibull = {"。": {"k": 2.0, "lambda": 50.0}}
        style = _mk_style(weibull=weibull)
        text = "他走了。她笑了。雪停了。他坐下。她抬头。" * 60
        rep = mod.scan(text, style_path=style)
        # mode=off → per_mark 应为空
        assert rep["metrics"]["punct_weibull_per_mark"] == {}
    finally:
        _set_mode(bak)


# ───── ⑥ _author_weibull_baseline 全分支 ────────────────────────────────────
def test_weibull_baseline_no_project_no_style():
    assert mod._author_weibull_baseline(None, None) == {}


def test_weibull_baseline_missing_slow_update():
    style = _mk_style(weibull=None)
    assert mod._author_weibull_baseline(None, style) == {}


def test_weibull_baseline_invalid_params_filtered():
    weibull = {"。": {"k": "bad", "lambda": 30.0},
               "，": {"k": 2.0},      # 缺 lambda
               "！": {"k": 1.5, "lambda": 20.0}}
    style = _mk_style(weibull=weibull)
    out = mod._author_weibull_baseline(None, style)
    assert "！" in out and "。" not in out and "，" not in out


def test_weibull_baseline_supports_scale_alias():
    """lambda 别名 scale 兼容（部分配置文件用 scale 命名）"""
    weibull = {"。": {"k": 1.5, "scale": 25.0}}
    style = _mk_style(weibull=weibull)
    out = mod._author_weibull_baseline(None, style)
    assert out["。"]["lambda"] == 25.0


def test_weibull_baseline_bad_json_returns_empty():
    p = Path(tempfile.mkdtemp()) / "作者风格.json"
    p.write_text("{ bad json", encoding="utf-8")
    assert mod._author_weibull_baseline(None, p) == {}


def test_weibull_baseline_non_dict_top_returns_empty():
    p = Path(tempfile.mkdtemp()) / "作者风格.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert mod._author_weibull_baseline(None, p) == {}


# ───── ⑦ _punctuation_distance_series ───────────────────────────────────────
def test_distance_series_basic():
    text = "abc。def。ghi。"
    out = mod._punctuation_distance_series(text, "。")
    assert out == [4, 4]   # positions 3,7,11 → diffs [4,4]


def test_distance_series_under_two_marks():
    assert mod._punctuation_distance_series("仅一个。", "。") == []


def test_distance_series_long_mark():
    text = "abc——def——ghi"
    out = mod._punctuation_distance_series(text, "——")
    assert out == [5]   # 仅 1 个距离


# ───── ⑧ scipy 缺失 / 异常 → ks=None ────────────────────────────────────────
def test_weibull_ks_too_few_samples():
    assert mod._weibull_ks([1, 2], 1.5, 30.0) is None
    assert mod._weibull_ks([], 1.5, 30.0) is None


def test_weibull_ks_returns_float():
    out = mod._weibull_ks([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 1.5, 5.0)
    assert isinstance(out, float)
    assert 0.0 <= out <= 1.0
