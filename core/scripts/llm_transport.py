#!/usr/bin/env python3
"""llm_transport.py — 统一 LLM transport 层（程序驱动 M1 · 2026-06-10）

收敛 gen_writer / gen_creative / distill_replicate / ai_wrapper 4 份近重复 transport
为单一模块。新代码（judge_runner / orchestrator）一律走本模块；存量脚本逐个迁移，
迁移前行为零回归（本模块不 import 它们、不改它们）。

统一的 8 件事：
1. 双协议分发：profile.protocol == 'gemini' → 原生 streamGenerateContent SSE
   （隐式前缀缓存 cachedContentTokenCount 真省成本）；否则 OpenAI /v1/chat/completions。
2. 异常归一：双协议全部归一到 TransportRateLimit / TransportTimeout / TransportError。
   此前 gemini path 抛 urllib/httpx 原生异常 → 不进 gen_writer 的同 profile 重试分支
   （只 catch openai 的 RateLimitError/APITimeoutError）→ 429 直接降级 fallback 链。
3. finish_reason 归一：gemini 的 MAX_TOKENS → 'length'，其余非空 → 'stop'（openai 口径）。
4. 截断续写循环（finish_reason=='length' ≤ N 轮）抽成 generate() 共享——判断层深 schema
   JSON 比散文更脆（截一半 unparseable），续写补全而非整发重试（同位置再截不收敛）。
5. gemini 原生 path 补 generationConfig.thinkingConfig.thinkingLevel（小写 low/medium/high·
   与 thinkingBudget 互斥）。此前 thinking_level 只在 openai path 经 extra_body 生效，
   protocol=gemini 的 reasoning profile thinking 默认 HIGH 吃光输出预算 → 深 schema 集体截断。
   依据：ai.google.dev/gemini-api/docs/gemini-3（2026-06 确认 3.1-pro 支持 low/medium/high）。
6. gemini SSE 从 urllib 迁 httpx：urllib 无 read-timeout，实测代理 stream 中途断连僵死
   43min（distill_replicate 2026-06 事故）。httpx 是 openai SDK 传递依赖，零新增打包面。
7. 429 优先解析 Retry-After header（provider 给了精确等待就别盲目 2/4/8s 指数退避）。
8. 空响应守卫：HTTP 200 但零 content（内容过滤 / reasoning 全进 thought 段）→
   TransportEmpty → 进重试/降级，杜绝拿空文本报成功。

北极星边界：本层只管字节进出 + 容错，不做任何内容裁决（裁决在 audit_hub / 顾问在 judge）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gen_model_loader import GenModelLoader, Profile, reasoning_extra_body  # noqa: E402

try:
    from secrets_store import redact as _redact  # BYOK key 脱敏（gemini key 在 URL）
except Exception:
    def _redact(s):
        import re as _r
        return _r.sub(r"(key=)[^&\s]+", r"\1***", str(s))

# 与 ai_wrapper.py:154 / gen_writer.GEN_MODEL_TIMEOUT 对齐
DEFAULT_TIMEOUT = 180.0
CONNECT_TIMEOUT = 15.0


# ============ 异常归一 ============
class TransportError(Exception):
    """transport 层通用失败（连接断 / 5xx / 协议错）——可降级 fallback。"""


class TransportRateLimit(TransportError):
    """429 限流。retry_after：provider Retry-After header 给的精确秒数（无则 None）。"""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class TransportTimeout(TransportError):
    """连接/读超时。"""


class TransportEmpty(TransportError):
    """HTTP 200 但零 content（内容过滤 / reasoning model 全进 thought 段）。"""


class TransportExhausted(Exception):
    """active + 整条 fallback 链全部失败（与 GenModelExhaustedError 同语义·transport 自有）。"""

    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        msg = "全部 gen-model profile 调用失败：\n" + "\n".join(
            f"  - {name}: {reason}" for name, reason in failures)
        super().__init__(msg)


# ============ 重试策略 ============
@dataclass
class RetryPolicy:
    max_retries: int = 3          # 同 profile 限流/超时有限重试（再降级 fallback）
    base_delay: float = 2.0       # 指数退避基础秒（2,4,8）
    max_cont_rounds: int = 3      # 截断续写轮数上限（与 gen_writer 同构）

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(retry_after, 120.0)  # provider 给的精确等待优先（钳到 2min 防恶意 header）
        return self.base_delay * (2 ** (attempt - 1))


@dataclass
class GenResult:
    text: str
    profile: Profile
    finish_reason: str | None
    cont_rounds: int = 0          # 实际发生的截断续写轮数
    retries: int = 0              # 实际发生的同 profile 重试次数
    fallback_index: int = 0       # 0=active 成功；>0=降级到第 N 个 fallback


# ============ 工具 ============
def gemini_host(base_url: str) -> str:
    """从 OpenAI 风格 base_url(.../v1) 推 gemini 原生 host（去 /v1 尾）。"""
    return base_url.rsplit("/v1", 1)[0] if "/v1" in base_url else base_url.rstrip("/")


def default_cont_msg(reason: str = "length") -> str:
    """通用续写指令（transport 级缺省·writer/judge 各自传更贴业务的 builder 覆盖）。"""
    return ("上一条回复因长度上限被截断了。请接着上文最后一个字符继续输出，"
            "不要重复已输出的内容、不要重新开头。如果正在输出 JSON，"
            "请从断点处继续补完剩余的 JSON（保持嵌套结构闭合）。")


def _parse_retry_after(headers) -> float | None:
    """从响应 headers 抽 Retry-After 秒数（数字格式；HTTP-date 格式少见不解析）。"""
    if not headers:
        return None
    try:
        v = headers.get("retry-after") or headers.get("Retry-After")
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def parse_json_loose(reply: str, fallback: dict | None = None) -> dict:
    """三级宽松 JSON 抽取（合并 gen_creative._parse_json_loose + ai_wrapper 正则范式）。

    reasoning 模型前置 thinking 里可能混 {} 片段——优先 ```json 围栏（最后一个，
    模型常先打草稿再给终稿），其次整体 strip 后首字符判定，最后 first{...last} 兜底。
    全失败 → fallback（缺省 {"_parse_failed": True, "raw_text": ...}）。
    """
    if fallback is None:
        fallback = {"_parse_failed": True, "raw_text": reply}
    # 1) ```json 围栏（取最后一个——前面的可能是 thinking 草稿）
    fences = list(re.finditer(r"```json\s*\n(.*?)\n```", reply, re.DOTALL))
    for m in reversed(fences):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    # 2) 纯 JSON（strip 后首字符 { 或 [）
    stripped = reply.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    # 3) first { ... last }
    first, last = stripped.find("{"), stripped.rfind("}")
    if 0 <= first < last:
        try:
            return json.loads(stripped[first:last + 1])
        except json.JSONDecodeError:
            pass
    return fallback


