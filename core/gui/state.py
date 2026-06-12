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
import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# 🔴 workspace-in-frozen 修复：GUI 建书落点用 user_workspace_dir()（dev=仓库根/workspace·
# frozen=%APPDATA%/ruoyuai/workspace 可写）·与 plan_tracker.PROJECTS_DIR 统一（否则 frozen
# 下 GUI 建在 dist、resolve_project_root 找在别处 → 建书链路断·真 outline e2e 抓出）。
try:
    import sys as _sys
    _sd = str(Path(__file__).resolve().parent.parent / "scripts")
    if _sd not in _sys.path:
        _sys.path.insert(0, _sd)
    from frozen_util import user_workspace_dir as _uwd
    NOVELS_DIR = _uwd() / "novels"
except Exception:
    NOVELS_DIR = REPO_ROOT / "workspace" / "novels"

# 走向卡等待上限（秒）——超时返回 None 让流水线报错停在停顿点（可 resume），
# 绝不静默替用户做选择（北极星③）。
PAUSE_WAIT_TIMEOUT = 86400.0   # 复验修 P1-4：1h→24h（过夜走开不烧重跑 API）

# 应用版本（P1-5 升级迁移锚点）：写进用户数据区 data_version.json——
# 未来版本升级时据此判断旧数据要不要迁移（现在没有迁移逻辑·先把锚打下）。
APP_VERSION = "1.0.0"


