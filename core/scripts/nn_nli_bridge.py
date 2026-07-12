#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔴 2026-07-03 中文NLI蕴含桥
"""nn_nli_bridge.py — 系统 py(3.14·无 torch) → venv py(3.10·torch) 的中文 NLI 推理 subprocess 桥。

【为什么是桥】若渝主流水线跑系统 Python（无 torch）；中文 NLI 模型跑 core/ml/.venv（torch）。
两进程隔离。系统侧组件（scanner）**不能直接 import 模型**，而是经本桥用 subprocess
批量调 `core/ml/.venv/Scripts/python.exe core/ml/nli/nli_infer.py --batch in.jsonl --out out.jsonl`
做推理（批量·非实时，与 vad/coherence/surprisal/coref 4 桥同架构）。

【消费方（2 个·均 advisory 补充证据·绝不否决字面判断）】
  · cross_book_invariant_scanner.py —— 跨书硬规则字面否定词窗口命中后·NLI 补充矛盾置信度证据
    正文段落是否蕴含声明（字面命中/未命中仍是第一判断，NLI 只补弱信号的灰色地带）

【默认安全铁律（北极星⑤·零回归）】以下任一情况 → 返回 None（逐条）→ **调用方回退纯字面判断**，绝不崩：
  · RUOYU_NN_NLI != "1"（默认 off·门控未开·刻意不进 nn_runtime_defaults 创作默认开启列表·
    待主线程验收后再决定是否默认开）
  · venv python 不存在 / nli_infer.py 不存在 / checkpoint 不存在
  · subprocess 失败 / 超时 / 退码非 0 / 输出缺失 / 解析失败 / 条数失配
  · nli_infer 返回 source != "nli"（unavailable/error）→ 视为「模型不可用」→ None

→ 若渝必须「无 NN 也能跑」：env 默认 off 时本桥所有函数等价 no-op。

【输出契约】predict_batch(pairs) → list[dict|None]，与 pairs 一一对应；
  命中模型的元素 = {"label": "entailment"|"neutral"|"contradiction"（归一化小写），
                    "probs": {"entailment":float,"neutral":float,"contradiction":float}, "source": "nli"}，
  其余 = None。

Env 门控: RUOYU_NN_NLI（默认 off）· 可选 RUOYU_NLI_CKPT 覆盖 checkpoint 路径。

用法（CLI 自测）：python nn_nli_bridge.py "张三把钥匙给了李四" "李四拿到了钥匙"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# core/scripts/nn_nli_bridge.py → core/ 根
_CORE = Path(__file__).resolve().parent.parent
_VENV_PY = _CORE / "ml" / ".venv" / "Scripts" / "python.exe"   # Windows venv
_VENV_PY_POSIX = _CORE / "ml" / ".venv" / "bin" / "python"     # POSIX venv（容错）
_NLI_INFER = _CORE / "ml" / "nli" / "nli_infer.py"
_DEFAULT_CKPT = _CORE / "ml" / "models" / "nli" / "erlangshen-roberta-110m-nli"

_DEFAULT_TIMEOUT = 180   # 秒·模型加载 + 批量推理（非实时·宁慢勿挂）

# checkpoint config.json id2label 是大写（CONTRADICTION/NEUTRAL/ENTAILMENT）·
# 系统侧统一归一化小写 key 方便消费方 dict.get()（未知 label 兜底 .lower()·不硬编码穷举外）。
_LABEL_NORM = {"ENTAILMENT": "entailment", "NEUTRAL": "neutral", "CONTRADICTION": "contradiction"}


def _venv_python() -> "Path | None":
    if _VENV_PY.exists():
        return _VENV_PY
    if _VENV_PY_POSIX.exists():
        return _VENV_PY_POSIX
    return None


def _resolve_ckpt() -> "str | None":
    """RUOYU_NLI_CKPT 优先；否则默认 erlangshen-roberta-110m-nli（存在才返回）。都无 → None（不 spawn）。"""
    env_ckpt = os.environ.get("RUOYU_NLI_CKPT")
    if env_ckpt and Path(env_ckpt).exists():
        return env_ckpt
    if _DEFAULT_CKPT.exists():
        return str(_DEFAULT_CKPT)
    return None


def enabled() -> bool:
    """门控总开关：RUOYU_NN_NLI=1 且 venv+nli_infer+ckpt 三者齐备。便宜检查·不 spawn。"""
    if os.environ.get("RUOYU_NN_NLI") != "1":
        return False
    if _venv_python() is None or not _NLI_INFER.exists():
        return False
    return _resolve_ckpt() is not None


def _parse_one(obj) -> "dict | None":
    """单条输出解析：仅当真模型命中（source==nli 且 label 非空）才采纳，否则 None；label/probs
    key 归一化小写。subprocess（jsonl 逐行）和 daemon（dict 列表）两条路径共用本函数，防口径漂移。"""
    if isinstance(obj, dict) and obj.get("source") == "nli" and obj.get("label"):
        raw_probs = obj.get("probs") or {}
        norm_probs = {_LABEL_NORM.get(k, str(k).lower()): v for k, v in raw_probs.items()}
        norm_label = _LABEL_NORM.get(obj["label"], str(obj["label"]).lower())
        return {"label": norm_label, "probs": norm_probs, "source": "nli"}
    return None


def _daemon_infer(items: list, timeout: float) -> "list | None":
    """daemon-first 尝试（Wave-5 常驻推理 daemon·~0.1s）：未启用/不可达/结果条数不齐 → None，
    调用方回退既有 subprocess 路径（~25s）。daemon 任何问题都不抛异常（try/except 兜底）。"""
    try:
        import nn_daemon_client
        if nn_daemon_client.enabled() and nn_daemon_client.ensure_daemon():
            res = nn_daemon_client.infer("nli", items, timeout=timeout)
            if res is not None and len(res) == len(items):
                return res
    except Exception:
        pass
    return None


def predict_batch(pairs: "list[dict]", timeout: "float | None" = None) -> "list[dict | None]":
    """批量 NLI 推理。pairs[i] = {"premise": str, "hypothesis": str}。

    任何不可用/失败 → 全 None（调用方回退纯字面判断·不崩）。保序一一对应。
    daemon-first：常驻推理 daemon 命中 → 直接用其结果（与 subprocess 路径共用 _parse_one 后处理）；
    daemon 未启用/不可达/结果异常 → 回退既有 subprocess 路径（零回归）。
    """
    n = len(pairs)
    if n == 0:
        return []
    none_list: "list[dict | None]" = [None] * n
    if not enabled():
        return none_list

    # 统一净化（daemon items 与 subprocess jsonl 共用同一份·避免两处各写一遍口径漂移）
    sanitized = [{"premise": str((pr or {}).get("premise", "")),
                  "hypothesis": str((pr or {}).get("hypothesis", ""))} for pr in pairs]

    daemon_res = _daemon_infer(sanitized, timeout or _DEFAULT_TIMEOUT)
    if daemon_res is not None:
        return [_parse_one(o) for o in daemon_res]

    py = _venv_python()
    ckpt = _resolve_ckpt()
    if py is None or ckpt is None:
        return none_list

    tmpdir = tempfile.mkdtemp(prefix="ruoyu_nn_nli_")
    in_path = Path(tmpdir) / "in.jsonl"
    out_path = Path(tmpdir) / "out.jsonl"
    try:
        with in_path.open("w", encoding="utf-8") as f:
            for rec in sanitized:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        env = dict(os.environ)
        env["RUOYU_NLI_CKPT"] = ckpt
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.run(
                [str(py), str(_NLI_INFER), "--batch", str(in_path), "--out", str(out_path)],
                capture_output=True, timeout=timeout or _DEFAULT_TIMEOUT, env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[nn_nli_bridge] subprocess 失败·回退字面判断：{type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr)
            return none_list
        if proc.returncode != 0 or not out_path.exists():
            err = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
            print(f"[nn_nli_bridge] nli_infer 退码={proc.returncode}·回退字面判断：{err}", file=sys.stderr)
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
            print(f"[nn_nli_bridge] 输出条数失配 {len(results)}!={n}·回退字面判断", file=sys.stderr)
            return none_list
        return results
    except Exception as e:  # noqa: BLE001 任何意外 → 回退（绝不崩主流水线）
        print(f"[nn_nli_bridge] 异常·回退字面判断：{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return none_list
    finally:
        try:
            in_path.unlink(missing_ok=True)
            out_path.unlink(missing_ok=True)
            os.rmdir(tmpdir)
        except OSError:
            pass


def predict_one(premise: str, hypothesis: str, timeout: "float | None" = None) -> "dict | None":
    """单条便捷封装（内部仍走批量·零开销差异）。失败 → None。"""
    return predict_batch([{"premise": premise, "hypothesis": hypothesis}], timeout=timeout)[0]


def entails(premise: str, hypothesis: str, threshold: float = 0.5,
            timeout: "float | None" = None) -> "bool | None":
    """便捷函数：premise 是否蕴含 hypothesis。

    返回 True/False = 模型给出明确判断（entailment 且置信度 ≥ threshold）；
    返回 None = 模型不可用（门控关/推理失败）——调用方必须区分 None≠False，
    绝不能把 None 当「不蕴含」处理去否决既有字面判断（北极星⑤：本桥只补证据不裁决）。
    """
    res = predict_one(premise, hypothesis, timeout=timeout)
    if res is None:
        return None
    return res["label"] == "entailment" and res["probs"].get("entailment", 0.0) >= threshold


def main():
    """CLI 自测：python nn_nli_bridge.py "前提句" "假设句" """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if len(args) >= 2:
        premise, hypothesis = args[0], args[1]
    else:
        premise, hypothesis = "张三把钥匙给了李四", "李四拿到了钥匙"
    print(f"enabled={enabled()}  ckpt={_resolve_ckpt()}  venv={_venv_python()}")
    res = predict_one(premise, hypothesis)
    print(json.dumps({"premise": premise, "hypothesis": hypothesis, "result": res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