# ============ 单次流式调用（双协议） ============
def stream_once(profile: Profile, system: str, user: str, max_tokens: int, *,
                prior_assistant: str | None = None,
                cont_msg: str | None = None,
                temperature: float | None = None,
                response_format_json: bool = False,
                echo: bool = False,
                client=None) -> tuple[str, str | None]:
    """单次 stream 生成 → (text, finish_reason 归一到 openai 口径)。

    所有失败抛 Transport* 异常（双协议归一）——这是与 gen_writer._stream_once 的关键差异，
    gemini path 的 429/timeout 也能进上层同 profile 重试。
    response_format_json：openai path 加 response_format=json_object（provider 不支持
    自动退回）；gemini path 加 responseMimeType=application/json 软约束（不上 responseSchema·
    reasoning 模型强 schema 集体失败实证）。
    """
    _protocol = getattr(profile, "protocol", "openai")
    if _protocol == "gemini":
        return _stream_once_gemini(profile, system, user, max_tokens,
                                   prior_assistant=prior_assistant, cont_msg=cont_msg,
                                   temperature=temperature,
                                   response_format_json=response_format_json, echo=echo)
    if _protocol == "anthropic":
        return _stream_once_anthropic(profile, system, user, max_tokens,
                                      prior_assistant=prior_assistant, cont_msg=cont_msg,
                                      temperature=temperature,
                                      response_format_json=response_format_json, echo=echo)
    return _stream_once_openai(profile, system, user, max_tokens,
                               prior_assistant=prior_assistant, cont_msg=cont_msg,
                               temperature=temperature,
                               response_format_json=response_format_json, echo=echo,
                               client=client)


