#!/usr/bin/env python3
"""secrets_store.py — keyring 薄抽象（BYOK · 2026-06-10 · GUI 删档后 2026-06-20 收窄）

主代理（Claude Code CLI）唯一入口形态下，本模块仍管 gen-model BYOK key 与联网调研
search key。密钥经 keyring（Windows 凭据管理器 DPAPI 用户级加密）存储，绝不落 .env /
任何文件。本模块是 keyring 的唯一封装入口。

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
_TRIAL_USERNAME = "__trial_token__"     # 试用 token 保留名（双下划线·避开 profile 名）

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
    类名/模块路径字符串匹配脆弱）。loader 降级与 GUI「密钥库不可用」提示共用此判据。
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
    except _PwDelErr:                    # 本就不存在 → 无可删·返 False（GUI 清除按钮不当错误显示）
        return False
    except Exception:
        return False


def has_api_key(profile_name: str) -> bool:
    return bool(get_api_key(profile_name))


# ============ 联网调研 search API key（BYOK·service 独立·D1·2026-06-15）============
# 蓝图 D1：exe 模式 novel-researcher 联网调研失效（gen-model 无 web）→ BYOK search key
# （Tavily 首选）走同款 keyring·独立命名空间与 gen-model key 隔离。username=provider。
SERVICE_SEARCH = "ruoyuai-search"


def get_search_key(provider: str = "tavily") -> str | None:
    """联网调研 search API key（BYOK·username=provider：tavily/brave/exa）。"""
    if _keyring is None:
        return None
    try:
        v = _keyring.get_password(SERVICE_SEARCH, provider)
        return v or None
    except Exception:
        _log.debug("keyring get search failed for %s", provider)    # 不带 key
        return None


def set_search_key(provider: str, key: str) -> bool:
    if _keyring is None:
        return False
    k = (key or "").strip()
    if not k:                            # 空串 = 清除（避免存空串污染优先级判断）
        return delete_search_key(provider)
    try:
        _keyring.set_password(SERVICE_SEARCH, provider, k)
        return get_search_key(provider) == k    # set→get 回读校验（frozen 静默失败防线）
    except Exception:
        _log.debug("keyring set search failed for %s", provider)
        return False


def delete_search_key(provider: str = "tavily") -> bool:
    if _keyring is None:
        return False
    try:
        _keyring.delete_password(SERVICE_SEARCH, provider)
        return True
    except _PwDelErr:                    # 本就不存在 → 无可删
        return False
    except Exception:
        return False


def has_search_key(provider: str = "tavily") -> bool:
    return bool(get_search_key(provider))


# 🔴 已删除（2026-06-20·A 方案回滚）：跨家族 judge Claude BYOK key（SERVICE_CLAUDE /
# get_claude_key / set_claude_key / has_claude_key / delete_claude_key）。主代理 Claude
# Code CLI 唯一入口后，跨家族 judge 复审改走主代理 inline 文件协议（下一 phase 改造），
# 不再走 keyring 存 Anthropic API key。


def get_trial_token() -> str | None:
    return get_api_key(_TRIAL_USERNAME)


def set_trial_token(token: str) -> bool:
    return set_api_key(_TRIAL_USERNAME, token)


# ============ key 脱敏（对抗审查 must_fix#3：gemini key 在 URL·err 字符串泄漏面）============
import re as _re

_KEY_PATTERNS = [
    _re.compile(r"(key=)([^&\s\"']+)"),        # URL ?key=<KEY>（gemini 原生协议）
    _re.compile(r"(sk-)[A-Za-z0-9_\-]{8,}"),    # OpenAI 风格 sk-...
]


def redact(text: str) -> str:
    """把任意文本里的 API key 脱敏（key=*** / sk-***）。

    gemini 原生协议把 key 放 URL（gen_writer/llm_transport），HTTPError/URLError 的 str()
    会带整条 URL → 经 stderr → GUI LogBuffer → 界面。任何可能含 key 的 err/URL 字符串
    在打到 stderr / 回显 GUI 前必须过本函数。
    """
    if not text:
        return text
    out = text
    out = _KEY_PATTERNS[0].sub(r"\1***", out)
    out = _KEY_PATTERNS[1].sub(r"\1***", out)
    return out


# ============ --probe CLI（BYOK 出货验证 gate · exe 上 keyring 零验证缺口 · 2026-06-13）============
_PROBE_USERNAME = "__probe__"           # probe 专用用户名（双下划线·与 profile 名空间隔离）


def probe() -> dict:
    """keyring 真后端探活：is_available() + set→get→delete 回环。

    🔴 回环用临时 service（ruoyuai-probe-<pid>·每进程独立·用完即删），
    绝不碰真 SERVICE=ruoyuai-gen-model——出货验证跑在真机上，用户真 key 不可受影响。
    哨兵值是一次性随机串非真 key，但仍不打印（与本模块安全不变量同纪律）。
    """
    import os

    available = is_available()
    roundtrip_ok = False
    if available and _keyring is not None:
        svc = f"ruoyuai-probe-{os.getpid()}"
        sentinel = "probe-" + os.urandom(8).hex()
        try:
            _keyring.set_password(svc, _PROBE_USERNAME, sentinel)
            roundtrip_ok = _keyring.get_password(svc, _PROBE_USERNAME) == sentinel
        except Exception:
            roundtrip_ok = False
        finally:
            try:                         # 清理临时 service（set 失败时 delete 抛 → 同样吞掉）
                _keyring.delete_password(svc, _PROBE_USERNAME)
            except Exception:
                pass
    return {"available": available, "roundtrip_ok": roundtrip_ok}


def main(argv=None) -> int:
    """CLI 入口（frozen multi-call dispatch 经 orchestrator.run_script_in_process 调 main()）。"""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="secrets_store", description="BYOK keyring 薄抽象工具")
    ap.add_argument("--probe", action="store_true",
                    help="后端探活 + 临时 service 回环（绝不碰真 service）·JSON 输出·失败非零退出")
    args = ap.parse_args(argv)
    if args.probe:
        result = probe()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if (result["available"] and result["roundtrip_ok"]) else 1
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
