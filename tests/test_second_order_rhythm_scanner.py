# -*- coding: utf-8 -*-
"""round2#3 second_order_rhythm_scanner 测试（2026-06-16·二阶句长节奏·order-sensitive 补探针7 盲区）。

🔴 三重验证：① 真作者原文 active 0 误报（Δ²var/var≥3.78>3.0 金标准）② 合成平滑正弦缓变 FAIL（判别力·
order-sensitive 真抓时序塌缩·探针7 的 permutation-invariant std 看不见）③ 纯匀速 var=0→None 与探针7
解耦防双计数。code 不进 hard_gate·默认 shadow。零依赖·仓库根 pytest 入口。
"""
import glob
import io
import math
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import second_order_rhythm_scanner as S  # noqa: E402


def _set_mode(v):
    if v is None:
        os.environ.pop("SECOND_ORDER_RHYTHM_MODE", None)
    else:
        os.environ["SECOND_ORDER_RHYTHM_MODE"] = v


def _w(text):
    tf = Path(tempfile.mkdtemp()) / "draft.txt"
    tf.write_text(text, encoding="utf-8")
    return str(tf)


def test_default_shadow():
    """env 未设 → 默认 shadow（影子·检测力待 gen-model 验证）。"""
    _set_mode(None)
    assert S._mode() == "shadow"


def test_active_smooth_sine_fails():
    """🔴 order-sensitive 判别力：合成平滑正弦缓变（var 大·二阶平滑）→ FAIL（探针7 的 std 看不见）。"""
    _set_mode("active")
    try:
        lens = [int(15 + 8 * math.sin(i * 0.15)) for i in range(200)]
        text = "\n".join("他" + "走" * (L - 2) + "。" for L in lens)
        r = S.scan(_w(text))
        assert r["verdict"] == "FAIL_MINOR", r
        assert r["second_diff_var_normed"] < S.SECOND_ORDER_FLOOR, r
    finally:
        _set_mode(None)


def test_flat_decoupled_from_probe7():
    """纯匀速（var=0）→ None 不报（探针7 管·解耦防双计数·北极星）。"""
    _set_mode("active")
    try:
        text = "\n".join("他走走走走走。" for _ in range(200))
        r = S.scan(_w(text))
        assert r["verdict"] == "PASS" and r["second_diff_var_normed"] is None, r
    finally:
        _set_mode(None)


def test_golden_real_author_no_false_positive():
    """🔴 金标准：真作者原文 active → 0 误报（Δ²var/var≥3.78>3.0·跨作者稳定）。本地无 → skip。"""
    cands = glob.glob(str(_ROOT / "workspace" / "styles" / "*" / "原文"))
    _set_mode("active")
    ran = 0
    try:
        for d in cands[:6]:
            raws = sorted(glob.glob(str(Path(d) / "第0*章.txt")))[:6]
            if len(raws) < 6:
                continue
            text = "".join(io.open(f, encoding="utf-8").read() for f in raws)
            r = S.scan(_w(text))
            assert r["verdict"] == "PASS", (Path(d).parent.name, r)
            ran += 1
    finally:
        _set_mode(None)
    if ran == 0:
        print("[SKIP] 真作者原文本地无·金标准跳过")


def test_code_never_hard_gate():
    """制度锁：SECOND_ORDER_RHYTHM_FLAT 绝不进 HARD_GATE_CODES（北极星⑤）。"""
    import audit_hub
    assert "SECOND_ORDER_RHYTHM_FLAT" not in audit_hub.HARD_GATE_CODES
    assert audit_hub._gate_level_for("SECOND_ORDER_RHYTHM_FLAT", "error") == "advisory"


def test_few_sentences_skip():
    """句子太少（<10）→ skip（不可估）。"""
    _set_mode("active")
    try:
        r = S.scan(_w("他来。她走。"))
        assert r["verdict"] == "PASS" and "句子太少" in r.get("note", "")
    finally:
        _set_mode(None)