def _stream_once_openai(profile: Profile, system: str, user: str, max_tokens: int, *,
                        prior_assistant: str | None, cont_msg: str | None,
                        temperature: float | None, response_format_json: bool,
                        echo: bool, client=None) -> tuple[str, str | None]:
    from openai import OpenAI
    try:
        from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                            RateLimitError)
    except ImportError:  # 极旧 SDK（不应发生·openai>=1.x 均有）
        APIConnectionError = APIStatusError = APITimeoutError = RateLimitError = ()

    if client is None:
        client = OpenAI(api_key=profile.api_key, base_url=profile.base_url,
                        timeout=DEFAULT_TIMEOUT)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    if prior_assistant:
        messages.append({"role": "assistant", "content": prior_assistant})
        messages.append({"role": "user", "content": cont_msg or default_cont_msg()})

    kw = dict(model=profile.model, messages=messages, max_tokens=max_tokens,
              temperature=profile.temperature if temperature is None else temperature,
              stream=True, stream_options={"include_usage": True})
    _extra = reasoning_extra_body(profile)  # helper 单一真理源(thinking_level/reasoning_effort·防 thinking 暴走)
    if _extra:
        kw["extra_body"] = _extra
    if response_format_json:
        kw["response_format"] = {"type": "json_object"}

    def _run(kwargs):
        text, finish, usage = "", None, {}
        try:
            import gen_throttle
            gen_throttle.wait()   # 限速端点全局节流（judge 走此 OpenAI path）
        except Exception:
            pass
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            # usage 在 stream 末尾 chunk（include_usage 时·该 chunk choices 常为空）
            cu = getattr(chunk, "usage", None)
            if cu is not None:
                usage = _openai_usage_to_dict(cu)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            piece = getattr(choice.delta, "content", None)
            if piece:
                text += piece
                if echo:
                    sys.stderr.write(piece)
                    sys.stderr.flush()
            if getattr(choice, "finish_reason", None):
                finish = choice.finish_reason
        return text, finish, usage

    try:
        try:
            _t, _f, _u = _run(kw)
        except Exception as fmt_err:
            # provider 不支持某 kwarg → 去掉重试一次（ai_wrapper 范式·response_format / stream_options）
            es = str(fmt_err).lower()
            _retried = False
            if response_format_json and "response_format" in es:
                kw.pop("response_format", None)
                _retried = True
            if "stream_options" in es:
                kw.pop("stream_options", None)  # 老 provider 不认 → 去掉（usage 拿不到·账本跳该次·零崩）
                _retried = True
            if _retried:
                print(f"[llm_transport] kwarg 不支持，退回重试: {str(fmt_err)[:120]}")
                _t, _f, _u = _run(kw)
            else:
                raise
        # token ledger 对称（judge 全走此 OpenAI path·此前漏记 → 账本只有 writer 没 judge）
        _record_token_usage(_u, profile.model, protocol="openai")
        return _t, _f
    except RateLimitError as e:
        ra = _parse_retry_after(getattr(getattr(e, "response", None), "headers", None))
        raise TransportRateLimit(f"429 rate-limited: {str(e)[:200]}", retry_after=ra) from e
    except APITimeoutError as e:
        raise TransportTimeout(f"timeout: {str(e)[:200]}") from e
    except APIConnectionError as e:
        raise TransportError(f"connection: {str(e)[:200]}") from e
    except APIStatusError as e:
        status = getattr(e, "status_code", None)
        if status == 429:
            ra = _parse_retry_after(getattr(getattr(e, "response", None), "headers", None))
            raise TransportRateLimit(f"429 via status: {str(e)[:200]}", retry_after=ra) from e
        raise TransportError(f"HTTP {status}: {str(e)[:200]}") from e
    except TransportError:
        raise
    except Exception as e:
        # 流式中途断连等（httpx.ReadError 经 SDK 漏出）一律归一
        raise TransportError(f"{type(e).__name__}: {str(e)[:200]}") from e


