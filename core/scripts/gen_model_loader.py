#!/usr/bin/env python3
"""
gen_model_loader.py — 多 profile loader + fallback 链（gen_writer/gen_fixer 等润色链共用）

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
import json
import os
import re

try:
    import secrets_store  # keyring 薄抽象（BYOK 可选密钥源，见下方三级优先级解析）
except Exception:
    secrets_store = None


def _resolve_api_key_with_source(name: str, env_file_value: str) -> tuple[str, str]:
    """三级优先级解析 api_key，返回 (key, source)。source ∈ {keyring, environ, file, none}。

    优先级（第一个 strip 后非空者胜）：
      1. keyring（BYOK·分发版主路径·DPAPI 加密）— **会覆盖 .env**·静默覆盖陷阱
      2. os.environ["GEN__<name>__API_KEY"]（CI/容器/临时覆盖）
      3. env_file_value（.env 文本）

    keyring 旧 key 会静默覆盖 .env 新 key——改 .env 后 writer 可能仍读到 keyring 里的旧
    密钥，表现为莫名的鉴权失败。本函数返回 source 让 `dump_key_sources` / `--diag` CLI
    能直观告诉用户「.env 改了但生效的是 keyring」。
    """
    if secrets_store is not None:
        try:
            if secrets_store.is_available():
                kr = secrets_store.get_api_key(name)
                if kr and kr.strip():
                    return kr.strip(), "keyring"
        except Exception:
            pass  # keyring 故障绝不影响下游降级
    env_v = (os.environ.get(f"GEN__{name}__API_KEY") or "").strip()
    if env_v:
        # environ vs file 区分：load_dotenv(override=True) 后两者等值时倾向报 file
        if env_v == (env_file_value or "").strip():
            return env_v, "file"
        return env_v, "environ"
    file_v = (env_file_value or "").strip()
    if file_v:
        return file_v, "file"
    return "", "none"


def _resolve_api_key(name: str, env_file_value: str) -> str:
    """三级优先级解析 api_key（只返回 key，不返回来源）。

    详细优先级与陷阱说明见 `_resolve_api_key_with_source`。
    """
    key, _ = _resolve_api_key_with_source(name, env_file_value)
    return key


@dataclass
class Profile:
    name: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int | None  # None = 从 model_probe 缓存读
    protocol: str = "openai"  # openai(默认·/v1/chat/completions) | gemini(原生·streamGenerateContent·支持隐式前缀缓存)
    thinking_level: str | None = None  # gemini-3.x reasoning 模型思考档(LOW/MEDIUM/HIGH)·走 extra_body·LOW=回收15-25k输出预算给正文(治pro偏短)·None=不传(flash等非reasoning)
    reasoning_effort: str | None = None  # OpenAI 标准 reasoning 参数(low/medium/high)·走 extra_body·部分 new-api 中转站认此而非 gemini 专有 thinking_level(中转站上 thinking_level 被忽略会导致 thinking 失控暴走·reasoning_effort 更受控)·与 thinking_level 独立·按 profile 配置·None=不传
    max_prompt_chars: int | None = None  # 中转站 prompt 体量上限(中文字符数·含 system+user)·超限直接跳 fallback 不等超时(部分中转站超出约 34k 中文字符/100KB 会返回 500)·None=无限制


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
            # 优先 cwd/.env，其次仓库根 .env。
            cwd_env = Path(".env")
            repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
            if cwd_env.exists():
                env_path = cwd_env
            else:
                env_path = repo_env
        self.env_path = Path(env_path)
        # 显式加载 config 到 os.environ（GEN_MODEL_ACTIVE/FALLBACK 经此进 environ）
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
                    api_key=_resolve_api_key(name, fields.get("api_key") or ""),
                    temperature=float(temp_str),
                    max_tokens=int(max_tok_str) if max_tok_str else None,
                    protocol=((fields.get("protocol") or "openai").strip().lower() or "openai"),
                    thinking_level=((fields.get("thinking_level") or "").strip().upper() or None),
                    reasoning_effort=((fields.get("reasoning_effort") or "").strip().lower() or None),
                    max_prompt_chars=int(fields["max_prompt_chars"]) if fields.get("max_prompt_chars", "").strip() else None,
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

    def dump_key_sources(self) -> list[dict]:
        """诊断：列出所有 profile 的 key 来源（keyring/environ/file/none）+ key 尾部 + .env 文本是否定义。

        让用户 / agent 一眼看到「.env 改了但生效的是 keyring 旧 key」，直接定位覆盖陷阱。
        """
        if not self.env_path.exists():
            return []
        text = self.env_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"^GEN__(.+?)__([A-Z_]+)[ \t]*=[ \t]*(.*?)[ \t]*$",
            re.MULTILINE,
        )
        env_keys: dict[str, str] = {}
        for m in pattern.finditer(text):
            name, field, value = m.group(1), m.group(2), m.group(3)
            if name.startswith("<"):
                continue
            if field.lower() == "api_key":
                env_keys[name] = value.strip()
        active = (os.environ.get("GEN_MODEL_ACTIVE") or "").strip()
        rows: list[dict] = []
        for name in sorted(env_keys.keys()):
            env_file_v = env_keys.get(name, "")
            resolved_key, source = _resolve_api_key_with_source(name, env_file_v)
            row = {
                "profile": name,
                "is_active": name == active,
                "source": source,
                "resolved_tail": resolved_key[-8:] if resolved_key else None,
                "env_file_defined": bool(env_file_v),
                "env_file_tail": env_file_v[-8:] if env_file_v else None,
                "keyring_overrides_env": False,
            }
            if source == "keyring" and env_file_v and resolved_key != env_file_v:
                row["keyring_overrides_env"] = True
            rows.append(row)
        return rows


class PromptTooLargeError(Exception):
    """prompt 超过 profile.max_prompt_chars 上限·触发 fallback 跳转而非等超时"""
    pass


class GenModelExhaustedError(Exception):
    """active + 整条 fallback 链全部失败"""
    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        msg = "全部 gen-model profile 调用失败：\n" + "\n".join(
            f"  - {name}: {reason}" for name, reason in failures
        )
        super().__init__(msg)


# ============ 便捷函数 ============

def reasoning_extra_body(profile) -> dict:
    """openai-path reasoning 控制 extra_body 单一真理源（thinking_level/reasoning_effort 独立·都注入·按 profile 配）。

    所有走 OpenAI 兼容 chat.completions.create 的 gen-model 调用统一用此构造 extra_body，防止
    inline 漂移/漏注入——独立裸调用容易漏 reasoning 控制，导致 thinking 暴走、content 空、500。
    thinking_level=gemini 专有(pie-xian 认)·reasoning_effort=OpenAI 标准(elysiver/new-api 中转认)·
    二者独立按 profile 配。空 dict=非 reasoning profile(flash 等)不注入。"""
    e = {}
    if getattr(profile, "thinking_level", None):
        e["thinking_level"] = profile.thinking_level
    if getattr(profile, "reasoning_effort", None):
        e["reasoning_effort"] = profile.reasoning_effort
    return e


def load_model_capabilities_cache() -> dict:
    """加载 model_probe.py 写出的能力缓存（gen_writer/gen_fixer 共用单一真理源）。"""
    try:
        from frozen_util import user_data_dir as _udd
        cache_path = _udd() / '.claude' / '.model_capabilities.json'
    except Exception:
        cache_path = Path(__file__).parent.parent.parent / '.claude' / '.model_capabilities.json'
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def resolve_max_tokens(profile: Profile) -> tuple[int, str]:
    """决定 max_tokens 的优先级：
       1. profile.max_tokens 显式（最高）
       2. 缓存的 recommended_max_tokens_for_writing
       3. 保守默认值 16000
       返回 (max_tokens, source)
    """
    if profile.max_tokens is not None:
        return profile.max_tokens, 'profile_explicit'

    cache = load_model_capabilities_cache()
    caps = cache.get('model_capabilities', {}).get(profile.model)
    if caps:
        return caps.get('recommended_max_tokens_for_writing', 16000), f"cache:{caps.get('source', 'unknown')}"

    return 16000, 'default_fallback_16k_NO_PROBE_YET'


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


# ============ CLI ============

def main(argv=None) -> int:
    """诊断 CLI。

    用法：
      py core/scripts/gen_model_loader.py diag        # 列所有 profile 的 key 来源 + 警告 keyring 覆盖
      py core/scripts/gen_model_loader.py diag --json # JSON 输出
    """
    import argparse
    import json
    import sys as _sys

    ap = argparse.ArgumentParser(prog="gen_model_loader", description="gen-model profile 诊断")
    sub = ap.add_subparsers(dest="cmd")
    diag = sub.add_parser("diag", help="列出每个 profile 的 key 来源（keyring/environ/file）")
    diag.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)

    if args.cmd == "diag":
        loader = GenModelLoader()
        rows = loader.dump_key_sources()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        active = (os.environ.get("GEN_MODEL_ACTIVE") or "").strip()
        print(f"[gen_model_loader diag] GEN_MODEL_ACTIVE = {active or '(unset)'}")
        print(f"  .env path: {loader.env_path}")
        print(f"  {len(rows)} profile(s) defined.\n")
        warnings = 0
        for r in rows:
            mark = "*" if r["is_active"] else " "
            src = r["source"]
            tail = f"...{r['resolved_tail']}" if r["resolved_tail"] else "(empty)"
            line = f"{mark} {r['profile']:<22} source={src:<8} key={tail}"
            print(line)
            if r["keyring_overrides_env"]:
                env_tail = f"...{r['env_file_tail']}" if r["env_file_tail"] else "(empty)"
                print(f"  ⚠️  keyring 覆盖 .env：keyring={tail} vs .env={env_tail}")
                print(f"     → 改 .env 不生效。用 secrets_store sync 或 set 写入新 key。")
                warnings += 1
        if warnings:
            print(f"\n⚠️  {warnings} 个 profile 的 keyring 与 .env 不一致，可能正经历静默覆盖。")
            print("    修复：py core/scripts/secrets_store.py set <profile> <key>  或  sync-from-env")
            return 1
        print("\nOK 无 keyring/.env 不一致。")
        return 0

    ap.print_help(_sys.stderr)
    return 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
