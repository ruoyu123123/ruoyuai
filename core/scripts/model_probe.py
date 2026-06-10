#!/usr/bin/env python3
"""
model_probe.py — LLM 模型能力探测器

三层探测：
  1. /v1/models 端点（中转站可达性 + 模型 ID 列表）
  2. 本地已知模型表（避免每次都联网）
  3. 联网兜底（WebSearch 不在 CLI 可用，由用户/主代理触发）

输出缓存：<REPO_ROOT>/.claude/.model_capabilities.json
格式：
{
  "schema_version": "1.0",
  "last_probed_at": "2026-05-19T...",
  "endpoint_status": {
    "base_url": "...",
    "reachable": true,
    "models_listed": ["deepseek-v4-pro", "deepseek-v4-flash"]
  },
  "model_capabilities": {
    "deepseek-v4-pro": {
      "context_window": 1048576,
      "max_output_tokens": 384000,
      "recommended_max_tokens_for_writing": 16000,
      "source": "openrouter+webresearch_2026-05-19",
      "notes": "..."
    }
  }
}

用法：
  python core/scripts/model_probe.py              # 用 .env 凭证探测
  python core/scripts/model_probe.py --list       # 只列缓存内容
  python core/scripts/model_probe.py --force      # 强制重新探测覆盖缓存
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ============ 已知模型表（不需联网即可用的 hardcoded knowledge）============
# 来源：官方文档 + OpenRouter + 联网搜索 2026-05
KNOWN_MODELS = {
    # DeepSeek 系列
    "deepseek-v4-pro": {
        "context_window": 1048576,
        "max_output_tokens": 384000,
        "recommended_max_tokens_for_writing": 16000,
        "architecture": "1.6T MoE / 49B activated",
        "source": "openrouter+huggingface_2026-05",
        "notes": "Think Max reasoning mode 推荐 ≥ 384K context",
    },
    "deepseek-v4-flash": {
        "context_window": 1048576,
        "max_output_tokens": 128000,
        "recommended_max_tokens_for_writing": 16000,
        "source": "apiyi+webresearch_2026-05",
        "notes": "$0.14/M input, 5x cheaper than V4 Pro",
    },
    "deepseek-chat": {
        "context_window": 65536,
        "max_output_tokens": 8192,
        "recommended_max_tokens_for_writing": 8000,
        "source": "deepseek_official_2026",
        "notes": "DeepSeek V3.x dialogue endpoint",
    },
    "deepseek-reasoner": {
        "context_window": 65536,
        "max_output_tokens": 65536,
        "recommended_max_tokens_for_writing": 16000,
        "source": "deepseek_official_2026",
        "notes": "R1 with thinking chain (含 thinking tokens)",
    },
    "deepseek-v3": {
        "context_window": 65536,
        "max_output_tokens": 8192,
        "recommended_max_tokens_for_writing": 8000,
        "source": "deepseek_official_2025",
    },
    # Claude 系列（参考）
    "claude-sonnet-4-6": {
        "context_window": 200000,
        "max_output_tokens": 64000,
        "recommended_max_tokens_for_writing": 32000,
        "source": "anthropic_official",
    },
    # GPT 系列（参考）
    "gpt-4o": {
        "context_window": 128000,
        "max_output_tokens": 16384,
        "recommended_max_tokens_for_writing": 16000,
        "source": "openai_official",
    },
}


def get_cache_path() -> Path:
    """缓存文件路径：.claude/.model_capabilities.json。
    🔴 frozen 可写数据修复：用 user_data_dir()（dev=仓库根·frozen=%APPDATA%/ruoyuai 可写）——
    否则 frozen 下 cache_dir.mkdir 写只读 bundle 必失败。与 gen_* 读端一致。"""
    try:
        import sys as _s
        if str(Path(__file__).resolve().parent) not in _s.path:
            _s.path.insert(0, str(Path(__file__).resolve().parent))
        from frozen_util import user_data_dir as _udd
        repo_root = _udd()
    except Exception:
        repo_root = Path(__file__).parent.parent.parent
    cache_dir = repo_root / '.claude'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / '.model_capabilities.json'


def load_cache() -> dict:
    p = get_cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_cache(data: dict):
    p = get_cache_path()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def probe_models_endpoint(api_key: str, base_url: str) -> dict:
    """探测中转站 /v1/models"""
    import requests
    url = f"{base_url.rstrip('/')}/models"
    try:
        r = requests.get(url, headers={'Authorization': f'Bearer {api_key}'}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            models = [m.get('id') for m in data.get('data', [])]
            return {
                'base_url': base_url,
                'reachable': True,
                'status_code': 200,
                'models_listed': models,
                'raw_metadata': data.get('data', [])[:10],
            }
        else:
            return {
                'base_url': base_url,
                'reachable': False,
                'status_code': r.status_code,
                'error': r.text[:200],
            }
    except Exception as e:
        return {
            'base_url': base_url,
            'reachable': False,
            'error': f"{type(e).__name__}: {e}",
        }


def get_capabilities(model_id: str) -> dict:
    """查模型能力（本地表 → 模糊匹配 → 默认）"""
    # 精确匹配
    if model_id in KNOWN_MODELS:
        return KNOWN_MODELS[model_id].copy()
    # 模糊匹配（前缀）
    for known_id, caps in KNOWN_MODELS.items():
        if model_id.startswith(known_id):
            result = caps.copy()
            result['notes'] = result.get('notes', '') + f' [模糊匹配 {known_id}]'
            return result
    # 默认（保守）
    return {
        'context_window': 32768,
        'max_output_tokens': 8192,
        'recommended_max_tokens_for_writing': 8000,
        'source': 'default_conservative_unknown_model',
        'notes': f'未知模型 {model_id}，使用保守默认值；建议手动确认能力后填 .env',
    }


def load_dotenv_safely(path: str = None):
    from dotenv import load_dotenv
    if path:
        load_dotenv(path)
    else:
        # 显式路径，避免 find_dotenv 失败
        repo_root = Path(__file__).parent.parent.parent
        env_path = repo_root / '.env'
        if env_path.exists():
            load_dotenv(env_path)


def main():
    parser = argparse.ArgumentParser(description='LLM 模型能力探测器')
    parser.add_argument('--list', action='store_true', help='只列当前缓存内容')
    parser.add_argument('--force', action='store_true', help='强制重新探测覆盖缓存')
    parser.add_argument('--env-file', help='.env 文件路径（默认仓库根 .env）')
    args = parser.parse_args()

    cache_path = get_cache_path()

    if args.list:
        cache = load_cache()
        if not cache:
            print(f"[probe] 缓存空：{cache_path}")
        else:
            print(json.dumps(cache, ensure_ascii=False, indent=2))
        return

    load_dotenv_safely(args.env_file)

    # v2 改造（2026-05-19）：从 active gen-model profile 读，不再读 DEEPSEEK_*
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from gen_model_loader import GenModelLoader, GenModelConfigError
        loader = GenModelLoader()
        profile = loader.get_active_profile()
        api_key = profile.api_key
        base_url = profile.base_url
        model_id = profile.model
    except GenModelConfigError as e:
        print(f"[probe] {e}", file=sys.stderr)
        print("  跑 python core/scripts/gen_model.py list / switch 修复", file=sys.stderr)
        sys.exit(2)
    except ImportError:
        # Fallback：gen_model_loader 缺失时回落老字段（兼容期）
        api_key = os.getenv('DEEPSEEK_API_KEY')
        base_url = os.getenv('DEEPSEEK_BASE_URL')
        model_id = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
        if not api_key or not base_url:
            print("[probe] active profile 加载失败 + .env 缺 legacy DEEPSEEK_*", file=sys.stderr)
            sys.exit(2)

    print(f"[probe] base_url={base_url}")
    print(f"[probe] target_model={model_id}")
    print()

    # 1. 探测 /v1/models
    print(f"[probe] step 1: GET /models ...")
    endpoint_status = probe_models_endpoint(api_key, base_url)
    print(f"  reachable: {endpoint_status['reachable']}")
    if endpoint_status['reachable']:
        models = endpoint_status['models_listed']
        print(f"  available models ({len(models)}): {models}")
        if model_id not in models:
            print(f"  [WARN] 你配的 {model_id} 不在中转站模型列表中!")
    else:
        print(f"  error: {endpoint_status.get('error') or endpoint_status.get('status_code')}")

    # 2. 查每个模型的能力
    print(f"\n[probe] step 2: 查询模型能力（本地表 + 模糊匹配）")
    model_caps = {}
    for mid in endpoint_status.get('models_listed', [model_id]):
        caps = get_capabilities(mid)
        model_caps[mid] = caps
        print(f"  {mid}:")
        print(f"    context_window: {caps['context_window']:,}")
        print(f"    max_output_tokens: {caps['max_output_tokens']:,}")
        print(f"    recommended_for_writing: {caps['recommended_max_tokens_for_writing']:,}")
        print(f"    source: {caps['source']}")

    # 3. 保存缓存
    cache = {
        'schema_version': '1.0',
        'last_probed_at': datetime.now().isoformat(),
        'endpoint_status': endpoint_status,
        'model_capabilities': model_caps,
    }
    save_cache(cache)
    print(f"\n[probe] 缓存写入: {cache_path}")
    print(f"[probe] gen_writer.py / gen_fixer.py / gen_creative.py 启动时自动读取")


if __name__ == '__main__':
    main()