def build_gemini_body(profile: Profile, system: str, user: str, max_tokens: int, *,
                      prior_assistant: str | None = None, cont_msg: str | None = None,
                      temperature: float | None = None,
                      response_format_json: bool = False) -> dict:
    """gemini 原生请求体（纯函数·可测）。

    稳定 system 放 systemInstruction → 跨调用隐式前缀缓存命中（cachedContentTokenCount）。
    thinkingConfig.thinkingLevel：gemini-3.x 档位小写（low/medium/high·与 thinkingBudget
    互斥）——此前缺失，protocol=gemini 的 reasoning profile thinking 默认 HIGH 吃光输出
    预算（判断层深 schema 头号失败模式）。
    """
    contents = [{"role": "user", "parts": [{"text": user}]}]
    if prior_assistant:
        contents.append({"role": "model", "parts": [{"text": prior_assistant}]})
        contents.append({"role": "user", "parts": [{"text": cont_msg or default_cont_msg()}]})

    gen_cfg: dict = {"maxOutputTokens": max_tokens,
                     "temperature": profile.temperature if temperature is None else temperature}
    if getattr(profile, "thinking_level", None):
        gen_cfg["thinkingConfig"] = {"thinkingLevel": profile.thinking_level.lower()}
    if response_format_json:
        gen_cfg["responseMimeType"] = "application/json"  # 软约束·不上 responseSchema
    return {"systemInstruction": {"parts": [{"text": system}]},
            "contents": contents, "generationConfig": gen_cfg}


def _openai_usage_to_dict(u) -> dict:
    """OpenAI CompletionUsage（pydantic 对象）→ dict。model_dump 优先·fallback getattr。
    stream include_usage 末尾 chunk 给 usage（prompt_tokens/completion_tokens/total_tokens
    + prompt_tokens_details.cached_tokens）。容错任何 SDK 版本/provider 形态。"""
    if u is None:
        return {}
    try:
        d = u.model_dump()   # pydantic v2
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    out = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if v is not None:
            out[k] = v
    det = getattr(u, "prompt_tokens_details", None)
    if det is not None:
        ct = getattr(det, "cached_tokens", None)
        if ct is not None:
            out["prompt_tokens_details"] = {"cached_tokens": ct}
    return out


