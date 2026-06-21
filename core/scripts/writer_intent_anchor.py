#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""writer_intent_anchor.py — 反算法议程占领 · 4 维盲意图卡 · R24 W12 Batch-KK · P1

【缺口 · AGI 协作 reflexivity / 反算法议程占领】
弱模型在长上下文里会被自身先前 output / scanner advisory / 题材 prior 牵着走，
渐进偏离用户最初想要的 4 维意图：want / antagonist / stake / tone-word。
本模块在 cluster-write step 1 前置一张「盲意图卡」（4 字段）→ SHA-256 锁定，
之后任何环节改不了；step 6（草稿落地后）由 agenda_drift_scanner 用 char-Jaccard
比对草稿与 4 字段，任一 < 0.62 → WRITER_INTENT_AGENDA_DRIFT advisory。

【做法 · 确定性 · 零 LLM/零联网】
  · CLI `create` 写 cluster_<key>_writer_intent.json:
       {want, antagonist, stake, tone_word, _sha256, _created_ts, _v}
  · _sha256 = sha256(canonical_json({4 fields without _sha256}))
  · CLI `verify` 重算 _sha256 比对·不符 exit 2
  · CLI `read`   原样输出（build_manifest / scanner 消费）

【写入路径】<project>/_数据库/.writer_intent/cluster_<key>_writer_intent.json
  · 用户写完 OS-level immutable（chmod 0o444 best-effort·Windows 取消只写位）
  · 任一字段空 / 非 str → exit 2

【北极星】②④⑤ advisory · cluster · 绝不 hard_gate
  本模块只产盲意图卡 + SHA-256 校验·不裁决 verdict（drift 由 scanner 出）。

用法:
  python writer_intent_anchor.py create <project> <cluster_key> \
      --want "..." --antagonist "..." --stake "..." --tone-word "..."
  python writer_intent_anchor.py verify <project> <cluster_key>
  python writer_intent_anchor.py read   <project> <cluster_key>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_REQUIRED_FIELDS = ("want", "antagonist", "stake", "tone_word")
_SCHEMA_VERSION = "1.0"


def _anchor_path(project_root: Path, cluster_key: str) -> Path:
    return (Path(project_root) / "_数据库" / ".writer_intent"
            / f"cluster_{cluster_key}_writer_intent.json")


def _canonical_json(data: dict) -> str:
    """规范化 JSON 用于 SHA-256（key 排序·无空格）"""
    clean = {k: data[k] for k in sorted(data.keys()) if not k.startswith("_sha256")}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _compute_sha256(data: dict) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _validate_fields(want, antagonist, stake, tone_word) -> tuple[bool, str]:
    """4 字段必须均为非空 str·tone_word 限单词/短词组（≤16 CJK 字符）"""
    for name, val in [("want", want), ("antagonist", antagonist),
                      ("stake", stake), ("tone_word", tone_word)]:
        if not isinstance(val, str) or not val.strip():
            return False, f"字段 {name} 必须为非空 str"
    if len(tone_word.strip()) > 16:
        return False, f"tone_word 过长(>16 字符)"
    return True, ""


def cmd_create(args) -> int:
    project = Path(args.project)
    cluster_key = args.cluster_key
    ok, msg = _validate_fields(args.want, args.antagonist, args.stake, args.tone_word)
    if not ok:
        print(f"[writer_intent] {msg}", file=sys.stderr)
        return 2
    payload = {
        "want": args.want.strip(),
        "antagonist": args.antagonist.strip(),
        "stake": args.stake.strip(),
        "tone_word": args.tone_word.strip(),
        "cluster_key": cluster_key,
        "_v": _SCHEMA_VERSION,
        "_created_ts": int(time.time()),
    }
    payload["_sha256"] = _compute_sha256(payload)
    p = _anchor_path(project, cluster_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 不覆盖（盲意图卡一旦写定·防 reflexive 偷改）·--force 才覆
    if p.exists():
        if not args.force:
            print(f"[writer_intent] {p} 已存在·--force 覆写", file=sys.stderr)
            return 2
        # --force：先解锁 0o444（best-effort）·跨平台兼容
        try:
            os.chmod(p, 0o644)
        except (OSError, PermissionError):
            pass
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    # best-effort 只读化（POSIX 0o444 / Windows 0o444 等同移除写位）
    try:
        os.chmod(p, 0o444)
    except (OSError, PermissionError):
        pass
    print(json.dumps({"ok": True, "path": str(p),
                      "sha256": payload["_sha256"]},
                     ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    project = Path(args.project)
    p = _anchor_path(project, args.cluster_key)
    if not p.exists():
        print(f"[writer_intent] 缺 {p}", file=sys.stderr)
        return 2
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[writer_intent] 读取失败: {e}", file=sys.stderr)
        return 2
    stored = data.get("_sha256")
    fields = {k: data.get(k) for k in _REQUIRED_FIELDS}
    if not all(isinstance(v, str) for v in fields.values()):
        print("[writer_intent] 字段缺失或非 str", file=sys.stderr)
        return 2
    base = {k: data[k] for k in data if k != "_sha256"}
    expected = _compute_sha256(base)
    ok = stored == expected
    print(json.dumps({"ok": ok, "stored": stored, "expected": expected,
                      **fields}, ensure_ascii=False))
    return 0 if ok else 2


def cmd_read(args) -> int:
    project = Path(args.project)
    p = _anchor_path(project, args.cluster_key)
    if not p.exists():
        print(json.dumps({"ok": False, "exists": False}, ensure_ascii=False))
        return 1
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": str(e)[:120]},
                         ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **data}, ensure_ascii=False))
    return 0


def load_anchor(project_root, cluster_key: str) -> dict | None:
    """供 build_manifest / scanner 用·SHA-256 校验失败返回 None。"""
    p = _anchor_path(Path(project_root), cluster_key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stored = data.get("_sha256")
    base = {k: data[k] for k in data if k != "_sha256"}
    if stored != _compute_sha256(base):
        return None
    return data


def main():
    ap = argparse.ArgumentParser(description="盲意图卡 4 维 SHA-256 锁定")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("project")
    pc.add_argument("cluster_key")
    pc.add_argument("--want", required=True)
    pc.add_argument("--antagonist", required=True)
    pc.add_argument("--stake", required=True)
    pc.add_argument("--tone-word", required=True, dest="tone_word")
    pc.add_argument("--force", action="store_true")
    pc.set_defaults(func=cmd_create)

    pv = sub.add_parser("verify")
    pv.add_argument("project")
    pv.add_argument("cluster_key")
    pv.set_defaults(func=cmd_verify)

    pr = sub.add_parser("read")
    pr.add_argument("project")
    pr.add_argument("cluster_key")
    pr.set_defaults(func=cmd_read)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
