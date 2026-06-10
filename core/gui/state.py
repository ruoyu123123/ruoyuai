#!/usr/bin/env python3
"""state.py — GUI 应用状态层（纯逻辑 · 零 nicegui 依赖 · 2026-06-10）

三件事：
1. AppState：跨页面共享状态（项目列表 / 运行状态 / 日志环形缓冲 / 停顿桥）。
   UI 用 ui.timer 轮询本对象——工作线程只写 AppState，UI 线程只读 + 经
   PauseBridge.respond 应答，避免任何跨线程直接操作 UI 元素（NiceGUI 官方
   推荐的轮询模式，最少踩坑面）。
2. PauseBridge：orchestrator 的 pause_for_user 停顿点（走向卡）在**工作线程**
   被调用，必须阻塞工作线程等 UI 线程的用户选择——threading.Event 桥接。
3. StderrTee：orchestrator/judge_runner/子进程汇报都打 stderr——进程级 tee
   把每行同时写原 stderr + 推入日志环形缓冲（GUI ui.log 轮询消费）。
"""
from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NOVELS_DIR = REPO_ROOT / "workspace" / "novels"

# 走向卡等待上限（秒）——超时返回 None 让流水线报错停在停顿点（可 resume），
# 绝不静默替用户做选择（北极星③）。
PAUSE_WAIT_TIMEOUT = 3600.0


# ============ 停顿桥（工作线程 ⇄ UI 线程） ============
class PauseBridge:
    """orchestrator pause_handler 的线程桥（req_id 代际令牌防陈旧/串台应答）。

    工作线程：request() 登记待选项（带单调 req_id）+ 阻塞等 Event。
    UI 线程：ui.timer 轮询 pending → 弹 dialog（捕获 req_id）→ respond(answer, req_id) 放行。

    对抗审查根治（2026-06-10）：
    - respond 必须带 req_id 且在锁内校验「仍是当前 live pending 且未应答」才接受——
      杜绝多 tab 抢答覆盖、超时后陈旧 dialog 迟点污染下一轮走向选择（北极星③）。
    - request 退出/超时把 _active_rid 置 None，让任何迟到 respond 自动失效。
    """

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self.pending: dict | None = None   # {"step","spec","options","req_id",...}
        self._answer = None
        self._req_counter = 0
        self._active_rid: int | None = None  # 当前等待应答的 req_id（None=无人等待）

    def request(self, step: dict, spec: dict, options: list):
        """pause_handler 签名兼容（orchestrator 直接把本方法当 handler 传入）。"""
        with self._lock:
            self._event.clear()
            self._answer = None
            self._req_counter += 1
            rid = self._req_counter
            self._active_rid = rid
            self.pending = {"step": step.get("n"), "spec": dict(spec or {}),
                            "options": list(options or []),
                            "req_id": rid, "requested_at": time.time()}
        ok = self._event.wait(timeout=PAUSE_WAIT_TIMEOUT)
        with self._lock:
            self.pending = None
            self._active_rid = None        # 退出后任何迟到 respond 失效
            if not ok:
                return None  # 超时 → orchestrator 报错停在停顿点（可 resume）
            return self._answer

    def respond(self, answer, req_id: int | None = None) -> bool:
        """UI 线程：提交用户选择，放行工作线程。

        返回 True=被接受；False=已过期/重复/无人等待（陈旧 dialog 迟点·多 tab 抢答后手）。
        req_id=None 时退化为「仅校验有 live pending」（向后兼容·但 GUI 应传 req_id）。
        """
        with self._lock:
            if self._active_rid is None or self._answer is not None:
                return False               # 无人等待 / 已被先到者应答
            if req_id is not None and req_id != self._active_rid:
                return False               # 陈旧轮次（捕获时的 rid 已不是当前轮）
            self._answer = answer
            accepted_rid = self._active_rid
        self._event.set()
        return True

    @property
    def waiting(self) -> bool:
        return self.pending is not None

    def current_req_id(self) -> int | None:
        with self._lock:
            return self._active_rid


