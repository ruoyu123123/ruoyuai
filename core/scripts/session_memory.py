#!/usr/bin/env python3
"""session_memory.py — 跨会话记忆（P2-4，借鉴 claude-mem，MVP 聚合版）

【为什么有这个模块】
我们已有：
  - WAL：单命令内的细粒度断点（save_state 的 completed_steps）
  - plan_tracker：跨命令的强制规划（plan_id + steps + attestation）
  - learning_loop：跨章节的经验沉淀（success/failure patterns）
但**没有 session 级跨会话记忆** —— 上次会话做了什么、未完成的 plan、最近
提交、关键决策。每次新会话主代理只能靠用户重述或自己 git log。

【MVP 设计 —— 聚合式而非压缩式】
不做向量检索 / LLM 压缩 / 后台 worker，仅聚合现有结构化数据：
  - git log（since session start）—— 本会话的提交活动
  - plan_tracker.list_plans(active=True) —— 未完成的 plan
  - 时长（now - session_start）—— 会话开销
聚合内容写进 session JSON 持久化；SessionStart hook 自动读最近 3 个注入 stderr，
让主代理 / 用户开机即知「上次到哪」。

【与既有子系统的边界（一体化纪律）】
  - WAL：不读不写（L8.4 共存约定）
  - plan_tracker：只**读** list_plans，不修改 plan 状态 / attestation
  - learning_loop：不进经验库（session summary 是会话状态，不是写作模式）

【存储】
  core/claude-home/.sessions/<YYYYMMDDTHHMMSS>.json  —— 已结束的 session
  core/claude-home/.sessions/.current_session         —— 当前活跃 session marker
  超过 30 天的 session 文件由 prune() 自动清理（SessionStart hook 顺手跑）

【CLI】
  python session_memory.py start          # 启动新 session（建 marker）
  python session_memory.py end            # 结束 session（生成 summary 持久化）
  python session_memory.py recall [--n N] # 列最近 N 个 session summary（默认 3）
  python session_memory.py prune [--days N]  # 清理超过 N 天的（默认 30）
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_DIR = REPO_ROOT / "core" / "claude-home" / ".sessions"
MARKER_FILE = SESSIONS_DIR / ".current_session"

# 默认参数
DEFAULT_RECALL_N = 3
DEFAULT_PRUNE_DAYS = 30
# 注入 context 的硬约束（防止单 session 摘要爆炸）
MAX_COMMITS_PER_SESSION = 10
MAX_PLANS_PER_SESSION = 5


# ============ 内部辅助 ============

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_unix() -> int:
    return int(datetime.now().timestamp())


def _safe_load(p: Path) -> dict | None:
    """容错 JSON 读取，损坏 / 不存在 / IO 错均返回 None。"""
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _safe_save(p: Path, data: dict) -> bool:
    """容错 JSON 写入。"""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError):
        return False


# ============ 公共 API ============

def start_session() -> str:
    """创建 session marker。返回 session_id。
    若已有 marker（上次未正常 end）→ 覆盖（不阻断主流程，自愈优先）。"""
    sid = datetime.now().strftime("%Y%m%dT%H%M%S")
    payload = {
        "session_id": sid,
        "started_at": _now_iso(),
        "started_at_unix": _now_unix(),
        "ended_at": None,
        "summary": None,
    }
    _safe_save(MARKER_FILE, payload)
    return sid


def _build_summary(started_at_unix: int) -> dict:
    """聚合 git log + plan 活跃情况作为 session summary。
    所有数据来源失败都降级为空段，永不抛异常 —— hook 调用方依赖此承诺。"""
    summary = {
        "commits": [],
        "active_plans": [],
        "duration_min": 0,
    }
    if started_at_unix:
        duration_sec = _now_unix() - started_at_unix
        summary["duration_min"] = round(duration_sec / 60, 1)
        # git log since session start
        since_iso = datetime.fromtimestamp(started_at_unix).isoformat(timespec="seconds")
        try:
            r = subprocess.run(
                ["git", "log", f"--since={since_iso}", "--oneline",
                 f"-{MAX_COMMITS_PER_SESSION}"],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
                encoding="utf-8", timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                summary["commits"] = [ln for ln in r.stdout.strip().splitlines()
                                       if ln.strip()][:MAX_COMMITS_PER_SESSION]
        except (subprocess.SubprocessError, OSError):
            pass
    # 活跃 plan（从 plan_tracker 只读）
    try:
        scripts_dir = str(REPO_ROOT / "core" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import plan_tracker  # noqa: WPS433  局部导入是为了延迟到运行时
        active = plan_tracker.list_plans(active_only=True)
        summary["active_plans"] = [
            {
                "id": p.get("id"),
                "command": p.get("command"),
                "project": p.get("project"),
                "chapter": p.get("chapter"),
            }
            for p in (active or [])[:MAX_PLANS_PER_SESSION]
        ]
    except Exception:
        # plan_tracker 不可用 / 损坏：摘要照常产出（plans 段为空）
        pass
    return summary


def end_session() -> dict:
    """收尾 session：生成 summary 持久化到 .sessions/<sid>.json，移除 marker。
    无 marker（如已 end 过 / 从未 start）→ 返回 {ok: False, reason}，不抛异常。"""
    marker = _safe_load(MARKER_FILE)
    if not marker:
        return {"ok": False, "reason": "no active session marker"}
    sid = marker.get("session_id") or datetime.now().strftime("%Y%m%dT%H%M%S")
    started_unix = marker.get("started_at_unix") or 0
    summary = _build_summary(started_unix)
    marker["ended_at"] = _now_iso()
    marker["summary"] = summary
    target = SESSIONS_DIR / f"{sid}.json"
    if not _safe_save(target, marker):
        return {"ok": False, "reason": "save failed"}
    try:
        MARKER_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "session_id": sid, "summary": summary}


def recall(n: int = DEFAULT_RECALL_N) -> list:
    """返回最近 N 个**已结束** session 的轻量摘要列表（按 session_id 倒序）。
    永不抛异常；目录不存在 / 单个 JSON 损坏 → 跳过对应文件。
    输出已含 summary 段，调用方直接展示即可。"""
    if not SESSIONS_DIR.is_dir():
        return []
    files = sorted(
        (f for f in SESSIONS_DIR.glob("*.json") if f.name != ".current_session"),
        key=lambda p: p.name,
        reverse=True,
    )[:max(1, n)]
    out = []
    for f in files:
        d = _safe_load(f)
        if not isinstance(d, dict):
            continue
        out.append({
            "session_id": d.get("session_id"),
            "started_at": d.get("started_at"),
            "ended_at": d.get("ended_at"),
            "summary": d.get("summary"),
        })
    return out


def prune(days: int = DEFAULT_PRUNE_DAYS) -> int:
    """清理超过 days 天的 session 文件（按 mtime）。返回删除数。
    永不抛异常；单个文件无法删除 → 跳过。"""
    if not SESSIONS_DIR.is_dir():
        return 0
    cutoff = (datetime.now() - timedelta(days=max(1, days))).timestamp()
    deleted = 0
    for f in SESSIONS_DIR.glob("*.json"):
        if f.name == ".current_session":
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


# ============ CLI ============

def _get_arg(name: str, default):
    """从 argv 取 --name VALUE，失败返回 default。"""
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    if idx + 1 >= len(sys.argv):
        return default
    try:
        return int(sys.argv[idx + 1])
    except ValueError:
        return default


def main():
    if len(sys.argv) < 2:
        print("用法: session_memory.py start|end|recall|prune", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "start":
        sid = start_session()
        print(sid)
        sys.exit(0)
    if cmd == "end":
        res = end_session()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res.get("ok") else 1)
    if cmd == "recall":
        n = _get_arg("--n", DEFAULT_RECALL_N)
        print(json.dumps(recall(n), ensure_ascii=False, indent=2))
        sys.exit(0)
    if cmd == "prune":
        days = _get_arg("--days", DEFAULT_PRUNE_DAYS)
        n = prune(days)
        print(f"pruned {n} sessions older than {days}d")
        sys.exit(0)
    print(f"未知子命令: {cmd}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
