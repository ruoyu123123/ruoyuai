#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN连贯性评分集成
"""nn_coherence_bridge.py — 系统 py(3.14·无 torch) → venv py(3.10·torch) 的连贯性推理 subprocess 桥。

【为什么是桥】若渝主流水线跑系统 Python（无 torch）；连贯性评分模型跑 core/ml/.venv（torch）。
两进程隔离。系统侧组件（save_state / scanner）**不能直接 import 模型**，而是经本桥用 subprocess
批量调 `core/ml/.venv/Scripts/python.exe core/ml/coherence/coherence_infer.py --batch in.jsonl --out out.jsonl`
做推理（批量·非实时·每 cluster 一次性整批调用以摊薄模型加载开销）。

【两种调用形态】
  · predict_batch(texts)        — 单文本窗口连贯性（每行 {"text": ...}）
  · predict_pairs(pairs)        — 文本对衔接连贯性（每行 {"text_a": ..., "text_b": ...}·加 --mode pairs）

【默认安全铁律（北极星⑤·零回归）】以下任一情况 → 返回 None（逐条）→ **调用方回退启发式**，绝不崩：
  · RUOYU_NN_COHERENCE != "1"（默认 off·门控未开）
  · venv python 不存在 / coherence_infer.py 不存在
  · checkpoint 不存在（RUOYU_COHERENCE_CKPT 未设且默认 ckpt 缺）
  · subprocess 失败 / 超时 / 退码非 0 / 输出缺失 / 解析失败 / 条数失配
  · coherence_infer 退非模型模式（source != "model"）→ 视为「模型不可用」→ None（让调用方用自己的启发式）

→ 若渝必须「无 NN 也能跑」：env 默认 off 时本桥所有函数等价 no-op。

【输出契约】predict_batch(texts) / predict_pairs(pairs) → list[dict|None]，与输入一一对应；
  命中模型的元素 = {"coherence_score": float, "is_coherent": bool, "source": "model"}，
  其余 = None。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# core/scripts/nn_coherence_bridge.py → core/ 根
_CORE = Path(__file__).resolve().parent.parent
_VENV_PY = _CORE / "ml" / ".venv" / "Scripts" / "python.exe"   # Windows venv
_VENV_PY_POSIX = _CORE / "ml" / ".venv" / "bin" / "python"     # POSIX venv（容错）
_COHERENCE_INFER = _CORE / "ml" / "coherence" / "coherence_infer.py"
_DEFAULT_CKPT = _CORE / "ml" / "coherence" / "runs" / "coherence_v1"

_DEFAULT_TIMEOUT = 180   # 秒·模型加载 + 批量推理（非实时·宁慢勿挂）


def _venv_python() -> "Path | None":
    if _VENV_PY.exists():
        return _VENV_PY
    if _VENV_PY_POSIX.exists():
        return _VENV_PY_POSIX
    return None


def _resolve_ckpt() -> "str | None":
    """RUOYU_COHERENCE_CKPT 优先；否则默认 coherence_v1（存在才返回）。都无 → None（不 spawn）。"""
    env_ckpt = os.environ.get("RUOYU_COHERENCE_CKPT")
    if env_ckpt and Path(env_ckpt).exists():
        return env_ckpt
    if _DEFAULT_CKPT.exists():
        return str(_DEFAULT_CKPT)
    return None


def enabled() -> bool:
    """门控总开关：RUOYU_NN_COHERENCE=1 且 venv+coherence_infer+ckpt 三者齐备。便宜检查·不 spawn。"""
    if os.environ.get("RUOYU_NN_COHERENCE") != "1":
        return False
    if _venv_python() is None or not _COHERENCE_INFER.exists():
        return False
    return _resolve_ckpt() is not None


def _parse_one(obj) -> "dict | None":
    """单条输出解析：仅当真模型命中（source==model 且 coherence_score 非空）才采纳，否则 None。"""
    if not (isinstance(obj, dict) and obj.get("source") == "model"):
        return None
    score = obj.get("coherence_score")
    if score is None:
        return None
    try:
        score = float(score)
    except (TypeError, ValueError):
        return None
    raw_flag = obj.get("is_coherent")
    is_coherent = bool(raw_flag) if raw_flag is not None else bool(score >= 0.5)
    return {"coherence_score": score, "is_coherent": is_coherent, "source": "model"}


def _run_infer(records: "list[dict]", extra_args: "list[str]",
               timeout: "float | None") -> "list[dict | None]":
    """通用批量推理 worker：写 jsonl → subprocess → 读 jsonl → 解析 → 保序一一对应。

    任何不可用/失败 → 全 None（调用方回退启发式·不崩）。records 与返回值同序同长。
    """
    n = len(records)
    if n == 0:
        return []
    none_list: "list[dict | None]" = [None] * n
    if not enabled():
        return none_list
    py = _venv_python()
    ckpt = _resolve_ckpt()
    if py is None or ckpt is None:
        return none_list

    tmpdir = tempfile.mkdtemp(prefix="ruoyu_nn_coherence_")
    in_path = Path(tmpdir) / "in.jsonl"
    out_path = Path(tmpdir) / "out.jsonl"
    try:
        with in_path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        env = dict(os.environ)
        env["RUOYU_COHERENCE_CKPT"] = ckpt
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.run(
                [str(py), str(_COHERENCE_INFER), "--batch", str(in_path),
                 "--out", str(out_path), *extra_args],
                capture_output=True, timeout=timeout or _DEFAULT_TIMEOUT, env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[nn_coherence_bridge] subprocess 失败·回退启发式：{type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)
            return none_list
        if proc.returncode != 0 or not out_path.exists():
            err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            print(f"[nn_coherence_bridge] coherence_infer 退码={proc.returncode}·回退启发式：{err}",
                  file=sys.stderr)
            return none_list

        results: "list[dict | None]" = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                results.append(None)
                continue
            results.append(_parse_one(obj))

        if len(results) != n:
            print(f"[nn_coherence_bridge] 输出条数失配 {len(results)}!={n}·回退启发式", file=sys.stderr)
            return none_list
        return results
    except Exception as e:  # noqa: BLE001 任何意外 → 回退（绝不崩主流水线）
        print(f"[nn_coherence_bridge] 异常·回退启发式：{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return none_list
    finally:
        try:
            in_path.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
            os.rmdir(tmpdir)
        except OSError:
            pass


def predict_batch(texts: "list[str]", timeout: "float | None" = None) -> "list[dict | None]":
    """批量单文本窗口连贯性。任何不可用/失败 → 全 None（调用方回退启发式·不崩）。保序一一对应。"""
    records = [{"text": str(t)} for t in texts]
    return _run_infer(records, [], timeout)


def predict_pairs(pairs: "list[tuple[str, str]]", timeout: "float | None" = None) -> "list[dict | None]":
    """批量文本对衔接连贯性（--mode pairs）。任何不可用/失败 → 全 None（不崩）。保序一一对应。"""
    records = [{"text_a": str(a), "text_b": str(b)} for (a, b) in pairs]
    return _run_infer(records, ["--mode", "pairs"], timeout)


def predict_one(text: str, timeout: "float | None" = None) -> "dict | None":
    """单条便捷封装（内部仍走批量·零开销差异）。失败 → None。"""
    return predict_batch([text], timeout=timeout)[0]


def predict_pair(text_a: str, text_b: str, timeout: "float | None" = None) -> "dict | None":
    """单对便捷封装（内部仍走批量·零开销差异）。失败 → None。"""
    return predict_pairs([(text_a, text_b)], timeout=timeout)[0]


def main():
    """CLI 自测：python nn_coherence_bridge.py "文本1" "文本2" ..."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texts = sys.argv[1:] or [
        "他攥紧了拳头，指节发白，转身朝门口走去。门外的雨还在下。",
        "苹果是一种水果。光速约为每秒三十万公里。他昨天买了一双鞋。",
    ]
    print(f"enabled={enabled()}  ckpt={_resolve_ckpt()}  venv={_venv_python()}")
    print("== 单文本窗口 ==")
    for t, r in zip(texts, predict_batch(texts)):
        print(json.dumps({"text": t, "result": r}, ensure_ascii=False))
    if len(texts) >= 2:
        print("== 文本对衔接 ==")
        pairs = [(texts[0], texts[1])]
        for (a, b), r in zip(pairs, predict_pairs(pairs)):
            print(json.dumps({"text_a": a, "text_b": b, "result": r}, ensure_ascii=False))


if __name__ == "__main__":
    main()