def ensure_data_version_marker() -> None:
    """在用户数据区落 data_version.json（GUI 启动时调·失败不阻断）。"""
    try:
        from frozen_util import user_data_dir
        marker = user_data_dir() / "data_version.json"
        prev = {}
        if marker.exists():
            try:
                prev = json.loads(marker.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        info = {"app_version": APP_VERSION,
                "first_version": prev.get("first_version", APP_VERSION),
                "last_run": time.strftime("%Y-%m-%d %H:%M:%S")}
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except Exception:
        pass


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
        # 复验修（stop×pause 互锁）：停止按钮可打断 pause 等待（runner 注入同一 Event·
        # 否则走向卡弹着时按停止最长 24h 不生效）
        self.cancel_event: "threading.Event | None" = None

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
        # 分片等待：每 1s 检查停止信号（cancel → 返回 None → orchestrator 走
        # paused_at 体面退出·plan 留在停顿步可续跑）
        import time as _time
        _deadline = _time.monotonic() + PAUSE_WAIT_TIMEOUT
        ok = False
        while True:
            remaining = _deadline - _time.monotonic()
            if remaining <= 0:
                break
            if self._event.wait(timeout=min(1.0, remaining)):
                ok = True
                break
            if self.cancel_event is not None and self.cancel_event.is_set():
                break                      # 用户停止 → 走超时同款 None 返回路径
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
        self._event.set()
        return True

    @property
    def waiting(self) -> bool:
        return self.pending is not None

    def current_req_id(self) -> int | None:
        with self._lock:
            return self._active_rid


# ============ 文件日志 sink（持久化·时间戳·按日期+大小轮转·全 try/except 吞错） ============
import datetime as _dt


def classify_line(line: str) -> tuple:
    """从行内既有标记廉价派生 (level, comp)——must_fix#2：命令行真实前缀是
    `[orchestrator] ▶ step` 不是裸 `▶ step`·须匹配 [orchestrator]/派单/退出码。

    🔴 轮次1 实测收紧：「异常/❌/拒绝」是小说正文/灵感卡/verify 评分的高频合法字符，
    裸子串匹配会把创作内容误标 ERROR（污染 grep ERROR 排错纪律）。内容词分级只对
    **系统前缀行**（[gui*]/[orchestrator]/[FATAL] 等）生效；raw 子进程 stdout 只认
    Traceback/[FATAL] 强信号。模块级公共函数（文件日志 + UI 日志着色共用·P4）。"""
    is_system = line.startswith(("[gui", "[orchestrator", "[judge", "[FATAL"))
    if "[FATAL]" in line or line.startswith("Traceback") \
            or line.lstrip().startswith(("File \"", "Traceback")):
        level = "ERROR"
    elif is_system and ("❌" in line or "异常" in line):
        level = "ERROR"
    elif is_system and ("⚠" in line or "WARN" in line or "已失效" in line
                        or "拒绝" in line):
        level = "WARN"
    elif "WARN" in line[:30]:    # 子进程自标 WARN 前缀（[orchestrator] WARN 等）
        level = "WARN"
    else:
        level = "INFO"
    if line.startswith("[gui:card]") or "走向卡" in line:
        comp = "card"
    elif line.startswith("[gui:page]"):
        comp = "page"
    elif line.startswith("[gui:run]"):
        comp = "run"
    elif line.startswith("[gui:event]") or line.startswith("[gui]"):
        comp = "gui"
    elif line.startswith("[orchestrator]") or "▶ step" in line or "派单" in line \
            or "退出码" in line or line.startswith("⏸"):
        comp = "orch"
    elif "judge" in line[:14] or "[judge" in line:
        comp = "judge"
    else:
        comp = "raw"
    return level, comp


def _redact_for_log(line: str) -> str:
    """落盘前统一脱敏（防御纵深·must_fix#1）：llm_transport/gen_writer 兜底异常会把含
    gemini key-in-URL 的原文经 stderr→tee 落盘·持久化比内存更危险·这里再过一道 redact。"""
    try:
        from secrets_store import redact
        return redact(line)
    except Exception:
        return line


class _FileLogSink:
    """LogBuffer 的可选文件落地：每行 `ts | level | comp | message`（实测时 grep 切界面/命令）。

    纪律（日志是观察层不是关键路径）：全 open/write try/except 吞错→失败退化纯内存（仿
    StderrTee.write）·utf-8（frozen Windows GBK 会因中文/emoji 崩）·行缓冲近实时·
    无 logging.handlers（避免与 StderrTee 接管 sys.stderr 互喂双写）·线程安全由调用方
    LogBuffer._lock 保证（本类只在 append 临界区内被调·不自带锁）。
    """
    MAX_BYTES = 8 * 1024 * 1024
    BACKUP = 3

    def __init__(self, log_dir):
        self._dir = Path(log_dir)
        self._fp = None
        self._day = None
        self._path = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._open_today()
        except Exception:
            self._fp = None

    @property
    def path(self):
        return self._path

    def _today(self) -> str:
        return _dt.date.today().isoformat()

    def _open_today(self):
        day = self._today()
        self._path = self._dir / f"ruoyuai-gui-{day}.log"
        self._fp = open(self._path, "a", encoding="utf-8", buffering=1)
        self._day = day

    def _maybe_rotate(self):
        if self._day != self._today():
            try:
                if self._fp:
                    self._fp.close()
            except Exception:
                pass
            self._open_today()
            return
        try:
            if self._path and self._path.exists() and \
                    self._path.stat().st_size > self.MAX_BYTES:
                self._fp.close()
                for i in range(self.BACKUP, 0, -1):
                    src = self._path if i == 1 else \
                        self._path.with_name(self._path.name + f".{i-1}")
                    dst = self._path.with_name(self._path.name + f".{i}")
                    if src.exists():
                        try:
                            if dst.exists():
                                dst.unlink()
                            src.rename(dst)
                        except Exception:
                            pass
                self._fp = open(self._path, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    _classify = staticmethod(lambda line: classify_line(line))

    def write(self, line: str):
        if self._fp is None:
            return
        try:
            self._maybe_rotate()
            ts = _dt.datetime.now().isoformat(timespec="milliseconds")
            level, comp = self._classify(line)
            self._fp.write(f"{ts} | {level:<5} | {comp:<5} | {_redact_for_log(line)}\n")
        except Exception:
            self._fp = None


# ============ 日志环形缓冲（线程安全 + 单调游标） ============
class LogBuffer:
    """deque(maxlen) + 单调总计数：UI 端用游标取「自上次以来」的新行，
    轮转挤掉旧行时游标语义仍正确（不重复不丢当前窗口内的行）。

    log_file=None → 纯内存（dev 测试 zero-dep 逐字节零回归）；传目录 → 惰性建文件 sink，
    所有 append 同锁临界区内旁路落盘（命令状态经 stderr-tee 自然汇入·不重复记）。"""

    def __init__(self, maxlen: int = 2000, log_file=None):
        self._d = deque(maxlen=maxlen)
        self.total = 0
        self._lock = threading.Lock()
        self._file = _FileLogSink(log_file) if log_file else None

    @property
    def log_file_path(self):
        return self._file.path if self._file else None

    def append(self, line: str):
        with self._lock:
            self._d.append(line)
            self.total += 1
            if self._file is not None:
                self._file.write(line)

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
    # —— 写作平台感升级（2026-06-12·调研落地）——
    total_chars: int = 0        # 全书字数（章节 txt 累计·_WC_CACHE 缓存）
    total_wan: str = "0"        # 万字显示串（如 "3.2"）
    style_name: str = ""        # 风格档名（作者风格.json 的 author/source）


# 字数缓存：path → (mtime, size, chars)。命中=纯 stat 开销·refresh_projects 可控。
_WC_CACHE: dict = {}


def _chapter_chars(txt: Path) -> int:
    try:
        st = txt.stat()
        hit = _WC_CACHE.get(txt)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
        n = len(txt.read_text(encoding="utf-8"))
        _WC_CACHE[txt] = (st.st_mtime, st.st_size, n)
        return n
    except Exception:
        return 0


def scan_chapters(root: Path) -> list:
    """只读章节目录（P3·写作平台 Binder 范式）：[{num, title, chars}]。

    不进 scan_project 主路径（保持 refresh_projects 轻量）——页面按需调用。
    title 读 第N章_changes.json 的 title（可空）。"""
    out = []
    ch_dir = root / "章节"
    if not ch_dir.exists():
        return out
    for d in sorted(ch_dir.iterdir()):
        m = re.match(r"第(\d+)章", d.name)
        if not (m and d.is_dir()):
            continue
        num = int(m.group(1))
        txt = d / f"{d.name}.txt"
        title = ""
        cj = d / f"{d.name}_changes.json"
        if cj.exists():
            try:
                title = str(json.loads(cj.read_text(encoding="utf-8"))
                            .get("title") or "")
            except Exception:
                title = ""
        out.append({"num": num, "title": title,
                    "chars": _chapter_chars(txt) if txt.exists() else 0})
    out.sort(key=lambda c: c["num"])
    return out


# ============ 数据库只读查看（P0-3 正典毒化第一级缓解 · 只看不改） ============
# 白名单 = 真实项目 _数据库/ 实测存在的文件名（凿窍纪 ls 确认）。「锁定事实」没有独立
# 文件——locked_facts 挂在 人物卡.json 每张角色卡内（与 locked_fact_cross_scene_scanner
# 读取路径同源），看「人物卡」tab 即可。
# 🔴 严守只读：本层绝不提供写回（可编辑白名单是待拍板的产品决策）。
DB_VIEW_FILES: tuple = ("人物卡", "世界状态", "伏笔表", "大势卡", "事件簇")


def read_db_json(project_root: Path, name: str) -> str:
    """读 _数据库/<name>.json 给 GUI 只读渲染（纯函数·零 nicegui 依赖）。

    返回值永远是「可直接展示的字符串」，绝不向 UI 层抛异常：
    - 正常 → json 重排（ensure_ascii=False indent=2·只为可读·不回写磁盘）
    - 损坏 JSON / 非法 UTF-8 → 前缀提示行 + 原文原样（让用户看得出哪里坏·只看不改）
    - 缺文件 / 越白名单 → 中文提示行（缺文件不是故障——大纲初始化前本就没有）
    """
    if name not in DB_VIEW_FILES:
        return f"（不支持查看：{name}——可看：{'、'.join(DB_VIEW_FILES)}）"
    p = Path(project_root) / "_数据库" / f"{name}.json"
    if not p.exists():
        return f"（{name}.json 还不存在——建好大纲后自动生成）"
    try:
        # errors="replace"：非法 UTF-8 字节不抛 UnicodeDecodeError——
        # 走下方「损坏 JSON 展示原文」同一条路径（损坏正是要看的东西）
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"（{name}.json 读取失败：{e}）"
    try:
        data = json.loads(raw)
    except ValueError:
        return (f"⚠️ {name}.json 不是合法 JSON（可能被中断的写入弄坏了）"
                f"——以下为原文原样（只读）：\n\n{raw}")
    return json.dumps(data, ensure_ascii=False, indent=2)


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
        total = 0
        n_ch = 0
        for d in ch_dir.iterdir():
            if not re.match(r"第\d+章", d.name):
                continue
            n_ch += 1
            txt = d / f"{d.name}.txt"
            if txt.exists():
                total += _chapter_chars(txt)
        info.chapters_written = n_ch
        info.total_chars = total
        info.total_wan = f"{total / 10000:.1f}" if total else "0"
    # 风格档名（must_fix：用户偏好.json 无风格名·读 作者风格.json 的 author/source）
    prof_p = db / "作者风格.json"
    if prof_p.exists():
        try:
            prof = json.loads(prof_p.read_text(encoding="utf-8"))
            if isinstance(prof, dict):
                info.style_name = str(prof.get("author") or prof.get("作者")
                                      or prof.get("source") or prof.get("book")
                                      or "")[:20]
        except Exception:
            pass
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

    # 🎉 完本终态（P0-2 缺漏修复 · 2026-06-12）：cluster_emergence_engine 在 ME 池真耗尽
    # 时写 _数据库/.book_complete.json（完本不是故障）。放在所有「下一步动作」推断的
    # 最前面短路——完本书绝不再建议写下一块；统计字段（章数/字数/clusters_done）
    # 已在上方算好，照常给导出页用。完本 note 覆盖解析期可能置的损坏 note（完本是主导状态）。
    if (db / ".book_complete.json").exists():
        info.note = "🎉 本书已完本（大势走完）——去导出全文吧"
        info.next_action = ""
        info.next_key = ""
        return info

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
            info.note = ("这本书还没建好大纲——去「新建书」页开始；"
                         "若上次建书中断，去「Plan 续跑」页从断点继续")
    else:
        info.note = "这本书没有进行中的故事块——点「保存状态」可生成下一段走向"
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


def _make_log_buffer() -> "LogBuffer":
    """GUI 运行态 LogBuffer：落 user_data_dir()/logs（dev=仓库根/logs·frozen=%APPDATA%/
    ruoyuai/logs）。**must_fix#3 测试旁路**：pytest 运行 / RUOYUAI_GUI_LOG_DISABLE=1 →
    纯内存（不往工作树 repo/logs 撒文件污染 git·8 处 AppState() 测试零副作用）。
    取路径失败 → 纯内存（绝不让日志初始化拖垮 import app→GUI 起不来）。"""
    if os.environ.get("RUOYUAI_GUI_LOG_DISABLE") == "1" or "pytest" in sys.modules:
        return LogBuffer()
    override = os.environ.get("RUOYUAI_GUI_LOG_DIR")
    try:
        if override:
            return LogBuffer(log_file=Path(override))
        from frozen_util import user_data_dir
        return LogBuffer(log_file=user_data_dir() / "logs")
    except Exception:
        return LogBuffer()


# ============ 应用状态 ============
@dataclass
class AppState:
    projects: list = field(default_factory=list)
    selected: str = ""                      # 选中项目名
    running: bool = False
    current_command: str = ""               # cluster-write / cluster-save-state
    current_step: str = ""                  # "3/7 cluster-quality-full-stack"
    last_result: str = ""                   # 上次运行结论（成功/失败原因）
    # 任务完成单调计数（A4 数据破坏修复）：UI per-client 比对·变化即刷新项目/风格/key
    # （防写作台陈旧 next_key 重写已完成 cluster·比布尔边沿稳——不漏两 tick 间快跑）
    runs_finished: int = 0
    log_buffer: LogBuffer = field(default_factory=_make_log_buffer)
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
