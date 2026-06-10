#!/usr/bin/env python3
"""runner.py — GUI 流水线执行器（纯逻辑 · 零 nicegui 依赖 · 2026-06-10）

在**工作线程**跑 orchestrator.run_command（同步长任务），与 UI 的衔接全走
AppState（UI ui.timer 轮询）+ PauseBridge（走向卡阻塞桥）。

线程纪律：
- 本模块只写 AppState 字段（str/bool 原子赋值）+ LogBuffer（自锁），不碰 UI 元素。
- 同一时刻只允许一条流水线在跑（_run_lock 非阻塞获取，拿不到就拒绝）。
"""
from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _REPO / "core" / "scripts"
for p in (str(_REPO), str(_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.gui.state import AppState  # noqa: E402

import orchestrator as orc  # noqa: E402


class PipelineRunner:
    """驱动 cluster-write / cluster-save-state（可连跑）的工作线程封装。"""

    def __init__(self, state: AppState):
        self.state = state
        self._run_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---- 对 UI 暴露的入口 ----
    def start(self, commands: list[str], project: str, key: str, *,
              auto_pilot: bool = False, resume_plan_id: str | None = None) -> bool:
        """启动流水线（后台线程）。已有流水线在跑 → 返回 False 拒绝。

        commands: ["cluster-write"] / ["cluster-save-state"] /
                  ["cluster-write", "cluster-save-state"]（连跑）
        """
        if not self._run_lock.acquire(blocking=False):
            self.state.log_buffer.append("[gui] 已有流水线在运行，忽略本次启动")
            return False
        self.state.running = True
        self.state.last_result = ""
        # thread.start() 在系统线程资源枯竭时抛 RuntimeError——若不兜底，锁已 acquire
        # 但 _work 永不运行 → finally 永不 release → 后续 start() 永久被拒、running
        # 永久卡 True，非技术用户只能重启 exe（对抗审查根因 D）。
        try:
            self._thread = threading.Thread(
                target=self._work, name="pipeline",
                args=(list(commands), project, key, auto_pilot, resume_plan_id),
                daemon=True)
            self._thread.start()
        except BaseException as e:        # RuntimeError/MemoryError 等都要复位不变量
            self.state.running = False
            self._thread = None
            self.state.last_result = f"❌ 无法启动流水线线程（系统资源不足？）：{e}"
            self.state.log_buffer.append(f"[gui] {self.state.last_result}")
            self._run_lock.release()
            return False
        return True

    def list_resumable(self) -> list[dict]:
        """活跃（未完成）plan 列表——断点续跑入口。"""
        import plan_tracker as pt
        out = []
        for item in pt.find_active_plans():
            plan = item.get("plan") or {}
            steps = plan.get("steps") or []
            done = sum(1 for s in steps if s.get("status") == "completed")
            out.append({"plan_id": plan.get("id", ""),
                        "command": plan.get("command", ""),
                        "project": plan.get("project", ""),
                        "key": plan.get("key") or "",
                        "progress": f"{done}/{len(steps)}"})
        return out

    # ---- 工作线程 ----
    def _work(self, commands: list[str], project: str, key: str,
              auto_pilot: bool, resume_plan_id: str | None):
        st = self.state
        try:
            for i, cmd in enumerate(commands):
                st.current_command = cmd
                st.current_step = ""
                st.log_buffer.append(f"[gui] ▶ 开始 {cmd}（项目={project} key={key}"
                                     f"{' · 续跑 ' + resume_plan_id if resume_plan_id else ''}）")

                def _on_step(n, name, total):
                    st.current_step = f"{n}/{total} {name}"

                summary = orc.run_command(
                    cmd, project, key=key,
                    resume_plan_id=resume_plan_id if i == 0 else None,
                    auto_pilot=auto_pilot,
                    pause_handler=st.bridge.request,   # 走向卡 → UI 桥
                    step_callback=_on_step)
                if summary.paused_at is not None:
                    st.last_result = (f"⏸ {cmd} 停在 step {summary.paused_at} 等用户输入"
                                      f"（plan={summary.plan_id}·可续跑）")
                    st.log_buffer.append(f"[gui] {st.last_result}")
                    return
                st.log_buffer.append(f"[gui] ✅ {cmd} 完成（plan={summary.plan_id}）")
            st.last_result = "✅ 全部完成"
        except orc.OrchestratorError as e:
            st.last_result = f"❌ 流水线停下：{e}"
            st.log_buffer.append(f"[gui] {st.last_result}")
            st.log_buffer.append("[gui] 修复后可在「Plan 续跑」页从断点继续")
        except Exception as e:
            st.last_result = f"❌ 非预期异常：{type(e).__name__}: {e}"
            st.log_buffer.append(f"[gui] {st.last_result}")
            for line in traceback.format_exc().splitlines()[-8:]:
                st.log_buffer.append(f"[gui]   {line}")
        finally:
            st.running = False
            st.current_command = ""
            st.current_step = ""
            self._run_lock.release()


# ============ 设置页数据（gen-model profile） ============
def list_profiles_masked() -> dict:
    """profile 概览（key 打码）——设置页只读展示 + active 标记。"""
    import os
    from gen_model_loader import GenModelLoader
    loader = GenModelLoader()
    active = (os.environ.get("GEN_MODEL_ACTIVE") or "").strip()
    rows = []
    import secrets_store
    for p in loader.list_profiles():
        masked = (p.api_key[:6] + "…" + p.api_key[-4:]) if len(p.api_key) > 12 \
            else ("已配置" if p.api_key else "未配置")
        rows.append({"name": p.name, "model": p.model or "(探测)",
                     "base_url": p.base_url, "protocol": p.protocol,
                     "thinking_level": p.thinking_level or "-",
                     "api_key": masked, "active": p.name == active,
                     "key_in_keyring": secrets_store.has_api_key(p.name)})
    return {"active": active, "profiles": rows,
            "keyring_available": secrets_store.is_available()}


# ============ BYOK 密钥管理（设置页·绝不把 key 写回 .env / 不展示明文）============
def save_api_key(name: str, key: str) -> bool:
    """存用户 key 到 keyring + 失效 loader 缓存（不 reset 则 GUI 录入不生效·风险1）。"""
    import secrets_store
    from gen_model_loader import reset_default_loader
    ok = secrets_store.set_api_key(name, key)
    reset_default_loader()
    return ok


def clear_api_key(name: str) -> bool:
    import secrets_store
    from gen_model_loader import reset_default_loader
    ok = secrets_store.delete_api_key(name)
    reset_default_loader()
    return ok


def profile_key_status(name: str) -> bool:
    import secrets_store
    return secrets_store.has_api_key(name)


def _classify_conn_err(e: Exception) -> str:
    """异常归人话 + 脱敏（gemini key 在 URL·绝不回显·must_fix#2/#3）。"""
    import secrets_store
    raw = secrets_store.redact(f"{type(e).__name__}: {e}")
    low = raw.lower()
    if "401" in raw or "invalid" in low or "unauthor" in low or "api key" in low:
        return "密钥无效（鉴权失败）"
    if "rate" in low or "429" in raw or "quota" in low:
        return "额度不足或被限流"
    if "timeout" in low or "timed out" in low:
        return "网络或代理超时"
    return f"连接失败：{raw[:80]}"


def test_profile_connection(name: str) -> dict:
    """极短探针验证 key 能用（新建 loader 绕缓存·归一人话·redact key）。"""
    from gen_model_loader import GenModelLoader
    import llm_transport as T
    try:
        p = GenModelLoader().get_profile(name)
        if p is None or not p.api_key:
            return {"ok": False, "message": "未配置密钥"}
        T.stream_once(p, "ping", "回复ok", max_tokens=8)
        return {"ok": True, "message": f"连接成功（模型 {p.model or p.name}）"}
    except Exception as e:
        return {"ok": False, "message": _classify_conn_err(e)}