def _record_token_usage(usage: dict, model: str, protocol: str = "gemini") -> None:
    """token ledger（一人公司·BYOK 用户看烧多少钱）：单次 usage append 到 env
    RUOYU_TOKEN_LEDGER 指向的 jsonl。env 未设 → 不记（零侵入零回归）。落盘失败绝不崩
    transport（账本是 advisory·不影响写作主轨·北极星⑤）。

    2026-06-16 对称双协议（judge 全走 OpenAI path·此前漏记 → 账本只有 writer 没 judge）：
      protocol='gemini' → 原生字段（promptTokenCount/candidatesTokenCount/…）；
      protocol='openai' → OpenAI 兼容字段（prompt_tokens/completion_tokens/total_tokens·
      cached 取 prompt_tokens_details.cached_tokens）。归一到统一账本字段（summarize 直接消费）。"""
    import os as _os
    ledger_path = _os.environ.get("RUOYU_TOKEN_LEDGER")
    if not ledger_path or not usage:
        return
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path
        if protocol == "gemini":
            prompt_t = int(usage.get("promptTokenCount", 0) or 0)
            output_t = int(usage.get("candidatesTokenCount", 0) or 0)
            cached_t = int(usage.get("cachedContentTokenCount", 0) or 0)
            total_t = int(usage.get("totalTokenCount", 0) or 0)
        elif protocol == "anthropic":
            # Anthropic /v1/messages usage 字段 input_tokens/output_tokens(+ cache_read_input_tokens/
            # cache_creation_input_tokens)·无 total 字段·归一时 total=input+output·cached 取 cache_read。
            prompt_t = int(usage.get("input_tokens", 0) or 0)
            output_t = int(usage.get("output_tokens", 0) or 0)
            cached_t = int(usage.get("cache_read_input_tokens", 0) or 0)
            total_t = prompt_t + output_t
        else:  # openai 兼容（judge / 非 gemini profile·此前完全漏记）
            prompt_t = int(usage.get("prompt_tokens", 0) or 0)
            output_t = int(usage.get("completion_tokens", 0) or 0)
            _details = usage.get("prompt_tokens_details")
            cached_t = int(((_details or {}).get("cached_tokens")
                            if isinstance(_details, dict) else 0) or 0)
            total_t = int(usage.get("total_tokens", 0) or 0)
        rec = {
            "ts": _time.time(),
            "model": model,
            "protocol": protocol,
            "prompt_tokens": prompt_t,
            "output_tokens": output_t,
            "cached_tokens": cached_t,
            "total_tokens": total_t,
        }
        p = _Path(ledger_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass   # 账本落盘失败绝不崩 transport


def _stream_once_gemini(profile: Profile, system: str, user: str, max_tokens: int, *,
                        prior_assistant: str | None, cont_msg: str | None,
                        temperature: float | None, response_format_json: bool,
                        echo: bool) -> tuple[str, str | None]:
    """gemini 原生 streamGenerateContent SSE（httpx · 强制 read-timeout）。"""
    import httpx

    host = gemini_host(profile.base_url)
    url = (f"{host}/v1beta/models/{profile.model}:streamGenerateContent"
           f"?alt=sse&key={profile.api_key}")
    body = build_gemini_body(profile, system, user, max_tokens,
                             prior_assistant=prior_assistant, cont_msg=cont_msg,
                             temperature=temperature,
                             response_format_json=response_format_json)

    text, finish_raw, usage = "", None, {}
    try:
        import gen_throttle
        gen_throttle.wait()   # 限速端点全局节流（gemini native SSE path）
    except Exception:
        pass
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=DEFAULT_TIMEOUT,
                            write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout) as hc:
            with hc.stream("POST", url, json=body,
                           headers={"Content-Type": "application/json"}) as resp:
                if resp.status_code == 429:
                    ra = _parse_retry_after(resp.headers)
                    raise TransportRateLimit(f"429 gemini-native", retry_after=ra)
                if resp.status_code >= 400:
                    resp.read()
                    raise TransportError(
                        f"HTTP {resp.status_code} gemini-native: {resp.text[:200]}")
                for line in resp.iter_lines():
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        d = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for c0 in (d.get("candidates") or [])[:1]:
                        for part in (c0.get("content", {}).get("parts") or []):
                            if part.get("thought"):  # 跳过 reasoning thought 段
                                continue
                            t = part.get("text")
                            if t:
                                text += t
                                if echo:
                                    sys.stderr.write(t)
                                    sys.stderr.flush()
                        if c0.get("finishReason"):
                            finish_raw = c0["finishReason"]
                    if d.get("usageMetadata"):
                        usage = d["usageMetadata"]
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
            httpx.PoolTimeout) as e:
        raise TransportTimeout(f"gemini-native timeout: {_redact(str(e))[:200]}") from e
    except TransportError:
        raise
    except httpx.HTTPError as e:
        # 🔴 BYOK 脱敏（must_fix#3）：httpx 异常 str() 含请求 URL（?key=<KEY>）→ 脱敏后再抛
        raise TransportError(
            f"gemini-native {type(e).__name__}: {_redact(str(e))[:200]}") from e

    cached = usage.get("cachedContentTokenCount")
    if cached:
        print(f"\n[llm_transport][gemini] 缓存命中 cachedContentTokenCount={cached}"
              f"/{usage.get('promptTokenCount', '?')} prompt tokens", file=sys.stderr)
    # token ledger（B1 一人公司·BYOK 用户看烧多少钱）：env RUOYU_TOKEN_LEDGER 设则 append·零侵入
    _record_token_usage(usage, getattr(profile, "model", "gemini"))
    finish = "length" if finish_raw == "MAX_TOKENS" else ("stop" if finish_raw else None)
    return text, finish


