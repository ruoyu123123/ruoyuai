#!/usr/bin/env python3
"""secrets_store.py — keyring 薄抽象（2026-06-10 · GUI 删档后 2026-06-20 收窄 · 2026-06-21 BYOK 入口 DEPRECATED commit 2a4d7ce）

# 🔴 2026-06-28 移除exe/gen-model梳理方向：删 trial token / probe / search-key 三段 exe 分发专属逻辑。
# 保留 api-key 主体 + redact + is_available（gen_model_loader 创作链读 key 命脉·不可动）。

主代理（Claude Code CLI）唯一入口形态下，本模块功能收窄为：
- `redact()`：gemini key URL 脱敏（仍由 llm_transport / gen_writer 调用·硬功能保留）。
- gen-model BYOK key keyring 路径：仅 gen_model_loader._resolve_api_key 仍按三级优先级查
  keyring → environ → .env 文本，保留作向下兼容；实际 dev 走仓库根 .env。

安全不变量（仍生效）：
- 绝不 print/log key 明文（debug 也只打 service/username + bool）。
- 绝不把 key 写回 .env / 任何文件——keyring 是唯一持久化路径。
- 任何 keyring 异常吞成 None/False，绝不冒泡到 loader（让 loader 优雅降级到 .env）。

安全不变量（违反即 BYOK 泄漏）：
- 绝不 print/log key 明文（debug 也只打 service/username + bool）。
- 绝不把 key 写回 .env / 任何文件——唯一持久化路径是 keyring。
- 任何 keyring 异常吞成 None/False，绝不冒泡到 loader（让 loader 优雅降级到 .env）。

后端可替换（测试纪律）：不缓存 backend 引用 → 测试 keyring.set_keyring(MemKeyring())
即透明接管，绝不碰真 Credential Manager。

service/username 约定：SERVICE 固定，username = profile.name（与 .env 的
GEN__<name>__API_KEY 的 <name> 一一对齐·同一权威键）。
"""
from __future__ import annotations

import logging

SERVICE = "ruoyuai-gen-model"          # 固定命名空间·一个 service 管所有 profile key

_log = logging.getLogger("ruoyuai.secrets")

try:
    import keyring as _keyring
    from keyring.errors import PasswordDeleteError as _PwDelErr
except Exception:                       # ImportError 或 keyring 自身初始化异常
    _keyring = None
    _PwDelErr = Exception


def is_available() -> bool:
    """真实探测后端可用（非仅 import 成功）。frozen 退化成 fail/null backend 时返 False。

    用 isinstance 判定（对抗审查 must_fix#4：fail/null 后端 __name__ 都是 'Keyring'，
    类名/模块路径字符串匹配脆弱）。loader 降级到 .env 的判据。
    """
    if _keyring is None:
        return False
    try:
        kr = _keyring.get_keyring()
        bad = []
        try:
            from keyring.backends import fail as _fail
            bad.append(_fail.Keyring)
        except Exception:
            pass
        try:
            from keyring.backends import null as _null
            bad.append(_null.Keyring)
        except Exception:
            pass
        if bad and isinstance(kr, tuple(bad)):
            return False
        # 空 chainer（无可用子后端）也算不可用
        if type(kr).__name__ == "ChainerBackend" and not getattr(kr, "backends", None):
            return False
        return True
    except Exception:
        return False


def get_api_key(profile_name: str) -> str | None:
    if _keyring is None:
        return None
    try:
        v = _keyring.get_password(SERVICE, profile_name)
        return v or None
    except Exception:
        _log.debug("keyring get failed for %s/%s", SERVICE, profile_name)  # 不带 key
        return None


def set_api_key(profile_name: str, key: str) -> bool:
    if _keyring is None:
        return False
    k = (key or "").strip()
    if not k:                            # 空串 = 清除（避免存空串污染优先级判断）
        return delete_api_key(profile_name)
    try:
        _keyring.set_password(SERVICE, profile_name, k)
        return get_api_key(profile_name) == k   # set→get 回读校验（frozen 静默失败防线）
    except Exception:
        _log.debug("keyring set failed for %s/%s", SERVICE, profile_name)
        return False


def delete_api_key(profile_name: str) -> bool:
    if _keyring is None:
        return False
    try:
        _keyring.delete_password(SERVICE, profile_name)
        return True
    except _PwDelErr:                    # 本就不存在 → 无可删·返 False（清除操作不当错误显示）
        return False
    except Exception:
        return False


def has_api_key(profile_name: str) -> bool:
    return bool(get_api_key(profile_name))


# 🔴 已删除（2026-06-20·A 方案回滚）：跨家族 judge Claude BYOK key（SERVICE_CLAUDE /
# get_claude_key / set_claude_key / has_claude_key / delete_claude_key）。主代理 Claude
# Code CLI 唯一入口后，跨家族 judge 复审改走主代理 inline 文件协议，不再走 keyring 存 Anthropic API key。
# 🔴 2026-06-28 移除exe/gen-model梳理方向：删 search-key 段（SERVICE_SEARCH / get-set-delete-has_search_key·
# web_search_client 已删·此段已死）+ trial token 段（_TRIAL_USERNAME / get-set_trial_token·exe 试用分发专属）。


# ============ key 脱敏（对抗审查 must_fix#3：gemini key 在 URL·err 字符串泄漏面）============
import re as _re

