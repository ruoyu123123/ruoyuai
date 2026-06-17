"""emotion_curve_rescan_scanner.py 专属回归测试（确定性·零依赖·零 LLM/零联网）。

聚焦 **现有 tests/test_emotion_curve_rescan.py 未覆盖** 的核心确定性逻辑（不重复）：
  - `_mode()`：env 三态 + 缺省/非法回退 shadow；
  - `_strip_changes()`：两种 CHANGES 分隔符剥离 + 幂等 + 无分隔不动；
  - `_pearson()`：n<3 / 长度不一 / 常数曲线 → None；完美正/负相关数值；
  - `segment_valence_curve`：混合情绪加权 valence 数学 + 段分组 chunk 数；
  - `scan`：草稿段落太少 (<3 段) → note 跳过分支；坏 manifest JSON → 优雅降级；
  - `main()` CLI（含 sys.exit）走 subprocess 跑真 CLI：shadow 漂移仍 exit 0；
    active 漂移 warning → exit 1（退出码契约：advisory scanner 恒不阻断·active warning 才 1）。

约定：标准库 only·test_* 无参·失败 raise AssertionError·tempfile/utf-8·Windows。
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import emotion_curve_rescan_scanner as ecr  # noqa: E402

_TARGET = _SCRIPTS / "emotion_curve_rescan_scanner.py"


def _write(p, text):
    Path(p).write_text(text, encoding="utf-8")


def _set_mode(m):
    if m is None:
        os.environ.pop("EMOTION_RESCAN_MODE", None)
    else:
        os.environ["EMOTION_RESCAN_MODE"] = m


# ══════════════════════════════════════════════════════════════════════════
# _mode() —— env 三态 + 回退
# ══════════════════════════════════════════════════════════════════════════
def test_mode_default_and_fallbacks():
    """缺省 → shadow；非法值 → shadow；大小写/空格归一化；三个合法值原样返回。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode(None)
        assert ecr._mode() == "shadow", "未设 env 应默认 shadow"
        _set_mode("garbage")
        assert ecr._mode() == "shadow", "非法值应回退 shadow"
        _set_mode("  Active  ")
        assert ecr._mode() == "active", "应 strip + lower 归一化"
        _set_mode("OFF")
        assert ecr._mode() == "off"
        _set_mode("shadow")
        assert ecr._mode() == "shadow"
    finally:
        _set_mode(bak)


# ══════════════════════════════════════════════════════════════════════════
# _strip_changes() —— 剥离尾部 CHANGES 段
# ══════════════════════════════════════════════════════════════════════════
def test_strip_changes_both_separators():
    """两种分隔符各自能剥；保留正文头部·去尾部；无分隔符原样返回。"""
    body = "第一段正文。\n\n第二段正文。"
    # FACTUAL 变体
    t1 = body + "\n---CHANGES_FACTUAL---\n{\"a\": 1}"
    assert ecr._strip_changes(t1) == body
    # 普通 CHANGES 变体
    t2 = body + "\n---CHANGES---\nsome json"
    assert ecr._strip_changes(t2) == body
    # 无分隔符 → 原样
    assert ecr._strip_changes(body) == body


def test_strip_changes_idempotent():
    """已剥离的文本再剥一次不变（幂等安全）。"""
    body = "正文内容在这里。"
    once = ecr._strip_changes(body + "\n---CHANGES---\nx")
    assert ecr._strip_changes(once) == once == body


# ══════════════════════════════════════════════════════════════════════════
# _pearson() —— 趋势相关（None 守卫 + 数值）
# ══════════════════════════════════════════════════════════════════════════
def test_pearson_none_guards():
    """n<3 / 长度不一 / 常数曲线（方差≈0）→ None（不判 drift）。"""
    assert ecr._pearson([1.0, 2.0], [1.0, 2.0]) is None, "n<3 → None"
    assert ecr._pearson([1.0, 2.0, 3.0], [1.0, 2.0]) is None, "长度不一 → None"
    assert ecr._pearson([0.5, 0.5, 0.5], [0.1, 0.5, 0.9]) is None, "常数曲线 → None"
    assert ecr._pearson([0.1, 0.5, 0.9], [0.5, 0.5, 0.5]) is None, "另一侧常数 → None"


