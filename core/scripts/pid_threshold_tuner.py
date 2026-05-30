#!/usr/bin/env python3
"""pid_threshold_tuner.py — L2-1 保守增量 PI 阈值控制器（2026-05-30）

【为什么有这个模块（北极星⑤顾问非法官 · 防矫枉过正）】
L2-0（audit_hub soft-cap）是「降档止血」：命中即降一档 severity，事后补救。
L2-1 升级为完整保守增量 PI 控制器（对标 Filieri SEAMS2015 自适应软件控制 / CFAR
恒虚警率）—— 主动把真作者原文被误判的虚警率(FPR)驱动到 0，同时不放任 AI 检出率
崩塌（双目标）。控制对象严格圈定 4 个连续 advisory 阈值：

    para_mean_len.max / dialogue_ratio(.min/.max) / long_para_per_chapter / quota_per_word.max

【双目标误差】e = w1·(真作者FPR − 0) − w2·(AI检出率 − τ)
  · FPR>0（真作者被误 FAIL）→ e>0 → 放松阈值（朝放宽方向）。
  · AI 检出率 < τ（阈值太松漏检 AI 腔）→ (rate−τ)<0 → e 减小 → 收紧。
  · 回测无 AI 样本时 ai_detect_rate=None → 退化单目标（只压 FPR）。

【增量式 PI（天然抗 windup）】Δθ = Kp·(e − e_prev) + Ki·e
  增量式不累加积分项历史绝对量（只记 e_prev），天然抗 integrator windup。

【防震荡三件套】
  1. 死区随样本量缩放：deadband = BASE_DEADBAND × sqrt(MIN_SAMPLES / max(n,1))
     —— 样本越少死区越大（少样本 FPR 噪声大·不轻易动阈值）。
  2. band 硬边界 clamp：每个键 Δ 钳进物理安全 band（绝不破物理边界）。
  3. 低频每 cluster 更新：state 记 last_update_cluster·同 cluster 不重复迭代。

【保守增益】Kp=0.2·Ku（Ziegler-Nichols 0.45Ku 的更保守版）；无回测 Ku 时极保守
缺省 Kp=0.05 / Ki=0.02。

【物理隔离回路外（北极星⑤ + 本件红线）】
  · _CONTROLLED_KEYS 白名单只含 4 键 · 控制器硬拒其余键（raise + 过滤）。
  · 15 个 HARD_GATE_CODES 绝不出现在本模块任何路径（结构性物理隔离）。
  · env PID_THRESHOLD_MODE 默认 off：回测验证 FPR 收敛前完全不生效。

【per 作者状态】workspace/styles/{作者}/pid_threshold_state.json
  {"version":1,"e_prev":{key:float},"theta_delta":{key:{"_scalar":float}},
   "n_samples":int,"last_update_cluster":str,"fpr_history":[...],"updated_at":...}
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

# 被控 4 键白名单（物理隔离 · 硬拒其余）
_CONTROLLED_KEYS = ("para_mean_len", "dialogue_ratio", "long_para_per_chapter", "quota_per_word")

# 每键物理安全 band（clamp 用 · PID Δ 叠加后阈值绝不越界）
_PHYS_BOUNDS = {
    "para_mean_len":         {"max": (10.0, 80.0)},
    "dialogue_ratio":        {"min": (0.0, 0.50), "max": (0.40, 1.0)},
    "long_para_per_chapter": {"max_ratio": (0.01, 0.30), "max": (1.0, 12.0)},
    "quota_per_word":        {"max": (3.0, 12.0)},
}

# 保守增益（无回测 Ku 时极保守缺省）
_DEFAULT_KP = 0.05
_DEFAULT_KI = 0.02
# 双目标权重（FPR 优先）
_W1_FPR = 1.0
_W2_AI = 0.5
# 死区基线 + 最小样本（死区随样本量缩放）
_BASE_DEADBAND = 0.05
_MIN_SAMPLES = 8
# 单步 Δ 最大幅度（保守限幅·防单 cluster 跳变）
_MAX_STEP_FRAC = 0.10


def _mode() -> str:
    """PID_THRESHOLD_MODE：默认 off（回测验证前不生效）· {off, shadow, active}。"""
    m = (os.environ.get("PID_THRESHOLD_MODE") or "off").strip().lower()
    return m if m in ("off", "shadow", "active") else "off"


def _state_path(author_dir: Path) -> Path:
    return Path(author_dir) / "pid_threshold_state.json"


def load_state(author_dir: Path) -> dict:
    p = _state_path(author_dir)
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("e_prev", {})
                d.setdefault("theta_delta", {})
                d.setdefault("n_samples", 0)
                return d
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return {"version": 1, "e_prev": {}, "theta_delta": {}, "n_samples": 0,
            "last_update_cluster": None, "fpr_history": [], "updated_at": None}


def save_state(author_dir: Path, state: dict) -> None:
    p = _state_path(author_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _deadband(n_samples: int) -> float:
    """死区随样本量缩放：样本越少死区越大（少样本噪声大·不轻易动阈值）。"""
    return _BASE_DEADBAND * math.sqrt(_MIN_SAMPLES / max(n_samples, 1))


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def compute_error(fpr: float, ai_detect_rate: float | None, tau: float = 0.8,
                  w1: float = _W1_FPR, w2: float = _W2_AI) -> float:
    """双目标误差 e = w1·(FPR−0) − w2·(AI检出率−τ)。
    ai_detect_rate=None（回测无 AI 样本）→ 退化单目标 e = w1·FPR。"""
    e = w1 * (fpr - 0.0)
    if ai_detect_rate is not None:
        e -= w2 * (ai_detect_rate - tau)
    return e


def _pi_step(e: float, e_prev: float, kp: float, ki: float) -> float:
    """增量式 PI（天然抗 windup）：Δ = Kp·(e − e_prev) + Ki·e。"""
    return kp * (e - e_prev) + ki * e


def update_controller(state: dict, errors: dict, cluster_id: str | None = None,
                      kp: float = _DEFAULT_KP, ki: float = _DEFAULT_KI,
                      n_samples: int | None = None) -> dict:
    """单步迭代：对每个被控键算增量 PI Δ（含死区/限幅），更新 state（不写盘）。
    errors: {key: e}（compute_error 产）。低频：同 cluster 不重复更新。
    硬拒非白名单键（北极星红线 · raise ValueError）。"""
    if cluster_id is not None and state.get("last_update_cluster") == cluster_id:
        return state  # 低频每 cluster 更新：同 cluster 已迭代过
    n = n_samples if n_samples is not None else state.get("n_samples", 0)
    db = _deadband(n)
    e_prev = state.setdefault("e_prev", {})
    theta = state.setdefault("theta_delta", {})
    for key, e in errors.items():
        if key not in _CONTROLLED_KEYS:
            raise ValueError(f"PID 拒绝非白名单键: {key}（仅允许 {_CONTROLLED_KEYS}）")
        if abs(e) < db:
            e_prev[key] = e
            continue  # 死区内：不动阈值（只记 e_prev）
        delta = _pi_step(e, e_prev.get(key, 0.0), kp, ki)
        delta = _clamp(delta, -_MAX_STEP_FRAC, _MAX_STEP_FRAC)  # 单步限幅（保守）
        cur = theta.get(key)
        prev_d = cur.get("_scalar", 0.0) if isinstance(cur, dict) else 0.0
        theta[key] = {"_scalar": prev_d + delta}
        e_prev[key] = e
    if cluster_id is not None:
        state["last_update_cluster"] = cluster_id
    if n_samples is not None:
        state["n_samples"] = n_samples
    return state


def _direction_unit(key: str, cfg: dict) -> dict:
    """把标量 Δ 映射到该键「放松方向」（e>0=FPR 高 → 放松）。
    返回 {sub_key: (当前基准值, 符号)}（符号决定放松 = 上调/下调）。"""
    units = {}
    if key == "para_mean_len":
        if "max" in cfg:
            units["max"] = (cfg["max"], +1)            # 放松 = max 上调
    elif key == "dialogue_ratio":
        if "min" in cfg:
            units["min"] = (cfg["min"], -1)            # 放松 = min 下调
        if "max" in cfg:
            units["max"] = (cfg["max"], +1)            # 放松 = max 上调
    elif key == "long_para_per_chapter":
        if "max_ratio" in cfg:
            units["max_ratio"] = (cfg["max_ratio"], +1)
        elif "max" in cfg:
            units["max"] = (cfg["max"], +1)
    elif key == "quota_per_word":
        if "max" in cfg:
            units["max"] = (cfg["max"], +1)
    return units


def apply_pid_delta(thresholds: dict, author_dir):
    """validate_style._apply_style_overrides 末尾叠加点：把 per-作者 PID Δ 叠到
    4 个被控键（作者档前馈已先施加·此为 PID 反馈微调·前馈优先级高于反馈）。

    · PID_THRESHOLD_MODE=off（默认）/ author_dir 缺失 → 返回原 thresholds（不改判决）。
    · shadow → 把将施加的 Δ 记 stderr·返回原阈值（零回归·观察用）。
    · active → 真叠加 Δ，每键钳进 _PHYS_BOUNDS（绝不破物理安全栏）。
    硬拒非白名单：只读写 _CONTROLLED_KEYS，其余键原样不动（物理隔离 HARD_GATE 回路外）。"""
    mode = _mode()
    if mode == "off" or author_dir is None:
        return thresholds
    state = load_state(Path(author_dir))
    theta = state.get("theta_delta", {})
    if not theta:
        return thresholds
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in thresholds.items()}
    for key in _CONTROLLED_KEYS:
        td = theta.get(key)
        if not isinstance(td, dict) or "_scalar" not in td:
            continue
        scalar = float(td["_scalar"])
        if scalar == 0.0 or key not in out or not isinstance(out[key], dict):
            continue
        cfg = dict(out[key])
        bounds = _PHYS_BOUNDS.get(key, {})
        for sub, (base, sign) in _direction_unit(key, cfg).items():
            if base is None:
                continue
            # Δ 按当前基准值比例施加（相对调整·尺度自适应不同量纲键）
            new_val = base + sign * scalar * abs(base if base else 1.0)
            lo, hi = bounds.get(sub, (-math.inf, math.inf))
            new_val = _clamp(new_val, lo, hi)
            if mode == "active":
                cfg[sub] = new_val
            else:  # shadow
                try:
                    print(f"[PID shadow] {key}.{sub}: {base:.4g} -> {new_val:.4g} "
                          f"(scalar={scalar:+.4g})", file=sys.stderr)
                except Exception:
                    pass
        if mode == "active":
            out[key] = cfg
    return out


# ── 离线回测（先行 · 不收敛不接线）────────────────────────────────
def _author_chapters(author_dir: Path, max_chapters: int | None = None) -> list[Path]:
    src = Path(author_dir) / "原文"
    if not src.is_dir():
        return []
    import re
    chs = sorted([p for p in src.glob("第*章.txt")
                  if re.search(r"第(\d+)章", p.name)],
                 key=lambda x: int(re.search(r"第(\d+)章", x.name).group(1)))
    if max_chapters and len(chs) > max_chapters:
        # 均匀采样（CFAR 离线校准惯例·代表性不偏首尾）
        step = len(chs) / max_chapters
        chs = [chs[int(i * step)] for i in range(max_chapters)]
    return chs


def _measure_fpr_per_key(author_dir: Path, style_path: Path, thresholds: dict,
                         max_chapters: int | None = None) -> dict:
    """对作者原文逐章跑 validate_style 的 4 个被控检查，统计 per-key FPR。
    真作者原文被某检查 FAIL = 该检查的虚警（FPR 应趋 0）。返回 {key: fpr}。"""
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    vs = importlib.import_module("validate_style")
    sa = importlib.import_module("style_analyzer")
    chs = _author_chapters(author_dir, max_chapters)
    if not chs:
        return {}
    counts = {k: 0 for k in _CONTROLLED_KEYS}
    n = 0
    for ch in chs:
        try:
            text = ch.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip():
            continue
        n += 1
        p = sa.analyze_text(text)
        # para_mean_len.max
        if p["paragraph_stats"]["mean"] > thresholds["para_mean_len"]["max"]:
            counts["para_mean_len"] += 1
        # dialogue_ratio.min/.max
        dr = p["dialogue_ratio"]
        drb = thresholds["dialogue_ratio"]
        if dr < drb.get("min", 0) or dr > drb.get("max", 1):
            counts["dialogue_ratio"] += 1
        # quota_per_word.max
        if sum((p.get("quota_word_hits") or {}).values()) > thresholds["quota_per_word"]["max"]:
            counts["quota_per_word"] += 1
        # long_para_per_chapter (复用 validate_style 官方计数口径：(warn,hard] 区间)
        lp = thresholds["long_para_per_chapter"]
        pm = thresholds.get("para_max_chars", {"warn": 80, "hard_gate": 120})
        lens = vs._para_cjk_lens(text)
        warn_th, hard_th = pm.get("warn", 80), pm.get("hard_gate", 120)
        long_n = sum(1 for n in lens if warn_th < n <= hard_th)
        total_paras = len(lens) or 1
        if "max_ratio" in lp:
            if long_n > int(total_paras * lp["max_ratio"]):
                counts["long_para_per_chapter"] += 1
        elif "max" in lp and long_n > lp["max"]:
            counts["long_para_per_chapter"] += 1
    return {k: (counts[k] / n if n else 0.0) for k in _CONTROLLED_KEYS}


def backtest(author_dir: Path, style_path: Path, rounds: int = 20,
             tau: float = 0.8, verbose: bool = True, max_chapters: int | None = None) -> dict:
    """离线回测：用作者原文 + 作者档跑 FPR 收敛曲线。
    每轮：测 per-key FPR → compute_error → update_controller（active 模拟）→ 施加 Δ → 重测。
    返回 {converged: bool, fpr_curve: [...], final_fpr: {...}}。"""
    import importlib
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    vs = importlib.import_module("validate_style")
    sd = json.loads(Path(style_path).read_text(encoding="utf-8"))
    base = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    thr = vs._apply_style_overrides(base, sd)   # 作者档前馈先施加
    state = {"version": 1, "e_prev": {}, "theta_delta": {}, "n_samples": 0,
             "last_update_cluster": None, "fpr_history": []}
    curve = []
    n_ch = len(_author_chapters(author_dir, max_chapters))
    for r in range(rounds):
        fpr_by_key = _measure_fpr_per_key(author_dir, style_path, thr, max_chapters)
        if not fpr_by_key:
            return {"converged": False, "reason": "no_chapters", "fpr_curve": []}
        mean_fpr = sum(fpr_by_key.values()) / len(fpr_by_key)
        curve.append(round(mean_fpr, 4))
        errors = {k: compute_error(fpr_by_key[k], None, tau) for k in _CONTROLLED_KEYS}
        state["n_samples"] = n_ch
        update_controller(state, errors, cluster_id=f"backtest_round_{r}", n_samples=n_ch)
        # 施加 active Δ 到 thr（模拟 apply_pid_delta active 路径）
        old = os.environ.get("PID_THRESHOLD_MODE")
        os.environ["PID_THRESHOLD_MODE"] = "active"
        try:
            thr = _apply_state_delta(thr, state)
        finally:
            if old is None:
                os.environ.pop("PID_THRESHOLD_MODE", None)
            else:
                os.environ["PID_THRESHOLD_MODE"] = old
        if verbose:
            print(f"  round {r}: mean_FPR={mean_fpr:.4f}  per_key={fpr_by_key}", file=sys.stderr)
    final = _measure_fpr_per_key(author_dir, style_path, thr, max_chapters)
    final_mean = sum(final.values()) / len(final) if final else 1.0
    converged = final_mean <= curve[0] and final_mean <= 0.15
    return {"converged": bool(converged), "fpr_curve": curve,
            "initial_fpr": curve[0] if curve else None,
            "final_fpr": round(final_mean, 4), "final_per_key": final}


def _apply_state_delta(thresholds: dict, state: dict) -> dict:
    """回测内联：把 state.theta_delta 直接叠到 thresholds（不读盘·复用 apply 逻辑核心）。"""
    theta = state.get("theta_delta", {})
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in thresholds.items()}
    for key in _CONTROLLED_KEYS:
        td = theta.get(key)
        if not isinstance(td, dict) or "_scalar" not in td:
            continue
        scalar = float(td["_scalar"])
        if scalar == 0.0 or key not in out or not isinstance(out[key], dict):
            continue
        cfg = dict(out[key])
        bounds = _PHYS_BOUNDS.get(key, {})
        for sub, (base, sign) in _direction_unit(key, cfg).items():
            if base is None:
                continue
            new_val = base + sign * scalar * abs(base if base else 1.0)
            lo, hi = bounds.get(sub, (-math.inf, math.inf))
            cfg[sub] = _clamp(new_val, lo, hi)
        out[key] = cfg
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="L2-1 PID 阈值控制器 / 离线回测")
    ap.add_argument("--backtest", action="store_true", help="跑离线 FPR 收敛回测")
    ap.add_argument("--author-dir", required=True, help="workspace/styles/{作者}")
    ap.add_argument("--style", required=True, help="作者风格.json 路径")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--tau", type=float, default=0.8)
    ap.add_argument("--max-chapters", type=int, default=None,
                    help="均匀采样上限（大书加速回测·留空=全量）")
    args = ap.parse_args(argv)
    if args.backtest:
        res = backtest(Path(args.author_dir), Path(args.style), args.rounds, args.tau,
                       max_chapters=args.max_chapters)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("converged") else 1
    print("用法: --backtest --author-dir <dir> --style <json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
