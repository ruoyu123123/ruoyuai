#!/usr/bin/env python3
"""web_search_client.py — 联网调研 search API 薄抽象（BYOK·D1·2026-06-15）

程序驱动 exe 模式下 novel-researcher 依赖的 Claude WebSearch/WebFetch 不可用
（gen-model 无 web 能力·PROGRAM_DRIVEN.md L251 自承 soft 降级），本模块补回联网调研：
BYOK search API key（Tavily 首选·返回 LLM-ready 摘要），key 经 secrets_store keyring
（service=ruoyuai-search·DPAPI 加密），与 gen-model BYOK 同机制。

key 解析三级（照 gen_model_loader._resolve_api_key 范式）：
  1. keyring（BYOK 主路径·secrets_store.get_search_key）
  2. os.environ["RUOYU_SEARCH_KEY"]（CI/临时覆盖）
  3. 未配 → raise SearchKeyMissing（调用方降级静态 SOP 模板·D1 exe 回退·绝不静默吞）

provider：tavily（默认·LLM-ready）/ 预留 brave/exa（薄抽象·端点可换·暂只实现 tavily）。

安全：绝不 print/log key 明文（err 字符串过 secrets_store.redact）。frozen 安全——
keyring 不可用→env 兜底→仍无→raise 让调用方静态降级（绝不崩调研流水线·北极星 failure soft）。

用法：python web_search_client.py "<query>" [--max 5] [--provider tavily]
退出码：0 成功（JSON 结果）· 3 key 未配（降级信号）· 1 搜索失败
"""
from __future__ import annotations

import os

CONNECT_TIMEOUT = 10.0
DEFAULT_TIMEOUT = 20.0
TAVILY_ENDPOINT = "https://api.tavily.com/search"
_SUPPORTED = ("tavily",)


class SearchKeyMissing(RuntimeError):
    """search API key 未配（keyring + env 都无）→ 调用方降级静态模板。"""


class SearchError(RuntimeError):
    """search 调用失败（网络/HTTP/解析）→ 调用方降级。"""


def _resolve_search_key(provider: str) -> str:
    """三级解析（照 gen_model_loader._resolve_api_key）：keyring > env > raise。"""
    # 1. keyring（BYOK 主路径）
    try:
        import secrets_store
        k = secrets_store.get_search_key(provider)
        if k:
            return k
    except Exception:
        pass                             # keyring 故障绝不冒泡·继续降级
    # 2. 环境变量（CI/临时覆盖）
    env_k = (os.environ.get("RUOYU_SEARCH_KEY") or "").strip()
    if env_k:
        return env_k
    # 3. 未配 → 显式 raise（调用方降级静态模板·绝不静默）
    raise SearchKeyMissing(
        f"search API key 未配（provider={provider}）——"
        f"GUI 设置录入或设 RUOYU_SEARCH_KEY 环境变量")


def search(query: str, max_results: int = 5, provider: str = "tavily",
           timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """联网搜索 → [{title, url, content}]（LLM-ready）。

    key 未配 → SearchKeyMissing；网络/HTTP/解析错 → SearchError。调用方据此降级静态模板。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query 不能为空")
    if provider not in _SUPPORTED:
        raise ValueError(f"未支持的 search provider: {provider}（当前仅 {_SUPPORTED}）")
    key = _resolve_search_key(provider)
    return _search_tavily(q, key, max_results, timeout)


def _search_tavily(query: str, key: str, max_results: int, timeout: float) -> list[dict]:
    import httpx
    payload = {
        "api_key": key,
        "query": query,
        "max_results": max(1, int(max_results)),
        "search_depth": "basic",
    }
    try:
        to = httpx.Timeout(connect=CONNECT_TIMEOUT, read=timeout,
                           write=timeout, pool=timeout)
        with httpx.Client(timeout=to) as hc:
            resp = hc.post(TAVILY_ENDPOINT, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        _raise_redacted(f"Tavily HTTP {e.response.status_code}")
    except httpx.HTTPError as e:
        _raise_redacted(f"Tavily 网络错误: {type(e).__name__}")
    except ValueError as e:              # json 解析失败
        _raise_redacted(f"Tavily 响应解析失败: {type(e).__name__}")
    return _normalize_tavily(data, max_results)


def _normalize_tavily(data: dict, max_results: int) -> list[dict]:
    """Tavily 响应 → LLM-ready [{title, url, content}]。"""
    out: list[dict] = []
    for item in (data.get("results") or [])[:max_results]:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "content": (item.get("content") or "").strip(),
        })
    return out


def _raise_redacted(msg: str):
    """err 字符串过脱敏（防 key 泄漏）后 raise SearchError。"""
    try:
        import secrets_store
        msg = secrets_store.redact(msg)
    except Exception:
        pass
    raise SearchError(msg) from None


def gather_research_context(queries, max_per_query: int = 3,
                            provider: str = "tavily") -> tuple[str, int]:
    """多 query 联网搜索 → 格式化成 research context markdown（喂 gen-model 当 driver 代读内容）。

    novel-researcher 在程序驱动模式是 gen-model judge（无 WebSearch）·本函数把真联网结果
    组装成 context block 经 run_judge 的 extra_blocks 注入·让 gen-model 基于真数据综合非编造。
    key 未配 → SearchKeyMissing 冒泡（调用方 catch 降级静态模板·北极星 failure soft）。
    返回 (context_md, source_count)；无结果返回 ("", 0)。
    """
    qs = [q.strip() for q in (queries or []) if q and q.strip()]
    if not qs:
        return "", 0
    blocks: list[str] = []
    total = 0
    for q in qs:
        results = search(q, max_results=max_per_query, provider=provider)
        if not results:
            continue
        lines = [f"### 查询：{q}"]
        for r in results:
            lines.append(f"- **{r['title']}**（{r['url']}）\n  {r['content']}")
            total += 1
        blocks.append("\n".join(lines))
    if not blocks:
        return "", 0
    header = (f"# 联网调研结果（{total} 条来源 · {len(blocks)} 个查询 · "
              f"基于此综合·勿凭记忆编造）\n")
    return header + "\n\n".join(blocks), total


def is_search_available(provider: str = "tavily") -> bool:
    """key 是否已配（keyring 或 env）——调用方预检决定走联网还是静态降级。"""
    try:
        _resolve_search_key(provider)
        return True
    except SearchKeyMissing:
        return False


def main(argv=None) -> int:
    """CLI 入口（frozen multi-call dispatch 经 orchestrator 调 main()）。"""
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(prog="web_search_client",
                                 description="联网调研 search API 薄抽象（BYOK·Tavily）")
    ap.add_argument("query", help="搜索查询")
    ap.add_argument("--max", type=int, default=5, help="最大结果数（默认 5）")
    ap.add_argument("--provider", default="tavily", help="search provider（默认 tavily）")
    args = ap.parse_args(argv)

    try:
        results = search(args.query, max_results=args.max, provider=args.provider)
    except SearchKeyMissing as e:
        print(json.dumps({"error": "search_key_missing", "detail": str(e),
                          "fallback": "static_template"}, ensure_ascii=False),
              file=sys.stderr)
        return 3                         # 降级信号（调用方走静态 SOP 模板）
    except (SearchError, ValueError) as e:
        print(json.dumps({"error": "search_failed", "detail": str(e)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"results": results, "count": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
