"""cluster 正文与角色对白的 embedding 索引。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import cluster_lookup
import cluster_summary_reader as csr


def _stable_hash_embedding(text: str, dim: int = 384) -> list[float]:
    ngrams = []
    for n in (2, 3):
        for i in range(len(text) - n + 1):
            ngrams.append(text[i:i+n])
    if not ngrams:
        return [0.0] * dim
    vec = [0.0] * dim
    for ng in ngrams:
        h = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if (h >> 1) & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if len(v1) != len(v2):
        return 0.0
    return sum(a * b for a, b in zip(v1, v2))


# ── 真语义 embedding 后端（opt-in · 全本地）──────
# 后端链：本地 sentence-transformers（mstyle/ruoyu_style/local bge）→ hash 兜底。
# 消费方（build_manifest RAG / voice drift）统一走 compute_embedding；
# 默认无本地包/未显式 opt-in → hash。
# ⚠️ 切换后端会改变维度（hash 384 / bge 512 / mstyle·ruoyu_style 768）→ 必须重建缓存
#    （rebuild 写 .embed_manifest.json 记 method+dim；cosine 维度不等返回 0，
#    drift 会显示 sim 异常提示重建）。
_BACKEND = None          # (method:str, dim:int, fn) 探测缓存
_LOCAL_MODEL = None      # 本地 sentence-transformers 模型 lazy 缓存
_MSTYLE_MODEL = None     # StyleDistance/mstyledistance 模型 lazy 缓存（真风格语义·CPU）


def _embed_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _local_embed(text: str) -> list[float]:
    global _LOCAL_MODEL
    if _LOCAL_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _LOCAL_MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _LOCAL_MODEL.encode(text[:8000], normalize_embeddings=True).tolist()


def _mstyle_embed(text: str) -> list[float]:
    """StyleDistance/mstyledistance（ACL2025 · content-independent 风格向量 · 含中文 · CPU 推理）。
    论文定位即 style-transfer-eval（复刻打分）→ 比 bge 的内容语义更对口「作者风格相似度」。768-dim。"""
    global _MSTYLE_MODEL
    if _MSTYLE_MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MSTYLE_MODEL = SentenceTransformer("StyleDistance/mstyledistance")
    return _MSTYLE_MODEL.encode(text[:8000], normalize_embeddings=True).tolist()


# ── NN风格声纹集成 — ruoyu_style 后端（venv subprocess 桥）────────────
# 若渝主流水线跑**系统 Python 3.14（无 torch）**，本仓 fine-tune 的风格/声纹模型只能在
# **venv Python 3.10（torch CUDA）** 加载。所以 ruoyu_style 后端**不在系统 py 直接 import
# sentence_transformers**（那会 ImportError），而是经 subprocess 调 venv python 跑
# `core/ml/style_embed/style_infer.py` 批量编码。
#
# 🔴 默认安全铁律：venv 缺 / 模型缺 / torch 缺 / subprocess 失败/超时 / 输出不符
#    → 一律返回 None（批量）或 raise（单条·被 compute_embedding 捕获兜底 hash）。
#    调用方回退现有 backend（stable_hash / 启发式 char-3gram）·**绝不崩**·零回归。
#
# 两个模型（一空间两用法·见 core/ml/style_embed/INTEGRATION.md）：
#   · author（默认·SFS）   → runs/style_embed_v1/final   （AP 0.887）
#   · character（千人千面） → runs/style_embed_char_v1/final（char voice AUC 0.653）
_RUOYU_MODEL_DIRS = {
    "author": "core/ml/style_embed/runs/style_embed_v1/final",
    "character": "core/ml/style_embed/runs/style_embed_char_v1/final",
}
_RUOYU_MODEL_ENV = {"author": "RUOYU_STYLE_MODEL", "character": "RUOYU_CHAR_STYLE_MODEL"}
_RUOYU_DIM_CACHE: dict = {}


def _ruoyu_venv_python() -> "Path | None":
    """定位 venv py3.10（带 torch）解释器。env RUOYU_STYLE_VENV_PY 可覆盖。缺则 None。"""
    root = _embed_repo_root()
    cands = []
    env_py = os.environ.get("RUOYU_STYLE_VENV_PY")
    if env_py:
        cands.append(Path(env_py))
    cands += [
        root / "core" / "ml" / ".venv" / "Scripts" / "python.exe",  # Windows
        root / "core" / "ml" / ".venv" / "bin" / "python",          # POSIX
        root / "core" / "ml" / ".venv" / "bin" / "python3",
    ]
    for c in cands:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    return None


def _ruoyu_model_path(model: str = "author") -> "Path | None":
    """解析模型目录（env 覆盖 > 默认相对路径）。目录不存在 → None。"""
    env_key = _RUOYU_MODEL_ENV.get(model)
    if env_key and os.environ.get(env_key):
        p = Path(os.environ[env_key])
    else:
        p = _embed_repo_root() / _RUOYU_MODEL_DIRS.get(model, _RUOYU_MODEL_DIRS["author"])
    return p if p.exists() else None


def ruoyu_style_dim(model: str = "author") -> "int | None":
    """读 ruoyu_meta.json 的 dim（纯 stdlib·不加载 torch）。模型缺 → None。"""
    if model in _RUOYU_DIM_CACHE:
        return _RUOYU_DIM_CACHE[model]
    mp = _ruoyu_model_path(model)
    if mp is None:
        return None
    try:
        dim = int(json.loads(
            (mp / "ruoyu_meta.json").read_text(encoding="utf-8")).get("dim", 768))
    except Exception:
        dim = 768
    _RUOYU_DIM_CACHE[model] = dim
    return dim


def ruoyu_style_available(model: str = "author") -> bool:
    """venv + 模型 + style_infer.py 三者俱在 → True（消费方先探测再决定是否走 NN）。"""
    if _ruoyu_venv_python() is None or _ruoyu_model_path(model) is None:
        return False
    return (_embed_repo_root() / "core" / "ml" / "style_embed" / "style_infer.py").exists()


def ruoyu_style_encode_batch(texts, model: str = "author",
                             timeout: int = 600) -> "list | None":
    """NN风格声纹集成 — 批量编码（subprocess 调 venv py + style_infer.py）。

    入：texts=list[str]，model ∈ {"author","character"}。
    出：list[list[float]]（与 texts 等长·已 L2 归一化）或 **None**（任何不可用/失败）。

    🔴 默认安全：venv/模型/torch 缺、subprocess 非零退出/超时、输出条数或内容不符
       → 返回 None（调用方回退 stable_hash / char-3gram）·绝不抛到调用栈外·绝不崩。
    """
    if not texts:
        return []
    # 🔴 daemon-first：常驻推理服务命中时 ~0.1s/批（模型驻内存），
    # 未启用(RUOYU_NN_DAEMON!=1 默认)/不可达/结果不齐 → 无缝落下方既有子进程冷启动路径。
    try:
        import nn_daemon_client
        if nn_daemon_client.enabled() and nn_daemon_client.ensure_daemon():
            res = nn_daemon_client.infer(
                "style_embed", [(t or "")[:8000] for t in texts], model=model, timeout=timeout)
            if res is not None and len(res) == len(texts):
                embs = [r.get("embedding") if isinstance(r, dict) else None for r in res]
                if all(e for e in embs):
                    return embs
    except Exception:  # noqa: BLE001 — daemon 任何问题都不影响子进程回退路径
        pass
    vpy = _ruoyu_venv_python()
    mp = _ruoyu_model_path(model)
    if vpy is None or mp is None:
        print(f"[embedding_store] ruoyu_style 不可用（venv={vpy is not None} "
              f"model={mp is not None}）→ 回退现有 backend", file=sys.stderr)
        return None
    infer = _embed_repo_root() / "core" / "ml" / "style_embed" / "style_infer.py"
    if not infer.exists():
        print("[embedding_store] ruoyu_style 缺 style_infer.py → 回退", file=sys.stderr)
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "in.jsonl"
            outp = Path(td) / "out.jsonl"
            with inp.open("w", encoding="utf-8") as fh:
                for t in texts:
                    fh.write(json.dumps({"text": (t or "")[:8000]},
                                        ensure_ascii=False) + "\n")
            proc = subprocess.run(
                [str(vpy), str(infer), "--model", str(mp),
                 "--input", str(inp), "--output", str(outp)],
                capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0 or not outp.exists():
                tail = (proc.stderr or "")[-300:]
                print(f"[embedding_store] ruoyu_style subprocess 失败"
                      f"(rc={proc.returncode}): {tail} → 回退", file=sys.stderr)
                return None
            embs = []
            for line in outp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    embs.append(json.loads(line).get("embedding"))
            if len(embs) != len(texts) or any(not e for e in embs):
                print(f"[embedding_store] ruoyu_style 输出条数/内容不符"
                      f"(got={len(embs)} want={len(texts)}) → 回退", file=sys.stderr)
                return None
            return embs
    except Exception as e:  # noqa: BLE001 — 桥任何异常都降级（默认安全）
        print(f"[embedding_store] ruoyu_style 桥异常降级: {str(e)[:160]}", file=sys.stderr)
        return None


def _ruoyu_style_embed(text: str) -> list[float]:
    """ruoyu_style 单条编码（backend fn 用）。桥不可用 → raise → compute_embedding 兜底 hash。"""
    embs = ruoyu_style_encode_batch([text], model="author")
    if not embs:
        raise RuntimeError("ruoyu_style bridge unavailable")
    return embs[0]


def _detect_backend():
    """探测 embedding 后端（一次，缓存）。返回 (method, dim, fn)。

    🔴 真语义是 **opt-in**：默认 hash（零回归 · 与现有缓存维度一致 · 即使环境恰好装了
    sentence-transformers 也不自动切，避免「维度混用导致 cosine=0 → voice drift/RAG 静默失效」+
    「首次 encode 触发模型下载拖慢流水线」）。只有用户显式设 EMBED_BACKEND 才启用真语义（全本地）：
      ① EMBED_BACKEND=mstyle（且装了 sentence-transformers）→ StyleDistance/mstyledistance
         （ACL2025 真风格语义 · content-independent · 含中文 · CPU · 风格相似度最对口）
      ② EMBED_BACKEND=ruoyu_style（NN风格声纹集成）→ 本仓 fine-tune
         风格模型（runs/style_embed_v1/final·dim 768）经 venv py3.10 subprocess 桥编码
         （系统 py3.14 无 torch·故不直接 import·走 ruoyu_style_encode_batch）。venv/模型缺
         → 降级 hash（默认安全·零崩）。
      ③ EMBED_BACKEND=local（且装了 sentence-transformers）→ 本地 bge（内容语义）
    切换后端务必先 `embedding_store.py <proj> rebuild` 重建缓存（维度变了）。"""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    _eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    # ① 用户显式 EMBED_BACKEND=mstyle + 装了包（真风格语义·风格相似度首选）
    if _eb == "mstyle":
        try:
            import sentence_transformers  # noqa: F401
            _BACKEND = ("mstyle:StyleDistance/mstyledistance", 768, _mstyle_embed)
            print("[embedding_store] 后端=mstyle StyleDistance/mstyledistance (dim=768·真风格语义) · 切后端记得 rebuild", file=sys.stderr)
            return _BACKEND
        except ImportError:
            print("[embedding_store] EMBED_BACKEND=mstyle 但未装 sentence-transformers，降级 hash", file=sys.stderr)
    # ② 🔴 NN风格声纹集成 · EMBED_BACKEND=ruoyu_style → 本仓 fine-tune 风格模型
    #    （venv subprocess 桥·系统 py3.14 无 torch·见上方 ruoyu_style_encode_batch）。
    #    默认安全：venv 或模型缺 → 降级 hash（保持 dim 384 一致·零崩）。
    if _eb == "ruoyu_style":
        dim = ruoyu_style_dim("author")
        vpy = _ruoyu_venv_python()
        infer = _embed_repo_root() / "core" / "ml" / "style_embed" / "style_infer.py"
        if dim is None or vpy is None or not infer.exists():
            print(f"[embedding_store] EMBED_BACKEND=ruoyu_style 不可用"
                  f"(model={dim is not None} venv={vpy is not None} "
                  f"infer={infer.exists()})，降级 hash", file=sys.stderr)
        else:
            mp = _ruoyu_model_path("author")
            _BACKEND = (f"ruoyu_style:{mp.name}", dim, _ruoyu_style_embed)
            print(f"[embedding_store] 后端=ruoyu_style {mp.name} (dim={dim}·venv subprocess 桥) "
                  f"· 切后端记得 rebuild", file=sys.stderr)
            return _BACKEND
    # ③ 用户显式 EMBED_BACKEND=local + 装了包
    if _eb == "local":
        try:
            import sentence_transformers  # noqa: F401
            _BACKEND = ("local:bge-small-zh-v1.5", 512, _local_embed)
            print("[embedding_store] 后端=本地 bge-small-zh-v1.5 (dim=512) · 切后端记得 rebuild", file=sys.stderr)
            return _BACKEND
        except ImportError:
            print("[embedding_store] EMBED_BACKEND=local 但未装 sentence-transformers，降级 hash", file=sys.stderr)
    # ④ 默认 hash（零回归 · 不因环境装了包就静默切换）
    _BACKEND = ("hash", 384, lambda t: _stable_hash_embedding(t, 384))
    return _BACKEND


def embedding_method() -> str:
    """当前 embedding 后端标识（供 .embed_manifest 记录 + 一致性校验）。"""
    return _detect_backend()[0]


# ── embedding 缓存 + 批量 API 性能层 ──────────────────────
# ruoyu_style 后端每次调用=新起 venv 子进程加载模型（暖机 ~23s/次），但 16 条 batch
# 边际成本仅 1.6s/条——单条 compute_embedding 模式下 scanner 逐段调用完全不可用。
# 因此：①(method, sha256(text)) 键内存+磁盘缓存（仅真后端·稳定语料如 prototype/锚点/skill
# 段落一次编码终身命中）；②compute_embeddings_batch 把 misses 合并成一次后端批调用
# （ruoyu_style 走既有 encode_batch 单子进程·API 走 list input）；③scanner 侧 scan() 开头
# prefetch_embeddings(全部待编码文本)，其后既有的逐条 compute_embedding 调用全部命中缓存。
# 纪律：失败绝不缓存（防 hash 兜底向量污染真后端键）；hash 后端不缓存（本身零成本）。

_EMBED_MEM_CACHE: "dict[tuple[str, str], list[float]]" = {}
_EMBED_CACHE_MAX_FILES = int(os.environ.get("RUOYU_EMBED_CACHE_MAX_FILES", "20000"))


def _embed_cache_enabled() -> bool:
    """磁盘+内存缓存开关（默认开·RUOYU_EMBED_CACHE=0 显式关）。"""
    return os.environ.get("RUOYU_EMBED_CACHE", "1") != "0"


def _embed_text_key(text: str) -> str:
    """缓存键：sha256(text[:8000])[:24]——与各后端一致的 8000 截断口径。"""
    return hashlib.sha256(((text or "")[:8000]).encode("utf-8")).hexdigest()[:24]


def _embed_cache_dir(method: str) -> Path:
    base = os.environ.get("RUOYU_EMBED_CACHE_DIR")
    root = Path(base) if base else (_embed_repo_root() / "core" / "ml" / ".cache" / "embeddings")
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", method)
    p = root / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def _embed_cache_get(method: str, key: str) -> "list[float] | None":
    hit = _EMBED_MEM_CACHE.get((method, key))
    if hit is not None:
        return hit
    fp = _embed_cache_dir(method) / f"{key}.json"
    if not fp.exists():
        return None
    try:
        vec = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(vec, list) and vec:
            _EMBED_MEM_CACHE[(method, key)] = vec
            return vec
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _embed_cache_put(method: str, key: str, vec: list) -> None:
    _EMBED_MEM_CACHE[(method, key)] = vec
    d = _embed_cache_dir(method)
    try:
        # 轻量容量护栏：超上限只留内存缓存，不再落盘（防无界增长）
        if sum(1 for _ in d.glob("*.json")) >= _EMBED_CACHE_MAX_FILES:
            return
        tmp = d / f".{key}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(vec), encoding="utf-8")
        os.replace(tmp, d / f"{key}.json")   # 原子替换·并行 scanner 安全
    except OSError:
        pass   # 磁盘缓存尽力而为·失败不影响主流程


def compute_embedding(text: str) -> list[float]:
    """真语义 embedding（降级链）。任何后端失败 → hash 兜底（永不崩，呼应 MAPE-K「失败记录降级」）。

    🔴 真后端结果过 (method, text-hash) 缓存——先 prefetch_embeddings()
    批量灌缓存，其后逐条调用零成本命中（hash 后端不缓存·失败兜底不缓存）。"""
    method, _dim, fn = _detect_backend()
    use_cache = method != "hash" and _embed_cache_enabled()
    key = _embed_text_key(text) if use_cache else ""
    if use_cache:
        hit = _embed_cache_get(method, key)
        if hit is not None:
            return hit
    try:
        v = fn(text)
        if v and len(v) > 0:
            if use_cache:
                _embed_cache_put(method, key, v)
            return v
    except Exception as e:
        print(f"[embedding_store] {method} 失败降级 hash: {str(e)[:120]}", file=sys.stderr)
    return _stable_hash_embedding(text, dim=384)


def _backend_batch_compute(method: str, fn, texts: "list[str]") -> "list[list[float] | None]":
    """按后端类型做一次真批量计算。返回与 texts 等长（失败条目 None·绝不抛）。"""
    if method.startswith("ruoyu_style:"):
        embs = ruoyu_style_encode_batch(texts, model="author")
        return list(embs) if embs else [None] * len(texts)
    # mstyle/local：模型已常驻进程内，逐条即批量（sentence-transformers 内部自带 batch）
    out: "list[list[float] | None]" = []
    for t in texts:
        try:
            v = fn(t)
            out.append(v if v else None)
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


def compute_embeddings_batch(texts: "list[str]") -> "list[list[float]]":
    """批量真语义 embedding（缓存 + 批量后端核心 API）。与 texts 等长·条目失败走 hash 兜底（不缓存）。

    dedupe → 缓存命中 → misses 一次后端批调用（ruoyu_style=单 venv 子进程编整批）→ 回填缓存。
    """
    if not texts:
        return []
    method, _dim, fn = _detect_backend()
    if method == "hash":
        return [_stable_hash_embedding(t or "", dim=384) for t in texts]
    use_cache = _embed_cache_enabled()
    keys = [_embed_text_key(t) for t in texts]
    results: "list[list[float] | None]" = [None] * len(texts)
    miss_key_order: "list[str]" = []
    miss_text_by_key: "dict[str, str]" = {}
    for i, (t, k) in enumerate(zip(texts, keys)):
        hit = _embed_cache_get(method, k) if use_cache else None
        if hit is not None:
            results[i] = hit
        elif k not in miss_text_by_key:
            miss_key_order.append(k)
            miss_text_by_key[k] = t or ""
    if miss_key_order:
        computed = _backend_batch_compute(method, fn, [miss_text_by_key[k] for k in miss_key_order])
        vec_by_key: "dict[str, list[float] | None]" = dict(zip(miss_key_order, computed))
        for k, v in vec_by_key.items():
            if v and use_cache:
                _embed_cache_put(method, k, v)
        for i, k in enumerate(keys):
            if results[i] is None:
                results[i] = vec_by_key.get(k)
    return [r if r else _stable_hash_embedding(texts[i] or "", dim=384)
            for i, r in enumerate(results)]


# ── 内容语义嵌入 API（风格/内容双轨·bge-small-zh） ──────────────
# 动机（真机测量·core/ml/calibration/reports/*_separability_20260704.md）：ruoyu_style 是作者
# 判别模型，对内容关系在单段粒度 AUC≈随机（0.51-0.56）；bge 内容模型三条达标线全过
# （0.859/0.763/0.808）。内容语义任务（呼应/触及/去重检索）走本 API；风格任务继续走
# compute_embedding（EMBED_BACKEND=ruoyu_style）。
# 纪律：不可用 → 返回 None（调用方回退字面逻辑）——内容语义**没有 hash 兜底**，hash 不是语义。

_CONTENT_METHOD = "content:bge-small-zh-v1.5"


def _content_infer_paths() -> "tuple[Path, Path] | None":
    venv_py = _embed_repo_root() / "core" / "ml" / ".venv" / "Scripts" / "python.exe"
    infer = _embed_repo_root() / "core" / "ml" / "content_embed" / "content_infer.py"
    if venv_py.exists() and infer.exists():
        return venv_py, infer
    return None


def content_backend_available() -> bool:
    """内容嵌入后端可用性（venv+infer 脚本+模型目录三者俱在·便宜检查不 spawn）。"""
    if _content_infer_paths() is None:
        return False
    ckpt = os.environ.get("RUOYU_CONTENT_EMBED_CKPT")
    if ckpt:
        return Path(ckpt).exists()
    return (_embed_repo_root() / "core" / "ml" / "models" / "content_embed" / "bge-small-zh-v1.5").exists()


def _content_embed_backend_batch(texts: "list[str]") -> "list[list[float] | None] | None":
    """真批量计算：daemon task=content_embed 优先（热 ~0.05s）→ venv subprocess 兜底（~10s 冷）。
    整体不可用 → None（不逐条假装）。"""
    try:
        import nn_daemon_client
        if nn_daemon_client.enabled() and nn_daemon_client.ensure_daemon():
            res = nn_daemon_client.infer("content_embed", texts, timeout=600)
            if res is not None and len(res) == len(texts):
                embs = [r.get("embedding") if isinstance(r, dict) else None for r in res]
                if all(e for e in embs):
                    return embs
    except Exception:  # noqa: BLE001 — daemon 任何问题落 subprocess
        pass
    paths = _content_infer_paths()
    if paths is None:
        return None
    venv_py, infer = paths
    try:
        with tempfile.TemporaryDirectory() as td:
            inp, outp = Path(td) / "in.jsonl", Path(td) / "out.jsonl"
            with inp.open("w", encoding="utf-8") as fh:
                for t in texts:
                    fh.write(json.dumps({"text": (t or "")[:8000]}, ensure_ascii=False) + "\n")
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            proc = subprocess.run([str(venv_py), str(infer), "--batch", str(inp), "--out", str(outp)],
                                  capture_output=True, timeout=1800, **kwargs)
            if proc.returncode != 0 or not outp.exists():
                return None
            embs = []
            for line in outp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    obj = json.loads(line)
                    embs.append(obj.get("embedding"))
            if len(embs) != len(texts) or any(not e for e in embs):
                return None
            return embs
    except Exception as e:  # noqa: BLE001 — 桥失败诚实返 None
        print(f"[embedding_store] content_embed subprocess 失败: {str(e)[:120]}", file=sys.stderr)
        return None


def compute_content_embeddings_batch(texts: "list[str]") -> "list[list[float]] | None":
    """内容语义批量编码（缓存复用统一批量缓存机制·method 键隔离于风格后端）。
    整体不可用 → None；可用则与 texts 等长（编码经 L2 归一·cosine=点积）。"""
    if not texts:
        return []
    if not content_backend_available():
        return None
    use_cache = _embed_cache_enabled()
    keys = [_embed_text_key(t) for t in texts]
    results: "list[list[float] | None]" = [None] * len(texts)
    miss_key_order: "list[str]" = []
    miss_text_by_key: "dict[str, str]" = {}
    for i, (t, k) in enumerate(zip(texts, keys)):
        hit = _embed_cache_get(_CONTENT_METHOD, k) if use_cache else None
        if hit is not None:
            results[i] = hit
        elif k not in miss_key_order:
            miss_key_order.append(k)
            miss_text_by_key[k] = t or ""
    if miss_key_order:
        computed = _content_embed_backend_batch([miss_text_by_key[k] for k in miss_key_order])
        if computed is None:
            return None   # 后端整体不可用 → 诚实 None（调用方回退字面逻辑）
        vec_by_key = dict(zip(miss_key_order, computed))
        for k, v in vec_by_key.items():
            if v and use_cache:
                _embed_cache_put(_CONTENT_METHOD, k, v)
        for i, k in enumerate(keys):
            if results[i] is None:
                results[i] = vec_by_key.get(k)
    if any(r is None for r in results):
        return None
    return results


def compute_content_embedding(text: str) -> "list[float] | None":
    """单条内容语义编码（批量的单元素路径·同缓存）。不可用 → None。"""
    out = compute_content_embeddings_batch([text])
    return out[0] if out else None


def prefetch_content_embeddings(texts: "list[str]") -> dict:
    """内容嵌入预热（与 prefetch_embeddings 同约定·供 scanner 语义分支开头调用）。"""
    if not texts:
        return {"total": 0, "unique": 0, "computed": 0}
    unique = {}
    for t in texts:
        unique.setdefault(_embed_text_key(t), t)
    hits = sum(1 for k in unique if _embed_cache_get(_CONTENT_METHOD, k) is not None)
    out = compute_content_embeddings_batch(list(unique.values()))
    return {"total": len(texts), "unique": len(unique), "cache_hits": hits,
            "computed": (len(unique) - hits) if out is not None else 0,
            "available": out is not None}


def prefetch_embeddings(texts: "list[str]") -> dict:
    """scanner 入口批量预热：一次后端批调用灌缓存，其后逐条 compute_embedding 零成本命中。

    返回统计 {total, unique, cache_hits, computed}（供日志/测试断言）。hash 后端直接 no-op。"""
    if not texts:
        return {"total": 0, "unique": 0, "cache_hits": 0, "computed": 0}
    method, _dim, _fn = _detect_backend()
    if method == "hash" or not _embed_cache_enabled():
        return {"total": len(texts), "unique": 0, "cache_hits": 0, "computed": 0,
                "skipped": "hash 后端/缓存关闭"}
    unique_keys = {}
    for t in texts:
        unique_keys.setdefault(_embed_text_key(t), t)
    hits_before = sum(1 for k in unique_keys if _embed_cache_get(method, k) is not None)
    compute_embeddings_batch(list(unique_keys.values()))
    return {"total": len(texts), "unique": len(unique_keys),
            "cache_hits": hits_before, "computed": len(unique_keys) - hits_before}


def _emb_dir(project_root: Path) -> Path:
    p = project_root / "_数据库" / ".embeddings"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── C1 风格余弦护栏（防 hash 静默冒充 mstyle 风格余弦）──────────────
class MstyleBackendError(RuntimeError):
    """风格余弦子分要求 mstyle 后端但当前不是 → 显式失败，绝不静默降级 hash 冒充。"""


def assert_mstyle_backend():
    """硬断言当前 embedding 后端真是 mstyle 且 import 成功（供 dev/蒸馏态的风格余弦消费方调用）。

    🔴 绝不静默降级 hash 冒充风格余弦（hash 是 md5 ngram 袋·风格语义=0）。
    返回 (method, dim)；非 mstyle / 未装 sentence-transformers → raise。
    ⚠️ 只在 dev/蒸馏工作站态调用——frozen 写作态不含 torch 依赖，消费方须先 is_frozen() 跳过（绝不崩写作流水线）。
    """
    eb = os.environ.get("EMBED_BACKEND", "").strip().lower()
    if eb != "mstyle":
        raise MstyleBackendError(f"风格余弦子分要求 EMBED_BACKEND=mstyle，当前={eb or '(未设·默认 hash)'}")
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as e:
        raise MstyleBackendError(f"EMBED_BACKEND=mstyle 但未装 sentence-transformers：{e}")
    method, dim, _ = _detect_backend()
    if not method.startswith("mstyle:"):
        raise MstyleBackendError(f"后端探测未落到 mstyle（method={method}）")
    return method, dim


def _embed_manifest_path(project_root: Path) -> Path:
    return _emb_dir(project_root) / ".embed_manifest.json"


def write_embed_manifest(project_root: Path):
    """rebuild 后落 .embed_manifest.json 记 method+dim（供 check_embed_manifest 校验用）。"""
    m, d, _ = _detect_backend()
    _embed_manifest_path(project_root).write_text(
        json.dumps({"method": m, "dim": d}, ensure_ascii=False), encoding="utf-8")


def check_embed_manifest(project_root: Path) -> "tuple[bool, str]":
    """返回 (ok, reason)。manifest.method != 当前后端 → 需 rebuild（维度可能变·cosine 维度不等返 0）。"""
    p = _embed_manifest_path(project_root)
    if not p.exists():
        return False, "no_manifest"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"manifest_corrupt: {e}"
    cur = embedding_method()
    return (rec.get("method") == cur), f"manifest={rec.get('method')} cur={cur}"


def _canonical_cluster_id(value) -> str:
    cluster_id = cluster_lookup.normalize_cluster_id(value)
    if not isinstance(value, str) or cluster_id != value:
        raise ValueError(f"cluster_id 必须是规范 cluster_NNN: {value!r}")
    return cluster_id


def _cluster_draft_path(project_root: Path, cluster_id: str) -> Path:
    return (
        project_root / "章节" / f"{cluster_id}_draft"
        / f"{cluster_id}_draft.txt"
    )


def store_cluster_embedding(project_root: Path, cluster_id: str):
    cluster_id = _canonical_cluster_id(cluster_id)
    text_path = _cluster_draft_path(project_root, cluster_id)
    try:
        text = text_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"cluster 终稿不存在: {text_path}") from exc
    if not text.strip():
        raise ValueError(f"cluster 终稿为空: {text_path}")
    chunks = [text[i:i+500] for i in range(0, len(text), 500)]
    chunk_embs = [compute_embedding(c) for c in chunks]
    record = {
        "scope": "cluster", "cluster_id": cluster_id,
        "wc": len(text), "n_chunks": len(chunks),
        "method": embedding_method(),   # 维度混用防护：记录生成时的后端
        "chunks": [{"idx": i, "text_preview": c[:80], "embedding": e} for i, (c, e) in enumerate(zip(chunks, chunk_embs))],
    }
    out_path = _emb_dir(project_root) / f"{cluster_id}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return out_path


def _extract_character_dialogues(
    project_root: Path,
    character_name: str,
    max_d: int = 20,
    cluster_ids: list[str] | None = None,
) -> list[str]:
    chars = json.loads((project_root / "_数据库" / "人物卡.json").read_text(encoding="utf-8"))
    char = next((c for c in chars.get("characters", []) if c.get("name") == character_name or c.get("id") == character_name), None)
    if not char:
        return []
    aliases = list(set([a for a in [char.get("name"), char.get("id")] + char.get("name_aliases", []) if a]))

    dialogues = []
    if cluster_ids is None:
        cluster_ids = [record["cluster_id"] for record in csr.get_clusters(project_root)]
    canonical_ids = [_canonical_cluster_id(cluster_id) for cluster_id in cluster_ids]
    files = [_cluster_draft_path(project_root, cluster_id) for cluster_id in canonical_ids]

    for f in files:
        text = f.read_text(encoding="utf-8")
        for alias in aliases:
            for m in re.finditer(re.escape(alias), text):
                start = m.start()
                window = text[max(0, start-200):min(len(text), start+200)]
                # 对话提取正则须覆盖中文弯引号（U+201C/U+201D）和「」角引号，不能只认 ASCII "，否则中文对话提取失效
                for q in re.findall(r'"([^"\n]{3,80})"|“([^”\n]{1,80})”|「([^」\n]{1,80})」', window):
                    d = next((g for g in q if g), "").strip()
                    if d and d not in dialogues:
                        dialogues.append(d)
                        if len(dialogues) >= max_d:
                            return dialogues
    return dialogues


def store_character_baseline(project_root: Path, character_name: str):
    dialogues = _extract_character_dialogues(project_root, character_name, max_d=20)
    if not dialogues:
        return None
    embs = [compute_embedding(d) for d in dialogues]
    n = len(embs)
    baseline = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
    norm = math.sqrt(sum(v * v for v in baseline))
    if norm > 0:
        baseline = [v / norm for v in baseline]
    record = {
        "scope": "character_dialogue", "character": character_name, "n_samples": n,
        "method": embedding_method(),   # 维度混用防护：记录生成时的后端
        "baseline_embedding": baseline, "sample_dialogues": dialogues[:5],
    }
    out_path = _emb_dir(project_root) / f"character_{character_name}.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return out_path


def compute_character_drift(
    project_root: Path,
    character_name: str,
    recent_cluster_id: str,
) -> dict:
    recent_cluster_id = _canonical_cluster_id(recent_cluster_id)
    baseline_path = _emb_dir(project_root) / f"character_{character_name}.json"
    if not baseline_path.exists():
        return {"error": "baseline 不存在", "character": character_name}
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_emb = baseline_data.get("baseline_embedding", [])
    # 维度混用防护：baseline 后端 ≠ 当前后端 → 提示重建（而非 cosine=0 误报 drift）
    bl_method = baseline_data.get("method", "hash")
    if bl_method != embedding_method():
        return {"drift": None, "character": character_name,
                "recent_cluster_id": recent_cluster_id,
                "reason": f"baseline method={bl_method} ≠ 当前={embedding_method()}，请先跑 embedding_store rebuild 重建"}

    dialogues = _extract_character_dialogues(
        project_root, character_name, max_d=10,
        cluster_ids=[recent_cluster_id],
    )
    if not dialogues:
        return {"drift": None, "character": character_name,
                "recent_cluster_id": recent_cluster_id, "reason": "本 cluster 无对话"}

    embs = [compute_embedding(d) for d in dialogues]
    n = len(embs)
    avg = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
    norm = math.sqrt(sum(v * v for v in avg))
    if norm > 0:
        avg = [v / norm for v in avg]
    sim = cosine_similarity(baseline_emb, avg)
    return {
        "character": character_name, "recent_cluster_id": recent_cluster_id,
        "baseline_samples": baseline_data.get("n_samples"),
        "recent_samples": n,
        "cosine_similarity": round(sim, 3),
        "drift": round(1 - sim, 3),
        "alert": "drift > 0.3" if (1 - sim) > 0.3 else "ok",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("action", choices=["rebuild", "drift", "cluster"])
    ap.add_argument("--character", default=None)
    ap.add_argument("--cluster", default=None)
    args = ap.parse_args()

    project_root = Path(args.project)

    if args.action == "rebuild":
        cluster_ids = [record["cluster_id"] for record in csr.get_clusters(project_root)]
        for cluster_id in cluster_ids:
            store_cluster_embedding(project_root, cluster_id)
            print(f"  [OK] {cluster_id} embedding")
        chars = json.loads((project_root / "_数据库" / "人物卡.json").read_text(encoding="utf-8"))
        for c in chars.get("characters", []):
            name = c.get("name")
            if name:
                p = store_character_baseline(project_root, name)
                if p:
                    print(f"  [OK] character_{name} baseline")
                else:
                    print(f"  [SKIP] character_{name}（无对话样本）")
        write_embed_manifest(project_root)   # C1：落 .embed_manifest.json 后端守卫
        print(f"[embedding_store] rebuild 完成")
        sys.exit(0)

    elif args.action == "drift":
        if not args.character or not args.cluster:
            print("[ERROR] drift 需 --character 和 --cluster", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(
            compute_character_drift(project_root, args.character, args.cluster),
            ensure_ascii=False, indent=2,
        ))
        sys.exit(0)

    elif args.action == "cluster":
        if not args.cluster:
            print("[ERROR] cluster 需 --cluster", file=sys.stderr)
            sys.exit(2)
        p = store_cluster_embedding(project_root, args.cluster)
        print(f"[OK] {args.cluster} → {p}")
        sys.exit(0)


if __name__ == "__main__":
    main()
