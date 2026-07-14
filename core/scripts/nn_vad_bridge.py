#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN情绪VAD集成
"""nn_vad_bridge.py — 系统 py(3.14·无 torch) → venv py(3.10·torch) 的 VAD 推理 subprocess 桥。

【为什么是桥】若渝主流水线跑系统 Python（无 torch）；情绪 VAD 模型跑 core/ml/.venv（torch）。
两进程隔离。系统侧组件（save_state / scanner）**不能直接 import 模型**，而是经本桥用 subprocess
批量调 `core/ml/.venv/Scripts/python.exe core/ml/emotion_vad/vad_infer.py --batch in.jsonl --out out.jsonl`
做推理（批量·非实时·每 cluster 一次性整批调用以摊薄模型加载开销）。

【默认安全铁律（北极星⑤·零回归）】以下任一情况 → 返回 None（逐条）→ **调用方回退启发式**，绝不崩：
  · RUOYU_NN_VAD != "1"（默认 off·门控未开）
  · venv python 不存在 / vad_infer.py 不存在
  · checkpoint 不存在（RUOYU_VAD_CKPT 未设且默认 ckpt 缺）
  · subprocess 失败 / 超时 / 退码非 0 / 输出缺失 / 解析失败 / 条数失配
  · vad_infer 退词典模式（source != "model"）→ 视为「模型不可用」→ None（让调用方用自己的启发式）

→ 若渝必须「无 NN 也能跑」：env 默认 off 时本桥所有函数等价 no-op。

【输出契约】predict_batch(texts) → list[dict|None]，与 texts 一一对应；
  命中模型的元素 = {"valence": float, "arousal": float, "dominance": float|None, "source": "model"}，
  其余 = None。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# core/scripts/nn_vad_bridge.py → core/ 根
sys.path.insert(0, str(Path(__file__).resolve().parent))
from proc_utils import run_utf8  # noqa: E402 · 子进程 UTF-8 单一真理源

_CORE = Path(__file__).resolve().parent.parent
_VENV_PY = _CORE / "ml" / ".venv" / "Scripts" / "python.exe"   # Windows venv
_VENV_PY_POSIX = _CORE / "ml" / ".venv" / "bin" / "python"     # POSIX venv（容错）
_VAD_INFER = _CORE / "ml" / "emotion_vad" / "vad_infer.py"
_DEFAULT_CKPT = _CORE / "ml" / "emotion_vad" / "checkpoints" / "va_base"

_DEFAULT_TIMEOUT = 180   # 秒·模型加载 + 批量推理（非实时·宁慢勿挂）


def _venv_python() -> "Path | None":
    if _VENV_PY.exists():
        return _VENV_PY
    if _VENV_PY_POSIX.exists():
        return _VENV_PY_POSIX
    return None


def _resolve_ckpt() -> "str | None":
    """RUOYU_VAD_CKPT 优先；否则默认 va_base（存在才返回）。都无 → None（不 spawn）。"""
    env_ckpt = os.environ.get("RUOYU_VAD_CKPT")
    if env_ckpt and Path(env_ckpt).exists():
        return env_ckpt
    if _DEFAULT_CKPT.exists():
        return str(_DEFAULT_CKPT)
    return None


def enabled() -> bool:
    """门控总开关：RUOYU_NN_VAD=1 且 venv+vad_infer+ckpt 三者齐备。便宜检查·不 spawn。"""
    if os.environ.get("RUOYU_NN_VAD") != "1":
        return False
    if _venv_python() is None or not _VAD_INFER.exists():
        return False
    return _resolve_ckpt() is not None


def _parse_one(obj) -> "dict | None":
    """单条输出解析：仅当真模型命中（source==model 且 valence 非空）才采纳，否则 None。
    subprocess（jsonl 逐行）和 daemon（dict 列表）两条路径共用本函数，防口径漂移。"""
    if isinstance(obj, dict) and obj.get("source") == "model" and obj.get("valence") is not None:
        return {"valence": obj.get("valence"), "arousal": obj.get("arousal"),
                "dominance": obj.get("dominance"), "source": "model"}
    return None


def _daemon_infer(items: list, timeout: float) -> "list | None":
    """daemon-first 尝试（Wave-5 常驻推理 daemon·~0.1s）：未启用/不可达/结果条数不齐 → None，
    调用方回退既有 subprocess 路径（~25s）。daemon 任何问题都不抛异常（try/except 兜底）。"""
    try:
        import nn_daemon_client
        if nn_daemon_client.enabled() and nn_daemon_client.ensure_daemon():
            res = nn_daemon_client.infer("vad", items, timeout=timeout)
            if res is not None and len(res) == len(items):
                return res
    except Exception:
        pass
    return None


def predict_batch(texts: "list[str]", timeout: "float | None" = None) -> "list[dict | None]":
    """批量推理。任何不可用/失败 → 全 None（调用方回退启发式·不崩）。保序一一对应。

    daemon-first：常驻推理 daemon 命中 → 直接用其结果（与 subprocess 路径共用 _parse_one 后处理）；
    daemon 未启用/不可达/结果异常 → 回退既有 subprocess 路径（零回归）。
    """
    n = len(texts)
    if n == 0:
        return []
    none_list: "list[dict | None]" = [None] * n
    if not enabled():
        return none_list

    daemon_res = _daemon_infer([str(t) for t in texts], timeout or _DEFAULT_TIMEOUT)
    if daemon_res is not None:
        return [_parse_one(o) for o in daemon_res]

    py = _venv_python()
    ckpt = _resolve_ckpt()
    if py is None or ckpt is None:
        return none_list

    tmpdir = tempfile.mkdtemp(prefix="ruoyu_nn_vad_")
    in_path = Path(tmpdir) / "in.jsonl"
    out_path = Path(tmpdir) / "out.jsonl"
    try:
        with in_path.open("w", encoding="utf-8") as f:
            for t in texts:
                f.write(json.dumps({"text": str(t)}, ensure_ascii=False) + "\n")

        env = dict(os.environ)
        env["RUOYU_VAD_CKPT"] = ckpt
        try:
            proc = run_utf8(
                [str(py), str(_VAD_INFER), "--batch", str(in_path), "--out", str(out_path)],
                env=env, text=False, timeout=timeout or _DEFAULT_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[nn_vad_bridge] subprocess 失败·回退启发式：{type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)
            return none_list
        if proc.returncode != 0 or not out_path.exists():
            err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            print(f"[nn_vad_bridge] vad_infer 退码={proc.returncode}·回退启发式：{err}", file=sys.stderr)
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
            print(f"[nn_vad_bridge] 输出条数失配 {len(results)}!={n}·回退启发式", file=sys.stderr)
            return none_list
        return results
    except Exception as e:  # noqa: BLE001 任何意外 → 回退（绝不崩主流水线）
        print(f"[nn_vad_bridge] 异常·回退启发式：{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return none_list
    finally:
        try:
            in_path.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
            os.rmdir(tmpdir)
        except OSError:
            pass


def predict_one(text: str, timeout: "float | None" = None) -> "dict | None":
    """单条便捷封装（内部仍走批量·零开销差异）。失败 → None。"""
    return predict_batch([text], timeout=timeout)[0]


def to_vad_bin(valence, arousal, dominance=None) -> dict:
    """连续 (V,A,D)∈[0,1] → {valence/arousal/dominance: VL|L|M|H|VH}。

    复用 vad_infer 的纯函数 binning（torch-free·BIN_EDGES 单一真理源）；import 失败则本地等价兜底。
    """
    try:
        sys.path.insert(0, str(_CORE / "ml" / "emotion_vad"))
        from vad_infer import vad_bin as _vb  # vad_infer 顶层 torch-free·仅 _ensure 内才 import torch
        return _vb(valence, arousal, dominance)
    except Exception:  # pragma: no cover — 路径异常时本地兜底（与 vad_infer.BIN_EDGES 一致）
        edges, labels = (0.2, 0.4, 0.6, 0.8), ("VL", "L", "M", "H", "VH")

        def _b(x):
            if x is None:
                return None
            for i, e in enumerate(edges):
                if x < e:
                    return labels[i]
            return labels[-1]
        return {"valence": _b(valence), "arousal": _b(arousal), "dominance": _b(dominance)}


def main():
    """CLI 自测：python nn_vad_bridge.py "文本1" "文本2" ..."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texts = sys.argv[1:] or ["他攥紧了拳头，指节发白", "她笑了笑，心里很幸福"]
    print(f"enabled={enabled()}  ckpt={_resolve_ckpt()}  venv={_venv_python()}")
    for t, r in zip(texts, predict_batch(texts)):
        print(json.dumps({"text": t, "result": r}, ensure_ascii=False))


if __name__ == "__main__":
    main()