def test_pearson_perfect_positive_and_negative():
    """完美正相关 → +1；完美负相关 → -1（捕捉「该升却降」是 drift 主判据）。"""
    up = [0.1, 0.3, 0.5, 0.7, 0.9]
    down = [0.9, 0.7, 0.5, 0.3, 0.1]
    pos = ecr._pearson(up, up)
    neg = ecr._pearson(up, down)
    assert pos is not None and abs(pos - 1.0) < 1e-6, f"完美正相关应 ~+1，得 {pos}"
    assert neg is not None and abs(neg - (-1.0)) < 1e-6, f"完美负相关应 ~-1，得 {neg}"
    # 负相关远低于 CORR_FLOOR → 触发 drift 判据
    assert neg < ecr.CORR_FLOOR


# ══════════════════════════════════════════════════════════════════════════
# segment_valence_curve() —— 加权 valence 数学 + 段分组
# ══════════════════════════════════════════════════════════════════════════
def test_segment_weighted_mixed_emotion():
    """单段混合情绪 → 按词频加权平均 valence（钉死确定性公式）。"""
    # 1 个 joyful(valence 1.0) "高兴" + 1 个 sad(valence 0.12) "悲伤"
    # 期望 = (1*1.0 + 1*0.12) / 2 = 0.56
    curve = ecr.segment_valence_curve("他高兴又悲伤。", 10)
    assert len(curve) == 1
    expected = round((1 * 1.0 + 1 * 0.12) / 2, 4)
    assert curve[0] == expected, f"加权 valence 应 {expected}，得 {curve[0]}"


def test_segment_chunk_grouping_count():
    """段落按 n_seg 分组：6 段请求 3 段 → ceil(6/3)=2 段一组 → 输出 3 个点。"""
    paras = "\n\n".join([f"第{i}段无情绪词的中性叙述文字。" for i in range(6)])
    curve = ecr.segment_valence_curve(paras, 3)
    assert len(curve) == 3, f"6 段切 3 组应得 3 点，得 {len(curve)}"
    # 全无情绪词 → 每点 0.5 中性
    assert all(v == 0.5 for v in curve)


def test_segment_strips_changes_before_counting():
    """正文尾部 CHANGES 段在分段前被剥离（不污染情绪计数）。"""
    # CHANGES 段里塞满 joyful 词，但应被剥掉 → 正文中性 0.5
    text = "一段平淡无情绪的叙述。\n---CHANGES---\n高兴高兴高兴笑笑笑兴奋"
    curve = ecr.segment_valence_curve(text, 10)
    assert curve == [0.5], f"CHANGES 段应被剥离，得 {curve}"


