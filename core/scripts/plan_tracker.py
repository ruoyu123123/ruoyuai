#!/usr/bin/env python3
"""
plan_tracker.py — 多步命令的强制规划与执行追踪系统（Phase 1）

设计目标
--------
让小说系统的多步命令（cluster-save-state / cluster-write / distill-style /
outline）在 Agent 或命令执行时**无法跳步**：

🔴 v26+: 创作主链只接受 cluster mode；plan key 表示 cluster key / cluster id。

- 命令开始前必须 create 一个 plan，拿到 plan_id；
- 每完成一步必须 step <plan_id> --n N，脚本校验 expected_outputs；
- 命令结束时必须 end <plan_id>，检查所有 required 步骤已完成；
- 任何中途中止必须 abort <plan_id> --reason，留下审计痕迹。

与 save_state.py 内置 WAL 的关系
-------------------------------
- WAL 是 cluster-save-state 单命令内的细粒度断点恢复（completed_steps）；
- plan_tracker 是**所有命令**统一的强制规划层（plan_id + verified_outputs）；
- 二者**共存不冲突**——本工具不动 WAL 的任何字段；attestation 也只加 plan
  JSON 的 `_attestation` 字段，不触碰 WAL（见 lessons L8.4）。

防篡改 attestation（P1-1）
-------------------------
plan_tracker 是 plan JSON 的【唯一合法写入者】。每次合法写盘都把 plan 规范化
内容的 SHA-256 写进 `plan["_attestation"]`；每次写前读校验，不符 → 阻断。
任何不经 plan_tracker 的修改（Agent 直接 Edit / 旁路脚本 / prompt 注入写盘，
典型是「伪造 step 状态骗过跳步防御」）都会被 verify_attestation 抓出。
合法手动改 plan 后用 `reattest` 重新盖章。详见下方 attestation 段注释。

CLI 子命令
----------
- create   --command <cmd> --project <name> [--key <cluster-key-or-id>]
- step     <plan_id> --n <step_num> [--output <file>] [--skip-output]
                     [--tokens N] [--duration-ms N]   # P2-8：subagent 成本追踪
- end      <plan_id>
- status   <plan_id>
- list     [--active]
- abort    <plan_id> --reason <msg>
- verify   <plan_id>                 # P1-1：校验防篡改 attestation
- reattest <plan_id>                 # P1-1：合法手动改 plan 后重新盖章

Python API
----------
- create_plan(command, project, **kwargs) -> str
- step_complete(plan_id, n, output=None, skip_output=False) -> bool
- end_plan(plan_id) -> dict
- get_plan(plan_id) -> dict
- list_plans(active_only=False) -> list[dict]
- abort_plan(plan_id, reason) -> dict
- verify_plan(plan_id) -> str         # "ok"/"tampered"/"unattested"/"not_found"
- reattest_plan(plan_id) -> dict
- verify_attestation(plan: dict) -> str  # dict 级校验

存储位置
--------
- 模板：   <REPO_ROOT>/core/claude-home/plans/<command>.plan.json
- 运行时：
    * 项目相关（能解析项目根）→ <project_root>/_数据库/.plans/<plan_id>.json
    * 蒸馏风格项目          → <style_root>/.plans/<plan_id>.json
    * 无项目                → <REPO_ROOT>/core/claude-home/.plans/<plan_id>.json

plan_id 格式
-----------
{project}_{key}_{command}_{YYYYMMDDTHHMMSS}
其中 cluster-write / cluster-save-state 必须传 cluster key 或 cluster id；
数字 key 会规范为三位 cluster key（如 `1` / `cluster_001` → `001`）。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac  # 2026-05-29 修：attestation 改 HMAC 防伪造
import json
import os
import secrets  # 2026-05-29 修：本地密钥 + plan_id 随机后缀
import re  # 2026-05-30 北极星复审：_verify_agent_report emergence 校验(609)用 re.search，原模块级缺 → NameError
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

# 2026-06-13 残余非原子写收编：plan JSON 写盘走 atomic_json（tmp pid+uuid + fsync +
# os.replace）——崩溃/断电留半截 plan = json 解析失败 = attestation/断点恢复全废。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_json  # noqa: E402


# ============ 常量 / 路径 ============

REPO_ROOT = Path(__file__).resolve().parents[2]
# 🔴 frozen-aware（对抗审查同款 FATAL · 2026-06-10 阶段B GUI exe）：PyInstaller 把
# core/scripts 模块扁平收成顶层名，Path(__file__).parents[2] 在 frozen 下跑出 bundle 外 →
# load_template 读不到 core/claude-home/plans/*.json（orchestrator.run_command 首步即崩）。
# 只把**只读模板目录**改用 bundle_root()（frozen=_MEIPASS·dev=parents[2] 逐字节一致·datas
# 落 bundle_root()/core/claude-home/plans）；writable 的 plan 落盘目录（GLOBAL_PLANS_DIR/
# PROJECTS_DIR/STYLES_DIR）保持 REPO_ROOT 不动（不写进只读 _internal 概念区）。
try:
    from frozen_util import bundle_root as _bundle_root
    TEMPLATES_DIR = _bundle_root() / "core" / "claude-home" / "plans"
except Exception:
    TEMPLATES_DIR = REPO_ROOT / "core" / "claude-home" / "plans"
# 🔴 workspace-in-frozen 修复（真 outline e2e 抓出）：novels/styles 是**用户创作产物**·
# frozen 下用 user_workspace_dir()（dev=仓库根/workspace 逐字节一致·frozen=%APPDATA%/ruoyuai/
# workspace 可写）。否则 frozen 下 REPO_ROOT=dist 指错 → GUI 建书/resolve_project_root 全错位。
# 🔴 frozen 可写数据修复：无项目兜底 plan + attest HMAC 密钥用 user_data_dir()（每次盖章写）。
try:
    from frozen_util import user_data_dir as _udd, user_workspace_dir as _uwd
    _WRITABLE_ROOT = _udd()
    _WORKSPACE = _uwd()
except Exception:
    _WRITABLE_ROOT = REPO_ROOT
    _WORKSPACE = REPO_ROOT / "workspace"
PROJECTS_DIR = _WORKSPACE / "novels"
STYLES_DIR = _WORKSPACE / "styles"
GLOBAL_PLANS_DIR = _WRITABLE_ROOT / "core" / "claude-home" / ".plans"
# 2026-05-29 修：attestation HMAC 的机器本地密钥（与 GLOBAL_PLANS_DIR 同级隐藏文件）
ATTEST_KEY_PATH = GLOBAL_PLANS_DIR / ".attest_key"

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"

KNOWN_COMMANDS = (
    "distill-style",
    "distill-character",  # 🔴 2026-06-27 C13：角色蒸馏纳入 plan 强制规划层（6 步·PLAN_ID/STEP/attestation）
    "outline",
    "cluster-write",  # cluster 级写作流水线 7 步
    "cluster-save-state",  # cluster 级 save-state
)


# ============ JSON IO（统一 UTF-8 + ensure_ascii=False） ============

def _json_dump_safe(data: Any) -> str:
    """写盘前的自检：JSON 不可序列化直接 raise。"""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"[plan_tracker] JSON 序列化失败：{exc}") from exc


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    text = _json_dump_safe(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 2026-06-13 残余非原子写收编：裸 write_text → atomic_write_text 原子落盘。
    # 序列化仍由 _json_dump_safe 权威产出（ensure_ascii=False / indent=2 /
    # sort_keys=False + 不可序列化 → RuntimeError 自检），字节内容与旧实现完全一致，
    # 只换写盘方式（tmp 唯一名 + fsync + os.replace · 崩溃不留半截 plan）。
    atomic_json.atomic_write_text(path, text)


# ============ 防篡改 attestation（P1-1，借鉴 planning-with-files）============
#
# 威胁模型：plan_tracker.py 是 plan JSON 的【唯一合法写入者】。任何不经过
# plan_tracker 的修改（Agent 直接 Edit、旁路脚本、prompt 注入写盘）——典型是
# 「伪造 step 状态骗过跳步防御」——都应被检测到。
#
# 机制：每次合法写盘（create/step/end/abort）把 plan 规范化内容的 SHA-256 写进
# plan["_attestation"]；每次写前读（step/end/abort）校验，不符 → raise
# PlanTamperedError 阻断。只读操作（status/list/get_plan）仅 stderr 警告不阻断。
#
# 为什么用 inline 字段而非 sidecar 文件：
#   - 单文件原子写，无「写完 JSON 没写 sidecar」的崩溃窗口（L8.4「中途断电」安全）
#   - 无孤儿 sidecar 需要清理（cleanup 逻辑只认 *.json）
#   - 真实威胁（naive 直接编辑）inline/sidecar 都能抓；高级攻击者两者都防不住
# 规范化哈希（sort_keys + 紧凑分隔符，排除 _attestation 自身）→ 与磁盘 indent
# 格式解耦：重新美化/换行不会误判，只有【内容】变化才触发。

ATTESTATION_KEY = "_attestation"


class PlanTamperedError(Exception):
    """plan JSON 内容与 attestation 不符 —— 被 plan_tracker 之外的途径改过。"""


# 2026-05-29 修【安全·伪造】：原 attestation 用纯 SHA-256(规范化JSON)，攻击者改
# 内容后自己重算 hash 写回即过校验 —— 防篡改形同虚设。改用 HMAC-SHA256 + 机器
# 本地密钥（ATTEST_KEY_PATH）：攻击者没有密钥就无法伪造有效 attestation。
# 向后兼容：旧的纯 sha256 attestation 在 verify 时若 HMAC 不符但旧式 sha256 符合，
# 视为合法并由调用方自动 reattest（warn 一次），避免现存 plan 全部失效。

_ATTEST_KEY_CACHE: bytes | None = None


def _get_attest_key() -> bytes:
    """读取（或首次生成）机器本地 HMAC 密钥。
    密钥不存在 → secrets.token_bytes(32) 生成、写盘、chmod 0o600（Windows 容错）。"""
    global _ATTEST_KEY_CACHE
    if _ATTEST_KEY_CACHE is not None:
        return _ATTEST_KEY_CACHE
    if ATTEST_KEY_PATH.exists():
        key = ATTEST_KEY_PATH.read_bytes().strip()
        if key:
            _ATTEST_KEY_CACHE = key
            return key
    # 首次运行：生成新密钥
    key = secrets.token_bytes(32)
    ATTEST_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ATTEST_KEY_PATH.write_bytes(key)
    try:
        os.chmod(ATTEST_KEY_PATH, 0o600)
    except (OSError, NotImplementedError):
        pass  # Windows / 受限平台：chmod 不可用时容错
    _ATTEST_KEY_CACHE = key
    return key


def _canonical_plan_bytes(plan: dict) -> bytes:
    """attestation 哈希的输入：规范化 JSON 字节流，排除 _attestation 字段自身。"""
    payload = {k: v for k, v in plan.items() if k != ATTESTATION_KEY}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _compute_attestation(plan: dict) -> str:
    """2026-05-29 修：HMAC-SHA256(本地密钥, 规范化字节流)。"""
    return hmac.new(_get_attest_key(), _canonical_plan_bytes(plan),
                    hashlib.sha256).hexdigest()


def _compute_legacy_sha256(plan: dict) -> str:
    """旧式纯 SHA-256 attestation（仅用于向后兼容校验，不再用于盖章）。"""
    return hashlib.sha256(_canonical_plan_bytes(plan)).hexdigest()


def _attest(plan: dict) -> dict:
    """给 plan 盖章（原地写入 _attestation 字段）。所有合法写盘前调用。"""
    plan[ATTESTATION_KEY] = {
        "sha256": _compute_attestation(plan),
        "attested_at": datetime.now().isoformat(timespec="seconds"),
        "by": "plan_tracker",
    }
    return plan


def verify_attestation(plan: dict) -> str:
    """校验 plan dict 的 attestation。
    返回 "ok" / "legacy" / "tampered" / "unattested"。
      ok        = HMAC 匹配（当前格式）
      legacy    = HMAC 不符但旧式纯 sha256 符合 → 向后兼容，调用方应自动 reattest
      tampered  = 既不匹配 HMAC 也不匹配旧 sha256 → 真篡改
      unattested= 旧 plan（本功能引入前创建）—— 向后兼容，调用方不应阻断。
    2026-05-29 修：用 hmac.compare_digest 做常量时间比较。"""
    if not isinstance(plan, dict):
        return "unattested"
    att = plan.get(ATTESTATION_KEY)
    if not isinstance(att, dict) or not att.get("sha256"):
        return "unattested"
    stored = att["sha256"]
    if hmac.compare_digest(stored, _compute_attestation(plan)):
        return "ok"
    # 向后兼容：旧式纯 sha256 命中 → legacy（合法，需自动 reattest）
    if hmac.compare_digest(stored, _compute_legacy_sha256(plan)):
        return "legacy"
    return "tampered"


_TAMPER_MSG = (
    "[plan_tracker] ⚠️ 防篡改校验失败：plan 内容与 attestation 不符。\n"
    "  含义：此 plan JSON 被 plan_tracker 之外的途径改过"
    "（Agent 直接编辑 / 旁路脚本 / 注入写盘）。\n"
    "  若是你手动合理修改的 → 跑 `plan_tracker.py reattest <plan_id>` 重新盖章；\n"
    "  否则这是一次跳步/伪造尝试，已阻断。"
)


def _save_plan(path: Path, plan: dict) -> None:
    """plan 专用写盘：盖 attestation 章后落盘。所有 plan 写操作必须走这里。"""
    _attest(plan)
    _save_json(path, plan)


def _load_plan(path: Path, *, for_write: bool = False) -> dict:
    """plan 专用读盘 + 防篡改校验。
      for_write=True （step/end/abort 写前读）：tampered → raise PlanTamperedError
      for_write=False（status/get_plan 只读） ：tampered → 仅 stderr 警告，不阻断
      legacy（旧式纯 sha256 attestation）：2026-05-29 修 —— 合法但需迁移，自动
              用 HMAC 重新盖章并 warn 一次，避免现存 plan 在 HMAC 切换后全部失效。
      unattested（旧 plan）：两种模式都放行（向后兼容），下次写入时自动盖章。
    """
    plan = _load_json(path)
    if not isinstance(plan, dict):
        return plan
    state = verify_attestation(plan)
    if state == "legacy":
        # 2026-06-17 安全修复（HMAC #4·非对称硬化）：legacy=旧式纯 sha256·而 _compute_legacy_sha256
        # 是**公开无密钥**算法 → 攻击者可篡改 plan（伪造 step 状态）后公开重算盖章绕过防御
        # （forged-legacy 与 legit-legacy 都返回 legacy·无密钥校验器无法区分）。写路径信任无密钥
        # hash = 信任伪造 → 同 tampered 拦。合法老 plan（HMAC 迁移 2026-05-29 已满·终态 7 天 cleanup·
        # 极罕见）走 `plan_tracker.py reattest <plan_id>` 升级恢复。
        if for_write:
            raise PlanTamperedError(
                f"{_TAMPER_MSG}\n  文件：{path}\n"
                f"  （legacy 旧式 sha256·写路径不信任无密钥盖章[可被公开重算伪造]·"
                f"合法老 plan 跑 `plan_tracker.py reattest <plan_id>` 升级 HMAC）")
        # 读路径 tolerant：自动迁移 HMAC（观测平滑·legit 老 plan 升级·不破坏 get_plan/监控/GUI）
        print(f"[plan_tracker] ℹ️ 旧式 sha256 attestation 自动迁移为 HMAC：{path}")
        _save_plan(path, plan)
    elif state == "unattested":
        # 2026-06-17 安全修复（HMAC #4）：unattested=无 _attestation 字段 → 删 _attestation 即可绕过
        # 校验（删字段伪造通道）。写路径同 tampered 拦；读路径放行（真 unattested 老 plan 观测兼容·
        # 新建 plan create 时 _save_plan 已盖 HMAC·不受影响）。
        if for_write:
            raise PlanTamperedError(
                f"{_TAMPER_MSG}\n  文件：{path}\n"
                f"  （unattested 无 attestation 字段·写路径不放行无章 plan[删字段伪造通道]·"
                f"合法老 plan 跑 `plan_tracker.py reattest <plan_id>` 盖章）")
    elif state == "tampered":
        full_msg = f"{_TAMPER_MSG}\n  文件：{path}"
        if for_write:
            raise PlanTamperedError(full_msg)
        print(full_msg, file=sys.stderr)
    return plan


# ============ 项目根解析 ============

def resolve_project_root(project: str) -> Path | None:
    """根据 --project 推断项目根：先查 projects/，再查 styles/。

    返回 None 表示找不到对应项目（命令仍可继续，plan 落到 GLOBAL_PLANS_DIR）。
    """
    if not project:
        return None
    # 🔴 真机 e2e 抓修(2026-06-15)：project 本身是有效路径(完整/相对·非仅书名)→ 直接用。
    # 原仅查 PROJECTS_DIR/书名 → orchestrator CLI --project 传完整路径(workspace/novels/X)时
    # 返回 None → _verify_outputs project_root=None → expected_outputs 相对 cwd 误判缺失；
    # run_command 与 _verify_outputs 的项目根解析必须一致。
    direct = Path(project)
    if direct.exists():
        return direct
    candidate_a = PROJECTS_DIR / project
    if candidate_a.exists():
        return candidate_a
    candidate_b = STYLES_DIR / project
    if candidate_b.exists():
        return candidate_b
    return None


def runtime_plans_dir(project: str | None) -> Path:
    """运行时 plan.json 的存放目录。"""
    root = resolve_project_root(project) if project else None
    if root is None:
        return GLOBAL_PLANS_DIR
    # 项目根下：小说项目用 _数据库/.plans/，风格库用 .plans/
    if (root / "_数据库").exists():
        return root / "_数据库" / ".plans"
    return root / ".plans"


# ============ 模板加载 + 占位符替换 ============

def _template_path(command: str) -> Path:
    return TEMPLATES_DIR / f"{command}.plan.json"


def load_template(command: str) -> dict:
    """加载命令模板。模板必须存在；不存在直接 raise。"""
    p = _template_path(command)
    if not p.exists():
        raise FileNotFoundError(
            f"[plan_tracker] 模板不存在：{p}\n"
            f"已知模板目录：{TEMPLATES_DIR}\n"
            f"已知命令：{KNOWN_COMMANDS}"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"[plan_tracker] 模板 JSON 损坏：{p} — {exc}") from exc


def normalize_cluster_key(key: str | None, *, required: bool = False) -> str | None:
    """把 cluster key / cluster id 规范成三位 key。

    允许输入 `001`、`1`、`cluster_001`。非数字业务 key 保留原样；主线 cluster
    命令由 create_plan 在 required=True 时强制必须有 key。
    """
    if key is None:
        if required:
            raise ValueError("[plan_tracker] cluster-only 命令必须传 --key <cluster-key-or-id>")
        return None
    raw = str(key).strip()
    if not raw:
        if required:
            raise ValueError("[plan_tracker] cluster-only 命令必须传非空 --key <cluster-key-or-id>")
        return None
    m = re.fullmatch(r"(?:cluster_)?0*(\d+)", raw, re.IGNORECASE)
    if m:
        return f"{int(m.group(1)):03d}"
    return raw


def cluster_id_from_key(key: str | None) -> str | None:
    """由规范 key 得到 cluster id；非数字 key 也统一加 cluster_ 前缀。"""
    norm = normalize_cluster_key(key)
    if not norm:
        return None
    return norm if str(norm).startswith("cluster_") else f"cluster_{norm}"


def _substitute(text: str, project: str, key: str | None) -> str:
    """替换 {project} / {key} / {cluster_id} / {next_key} 占位符。

    cluster-only 契约下，plan 模板不再使用章号占位符。若模板仍含 `{ch...}`，
    创建 plan 直接失败，避免把旧单章路径静默替换为空。
    """
    import re
    if not isinstance(text, str):
        return text
    out = text.replace("{project}", project or "")
    if re.search(r"\{ch(?:[+\-]\d+)?(?::03d)?\}", out):
        raise ValueError(f"[plan_tracker] 模板仍含旧章号占位符，cluster-only 禁止使用：{text}")

    # v26: {next_key} 替换 (NNN → NNN+1 · 输出纯数字段 · 适配 cluster_{next_key}_xxx 模板)
    # 设计契约: plan template 写 `cluster_{next_key}_xxx` 时 key="001" → next_key="002"
    #          key="cluster_001" 也按内部数字段递增 + 剥前缀 → next_key="002"
    if "{next_key}" in out:
        next_key = ""
        if key:
            m = re.search(r"(\d+)", key)
            if m:
                next_num = int(m.group(1)) + 1
                # 复验修：强制 :03d 与 cluster_emergence_engine 完本/涌现两处
                # 对齐（原「保持原宽度」在 --key 2/0001 时与 engine 写的 WAL
                # 文件名不一致 → step11 expected_outputs FileNotFoundError 卡死）
                next_key = f"{next_num:03d}"
            else:
                next_key = key + "_next"
        out = out.replace("{next_key}", next_key)

    out = out.replace("{cluster_id}", cluster_id_from_key(key) or "")
    out = out.replace("{key}", key or "")
    return out


def _walk_substitute(node: Any, project: str, key: str | None) -> Any:
    if isinstance(node, str):
        return _substitute(node, project, key)
    if isinstance(node, list):
        return [_walk_substitute(x, project, key) for x in node]
    if isinstance(node, dict):
        return {k: _walk_substitute(v, project, key) for k, v in node.items()}
    return node


# ============ plan_id ============

def make_plan_id(command: str, project: str, key: str | None) -> str:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    # 微秒后缀确保同秒多次创建不冲突
    micro = datetime.now().strftime("%f")[:3]
    # 2026-05-29 修【可靠性】：同毫秒内连续 create 会产生相同 id 并覆盖前一个 plan。
    # 追加 secrets.token_hex(3) 随机后缀（6 hex 字符）彻底消除碰撞。
    rand = secrets.token_hex(3)
    if key:
        keypart = key
    else:
        keypart = "main"
    project_part = project if project else "noproject"
    return f"{project_part}_{keypart}_{command}_{ts}{micro}{rand}"


# ============ Python API ============

def create_plan(
    command: str,
    project: str,
    key: str | None = None,
) -> str:
    """创建一个 plan，返回 plan_id。

    cluster-write / cluster-save-state 为 cluster-only 命令，必须传
    --key，且会写入 cluster_key / cluster_id 运行时字段。
    """
    if command not in KNOWN_COMMANDS:
        # 不强制，但给出提示——允许未来扩展新命令
        print(f"[plan_tracker] 警告：未知命令 '{command}'，已知：{KNOWN_COMMANDS}")

    cluster_key_required = command in {"cluster-write", "cluster-save-state"}
    key = normalize_cluster_key(key, required=cluster_key_required)

    template = load_template(command)
    plan = _walk_substitute(deepcopy(template), project, key)

    plan_id = make_plan_id(command, project, key)
    now = datetime.now().isoformat(timespec="seconds")

    plan["id"] = plan_id
    plan["command"] = command
    plan["project"] = project
    plan["key"] = key
    plan["cluster_key"] = key if cluster_key_required else None
    plan["cluster_id"] = cluster_id_from_key(key) if cluster_key_required else None
    plan["created_at"] = now
    plan["started_at"] = now
    plan["completed_at"] = None
    plan["abort_reason"] = None

    # 运行时字段补全
    for step in plan.get("steps", []):
        step.setdefault("status", STATUS_PENDING)
        step.setdefault("verified_outputs", [])
        step.setdefault("started_at", None)
        step.setdefault("completed_at", None)
        step.setdefault("error", None)

    out_dir = runtime_plans_dir(project)
    out_path = out_dir / f"{plan_id}.json"
    _save_plan(out_path, plan)  # P1-1：创建即盖 attestation 章
    return plan_id


def _find_plan_path(plan_id: str) -> Path:
    """根据 plan_id 找到运行时文件路径。

    plan_id 第一段是 project，用它定位目录；找不到再扫全部备选目录。
    """
    project = plan_id.split("_", 1)[0] if "_" in plan_id else None
    primary = runtime_plans_dir(project) / f"{plan_id}.json"
    if primary.exists():
        return primary
    # 兜底：扫所有可能位置
    candidates = [GLOBAL_PLANS_DIR]
    if PROJECTS_DIR.exists():
        for p in PROJECTS_DIR.iterdir():
            if p.is_dir():
                candidates.append(p / "_数据库" / ".plans")
                candidates.append(p / ".plans")
    if STYLES_DIR.exists():
        for s in STYLES_DIR.iterdir():
            if s.is_dir():
                candidates.append(s / ".plans")
    for c in candidates:
        f = c / f"{plan_id}.json"
        if f.exists():
            return f
    raise FileNotFoundError(f"[plan_tracker] 找不到 plan：{plan_id}")


def get_plan(plan_id: str) -> dict:
    # 只读：tampered 时 _load_plan 仅 stderr 警告，不阻断观测
    return _load_plan(_find_plan_path(plan_id), for_write=False)


def verify_plan(plan_id: str) -> str:
    """按 plan_id 校验防篡改。返回 "ok"/"tampered"/"unattested"/"not_found"。
    供 PreToolUse hook 调用 —— 永不抛异常（出错一律当 not_found 处理）。
    必须用 RAW _load_json（不是 _load_plan）—— 本函数只报状态，不阻断。"""
    try:
        path = _find_plan_path(plan_id)
        plan = _load_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return "not_found"
    if not isinstance(plan, dict):
        return "not_found"
    return verify_attestation(plan)


def reattest_plan(plan_id: str) -> dict:
    """重新盖章 —— 用于【合法】手动修改 plan 之后。返回 {plan_id, was, sha256}。
    必须用 RAW _load_json —— reattest 的用途就是处理 tampered plan，
    不能走会因 tampered 抛异常的 _load_plan。"""
    path = _find_plan_path(plan_id)
    plan = _load_json(path)
    was = verify_attestation(plan)
    _save_plan(path, plan)  # 重新计算 + 写入 attestation
    return {
        "plan_id": plan_id,
        "was": was,
        "sha256": plan[ATTESTATION_KEY]["sha256"][:16] + "...",
    }


def _find_step(plan: dict, n) -> dict:
    """v17.10 修正：兼容 int / float / str 编号（如 '2.5' 旧模板）。"""
    for s in plan.get("steps", []):
        sn = s.get("n")
        # 字符串化后比较，兼容 1 / "1" / "2.5" / 2.5
        if str(sn) == str(n) or sn == n:
            return s
    raise ValueError(f"[plan_tracker] plan 中没有第 {n} 步")


def _verify_declared_report(project: str, declared: str) -> bool:
    """v28 程序驱动（2026-06-10）：plan 模板 steps[].judge_report_path 显式声明产物路径
    → 直接验该文件存在——JudgeReport 命名学从下方 _verify_agent_report 的 if/elif
    路径推算**外移到模板字段**（内嵌命名变体是回归高发区·v27 已修过失配）。

    支持 <round> 等角括号占位 → 文件名通配（reading-reflector 多轮任一存在即过）。
    相对路径以 project_root 为基准；项目找不到 → False，required 产物不能降级。
    """
    project_root = resolve_project_root(project) if project else None
    if not project_root:
        return False
    raw = declared.replace("{project_root}", str(project_root))
    raw = re.sub(r"<[a-z][a-z0-9_]*>", "*", raw)
    p = Path(raw)
    if not p.is_absolute():
        p = project_root / raw
    if "*" in p.name:
        return p.parent.exists() and bool(list(p.parent.glob(p.name)))
    return p.exists()


def _verify_agent_report(project: str, agent_name: str, cluster_id: str | None) -> bool:
    """v24 anti-skip: 校验 must_spawn_agent 字段对应的 JudgeReport 文件真实存在。

    cluster-only 路径规范：
    - novel-summarizer → _数据库/.wal/cluster_<key>_summary.json
    - novel-foreshadower → _数据库/.judge_reports/cluster_<key>_foreshadower.json
    - novel-reflector → _数据库/.wal/cluster_<key>_reflection.json
    - novel-reading-reflector → _数据库/.reading_reflection/cluster_<key>_round_*.json
    - novel-voice-checker → _数据库/.judge_reports/cluster_<key>_voice-checker.json
    - novel-outline-planner → _数据库/.wal/cluster_<key>_emergence.json
    - novel-writer → 章节/cluster_<key>_draft/cluster_<key>_draft.txt
    - novel-chapter-splitter → _数据库/.wal/splitter_cluster_<key>_decisions.json
    """
    project_root = resolve_project_root(project) if project else None
    if not project_root:
        return False
    if not cluster_id:
        return False

    db = project_root / "_数据库"

    candidates = []
    canonical_cluster_id = cluster_id_from_key(cluster_id) or str(cluster_id)
    key = canonical_cluster_id.replace("cluster_", "", 1)

    if agent_name == "novel-summarizer":
        candidates += [db / ".wal" / f"{canonical_cluster_id}_summary.json"]
    elif agent_name == "novel-foreshadower":
        candidates += [db / ".judge_reports" / f"{canonical_cluster_id}_foreshadower.json"]
    elif agent_name == "novel-reflector":
        candidates += [db / ".wal" / f"{canonical_cluster_id}_reflection.json"]
    elif agent_name == "novel-reading-reflector":
        rr_dir = db / ".reading_reflection"
        return rr_dir.exists() and bool(list(rr_dir.glob(f"{canonical_cluster_id}_round_*.json")))
    elif agent_name == "novel-voice-checker":
        candidates += [db / ".judge_reports" / f"{canonical_cluster_id}_voice-checker.json"]
    elif agent_name == "novel-outline-planner":
        candidates += [db / ".wal" / f"{canonical_cluster_id}_emergence.json"]
        _nm = re.search(r"(\d+)", key)
        if _nm:
            _nxt = int(_nm.group(1)) + 1
            candidates += [db / ".wal" / f"cluster_{_nxt:03d}_emergence.json"]
    elif agent_name == "novel-writer":
        candidates += [project_root / "章节" / f"{canonical_cluster_id}_draft" / f"{canonical_cluster_id}_draft.txt"]
    elif agent_name == "novel-chapter-splitter":
        candidates += [db / ".wal" / f"splitter_{canonical_cluster_id}_decisions.json"]

    return any(c.exists() for c in candidates)


def _apply_touch_outputs(plan: dict, step: dict, project: str | None) -> list[str]:
    """🔴 2026-06-26 加（cluster_001 翻车 sediment）：消费模板 step.touch_outputs。

    模板里有 touch_outputs 声明的（如 `.reading_reflection/.placeholder`）是
    "多轮产物文件名可变的存在性代理"，原意是 orchestrator/调用方跑完后 touch。
    Claude Code CLI 路径下没人 touch → expected_outputs 校验失败 → 主代理被迫手补。
    现在 plan_tracker 在 step --n 时自动 touch，免去主代理 New-Item 苦力。

    返回 touched 路径列表（仅作日志用，绝对路径）。
    """
    touch_list = step.get("touch_outputs") or []
    if not touch_list:
        return []
    project_root = resolve_project_root(project) if project else None
    touched: list[str] = []
    for raw in touch_list:
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute() and project_root is not None:
            p = project_root / raw
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("", encoding="utf-8")
            touched.append(str(p).replace("\\", "/"))
        except Exception:
            pass  # touch 失败不阻断；后续 expected_outputs 校验会暴露
    return touched


def _verify_outputs(plan: dict, step: dict, project: str | None) -> tuple[list[str], list[str]]:
    """校验 expected_outputs 是否存在。

    返回 (verified, missing)。绝对路径直接判断；相对路径以项目根为基准。
    """
    expected = step.get("expected_outputs", []) or []
    verified: list[str] = []
    missing: list[str] = []
    project_root = resolve_project_root(project) if project else None

    for raw in expected:
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute() and project_root is not None:
            p = project_root / raw
        if p.exists():
            verified.append(str(p).replace("\\", "/"))
        else:
            missing.append(str(p).replace("\\", "/"))
    return verified, missing


def step_complete(
    plan_id: str,
    n: int,
    output: str | None = None,
    skip_output: bool = False,
    tokens: int | None = None,
    duration_ms: int | None = None,
) -> bool:
    """标记第 n 步完成；如果 expected_outputs 不存在则拒绝（除非 skip_output）。

    output 参数允许追加一个额外验证文件（不在模板里的）。
    P2-8：可选 tokens / duration_ms 记录该步的 subagent 成本（cost tracking）。
    建议主代理在 Task 完成 notification 中取 total_tokens / duration_ms 传入。
    """
    path = _find_plan_path(plan_id)
    plan = _load_plan(path, for_write=True)  # P1-1：写前防篡改校验
    step = _find_step(plan, n)

    if step.get("status") == STATUS_COMPLETED:
        # 幂等：已完成的步骤不重复执行；但允许补录 cost（如果未记录过）
        if tokens is not None and step.get("tokens_used") is None:
            step["tokens_used"] = int(tokens)
        if duration_ms is not None and step.get("duration_ms") is None:
            step["duration_ms"] = int(duration_ms)
        if (tokens is not None or duration_ms is not None):
            _save_plan(path, plan)  # 持久化补录的 cost
        return True

    now = datetime.now().isoformat(timespec="seconds")
    step["started_at"] = step.get("started_at") or now

    # P2-8：记录本步的 subagent 成本（可选）
    if tokens is not None:
        step["tokens_used"] = int(tokens)
    if duration_ms is not None:
        step["duration_ms"] = int(duration_ms)

    # 🔴 2026-06-26：先消费 touch_outputs（模板声明的存在性代理）再校验 expected_outputs
    _apply_touch_outputs(plan, step, plan.get("project"))

    # 校验 expected_outputs
    if not skip_output:
        verified, missing = _verify_outputs(plan, step, plan.get("project"))
        if missing:
            step["status"] = STATUS_FAILED
            step["error"] = f"expected_outputs 不存在：{missing}"
            _save_plan(path, plan)
            raise FileNotFoundError(
                f"[plan_tracker] 第 {n} 步 expected_outputs 缺失：{missing}\n"
                f"主链 required step 必须补齐产物或修正 plan 模板，不得跳过输出校验"
            )
        step["verified_outputs"] = verified

    if output:
        # 用户传入的额外 output 也要存在
        op = Path(output)
        if not op.is_absolute():
            root = resolve_project_root(plan.get("project"))
            if root is not None:
                op = root / output
        if not op.exists() and not skip_output:
            step["status"] = STATUS_FAILED
            step["error"] = f"--output 不存在：{op}"
            _save_plan(path, plan)
            raise FileNotFoundError(f"[plan_tracker] --output 不存在：{op}")
        if op.exists():
            verified = step.get("verified_outputs", []) or []
            verified.append(str(op).replace("\\", "/"))
            step["verified_outputs"] = verified

    step["status"] = STATUS_COMPLETED
    step["completed_at"] = now
    step["error"] = None
    _save_plan(path, plan)
    return True


def end_plan(plan_id: str) -> dict:
    """完成检查。返回 {ok, missing_steps, missing_outputs}。"""
    path = _find_plan_path(plan_id)
    plan = _load_plan(path, for_write=True)  # P1-1：写前防篡改校验

    required = set(plan.get("required_steps", []) or [])
    optional = set(plan.get("optional_steps", []) or [])

    missing_steps: list[int] = []
    missing_outputs: list[dict] = []
    missing_agent_reports: list[dict] = []  # v24 anti-skip：must_spawn_agent 校验

    for step in plan.get("steps", []):
        n = step.get("n")
        # v24: required + completed optional 都要校验 expected_outputs + must_spawn_agent
        check_this = (n in required) or (n in optional and step.get("status") == STATUS_COMPLETED)
        if n in required and step.get("status") != STATUS_COMPLETED:
            missing_steps.append(n)
            continue
        if check_this:
            verified, missing = _verify_outputs(plan, step, plan.get("project"))
            if missing:
                missing_outputs.append({"step": n, "missing": missing})
            # v24 新增：must_spawn_agent 字段校验 JudgeReport 真存在
            must_agents = step.get("must_spawn_agent")
            if must_agents:
                if isinstance(must_agents, str):
                    must_agents = [must_agents]
                proj = plan.get("project", "")
                cluster_id = plan.get("cluster_id") or cluster_id_from_key(plan.get("key"))
                # v28 程序驱动：模板显式声明 judge_report_path（str 或 {agent: path} dict）
                # → 优先验声明路径（命名学外移）；未声明的 agent 回落旧路径推算。
                jrp = step.get("judge_report_path")
                # 🔴 2026-06-26 加 secondary declared report path（cluster_001 翻车 sediment）：
                # voice-checker 无违规时不写 primary brief（.checker_briefs/cluster_*_voice.json），
                # 但 secondary judge_report（.judge_reports/cluster_*_voice-checker.json）总有。
                # primary/secondary 都是显式正式产物路径，不做未声明路径猜测。
                jrp_sec = step.get("judge_report_path_secondary")
                for agent_name in must_agents:
                    declared = None
                    declared_sec = None
                    if isinstance(jrp, dict):
                        declared = jrp.get(agent_name)
                    elif isinstance(jrp, str) and jrp:
                        declared = jrp
                    if isinstance(jrp_sec, dict):
                        declared_sec = jrp_sec.get(agent_name)
                    elif isinstance(jrp_sec, str) and jrp_sec:
                        declared_sec = jrp_sec
                    if declared:
                        found = _verify_declared_report(proj, declared)
                        if not found and declared_sec:
                            found = _verify_declared_report(proj, declared_sec)
                    else:
                        found = _verify_agent_report(proj, agent_name, cluster_id)
                    if not found:
                        missing_agent_reports.append({
                            "step": n, "agent": agent_name,
                            "hint": "v24 anti-skip: 该 step 必须 spawn 此 agent 并产 JudgeReport"
                        })

    ok = (not missing_steps) and (not missing_outputs) and (not missing_agent_reports)
    now = datetime.now().isoformat(timespec="seconds")
    if ok:
        plan["completed_at"] = now
    plan["last_checked_at"] = now
    _save_plan(path, plan)

    # P2-8：聚合 cost（仅汇总有记录的步骤）
    total_tokens = sum(int(s.get("tokens_used") or 0) for s in plan.get("steps", []))
    total_duration_ms = sum(int(s.get("duration_ms") or 0) for s in plan.get("steps", []))
    steps_with_cost = sum(1 for s in plan.get("steps", []) if s.get("tokens_used") is not None)

    return {
        "ok": ok,
        "plan_id": plan_id,
        "missing_steps": missing_steps,
        "missing_outputs": missing_outputs,
        "missing_agent_reports": missing_agent_reports,  # v24 anti-skip
        "required_steps": sorted(required),
        "optional_steps": sorted(optional),
        "cost_summary": {                       # P2-8：subagent 成本汇总
            "total_tokens": total_tokens,
            "total_duration_ms": total_duration_ms,
            "steps_with_cost": steps_with_cost,
            "steps_total": len(plan.get("steps", [])),
        },
    }


def abort_plan(plan_id: str, reason: str) -> dict:
    path = _find_plan_path(plan_id)
    plan = _load_plan(path, for_write=True)  # P1-1：写前防篡改校验
    plan["abort_reason"] = reason
    plan["completed_at"] = None
    now = datetime.now().isoformat(timespec="seconds")
    plan["aborted_at"] = now
    for step in plan.get("steps", []):
        if step.get("status") in (STATUS_PENDING, STATUS_IN_PROGRESS):
            step["status"] = STATUS_ABORTED
    _save_plan(path, plan)
    return {"plan_id": plan_id, "aborted_at": now, "reason": reason}


def _scan_all_plan_files() -> dict[str, Path]:
    """扫描所有 plan.json 存放位置，返回 {plan_id_stem: file_path}。

    容错：任何目录不存在都跳过，不抛异常。
    """
    seen: dict[str, Path] = {}

    def _scan(d: Path):
        try:
            if not d.exists():
                return
            for f in d.glob("*.json"):
                seen[f.stem] = f
        except OSError:
            # 目录无权限/损坏：静默跳过
            return

    _scan(GLOBAL_PLANS_DIR)
    try:
        if PROJECTS_DIR.exists():
            for p in PROJECTS_DIR.iterdir():
                if p.is_dir():
                    _scan(p / "_数据库" / ".plans")
                    _scan(p / ".plans")
    except OSError:
        pass
    try:
        if STYLES_DIR.exists():
            for s in STYLES_DIR.iterdir():
                if s.is_dir():
                    _scan(s / ".plans")
    except OSError:
        pass
    return seen


def find_active_plans() -> list[dict]:
    """扫描所有可能位置的 .plans 目录，返回所有活跃 plan。

    返回结构：[{"path": str, "plan": dict}, ...]
    活跃 = 未 completed_at 且未 aborted_at。

    容错策略：
    - 任何目录不存在 → 跳过
    - 任何 JSON 损坏 → 不再静默消失，append 损坏条目
      {"corrupt": True, "path": str, "plan_id": 文件名 stem, "plan": {}}
      （消费方按 "corrupt" 键区分；带空 "plan" dict 保既有
      entry["plan"].get(...) 消费者 tolerant）
    - 缺失关键字段 → 视为非活跃（保守）

    本函数永不抛异常，最坏情况返回 []。
    """
    result: list[dict] = []
    try:
        seen = _scan_all_plan_files()
    except Exception:
        return result

    for plan_id, f in sorted(seen.items()):
        try:
            d = _load_json(f)
            if not isinstance(d, dict):
                continue
            is_done = bool(d.get("completed_at"))
            is_aborted = bool(d.get("aborted_at"))
            if is_done or is_aborted:
                continue
            # 防御：缺 id/steps 的 plan 直接跳过
            if not d.get("id") or not isinstance(d.get("steps"), list):
                continue
            result.append({
                "path": str(f).replace("\\", "/"),
                "plan": d,
                "tampered": verify_attestation(d) == "tampered",  # P1-1
            })
        except Exception:
            # 2026-06-13 修：损坏 JSON / IO 错误不再静默消失——append corrupt
            # 条目让 GUI Plan 续跑页可见 + 可清除（移 .corrupt/ 可恢复）
            result.append({
                "corrupt": True,
                "path": str(f).replace("\\", "/"),
                "plan_id": plan_id,  # = 文件名 stem
                "plan": {},          # 既有消费者 entry["plan"].get(...) tolerant
            })
            continue
    return result


def list_plans(active_only: bool = False) -> list[dict]:
    """列出所有 plan。active = 未 completed 也未 aborted。"""
    seen = _scan_all_plan_files()

    result: list[dict] = []
    for plan_id, f in sorted(seen.items()):
        try:
            d = _load_json(f)
        except Exception as exc:  # noqa: BLE001
            result.append({"id": plan_id, "error": str(exc), "path": str(f)})
            continue
        is_done = bool(d.get("completed_at"))
        is_aborted = bool(d.get("aborted_at"))
        is_active = not (is_done or is_aborted)
        if active_only and not is_active:
            continue
        result.append({
            "id": d.get("id"),
            "command": d.get("command"),
            "project": d.get("project"),
            "key": d.get("key"),
            "cluster_key": d.get("cluster_key"),
            "cluster_id": d.get("cluster_id") or cluster_id_from_key(d.get("key")),
            "active": is_active,
            "completed": is_done,
            "aborted": is_aborted,
            "tampered": verify_attestation(d) == "tampered",  # P1-1
            "path": str(f).replace("\\", "/"),
        })
    return result


# ============ CLI ============

def _cli_create(args: argparse.Namespace) -> int:
    plan_id = create_plan(
        command=args.command,
        project=args.project,
        key=args.key,
    )
    print(plan_id)
    return 0


def _cli_step(args: argparse.Namespace) -> int:
    try:
        step_complete(
            plan_id=args.plan_id,
            n=args.n,
            output=args.output,
            skip_output=args.skip_output,
            tokens=args.tokens,
            duration_ms=args.duration_ms,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except PlanTamperedError as exc:  # P1-1：plan 被旁路篡改，拒绝 step
        print(str(exc), file=sys.stderr)
        return 2
    plan = get_plan(args.plan_id)
    step = _find_step(plan, args.n)
    print(f"[OK] 第 {args.n} 步 ({step.get('name')}) 已完成 "
          f"verified={len(step.get('verified_outputs', []))}")
    return 0


def _cli_end(args: argparse.Namespace) -> int:
    try:
        res = end_plan(args.plan_id)
    except PlanTamperedError as exc:  # P1-1：plan 被旁路篡改，拒绝 end
        print(str(exc), file=sys.stderr)
        return 2
    # P2-8：cost 汇总输出（仅当至少一步记录了成本时显示）
    cost = res.get("cost_summary", {})
    if cost.get("steps_with_cost", 0) > 0:
        kt = cost["total_tokens"] / 1000 if cost["total_tokens"] else 0
        sec = cost["total_duration_ms"] / 1000 if cost["total_duration_ms"] else 0
        print(f"  cost: {kt:.1f}K tokens, {sec:.1f}s ({cost['steps_with_cost']}/"
              f"{cost['steps_total']} 步有成本记录)")
    if res["ok"]:
        print(f"[OK] plan {args.plan_id} 全部 required 步骤通过")
        return 0
    print(f"[FAIL] plan {args.plan_id} 未通过完成检查", file=sys.stderr)
    if res["missing_steps"]:
        print(f"  缺失步骤：{res['missing_steps']}", file=sys.stderr)
    if res["missing_outputs"]:
        for mo in res["missing_outputs"]:
            print(f"  第 {mo['step']} 步缺输出：{mo['missing']}", file=sys.stderr)
    if res.get("missing_agent_reports"):
        for ar in res["missing_agent_reports"]:
            print(f"  第 {ar['step']} 步缺 Agent JudgeReport：{ar['agent']} ({ar.get('hint', '')})", file=sys.stderr)
    return 2


def _cli_status(args: argparse.Namespace) -> int:
    try:
        plan = get_plan(args.plan_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    total = len(plan.get("steps", []))
    done = sum(1 for s in plan["steps"] if s.get("status") == STATUS_COMPLETED)
    att_state = verify_attestation(plan)  # P1-1
    att_label = {
        "ok": "✓ 有效",
        "legacy": "↻ 旧式 sha256（合法，下次写入自动迁移为 HMAC）",  # 2026-05-29 修
        "tampered": "⚠️ 被篡改！内容与 attestation 不符 —— 跑 reattest 或排查注入",
        "unattested": "— 未盖章（旧 plan，下次写入自动盖章）",
    }.get(att_state, att_state)
    print(f"[plan_tracker] {args.plan_id}")
    print(f"  command : {plan.get('command')}")
    print(f"  project : {plan.get('project')}")
    print(f"  key     : {plan.get('key')}")
    print(f"  cluster : {plan.get('cluster_id') or cluster_id_from_key(plan.get('key')) or '-'}")
    print(f"  防篡改  : {att_label}")
    print(f"  progress: {done}/{total}")
    for s in plan.get("steps", []):
        mark = {
            STATUS_COMPLETED: "[x]",
            STATUS_IN_PROGRESS: "[~]",
            STATUS_FAILED: "[!]",
            STATUS_SKIPPED: "[-]",
            STATUS_ABORTED: "[A]",
        }.get(s.get("status"), "[ ]")
        req = "(必)" if s.get("required") else "(可)"
        # P2-8：步骤行末尾追加 cost（如果有记录）
        cost_suffix = ""
        tok = s.get("tokens_used")
        dur = s.get("duration_ms")
        if tok is not None or dur is not None:
            bits = []
            if tok is not None:
                bits.append(f"{tok/1000:.1f}K tok")
            if dur is not None:
                bits.append(f"{dur/1000:.1f}s")
            cost_suffix = f"  [{', '.join(bits)}]"
        print(f"  {mark} {s.get('n'):>3} {req} {s.get('name')}{cost_suffix}")
        if s.get("error"):
            print(f"      ERROR: {s['error']}")
    if plan.get("abort_reason"):
        print(f"  ABORTED: {plan['abort_reason']}")
    return 0


def _cli_list(args: argparse.Namespace) -> int:
    plans = list_plans(active_only=args.active)
    if not plans:
        print("(no plans)")
        return 0
    for p in plans:
        if "error" in p:
            print(f"  [!] {p['id']} — {p['error']}")
            continue
        flag = "ACTIVE" if p["active"] else ("DONE" if p["completed"] else "ABORT")
        tamper = "  ⚠️ TAMPERED" if p.get("tampered") else ""  # P1-1
        print(f"  [{flag:6}] {p['id']}  cmd={p['command']}  "
              f"project={p['project']}  key={p.get('key') or '-'}  "
              f"cluster={p.get('cluster_id') or '-'}{tamper}")
    return 0


def _cli_abort(args: argparse.Namespace) -> int:
    try:
        res = abort_plan(args.plan_id, args.reason)
    except PlanTamperedError as exc:  # P1-1：plan 被旁路篡改，拒绝 abort
        print(str(exc), file=sys.stderr)
        print("  如确需中止此被篡改的 plan：先 reattest 再 abort。", file=sys.stderr)
        return 2
    print(f"[ABORTED] {res['plan_id']} reason={res['reason']}")
    return 0


def _cli_verify(args: argparse.Namespace) -> int:
    """P1-1：校验 plan 防篡改 attestation。"""
    state = verify_plan(args.plan_id)
    labels = {
        "ok": "✓ attestation 有效，plan 未被篡改",
        "tampered": "⚠️ 被篡改 —— plan 内容与 attestation 不符",
        "unattested": "— 未盖章（旧 plan，本功能引入前创建）",
        "not_found": "找不到该 plan",
    }
    print(f"[plan_tracker verify] {args.plan_id}: {labels.get(state, state)}")
    # 退出码：ok/unattested=0，tampered=2，not_found=1
    return {"ok": 0, "unattested": 0, "tampered": 2, "not_found": 1}.get(state, 1)


def _cli_reattest(args: argparse.Namespace) -> int:
    """P1-1：合法手动修改 plan 后重新盖章。"""
    try:
        res = reattest_plan(args.plan_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"[plan_tracker reattest] {res['plan_id']} 已重新盖章")
    print(f"  原状态: {res['was']}  →  新 sha256: {res['sha256']}")
    if res["was"] == "tampered":
        print("  注意：原 plan 处于 tampered 状态，已按【当前内容】重新盖章，"
              "请确认当前内容确实是你期望的。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plan_tracker",
        description="多步命令强制规划与执行追踪（Phase 1）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create", help="创建 plan")
    pc.add_argument("--command", required=True, choices=list(KNOWN_COMMANDS))
    pc.add_argument("--project", required=True)
    pc.add_argument("--key", default=None,
                    help="cluster-only 主线命令必填：cluster key 或 cluster id，如 001 / cluster_001")
    pc.set_defaults(func=_cli_create)

    ps = sub.add_parser("step", help="标记某步完成")
    ps.add_argument("plan_id")
    ps.add_argument("--n", type=str, required=True)  # v17.10: str 容错（支持 "2.5" 旧模板）
    ps.add_argument("--output", default=None,
                    help="可选：额外要校验存在的文件")
    ps.add_argument("--skip-output", action="store_true",
                    help="跳过 expected_outputs 校验；主链 required step 禁用，仅保留给非主链无固定产物维护任务")
    ps.add_argument("--tokens", type=int, default=None,
                    help="P2-8：本步消耗 token 数（subagent 成本追踪，可选）")
    ps.add_argument("--duration-ms", type=int, default=None,
                    help="P2-8：本步耗时毫秒（可选）")
    ps.set_defaults(func=_cli_step)

    pe = sub.add_parser("end", help="完成检查")
    pe.add_argument("plan_id")
    pe.set_defaults(func=_cli_end)

    pst = sub.add_parser("status", help="查看 plan 进度")
    pst.add_argument("plan_id")
    pst.set_defaults(func=_cli_status)

    pl = sub.add_parser("list", help="列出 plans")
    pl.add_argument("--active", action="store_true", help="只列活跃 plan")
    pl.set_defaults(func=_cli_list)

    pa = sub.add_parser("abort", help="中止 plan")
    pa.add_argument("plan_id")
    pa.add_argument("--reason", required=True)
    pa.set_defaults(func=_cli_abort)

    pv = sub.add_parser("verify", help="校验 plan 防篡改 attestation（P1-1）")
    pv.add_argument("plan_id")
    pv.set_defaults(func=_cli_verify)

    pr = sub.add_parser("reattest", help="合法手动改 plan 后重新盖章（P1-1）")
    pr.add_argument("plan_id")
    pr.set_defaults(func=_cli_reattest)

    pcl = sub.add_parser("cleanup", help="清理 DONE/ABORTED 状态的旧 plan（v17.5）")
    pcl.add_argument("--older-than-days", type=int, default=7,
                     help="只清理 N 天前的（默认 7）")
    pcl.add_argument("--dry-run", action="store_true",
                     help="只列出会被删除的，不实际删")
    pcl.add_argument("--all", action="store_true",
                     help="不限制天数，清所有终态 plan")
    pcl.set_defaults(func=_cli_cleanup)

    return p


def _cli_cleanup(args: argparse.Namespace) -> int:
    """清理 DONE/ABORTED 旧 plan。"""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=args.older_than_days)
    candidates = [GLOBAL_PLANS_DIR]
    # 也扫所有项目的 _数据库/.plans/
    projects_root = REPO_ROOT / "workspace" / "novels"
    if projects_root.exists():
        for proj in projects_root.iterdir():
            pd = proj / "_数据库" / ".plans"
            if pd.exists():
                candidates.append(pd)
    deleted = []
    kept = []
    for cdir in candidates:
        for f in cdir.glob("*.json"):
            try:
                plan = _load_json(f)
                # 终态判定：有 abort_reason / 所有 step 已 completed
                is_aborted = bool(plan.get("abort_reason"))
                steps = plan.get("steps", [])
                is_done = bool(steps) and all(
                    s.get("status") == STATUS_COMPLETED for s in steps
                )
                if not (is_aborted or is_done):
                    kept.append(f.name)
                    continue
                # 检查时间
                if not args.all:
                    completed_at = plan.get("completed_at") or plan.get("created_at")
                    if completed_at:
                        try:
                            t = datetime.fromisoformat(completed_at)
                            if t > cutoff:
                                kept.append(f.name)
                                continue
                        except ValueError:
                            pass
                if args.dry_run:
                    deleted.append(f"[DRY] {f.name}")
                else:
                    f.unlink()
                    deleted.append(f.name)
            except Exception as e:
                print(f"  [WARN] 跳过 {f.name}: {e}", file=sys.stderr)
    print(f"[plan_tracker cleanup] 删除 {len(deleted)} 个 plan，保留 {len(kept)} 个")
    for d in deleted[:20]:
        print(f"  - {d}")
    if len(deleted) > 20:
        print(f"  ... 还有 {len(deleted)-20} 个未显示")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
