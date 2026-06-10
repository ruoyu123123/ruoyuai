#!/usr/bin/env python3
"""
gen_model_loader.py — 多 profile loader + fallback 链（gen_writer/gen_fixer/gen_creative 共用）

设计：
- .env 中所有 GEN__<name>__<FIELD>=<value> 解析为 profile dict
- profile 名完全自由（不绑死任何供应商）
- get_active_profile() 返回当前激活的 profile，缺 key 抛 GenModelConfigError
- get_callable_profiles() 返回 active + fallback 链中 key 非空的 profile 列表（供 fallback 循环用）

字段约定（按 .env 模板）：
- GEN__<name>__MODEL
- GEN__<name>__BASE_URL
- GEN__<name>__API_KEY
- GEN__<name>__TEMPERATURE
- GEN__<name>__MAX_TOKENS
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import re

try:
    import secrets_store  # keyring 薄抽象（BYOK·唯一 import keyring 处）
except Exception:
    secrets_store = None


def _resolve_api_key(name: str, env_file_value: str) -> str:
    """三级优先级解析 api_key（第一个 strip 后非空者胜）：
      1. keyring（BYOK·分发版主路径·DPAPI 加密）
      2. os.environ["GEN__<name>__API_KEY"]（CI/容器/临时覆盖·仅当 .env 未定义该 key 时
         才有独立值——load_dotenv(override=True) 会把 .env 的 key 回灌 environ·见下注）
      3. env_file_value（.env 文本·dev 单一来源·现状逐字节不变）

    🔴 对抗审查 must_fix#1：__init__ 的 load_dotenv(env_path, override=True) 在 _parse_profiles
    前运行，会用 .env 值**覆盖** os.environ 里同名 GEN__<name>__API_KEY。故当 .env 定义了该
    key 时，environ 层 == .env 层（无观测差异）；只有 .env **未**定义该 key 时 environ 才是
    独立注入口（分发版无 .env / CI 场景）。这是真实可达且正确的语义，不做 environ 快照
    （北极星最小改动）。任何 keyring 故障 → 当 None 降级，绝不冒泡成 GenModelConfigError。
    """
    if secrets_store is not None:
        try:
            if secrets_store.is_available():
                kr = secrets_store.get_api_key(name)
                if kr and kr.strip():
                    return kr.strip()
        except Exception:
            pass  # keyring 故障绝不影响下游降级
    env_v = (os.environ.get(f"GEN__{name}__API_KEY") or "").strip()
    if env_v:
        return env_v
    return (env_file_value or "").strip()


@dataclass
class Profile:
    name: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int | None  # None = 从 model_probe 缓存读
    protocol: str = "openai"  # openai(默认·/v1/chat/completions) | gemini(原生·streamGenerateContent·支持隐式前缀缓存)
    thinking_level: str | None = None  # gemini-3.x reasoning 模型思考档(LOW/MEDIUM/HIGH)·走 extra_body·LOW=回收15-25k输出预算给正文(治pro偏短·2026-06-06联网调研)·None=不传(flash等非reasoning)


class GenModelConfigError(Exception):
    """profile 配置错误（active 缺失 / 不存在 / 缺 key）"""
    pass


class GenModelLoader:
    """多 profile 加载器。

    用法：
        loader = GenModelLoader()
        p = loader.get_active_profile()  # 当前激活 profile
        # 或 fallback 循环用：
        for p in loader.get_callable_profiles():
            try: ... 调用 p ...
            except (429/timeout): continue
    """

    def __init__(self, env_path: str | Path | None = None):
        builtin_cfg = (Path(__file__).resolve().parent.parent
                       / "config" / "gen_profiles.default.env")
        if env_path is None:
            # 优先 cwd/.env，其次脚本同级仓库根 .env，最后内置非密 config（分发模式）。
            cwd_env = Path(".env")
            repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
            if cwd_env.exists():
                env_path = cwd_env                      # ① dev cwd（逐字节不变）
            elif repo_env.exists():
                env_path = repo_env                     # ② 仓库根（逐字节不变）
            else:
                env_path = builtin_cfg                  # ③ 内置非密 config（分发模式）
        self.env_path = Path(env_path)
        # _dist_mode 按「最终解析出的 env_path == 内置 config」判定（must_fix#2）——
        # 显式传 builtin_cfg 路径也正确进入分发态，测试与生产口径统一。
        try:
            self._dist_mode = self.env_path.resolve() == builtin_cfg.resolve()
        except OSError:
            self._dist_mode = False
        # 显式加载 config 到 os.environ（GEN_MODEL_ACTIVE/FALLBACK 经此进 environ）
        try:
            from dotenv import load_dotenv
            load_dotenv(self.env_path, override=True)
            # 分发模式：叠加用户态 active 覆盖（仅 GEN_MODEL_ACTIVE/FALLBACK_CHAIN 两键）
            if self._dist_mode:
                _ovr = _user_override_path()
                if _ovr.exists():
                    load_dotenv(_ovr, override=True)    # 用户选的 active 赢
        except ImportError:
            pass  # 没有 dotenv 也行（loader 直接读文件解析）
        self._profiles_cache: dict[str, Profile] | None = None

    def _parse_profiles(self) -> dict[str, Profile]:
        """解析 .env 中所有 GEN__<name>__<field>=<value> 字段，组装 Profile dict。

        注意 name 允许含下划线（如 deepseek_kxaug），所以用 GEN__(.+?)__([A-Z_]+) 非贪婪匹配。
        """
        if self._profiles_cache is not None:
            return self._profiles_cache

        profiles: dict[str, dict[str, str]] = {}
        if not self.env_path.exists():
            self._profiles_cache = {}
            return {}

        text = self.env_path.read_text(encoding="utf-8")
        # name 用 (.+?) 非贪婪，field 用 [A-Z_]+ 全大写
        # value 用 [ \t]* 只匹配水平空白，避免 \s* 吃换行导致 value 串到下一行
        pattern = re.compile(
            r"^GEN__(.+?)__([A-Z_]+)[ \t]*=[ \t]*(.*?)[ \t]*$",
            re.MULTILINE,
        )
        for m in pattern.finditer(text):
            name, field, value = m.group(1), m.group(2), m.group(3)
            # 跳过注释行（虽然 ^ 已过滤）
            if name.startswith("<"):  # 模板占位 <name>
                continue
            profiles.setdefault(name, {})[field.lower()] = value

        # 构造 Profile 对象
        result: dict[str, Profile] = {}
        for name, fields in profiles.items():
            try:
                max_tok_str = (fields.get("max_tokens") or "").strip()
                temp_str = (fields.get("temperature") or "0.8").strip() or "0.8"
                result[name] = Profile(
                    name=name,
                    model=(fields.get("model") or "").strip(),
                    base_url=(fields.get("base_url") or "").strip(),
                    api_key=_resolve_api_key(name, fields.get("api_key") or ""),
                    temperature=float(temp_str),
                    max_tokens=int(max_tok_str) if max_tok_str else None,
                    protocol=((fields.get("protocol") or "openai").strip().lower() or "openai"),
                    thinking_level=((fields.get("thinking_level") or "").strip().upper() or None),
                )
            except (ValueError, KeyError):
                continue  # 字段解析失败 → 跳过该 profile

        self._profiles_cache = result
        return result

    def list_profiles(self) -> list[Profile]:
        """返回所有定义的 profile 列表（含 key 为空的）"""
        return list(self._parse_profiles().values())

    def get_profile(self, name: str) -> Profile | None:
        """按名取 profile，不存在返回 None"""
        return self._parse_profiles().get(name)

    def get_active_profile(self) -> Profile:
        """返回当前激活的 profile。

        失败条件（抛 GenModelConfigError）：
        - GEN_MODEL_ACTIVE 字段未设置
        - active 名对应的 profile 不存在
        - active profile 缺 API_KEY
        """
        active = (os.environ.get("GEN_MODEL_ACTIVE") or "").strip()
        if not active:
            raise GenModelConfigError(
                "GEN_MODEL_ACTIVE 字段未设置 — 请编辑 .env 或跑 "
                "python core/scripts/gen_model.py list / switch <name>"
            )
        p = self.get_profile(active)
        if p is None:
            raise GenModelConfigError(
                f"active profile '{active}' 不存在；现有 profile: "
                f"{[x.name for x in self.list_profiles()]}"
            )
        if not p.api_key:
            raise GenModelConfigError(
                f"active profile '{active}' 缺 API_KEY；请在设置页录入你的密钥，"
                f"或编辑 .env 填入 GEN__{active}__API_KEY"
            )
        return p

    def get_fallback_chain(self) -> list[str]:
        """从 GEN_MODEL_FALLBACK_CHAIN 取 fallback 顺序"""
        chain = (os.environ.get("GEN_MODEL_FALLBACK_CHAIN") or "").strip()
        if not chain:
            return []
        return [s.strip() for s in chain.split(",") if s.strip()]

    def get_callable_profiles(self) -> list[Profile]:
        """返回 active + fallback 链中可调用的 profile（key 非空）

        用于 fallback 循环：第一个失败切第二个，全部失败抛 GenModelExhaustedError。
        """
        active = self.get_active_profile()
        result = [active]
        all_profiles = self._parse_profiles()
        seen = {active.name}
        for name in self.get_fallback_chain():
            if name in seen:
                continue
            p = all_profiles.get(name)
            if p and p.api_key:
                result.append(p)
                seen.add(name)
        return result


class GenModelExhaustedError(Exception):
    """active + 整条 fallback 链全部失败"""
    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        msg = "全部 gen-model profile 调用失败：\n" + "\n".join(
            f"  - {name}: {reason}" for name, reason in failures
        )
        super().__init__(msg)


# ============ 便捷函数 ============

def _user_override_path() -> Path:
    """分发版用户态可写 active 覆盖文件（%APPDATA%/ruoyuai/user_overrides.env）。

    切 active 模型时写这里（仅 GEN_MODEL_ACTIVE/FALLBACK_CHAIN 两键），绝不碰只读的
    内置 config（_internal/core/config/）。dev 模式不用此文件（改 .env）。
    """
    base = os.environ.get("APPDATA") or str(Path.home() / ".ruoyuai")
    return Path(base) / "ruoyuai" / "user_overrides.env"


_default_loader: GenModelLoader | None = None


def get_default_loader() -> GenModelLoader:
    """获取默认 loader 单例（懒加载）"""
    global _default_loader
    if _default_loader is None:
        _default_loader = GenModelLoader()
    return _default_loader


def reset_default_loader():
    """测试用：重置 loader 单例（适用于 .env 改动后）"""
    global _default_loader
    _default_loader = None
