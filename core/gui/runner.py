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


def scan_distill_styles(min_chapters: int = 5) -> list[dict]:
    """列可复刻的风格库（有 skill_*.md + 原文/ ≥ min_chapters 章·multi-ref SFS 需）。

    阶段1 复刻半程：导现成 skill → 复刻 → SFS。must_fix#1：原文 章数 < min → multi-ref
    退化单 ref（铁律失真）→ 此处过滤掉，GUI 不让选（避免跑到评分才崩）。
    """
    from frozen_util import user_workspace_dir
    styles_dir = user_workspace_dir() / "styles"
    out = []
    if styles_dir.is_dir():
        for d in sorted(styles_dir.iterdir()):
            if not d.is_dir():
                continue
            skills = sorted(d.glob("skill*.md")) + sorted(d.glob("*skill*.md"))
            raw = list((d / "原文").glob("*.txt")) if (d / "原文").is_dir() else []
            if skills and len(raw) >= min_chapters:
                out.append({"name": d.name, "title": d.name,
                            "skill": skills[0].name, "raw_chapters": len(raw)})
    return out


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
            self.state.log_buffer.append(
                f"[gui:run] 启动 cmds={'+'.join(commands)} project={project} "
                f"key={key or '(空)'}{' resume=' + resume_plan_id if resume_plan_id else ''}"
                f" auto={auto_pilot}")
        except BaseException as e:        # RuntimeError/MemoryError 等都要复位不变量
            self.state.running = False
            self._thread = None
            self.state.last_result = f"❌ 无法启动流水线线程（系统资源不足？）：{e}"
            self.state.log_buffer.append(f"[gui] {self.state.last_result}")
            self._run_lock.release()
            return False
        return True

    def start_full_distill(self, book: str, author_text: str, *,
                           auto_pilot: bool = False) -> bool:
        """阶段3 全程蒸馏：建风格库目录 + 落作者作品 raw → 跑 distill-style plan。
        前置（照 new_book 范式·RUNNER.start 前 mkdir + 写 raw）。"""
        from frozen_util import user_workspace_dir
        # A6①：先查锁再 mkdir（否则已有任务在跑时 start 拒绝·留下孤儿目录锁死名字）
        if self._run_lock.locked():
            self.state.log_buffer.append("[gui] 已有任务在运行，忽略本次学风格")
            return False
        proj = user_workspace_dir() / "styles" / book
        if (proj / "原文").exists() or (proj / "skill_FINAL.md").exists():
            self.state.log_buffer.append(f"[gui:event] 蒸馏拦截《{book}》已存在")
            return False
        wal = proj / "蒸馏进度" / ".wal"
        wal.mkdir(parents=True, exist_ok=True)
        (wal / "raw_author_text.txt").write_text(author_text, encoding="utf-8")
        self.state.log_buffer.append(
            f"[gui:event] 开始学风格 book={book} 原文{len(author_text)}字")
        ok = self.start(["distill-style"], book, "", auto_pilot=auto_pilot)
        if not ok:
            # 启动被拒（竞态）→ 回滚本次创建物（防孤儿目录锁死风格名）
            import shutil
            shutil.rmtree(proj / "蒸馏进度", ignore_errors=True)
            try:
                proj.rmdir()
            except OSError:
                pass
        return ok

    def run_replicate(self, style_name: str, cluster_ref: str = "cluster_001") -> bool:
        """阶段1 复刻半程：distill_replicate(gen-model 复刻) → style_evaluator(multi-ref SFS)。
        后台线程·已有任务在跑返 False。"""
        if not self._run_lock.acquire(blocking=False):
            self.state.log_buffer.append("[gui] 已有任务在运行，忽略本次复刻")
            return False
        self.state.running = True
        self.state.last_result = ""
        try:
            self._thread = threading.Thread(
                target=self._replicate_work, name="replicate",
                args=(style_name, cluster_ref), daemon=True)
            self._thread.start()
        except BaseException as e:
            self.state.running = False
            self._thread = None
            self.state.last_result = f"❌ 无法启动复刻线程：{e}"
            self.state.log_buffer.append(f"[gui] {self.state.last_result}")
            self._run_lock.release()
            return False
        return True

    def _replicate_work(self, style_name: str, cluster_ref: str):
        import json
        import subprocess
        from frozen_util import child_python, user_workspace_dir
        st = self.state
        try:
            st.current_command = "复刻测试"
            style_dir = user_workspace_dir() / "styles" / style_name
            skills = sorted(style_dir.glob("skill*.md")) + sorted(style_dir.glob("*skill*.md"))
            if not skills:
                st.last_result = f"❌ {style_name} 无 skill 文件"
                st.log_buffer.append(f"[gui] {st.last_result}")
                return
            out_dir = style_dir / "复刻测试" / "gui"
            out_dir.mkdir(parents=True, exist_ok=True)
            replica = out_dir / f"{cluster_ref}_replica.txt"
            eval_out = out_dir / f"{cluster_ref}_eval.json"

            def _run(cmd_args, label, timeout_s: int = 1800):
                # 🔴 同类bug狩猎修（A8 的循环内超时检查是死代码）：readline 在子进程
                # **静默挂死**时永久阻塞 → 检查永不执行 → 锁永不释放 → 全 GUI 按钮永久
                # 禁用。真 watchdog = threading.Timer(输出无关)·kill 后管道 EOF 解除阻塞。
                # Windows kill 返 1 非负值 → 用 timed_out Event 区分超时与普通失败。
                import os as _os
                import threading as _threading
                st.log_buffer.append(f"[gui] ▶ {label} …")
                env = {**_os.environ, "PYTHONUNBUFFERED": "1",
                       "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
                p = subprocess.Popen([child_python()] + cmd_args, cwd=str(_REPO),
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace",
                                     env=env)
                timed_out = _threading.Event()

                def _kill_on_timeout():
                    timed_out.set()
                    try:
                        p.kill()
                    except Exception:
                        pass
                watchdog = _threading.Timer(timeout_s, _kill_on_timeout)
                watchdog.start()
                try:
                    for line in iter(p.stdout.readline, ""):
                        if line.strip():
                            st.log_buffer.append(line.rstrip())
                finally:
                    watchdog.cancel()
                try:
                    p.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                if timed_out.is_set():
                    st.log_buffer.append(f"[gui] ❌ {label} 超时（{timeout_s}s）已终止")
                    return 124
                return p.returncode

            rc = _run(["core/scripts/distill_replicate.py", "--style-skill", str(skills[0]),
                       "--mode", "cluster", "--cluster-ref", cluster_ref,
                       "--project", str(style_dir), "--output", str(replica)], "gen-model 复刻")
            if rc != 0 or not replica.exists():
                st.last_result = f"❌ 复刻失败（退出码 {rc}）——检查设置页 key 是否填了"
                st.log_buffer.append(f"[gui] {st.last_result}")
                return
            rc2 = _run(["core/scripts/style_evaluator.py", "--gen", str(replica),
                        "--multi-ref-from-dir", str(style_dir / "原文"),
                        "--multi-ref-count", "5", "--output", str(eval_out)], "多维风格评分 SFS")
            score = "?"
            try:
                if eval_out.exists():
                    ev = json.loads(eval_out.read_text(encoding="utf-8"))
                    score = ev.get("sfs_quick") or ev.get("total") or \
                        (ev.get("programmatic_score") or {}).get("total") or "?"
                    grade = ev.get("grade") or (ev.get("programmatic_score") or {}).get("grade") or ""
                    st.last_result = f"✅ 复刻 SFS 分：{score} {grade}（越高越像该作者）"
            except (OSError, json.JSONDecodeError):
                st.last_result = f"⚠️ 复刻完成但评分解析失败（退出码 {rc2}）"
            st.log_buffer.append(f"[gui] {st.last_result}")
        except Exception as e:
            st.last_result = f"❌ 复刻异常：{e}"
            st.log_buffer.append(f"[gui] {st.last_result}")
        finally:
            st.running = False
            st.current_command = ""
            st.runs_finished += 1          # A4：UI 边沿刷新信号
            self._thread = None
            self._run_lock.release()

    def list_resumable(self) -> list[dict]:
        """活跃（未完成）plan 列表——断点续跑入口。"""
        import plan_tracker as pt
        out = []
        for item in pt.find_active_plans():
            plan = item.get("plan") or {}
            # 测试/钩子产物（__hook_e2e__ 等）不给真实用户看（视觉评审抓出）
            if str(plan.get("project", "")).startswith("__"):
                continue
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
            # 完成信号按命令定制（A11·非技术用户要明确的「下一步去哪」）
            _MSG = {
                "outline": f"✅ 《{project}》已建好（大纲+全套设定）——去写作台写第一章",
                "distill-style": f"✅ 《{project}》风格学好了——可在蒸馏页测复刻、新建书里选用",
                "cluster-save-state": "✅ 已保存——下一段走向已生成，可继续写",
            }
            st.last_result = _MSG.get(commands[-1], "✅ 全部完成")
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
            st.runs_finished += 1          # A4：UI 边沿刷新信号（项目/风格/next_key）
            self._run_lock.release()


def active_key_ready() -> tuple:
    """(ready, note)——key 预检与流水线真实消费路径 by construction 一致（A3·四入口统一）。

    直接走 GenModelLoader.get_active_profile()（keyring>env>.env 三级已合并），不从打码
    字符串复刻判据。副产物：GEN_MODEL_ACTIVE 为空/坏配置给人话·.env 持 key 用户不被误拦。
    """
    import os
    from gen_model_loader import GenModelLoader
    active = (os.environ.get("GEN_MODEL_ACTIVE") or "").strip()
    try:
        p = GenModelLoader().get_active_profile()   # 每次新建·绕缓存
        if not (p.api_key or "").strip():
            return False, (f"当前使用的模型「{p.name}」还没配密钥——"
                           f"去「设置」页给标了『当前使用』的那张卡录入")
        return True, p.name
    except Exception as e:
        msg = str(e)
        if "API_KEY" in msg or "密钥" in msg:        # loader: 「active profile 'X' 缺 API_KEY」
            who = f"「{active}」" if active else ""
            # 实测 UX 陷阱：key 录在别的模型卡上仍报没配——提示必须点名是哪张卡
            return False, (f"当前使用的模型{who}还没配密钥——"
                           f"去「设置」页给标了『当前使用』的那张卡录入")
        if "GEN_MODEL_ACTIVE" in msg:
            return False, "还没选生效模型——去「设置」页选一个并录入密钥"
        return True, ""    # 未知异常 fail-open（不挡用户·流水线自己会报）


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


def switch_active(name: str) -> bool:
    """切 active 模型：dev 改 .env / dist 写 %APPDATA% user_overrides（绝不碰只读 config）。"""
    from gen_model_loader import GenModelLoader, reset_default_loader
    from gen_model import set_active
    try:
        loader = GenModelLoader()
        if loader.get_profile(name) is None:
            return False
        set_active(loader, name)
        reset_default_loader()
        return True
    except Exception:
        return False


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