# ============ Anthropic /v1/messages 协议（跨家族 judge ensemble · R10 W6 · 2026-06-20） ============
# 跨家族 judge（cross_family_judge_check）用 Claude 复审抑制 self-preference bias。Anthropic 协议
# 与 OpenAI/Gemini 都不同：① system 单独字段（不是 messages 数组里的 role=system）；② SSE 事件名
# 独特（content_block_delta / message_delta / message_start）；③ 无 finish_reason 而是 stop_reason
# （max_tokens/end_turn/stop_sequence/tool_use）；④ 必填 max_tokens。
# 业界源：docs.anthropic.com/en/api/messages（2026-06 GA 校验·content_block_delta.delta.text 是流式
# 文本累加点·message_delta.delta.stop_reason 是终止理由·usage 在 message_start.message.usage）。
def build_anthropic_body(profile: Profile, system: str, user: str, max_tokens: int, *,
                         prior_assistant: str | None = None, cont_msg: str | None = None,
                         temperature: float | None = None,
                         response_format_json: bool = False) -> dict:
    """Anthropic /v1/messages 请求体（纯函数·可测）。

    system 单独字段（不进 messages）→ 利于 Anthropic 的隐式 prompt caching；
    response_format_json=True 时在 system 末尾追加软约束「仅输出合法 JSON」
    （Anthropic 原生无 response_mime_type / response_format 字段）。
    """
    sys_text = system or ""
    if response_format_json:
        sys_text = (sys_text + "\n\n# 输出格式硬约束\n"
                    "请仅输出一个合法的 JSON 对象（用 ```json 围栏包裹），"
                    "外层不要任何说明文字。")
    messages = [{"role": "user", "content": user}]
    if prior_assistant:
        messages.append({"role": "assistant", "content": prior_assistant})
        messages.append({"role": "user", "content": cont_msg or default_cont_msg()})
    body: dict = {
        "model": profile.model,
        "max_tokens": max_tokens,
        "system": sys_text,
        "messages": messages,
        "stream": True,
    }
    if temperature is not None:
        body["temperature"] = temperature
    elif getattr(profile, "temperature", None) is not None:
        body["temperature"] = profile.temperature
    return body