# ============ 日志环形缓冲（线程安全 + 单调游标） ============
class LogBuffer:
    """deque(maxlen) + 单调总计数：UI 端用游标取「自上次以来」的新行，
    轮转挤掉旧行时游标语义仍正确（不重复不丢当前窗口内的行）。"""

    def __init__(self, maxlen: int = 2000):
        self._d = deque(maxlen=maxlen)
        self.total = 0
        self._lock = threading.Lock()

    def append(self, line: str):
        with self._lock:
            self._d.append(line)
            self.total += 1

    def since(self, cursor: int) -> tuple[list[str], int]:
        """返回 (cursor 之后的新行, 新 cursor=当前 total)。"""
        with self._lock:
            lines = list(self._d)
            total = self.total
        evicted = total - len(lines)
        start = max(cursor - evicted, 0)
        return lines[start:], total

    def __len__(self):
        with self._lock:
            return len(self._d)


# ============ stderr tee（日志捕获） ============
class StderrTee(io.TextIOBase):
    """进程级 stderr tee：写原 stderr + 按行推入环形缓冲。

    orchestrator/judge_runner/llm_transport 全部往 stderr 打日志（含子进程
    输出转写）——tee 一次安装全量捕获，模块零改动。
    """

    def __init__(self, original, sink: "LogBuffer"):
        self._orig = original
        self._sink = sink
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        try:
            self._orig.write(s)
        except Exception:
            pass  # 原 stderr 不可写（windowed exe）也不影响日志缓冲
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.rstrip("\r")
                if line.strip():
                    self._sink.append(line)
        return len(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

    @property
    def encoding(self):  # 部分库探测 stderr.encoding
        return getattr(self._orig, "encoding", "utf-8")


def install_stderr_tee(sink: "LogBuffer"):
    """幂等安装（重复调用不嵌套包裹）。返回 tee 对象。"""
    if isinstance(sys.stderr, StderrTee):
        return sys.stderr
    tee = StderrTee(sys.stderr, sink)
    sys.stderr = tee
    return tee


# ============ 项目扫描 ============
@dataclass
class ProjectInfo:
    name: str
    root: Path
    chapters_written: int = 0
    clusters_total: int = 0
    clusters_done: int = 0
    next_action: str = ""       # "cluster-write" | "cluster-save-state" | ""
    next_key: str = ""          # 建议操作的 cluster key（如 "002"）
    note: str = ""


def _cluster_key_of(cluster_id: str) -> str:
    m = re.search(r"(\d+)", cluster_id or "")
    return m.group(1) if m else ""


def scan_project(root: Path) -> ProjectInfo:
    """读一个小说项目的进度概览 + 推断下一步动作。

    推断规则（与 /write 主循环一致·只是建议·UI 允许用户改 key）：
    - 事件簇里最高号 status=in_progress 的 cluster：
        无草稿 → cluster-write 它
        有草稿但 故事块摘要 无该 cluster 条目 → cluster-save-state 它
        草稿+摘要都有 → 等涌现（save-state step11 会产下一 cluster）→ 提示已完成
    - 没有任何 in_progress → 提示走 Claude /outline 初始化（边界诚实）。
    """
    info = ProjectInfo(name=root.name, root=root)
    db = root / "_数据库"
    ch_dir = root / "章节"
    if ch_dir.exists():
        info.chapters_written = len([d for d in ch_dir.iterdir()
                                     if re.match(r"第\d+章", d.name)])
    clusters: list[dict] = []
    sj = db / "事件簇.json"
    if sj.exists():
        # except 用 ValueError（JSONDecodeError + UnicodeDecodeError 的公共祖先），
        # 顶层非 dict（数组/null/裸串）加守卫——否则 .get 抛 AttributeError 穿透
        # 本地 except → 友好提示永不触发（对抗审查根因 I）。
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                info.note = "事件簇.json 损坏（顶层非对象）"
            else:
                raw = data.get("clusters", [])
                if isinstance(raw, list):           # clusters 值必须是数组（防 null/标量·根因#4）
                    clusters = [c for c in raw if isinstance(c, dict)]
                else:
                    info.note = "事件簇.json 损坏（clusters 非数组）"
        except (OSError, ValueError):
            info.note = "事件簇.json 损坏"
    info.clusters_total = len(clusters)

    summarized: set[str] = set()
    summary_path = db / "故事块摘要.json"
    if summary_path.exists():
        # 🔴 故事块摘要.json.clusters 是 list[dict]（cluster_summary_store 产·
        # 全库 build_manifest 一致按 list-of-dict 取 .get('cluster_id')）——旧实现
        # 把它当 dict/str-list 解析 → summarized 恒空 → clusters_done 恒 0 +
        # 误推荐已完成 cluster 重跑 save-state（对抗审查根因 G）。
        try:
            sm = json.loads(summary_path.read_text(encoding="utf-8"))
            raw = sm.get("clusters", []) if isinstance(sm, dict) else []
            items = raw if isinstance(raw, list) else []   # clusters:null/标量 → 空（根因#4）
            for item in items:
                cid = item.get("cluster_id") if isinstance(item, dict) else item
                if isinstance(cid, str) and cid.startswith("cluster_"):
                    summarized.add(cid)
        except (OSError, ValueError):
            pass
    info.clusters_done = len(summarized)

    in_progress = [c for c in clusters if c.get("status") == "in_progress"]
    if in_progress:
        cur = max(in_progress,
                  key=lambda c: int(_cluster_key_of(c.get("cluster_id", "")) or 0))
        cid = cur.get("cluster_id", "")
        key = _cluster_key_of(cid)
        draft = root / "章节" / f"{cid}_draft" / f"{cid}_draft.txt"
        if not draft.exists():
            info.next_action, info.next_key = "cluster-write", key
        elif cid not in summarized:
            info.next_action, info.next_key = "cluster-save-state", key
        else:
            info.note = f"{cid} 已写完并保存——下一 cluster 待涌现（重跑 save-state 可再涌现）"
            info.next_action, info.next_key = "cluster-save-state", key
    elif not clusters:
        if not info.note:  # 「事件簇.json 损坏」等先置 note 不被覆盖
            info.note = ("未初始化（/outline 仍走 Claude 流程——"
                         "见 PROGRAM_DRIVEN.md 迁移状态）")
    else:
        info.note = "无 in_progress cluster——检查 事件簇.json status 字段"
    return info


def scan_projects(novels_dir: Path | None = None) -> list[ProjectInfo]:
    base = novels_dir or NOVELS_DIR
    result = []
    if not base.exists():
        return result
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "_数据库").exists():
            try:
                result.append(scan_project(d))
            except Exception as e:  # 单项目坏数据不拖垮列表
                result.append(ProjectInfo(name=d.name, root=d, note=f"扫描失败: {e}"))
    return result


# ============ 应用状态 ============
@dataclass
class AppState:
    projects: list = field(default_factory=list)
    selected: str = ""                      # 选中项目名
    running: bool = False
    current_command: str = ""               # cluster-write / cluster-save-state
    current_step: str = ""                  # "3/7 cluster-quality-full-stack"
    last_result: str = ""                   # 上次运行结论（成功/失败原因）
    log_buffer: LogBuffer = field(default_factory=LogBuffer)
    bridge: PauseBridge = field(default_factory=PauseBridge)
    # 🔴 不在此存日志游标：log_cursor 必须 per-client（每个浏览器 tab 各持一份），
    # 放共享 AppState 会让多 tab 瓜分日志（对抗审查根因 B）。游标由 app.py 的
    # index() 闭包持有，直接调 log_buffer.since(cursor)（LogBuffer.since 是多消费者
    # 安全的只读快照）。

    def refresh_projects(self, novels_dir: Path | None = None):
        self.projects = scan_projects(novels_dir)
        if not self.selected and self.projects:
            self.selected = self.projects[0].name

    def project(self) -> ProjectInfo | None:
        for p in self.projects:
            if p.name == self.selected:
                return p
        return None
