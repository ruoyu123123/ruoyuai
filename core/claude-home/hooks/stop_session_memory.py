#!/usr/bin/env python3
"""Stop hook：会话收尾时生成 summary 持久化。

关键约束：
- exit 0 永远放行；任何异常静默吞掉
- 与已有 ~/.claude/hooks/send_notify.sh stop 共存（不替换通知功能）
- 仅在有 marker（即本会话经历过 SessionStart）时才工作；无 marker 静默退出
"""
import os
import sys

_SCRIPTS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    import session_memory  # type: ignore
    res = session_memory.end_session()
    if res.get("ok"):
        print(f"📋 [Session Memory] 已保存 session {res['session_id']} summary",
              file=sys.stderr)
except Exception:
    pass

sys.exit(0)