_KEY_PATTERNS = [
    _re.compile(r"(key=)([^&\s\"']+)"),        # URL ?key=<KEY>（gemini 原生协议）
    _re.compile(r"(sk-)[A-Za-z0-9_\-]{8,}"),    # OpenAI 风格 sk-...
]


def redact(text: str) -> str:
    """把任意文本里的 API key 脱敏（key=*** / sk-***）。

    gemini 原生协议把 key 放 URL（gen_writer/llm_transport），HTTPError/URLError 的 str()
    会带整条 URL → 经 stderr 输出到日志。任何可能含 key 的 err/URL 字符串
    在打到 stderr / 日志前必须过本函数。
    """
    if not text:
        return text
    out = text
    out = _KEY_PATTERNS[0].sub(r"\1***", out)
    out = _KEY_PATTERNS[1].sub(r"\1***", out)
    return out


# 🔴 2026-06-28 移除exe/gen-model梳理方向：删 probe()（BYOK exe 出货验证 gate · _PROBE_USERNAME）
# + main 里 probe 子命令 / --probe alias（属 exe 分发专属·主代理 CLI 入口形态不需要）。


def _list_profiles() -> list[dict]:
    """列 keyring 里所有已存的 gen-model profile（不打 key 明文·只打 tail 8 位）。"""
    if _keyring is None:
        return []
    try:
        creds = _keyring.get_credential(SERVICE, None)  # 部分 backend 支持枚举·null 也 OK
    except Exception:
        pass
    # 标准 keyring API 没有跨 service 枚举；改读 .env 文本里的 profile 名 + 查每个有没有 key
    out: list[dict] = []
    from pathlib import Path
    import re as _r
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return out
    text = env_path.read_text(encoding="utf-8")
    names = sorted(set(_r.findall(r"^GEN__([A-Za-z0-9_]+)__API_KEY\s*=", text, flags=_r.MULTILINE)))
    for name in names:
        k = get_api_key(name)
        out.append({
            "profile": name,
            "in_keyring": bool(k),
            "tail": (k[-8:] if k else None),
        })
    return out


def _sync_from_env(dry_run: bool = False) -> dict:
    """把 .env 里所有 GEN__<name>__API_KEY 写入 keyring（覆盖 keyring 里旧值）。

    用于 cluster_001 写作翻车场景：用户改了 .env 的 key 但 keyring 旧 key 静默覆盖。
    sync-from-env 一键把 .env 的真相回灌到 keyring。
    """
    from pathlib import Path
    import re as _r
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return {"ok": False, "reason": "no .env"}
    text = env_path.read_text(encoding="utf-8")
    pat = _r.compile(r"^GEN__([A-Za-z0-9_]+)__API_KEY[ \t]*=[ \t]*(.*?)[ \t]*$", _r.MULTILINE)
    changed: list[dict] = []
    skipped: list[dict] = []
    for m in pat.finditer(text):
        name, new_key = m.group(1), m.group(2).strip()
        if not new_key:
            continue
        old = get_api_key(name)
        if old == new_key:
            skipped.append({"profile": name, "reason": "identical"})
            continue
        if dry_run:
            changed.append({
                "profile": name,
                "old_tail": (old[-8:] if old else None),
                "new_tail": new_key[-8:],
                "would_change": True,
            })
            continue
        ok = set_api_key(name, new_key)
        changed.append({
            "profile": name,
            "old_tail": (old[-8:] if old else None),
            "new_tail": new_key[-8:],
            "ok": ok,
        })
    return {"ok": True, "dry_run": dry_run, "changed": changed, "skipped": skipped}


def main(argv=None) -> int:
    """CLI 入口（可被外部脚本/子进程直接调用）。"""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="secrets_store", description="BYOK keyring 薄抽象工具")
    sub = ap.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="列 .env 已定义 profile 在 keyring 的状态（不打 key 明文）")
    p_list.add_argument("--json", action="store_true")

    p_set = sub.add_parser("set", help="写入/更新 keyring 中某 profile 的 key")
    p_set.add_argument("profile")
    p_set.add_argument("key")

    p_unset = sub.add_parser("unset", help="清除 keyring 中某 profile 的 key（让 .env 接管）")
    p_unset.add_argument("profile")

    p_sync = sub.add_parser("sync-from-env",
        help="🔴 把 .env 里所有 GEN__<name>__API_KEY 写进 keyring（治 keyring 静默覆盖 .env 陷阱）")
    p_sync.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)
    cmd = args.cmd  # 🔴 2026-06-28 移除exe/gen-model梳理方向：删 --probe alias 后直接取 cmd

    if cmd == "list":
        rows = _list_profiles()
        if getattr(args, "json", False):
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(f"keyring service: {SERVICE}")
            for r in rows:
                tail = f"...{r['tail']}" if r["tail"] else "(none)"
                print(f"  {r['profile']:<22} keyring={tail}")
        return 0
    if cmd == "set":
        ok = set_api_key(args.profile, args.key)
        print(json.dumps({"profile": args.profile, "ok": ok,
                          "tail": (get_api_key(args.profile) or "")[-8:] or None}))
        return 0 if ok else 1
    if cmd == "unset":
        ok = delete_api_key(args.profile)
        print(json.dumps({"profile": args.profile, "deleted": ok}))
        return 0
    if cmd == "sync-from-env":
        res = _sync_from_env(dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
