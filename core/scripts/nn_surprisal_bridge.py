#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-06-29 NN信息密度集成
"""nn_surprisal_bridge.py — 系统 py(3.14·无 torch) → venv py(3.10·torch) 的 surprisal 推理 subprocess 桥。

【为什么是桥】若渝主流水线跑系统 Python（无 torch）；GPT-2 surprisal 推理跑 core/ml/.venv（torch + minicons）。
两进程隔离。系统侧组件（surprisal_scanner）**不能直接 import 模型**，而是经本桥用 subprocess
批量调 `core/ml/.venv/Scripts/python.exe core/ml/surprisal/surprisal_infer.py --batch in.jsonl --out out.jsonl`
做推理。

【默认安全铁律（北极星⑤·零回归）】以下任一情况 → 返回 None（逐条）→ **调用方回退/静默降级**，绝不崩：
  · RUOYU_NN_SURPRISAL != "1"（默认 off·门控未开）
  · venv python 不存在 / surprisal_infer.py 不存在
  · subprocess 失败 / 超时 / 退码非 0 / 输出缺失 / 解析失败 / 条数失配
  · 推理条目 source == "error" → 该条 → None

→ 若渝必须「无 NN 也能跑」：env 默认 off 时本桥所有函数等价 no-op。

【输出契约】predict_batch(texts, ids) → list[dict|None]，与 texts 一一对应；
  命中模型的元素 = {"id": str, "mean_surprisal": float, "std_surprisal": float,
                     "max_surprisal": float, "min_surprisal": float,
                     "skewness": float, "kurtosis": float, "token_count": int, "source": "model"}，
  其余 = None。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# core/scripts/nn_surprisal_bridge.py → core/ 根
_CORE = Path(__file__).resolve().parent.parent
_VENV_PY = _CORE / "ml" / ".venv" / "Scripts" / "python.exe"   # Windows venv
_VENV_PY_POSIX = _CORE / "ml" / ".venv" / "bin" / "python"     # POSIX venv（容错）
_SURPRISAL_INFER = _CORE / "ml" / "surprisal" / "surprisal_infer.py"

_DEFAULT_TIMEOUT = 300   # 秒·GPT-2 推理比 BERT 慢（模型加载 + 批量自回归解码）


def _venv_python() -> "Path | None":
    if _VENV_PY.exists():
        return _VENV_PY
    if _VENV_PY_POSIX.exists():
        return _VENV_PY_POSIX
    return None


def enabled() -> bool:
    """门控总开关：RUOYU_NN_SURPRISAL=1 且 venv+surprisal_infer 齐备。便宜检查·不 spawn。"""
    if os.environ.get("RUOYU_NN_SURPRISAL") != "1":
        return False
    if _venv_python() is None or not _SURPRISAL_INFER.exists():
        return False
    return True


def _parse_one(obj, item_id) -> "dict | None":
    """单条输出解析：仅当真模型命中（source==model 且 mean_surprisal 非空）才采纳，否则 None。
    subprocess（jsonl 逐行·id 由 surprisal_infer._run_batch 写回 obj）和 daemon（裸 predict_batch
    结果·不带 id）两条路径共用本函数——id 统一用调用方按位置对应传入的 item_id（str 化对齐
    jsonl 往返后的字符串类型），防口径漂移。"""
    if isinstance(obj, dict) and obj.get("source") == "model" and obj.get("mean_surprisal") is not None:
        return {
            "id": str(item_id),
            "mean_surprisal": obj.get("mean_surprisal"),
            "std_surprisal": obj.get("std_surprisal"),
            "max_surprisal": obj.get("max_surprisal"),
            "min_surprisal": obj.get("min_surprisal"),
            "skewness": obj.get("skewness"),
            "kurtosis": obj.get("kurtosis"),
            "token_count": obj.get("token_count"),
            "source": "model",
        }
    return None


def _daemon_infer(items: list, timeout: float) -> "list | None":
    """daemon-first 尝试（Wave-5 常驻推理 daemon·~0.1s）：未启用/不可达/结果条数不齐 → None，
    调用方回退既有 subprocess 路径（~25s）。daemon 任何问题都不抛异常（try/except 兜底）。

    🔴 已知差异（不在本桥可修范围内·见交付报告）：daemon 侧 `_infer_surprisal(items, model=None)`
    忽略传入的 model 参数，只读 daemon 进程自己的 RUOYU_SURPRISAL_MODEL env（daemon 是长驻进程，
    调用方运行时改 env 不会被其感知）。本函数不传 model（传了也被忽略，避免误导）。
    """
    try:
        import nn_daemon_client
        if nn_daemon_client.enabled() and nn_daemon_client.ensure_daemon():
            res = nn_daemon_client.infer("surprisal", items, timeout=timeout)
            if res is not None and len(res) == len(items):
                return res
    except Exception:
        pass
    return None


def predict_batch(texts: "list[str]", ids: "list[str] | None" = None,
                  timeout: "float | None" = None) -> "list[dict | None]":
    """批量推理。任何不可用/失败 → 全 None（调用方静默降级·不崩）。保序一一对应。

    daemon-first：常驻推理 daemon 命中 → 直接用其结果（与 subprocess 路径共用 _parse_one 后处理）；
    daemon 未启用/不可达/结果异常 → 回退既有 subprocess 路径（零回归）。
    """
    n = len(texts)
    if n == 0:
        return []
    none_list: "list[dict | None]" = [None] * n
    if not enabled():
        return none_list

    # 构建 ID 列表（daemon 和 subprocess 两条路径都需要）
    if ids is None:
        ids = [f"para_{i:04d}" for i in range(n)]
    if len(ids) != n:
        print(f"[nn_surprisal_bridge] ids 长度 {len(ids)} != texts {n}·回退", file=sys.stderr)
        return none_list

    daemon_res = _daemon_infer([str(t) for t in texts], timeout or _DEFAULT_TIMEOUT)
    if daemon_res is not None:
        return [_parse_one(obj, item_id) for obj, item_id in zip(daemon_res, ids)]

    py = _venv_python()
    if py is None:
        return none_list

    # 模型名（环境变量透传）
    model_name = os.environ.get("RUOYU_SURPRISAL_MODEL", "uer/gpt2-chinese-cluecorpussmall")

    tmpdir = tempfile.mkdtemp(prefix="ruoyu_nn_surprisal_")
    in_path = Path(tmpdir) / "in.jsonl"
    out_path = Path(tmpdir) / "out.jsonl"
    try:
        # 写 input JSONL
        with in_path.open("w", encoding="utf-8") as f:
            for item_id, text in zip(ids, texts):
                f.write(json.dumps({"id": str(item_id), "text": str(text)},
                                   ensure_ascii=False) + "\n")

        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if model_name:
            env["RUOYU_SURPRISAL_MODEL"] = model_name

        # subprocess 调 venv python
        cmd = [str(py), str(_SURPRISAL_INFER),
               "--batch", str(in_path), "--out", str(out_path),
               "--model", model_name]
        try:
            proc = subprocess.run(
                cmd, capture_output=True,
                timeout=timeout or _DEFAULT_TIMEOUT, env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[nn_surprisal_bridge] subprocess 失败·回退：{type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)
            return none_list
        if proc.returncode != 0 or not out_path.exists():
            err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            print(f"[nn_surprisal_bridge] surprisal_infer 退码={proc.returncode}·回退：{err}",
                  file=sys.stderr)
            return none_list

        # 解析 output JSONL
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
            results.append(_parse_one(obj, obj.get("id")))

        if len(results) != n:
            print(f"[nn_surprisal_bridge] 输出条数失配 {len(results)}!={n}·回退", file=sys.stderr)
            return none_list
        return results
    except Exception as e:  # noqa: BLE001 任何意外 → 回退（绝不崩主流水线）
        print(f"[nn_surprisal_bridge] 异常·回退：{type(e).__name__}: {str(e)[:120]}",
              file=sys.stderr)
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


def main():
    """CLI 自测：python nn_surprisal_bridge.py "文本1" "文本2" ..."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    texts = sys.argv[1:] or ["他攥紧了拳头，指节发白", "一切都很平静，没有任何波澜"]
    print(f"enabled={enabled()}  venv={_venv_python()}")
    for t, r in zip(texts, predict_batch(texts)):
        print(json.dumps({"text": t, "result": r}, ensure_ascii=False))


if __name__ == "__main__":
    main()