# ══════════════════════════════════════════════════════════════════════════
# scan() —— 未覆盖分支：段落太少 / 坏 manifest
# ══════════════════════════════════════════════════════════════════════════
def test_scan_too_few_paragraphs_skips():
    """草稿 <3 段（valence 曲线 <3 点）→ note 跳过·不对账·不 warning。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "draft.txt"
            _write(dp, "只有一段。\n\n两段而已。")  # 2 段 < 3
            out = ecr.scan(dp, None)
            assert out["warning"] is None
            assert out["violations"] == []
            assert "note" in out and "段落太少" in out["note"]
            assert "actual_curve" not in out  # 提前返回·未算 actual
    finally:
        _set_mode(bak)


def test_scan_malformed_manifest_graceful():
    """manifest 是坏 JSON → _find_target 返回 ([], None) → 只记 actual 不对账（不崩）。"""
    bak = os.environ.get("EMOTION_RESCAN_MODE")
    try:
        _set_mode("active")
        with tempfile.TemporaryDirectory() as td:
            dp = Path(td) / "draft.txt"
            _write(dp, "\n\n".join(["高兴笑了欣喜。"] * 5 + ["悲伤哭泣心痛。"] * 5))
            mp = Path(td) / "broken.json"
            _write(mp, "{ this is not valid json ]")
            out = ecr.scan(dp, mp)
            assert "actual_reagan_shape" in out      # actual 算出来了
            assert out["warning"] is None             # 无可用 target → 不判 drift
            assert "note" in out and "无 target" in out["note"]
    finally:
        _set_mode(bak)


# ══════════════════════════════════════════════════════════════════════════
# main() CLI —— 真 argparse + 真 scan + 退出码契约（subprocess）
# ══════════════════════════════════════════════════════════════════════════
def _run_cli(draft, manifest=None, mode="shadow"):
    """跑真 CLI；注入 EMOTION_RESCAN_MODE env。返回 (returncode, stdout_text)。"""
    env = dict(os.environ)
    env["EMOTION_RESCAN_MODE"] = mode
    args = [sys.executable, str(_TARGET), str(draft)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    p = subprocess.run(args, capture_output=True, cwd=str(_ROOT), env=env)
    stdout = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    return p.returncode, stdout


def test_cli_shadow_drift_exits_0():
    """shadow 模式即便趋势漂移也恒 exit 0（advisory scanner 绝不阻断流水线·北极星⑤）。"""
    with tempfile.TemporaryDirectory() as td:
        # actual 全程上升
        draft = "\n\n".join([
            "恐惧战栗颤抖害怕。", "紧张担心忐忑心跳。", "平静镇定冷静。",
            "好奇疑惑纳闷。", "高兴笑了欣喜。", "兴奋欣喜畅快高兴大笑高兴。",
        ])
        dp = Path(td) / "draft.txt"; _write(dp, draft)
        # target 全程下降 → 趋势相反
        mp = Path(td) / "m.json"
        _write(mp, json.dumps({"t": {
            "emotion_curve_full": [1.0, 0.9, 0.7, 0.5, 0.3, 0.1],
            "matched_reagan_shape": "Tragedy"}}, ensure_ascii=False))
        rc, out = _run_cli(dp, mp, mode="shadow")
        assert rc == 0, f"shadow 漂移仍须 exit 0，得 {rc}; out={out}"
        report = json.loads(out)
        assert report["mode"] == "shadow"
        assert report["warning"] is None
        assert report["violations"] == []          # shadow 不上报判决
        assert report["gate_level"] == "advisory"


def test_cli_active_drift_warning_exits_1():
    """active 模式趋势漂移 → 顶层 warning → exit 1（退出码契约：有 warning 才 1）。"""
    with tempfile.TemporaryDirectory() as td:
        draft = "\n\n".join([
            "恐惧战栗颤抖害怕。", "紧张担心忐忑心跳。", "平静镇定冷静。",
            "好奇疑惑纳闷。", "高兴笑了欣喜。", "兴奋欣喜畅快高兴大笑高兴。",
        ])
        dp = Path(td) / "draft.txt"; _write(dp, draft)
        mp = Path(td) / "m.json"
        _write(mp, json.dumps({"t": {
            "emotion_curve_full": [1.0, 0.9, 0.7, 0.5, 0.3, 0.1],
            "matched_reagan_shape": "Tragedy"}}, ensure_ascii=False))
        rc, out = _run_cli(dp, mp, mode="active")
        assert rc == 1, f"active 漂移须 exit 1，得 {rc}; out={out}"
        report = json.loads(out)
        assert report["mode"] == "active"
        assert report["warning"] is not None
        assert report["verdict"] == "FAIL_MINOR"
        assert report["violations"], "active 漂移应有 violation"
        assert report["violations"][0]["kind"] == "emotion_curve_drift"
        assert report["gate_level"] == "advisory"   # 即便 FAIL 也恒 advisory


def test_cli_off_mode_exits_0_no_scan():
    """off 模式 → 直接返回（不读草稿）→ exit 0·verdict PASS。"""
    with tempfile.TemporaryDirectory() as td:
        # 故意给不存在的草稿路径：off 不读草稿也不该崩
        rc, out = _run_cli(Path(td) / "nonexistent.txt", None, mode="off")
        assert rc == 0, f"off 应 exit 0，得 {rc}; out={out}"
        report = json.loads(out)
        assert report["mode"] == "off"
        assert report["verdict"] == "PASS"
        assert report["warning"] is None


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[emotion_curve_rescan_scanner] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
