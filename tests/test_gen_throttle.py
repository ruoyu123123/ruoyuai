#!/usr/bin/env python3
"""gen_throttle 全局限速器测试（限速端点/中转站支持·真 e2e 暴露）。

默认关=零回归 · 设 GEN_MIN_INTERVAL_S 生效 · 4 处请求点(gen_writer 2 + llm_transport 2)接线。
"""
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_throttle as gt  # noqa: E402


def test_throttle_off_by_default_zero_regression():
    saved = os.environ.get("GEN_MIN_INTERVAL_S")
    os.environ.pop("GEN_MIN_INTERVAL_S", None)
    try:
        gt._last_call[0] = 0.0
        t0 = time.monotonic()
        gt.wait()
        gt.wait()
        assert time.monotonic() - t0 < 0.3, "默认应不限速（零回归）"
    finally:
        if saved is not None:
            os.environ["GEN_MIN_INTERVAL_S"] = saved


def test_throttle_enforces_min_interval():
    saved = os.environ.get("GEN_MIN_INTERVAL_S")
    os.environ["GEN_MIN_INTERVAL_S"] = "0.5"
    try:
        gt._last_call[0] = 0.0
        gt.wait()                       # 首次：_last_call=0 → 不等
        t1 = time.monotonic()
        gt.wait()                       # 第二次：须等 ~0.5s
        dt = time.monotonic() - t1
        assert 0.4 < dt < 0.9, f"min-interval 未生效（等了 {dt:.2f}s）"
    finally:
        if saved is None:
            os.environ.pop("GEN_MIN_INTERVAL_S", None)
        else:
            os.environ["GEN_MIN_INTERVAL_S"] = saved
        gt._last_call[0] = 0.0


def test_throttle_invalid_env_falls_to_off():
    saved = os.environ.get("GEN_MIN_INTERVAL_S")
    os.environ["GEN_MIN_INTERVAL_S"] = "not_a_number"
    try:
        gt._last_call[0] = 0.0
        t0 = time.monotonic()
        gt.wait()
        gt.wait()
        assert time.monotonic() - t0 < 0.3, "非法 env 应回退不限速"
    finally:
        if saved is None:
            os.environ.pop("GEN_MIN_INTERVAL_S", None)
        else:
            os.environ["GEN_MIN_INTERVAL_S"] = saved


def test_request_points_wired():
    """4 处 gen-model 请求点都调了 gen_throttle.wait()（gen_writer 2 + llm_transport 2）。"""
    gw = (_ROOT / "core" / "scripts" / "gen_writer.py").read_text(encoding="utf-8")
    lt = (_ROOT / "core" / "scripts" / "llm_transport.py").read_text(encoding="utf-8")
    assert gw.count("gen_throttle.wait()") >= 2, "gen_writer 未在 2 处请求点接节流"
    assert lt.count("gen_throttle.wait()") >= 2, "llm_transport 未在 2 处请求点接节流"


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
