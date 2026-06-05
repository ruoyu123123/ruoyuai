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


@dataclass
class Profile:
    name: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int | None  # None = 从 model_probe 缓存读
    protocol: str = "openai"  # openai(默认·/v1/chat/completions) | gemini(原生·streamGenerateContent·支持隐式前缀缓存)


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
        if env_path is None:
            # 优先 cwd/.env，其次脚本同级仓库根 .env
            cwd_env = Path(".env")
            if cwd_env.exists():
                env_path = cwd_env
            else:
                env_path = Path(__file__).parent.parent.parent / ".env"
        self.env_path = Path(env_path)
        # 显式加载 .env 到 os.environ
        try:
            from dotenv import load_dotenv
            load_dotenv(self.env_path, override=True)
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
                    api_key=(fields.get("api_key") or "").strip(),
                    temperature=float(temp_str),
                    max_tokens=int(max_tok_str) if max_tok_str else None,
                    protocol=((fields.get("protocol") or "openai").strip().lower() or "openai"),
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
                f"active profile '{active}' 缺 API_KEY；编辑 .env 填入 GEN__{active}__API_KEY"
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
