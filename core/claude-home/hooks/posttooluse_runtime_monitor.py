#!/usr/bin/env python3
"""PostToolUse Hook: 运行时报错监控（MAPE-K **Monitor** 层 · 2026-05-30 自学习能力）

触发：Bash 工具完成后，扫子进程输出里的**真实 Python Traceback / [FATAL] / [CRASH]**，
提取「错误指纹」append 到 `core/claude-home/runtime/incidents.jsonl`，供 self_heal_engine.py
（Analyze + Learn）消费 → 自愈知识库 + lesson。

设计纪律（呼应 memory feedback_verify_stderr_not_exitcode）：
- Monitor 查 **stderr 的 Traceback**，不信 exit code（exit 0 也可能 stderr 有 crash）。
- PostToolUse 观察层：**永不 exit 非 0**（外层 try 兜底 + 末尾 sys.exit(0)），绝不打断主流水线。
- 只记**真 Python crash**（Traceback + File 行 + 异常行）或脚本自报 [FATAL]/[CRASH]；
  排除 grep/echo/cat/git log 等搜索/打印类命令（其输出里的 "Traceback" 多是字面文本非真崩溃）。
- 错误指纹 signature = `script::error_type::location`，供 self_heal_engine 按复发计数。
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

TRACEBACK_MARK = "Traceback (most recent call last):"
EXC_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit)):?\s*(.*)$", re.M)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
SCRIPT_RE = re.compile(r"core[/\\]scripts[/\\](\w+\.py)")
FATAL_RE = re.compile(r"\[(FATAL|CRASH)\]\s*(.+)")
# 命令含这些 = 搜索/打印/版本控制类，输出里的 "Traceback" 多为字面文本，跳过避免误报
SEARCH_CMDS = ("grep", "rg ", "select-string", "findstr", "cat ", " type ",
               "head ", "tail ", "git log", "git show", "git diff", "echo ", "ast.parse")


def _norm_msg(msg: str) -> str:
    """归一错误消息（去具体引号内容/数字/路径），便于复发去重。"""
    msg = re.sub(r"['\"][^'\"]*['\"]", "'X'", msg)
    msg = re.sub(r"[A-Za-z]:[\\/][^\s]+", "PATH", msg)
    msg = re.sub(r"\b\d+\b", "N", msg)
    return msg.strip()[:200]


def _basename(p: str) -> str:
    return p.replace("\\", "/").split("/")[-1]


def main():
    try:
        # 2026-07-08 修（Windows 编码根因）：bytes 读 stdin·json 自动 UTF-8（GBK 控制台文本读会花）
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command", "") or ""
    cmd_low = command.lower()

    # 提取子进程 output（容错多 key：tool_response.stdout/stderr/output 或裸字符串）
    resp = payload.get("tool_response")
    if resp is None:
        resp = payload.get("tool_result") or payload.get("output") or ""
    if isinstance(resp, dict):
        out = "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output"))
    else:
        out = str(resp)
    if not out.strip():
        return

    # 搜索/打印/版本控制类命令：输出里的 Traceback 多是字面文本，不当真实崩溃
    is_search = any(s in cmd_low for s in SEARCH_CMDS)
    if is_search:
        return

    incidents = []

    # 1) 真 Python Traceback（取最后一段 = 最近崩溃）
    if TRACEBACK_MARK in out:
        seg = out[out.rfind(TRACEBACK_MARK):]
        files = FILE_LINE_RE.findall(seg)
        exc_matches = EXC_RE.findall(seg)
        exc_type, exc_msg = (exc_matches[-1] if exc_matches else ("UnknownError", ""))
        loc = f"{_basename(files[-1][0])}:{files[-1][1]}" if files else ""
        scripts = SCRIPT_RE.findall(seg) or SCRIPT_RE.findall(command)
        script = scripts[-1] if scripts else ""
        incidents.append({
            "kind": "python_traceback",
            "script": script,
            "error_type": exc_type,
            "location": loc,
            "message": _norm_msg(exc_msg),
            "signature": f"{script}::{exc_type}::{loc}",
            "raw": seg[:600],
        })

    # 2) [FATAL]/[CRASH] 标记（脚本/编排器自报致命）
    for kind, msg in FATAL_RE.findall(out):
        sc = SCRIPT_RE.search(command)
        sname = sc.group(1) if sc else ""
        nmsg = _norm_msg(msg)
        incidents.append({
            "kind": kind.lower(),
            "script": sname,
            "error_type": kind,
            "location": "",
            "message": nmsg,
            "signature": f"{sname}::{kind}::{nmsg[:60]}",
            "raw": msg.strip()[:300],
        })

    if not incidents:
        return

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    runtime_dir = project_dir / "core" / "claude-home" / "runtime"
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        with open(runtime_dir / "incidents.jsonl", "a", encoding="utf-8") as f:
            for inc in incidents:
                inc["ts"] = ts
                inc["command_snippet"] = command[:160]
                f.write(json.dumps(inc, ensure_ascii=False) + "\n")
        print(f"⚠️ [runtime-monitor] 捕获 {len(incidents)} 条运行时报错 → runtime/incidents.jsonl"
              f"（self_heal_engine 将学习规避）· 指纹: {incidents[0]['signature']}", file=sys.stderr)
    except Exception:
        pass  # 记录失败也不打断主流水线


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)  # MAPE-K Monitor / PostToolUse 观察层：永远 exit 0