def _stream_once_anthropic(profile: Profile, system: str, user: str, max_tokens: int, *,
                           prior_assistant: str | None, cont_msg: str | None,
                           temperature: float | None, response_format_json: bool,
                           echo: bool) -> tuple[str, str | None]:
    """Anthropic /v1/messages SSE 流式生成（httpx · 与 gemini path 同款异常归一）。"""
    import httpx

    base = (profile.base_url or "").rstrip("/")
    # 用户配 base_url 通常是 OpenAI 兼容 .../v1·Anthropic 原生也是 .../v1/messages·容错两种形态
    if base.endswith("/v1"):
        url = f"{base}/messages"
    elif base.endswith("/v1/messages"):
        url = base
    else:
        url = f"{base}/v1/messages"
    body = build_anthropic_body(profile, system, user, max_tokens,
                                prior_assistant=prior_assistant, cont_msg=cont_msg,
                                temperature=temperature,
                                response_format_json=response_format_json)
    headers = {
        "x-api-key": profile.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    text, finish_raw, usage = "", None, {}
    try:
        import gen_throttle
        gen_throttle.wait()
    except Exception:
        pass
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=DEFAULT_TIMEOUT,
                            write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT)
    try:
        with httpx.Client(timeout=timeout) as hc:
            with hc.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code == 429:
                    ra = _parse_retry_after(resp.headers)
                    raise TransportRateLimit(f"429 anthropic", retry_after=ra)
                if resp.status_code in (401, 403):
                    resp.read()
                    raise TransportError(
                        f"HTTP {resp.status_code} anthropic auth: "
                        f"{_redact(resp.text)[:200]}")
                if resp.status_code >= 400:
                    resp.read()
                    raise TransportError(
                        f"HTTP {resp.status_code} anthropic: "
                        f"{_redact(resp.text)[:200]}")
                # Anthropic SSE: event: <name>\ndata: <json>\n\n
                current_event = None
                for line in resp.iter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        d = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    et = current_event or d.get("type")
                    if et == "message_start":
                        msg = d.get("message", {}) or {}
                        u = msg.get("usage") or {}
                        if u:
                            usage.update(u)
                    elif et == "content_block_delta":
                        delta = d.get("delta", {}) or {}
                        # text_delta / thinking_delta 都可能·只取 text_delta（thinking 不要）
                        if delta.get("type") == "text_delta":
                            t = delta.get("text") or ""
                            if t:
                                text += t
                                if echo:
                                    sys.stderr.write(t)
                                    sys.stderr.flush()
                    elif et == "message_delta":
                        delta = d.get("delta", {}) or {}
                        if delta.get("stop_reason"):
                            finish_raw = delta["stop_reason"]
                        u = d.get("usage") or {}
                        if u:
                            usage.update(u)   # message_delta usage 含 output_tokens
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
            httpx.PoolTimeout) as e:
        raise TransportTimeout(f"anthropic timeout: {_redact(str(e))[:200]}") from e
    except TransportError:
        raise
    except httpx.HTTPError as e:
        raise TransportError(
            f"anthropic {type(e).__name__}: {_redact(str(e))[:200]}") from e

    _record_token_usage(usage, getattr(profile, "model", "claude"),
                        protocol="anthropic")
    # finish 归一到 openai 口径：max_tokens→length·end_turn/stop_sequence→stop
    if finish_raw == "max_tokens":
        finish = "length"
    elif finish_raw in ("end_turn", "stop_sequence", "tool_use"):
        finish = "stop"
    else:
        finish = "stop" if finish_raw else None
    return text, finish


