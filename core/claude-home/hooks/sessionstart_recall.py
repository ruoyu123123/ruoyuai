#!/usr/bin/env python3
"""SessionStart hook：注入最近 session 摘要 + 建当前 session marker。

关键约束（与 PostToolUse hook 同纪律）：
- exit 0 永远放行；任何异常静默吞掉，不打断 SessionStart 流程
- 注入内容走 stderr，长度受 session_memory 内部硬约束控制（< 10 commits +
  < 5 plans per session × 3 sessions）
- 与已有 ~/.claude/hooks/session_start.sh 共存（settings.json 同一 SessionStart
  数组可多 hook）—— 本 hook 不动 user-level 文件
"""
import os
import sys

_SCRIPTS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    import session_memory  # type: ignore
except Exception:
    sys.exit(0)  # 模块不可用 = 静默放行

# 启动新 session marker（失败不阻断）
new_sid = None
try:
    new_sid = session_memory.start_session()
except Exception:
    pass

# 顺手清理 30+ 天的旧 session 文件（失败不阻断）
try:
    session_memory.prune(30)
except Exception:
    pass

# 拉取最近 3 个会话的摘要注入 stderr
try:
    sessions = session_memory.recall(3)
except Exception:
    sessions = []

if sessions:
    print("📋 [Session Memory] 最近 3 个会话摘要（P2-4 跨会话注入）：",
          file=sys.stderr)
    for s in sessions:
        sid = s.get("session_id") or "?"
        ended = s.get("ended_at") or "未收尾"
        summ = s.get("summary") or {}
        dur = summ.get("duration_min", 0)
        commits = summ.get("commits", []) or []
        plans = summ.get("active_plans", []) or []
        print(f"  ▸ {sid}  (结束 {ended}, 时长 {dur}min)", file=sys.stderr)
        if commits:
            print(f"    commits ({len(commits)})：", file=sys.stderr)
            for c in commits[:3]:
                print(f"      • {c[:80]}", file=sys.stderr)
            if len(commits) > 3:
                print(f"      … 还有 {len(commits) - 3} 个", file=sys.stderr)
        if plans:
            print(f"    当时活跃 plan ({len(plans)})：", file=sys.stderr)
            for p in plans[:3]:
                pid = p.get("id", "?")
                cmd = p.get("command", "?")
                proj = p.get("project", "?")
                print(f"      • [{cmd}] {pid}  proj={proj}", file=sys.stderr)
    if new_sid:
        print(f"  ⇨ 当前新 session: {new_sid}", file=sys.stderr)

sys.exit(0)