# ============ 统一调用入口（fallback 链 + 重试 + 截断续写 + 空响应守卫） ============
def generate(loader_or_profiles, system: str, user: str, *,
             max_tokens: int | None = None,
             default_max_tokens: int = 16000,
             temperature: float | None = None,
             response_format_json: bool = False,
             cont_msg_builder=None,
             retry: RetryPolicy | None = None,
             echo: bool = False,
             label: str = "llm",
             _stream_fn=None) -> GenResult:
    """active → fallback 链逐个尝试；同 profile 限流/超时指数退避重试（尊重 Retry-After）；
    finish_reason=='length' 自动续写 ≤ retry.max_cont_rounds 轮；空响应换 profile。

    loader_or_profiles：GenModelLoader 或 list[Profile]（测试注入友好）。
    cont_msg_builder(reason:str, round:int)->str：业务自定义续写文案（writer 的 expand /
    judge 的「续 JSON 尾巴」）；缺省 default_cont_msg。
    _stream_fn：测试注入点（签名同 stream_once）。

    抛 TransportExhausted（全链失败）。
    """
    retry = retry or RetryPolicy()
    sfn = _stream_fn or stream_once
    if isinstance(loader_or_profiles, GenModelLoader):
        candidates = loader_or_profiles.get_callable_profiles()
    else:
        candidates = list(loader_or_profiles)
    if not candidates:
        raise TransportExhausted([("<none>", "无可用 profile（active/fallback 全空）")])

    failures: list[tuple[str, str]] = []
    for fb_index, profile in enumerate(candidates):
        mt = max_tokens if max_tokens is not None else (
            profile.max_tokens if profile.max_tokens else default_max_tokens)
        tag = f"[{label}]"
        if fb_index:
            print(f"\n{tag} [FALLBACK] -> {profile.name} ({profile.model})", file=sys.stderr)

        retries_used = 0
        try:
            # —— 同 profile 重试圈 ——
            # 🔴 轮次8 实测扩围：原只重试限流/超时·中转站瞬时 404 nginx 页/5xx/断流立即
            # 降级且零退避 → 主备同主机时亚分钟故障窗击穿全链（fallback 9ms 后同 404）。
            # 瞬时类 TransportError 同 profile 重试；认证/账号类（401/403/key）不可恢复
            # → 立即降级不浪费退避。TransportEmpty（内容过滤）也不重试。
            attempt = 0
            while True:
                try:
                    text, finish = sfn(profile, system, user, mt,
                                       prior_assistant=None, cont_msg=None,
                                       temperature=temperature,
                                       response_format_json=response_format_json,
                                       echo=echo)
                    break
                except TransportEmpty:
                    raise                          # 空响应非瞬时 → 直接降级
                except TransportError as re_err:
                    msg = str(re_err)
                    non_transient = any(k in msg for k in (
                        "401", "403", "unauthorized", "Unauthorized",
                        "forbidden", "Forbidden", "api key", "API key"))
                    attempt += 1
                    retries_used = attempt
                    if non_transient or attempt > retry.max_retries:
                        raise
                    ra = getattr(re_err, "retry_after", None)
                    delay = retry.delay_for(attempt, ra)
                    src = f"Retry-After={ra}s" if ra else "指数退避"
                    print(f"\n{tag} {profile.name} {type(re_err).__name__}，"
                          f"{delay:.0f}s 后重试 {attempt}/{retry.max_retries}（{src}）…")
                    time.sleep(delay)

            # —— 截断续写圈（length → 续写补全 · 绝不整发重试） ——
            cont_rounds = 0
            while finish == "length" and cont_rounds < retry.max_cont_rounds:
                cont_rounds += 1
                cmsg = (cont_msg_builder("length", cont_rounds) if cont_msg_builder
                        else default_cont_msg("length"))
                print(f"\n{tag} 输出截断(finish=length)，续写 {cont_rounds}/"
                      f"{retry.max_cont_rounds}…", file=sys.stderr)
                cont_text, finish = sfn(profile, system, user, mt,
                                        prior_assistant=text, cont_msg=cmsg,
                                        temperature=temperature,
                                        response_format_json=response_format_json,
                                        echo=echo)
                text += cont_text
            if finish == "length":
                print(f"\n{tag} WARN 续写 {cont_rounds} 轮后仍截断（尾部可能不完整）")

            # —— 空响应守卫 ——
            if not text.strip():
                raise TransportEmpty("HTTP 200 但零 content（内容过滤/reasoning 全进 thought）")

            return GenResult(text=text, profile=profile, finish_reason=finish,
                             cont_rounds=cont_rounds, retries=retries_used,
                             fallback_index=fb_index)
        except TransportError as e:
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"\n{tag} {profile.name} 失败 → 降级: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue
        except Exception as e:  # 非 transport 异常（SDK 内部炸等）同样降级、不崩调用方
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"\n{tag} {profile.name} 非预期异常 → 降级: {reason}", file=sys.stderr)
            failures.append((profile.name, reason))
            continue

    raise TransportExhausted(failures)
