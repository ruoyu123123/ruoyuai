#!/usr/bin/env python3
"""scaffold_genre_packs.py — 题材专属维度包【单一入口 + 验证】（蒸馏画骨·阶段3）

单一真理源 = core/claude-home/templates/genre_dimension_packs.json。
所有消费者（judge driver / scanner 路由 / build_manifest 注入）经 get_pack(genre) 读，
不各自硬编码题材维度（照顾弱模型 + 防题材逻辑散落）。

风格分两层：作者层(句法/声纹/节奏=通用维度池 always-on) + 题材层(内容工艺=按 genre 激活)。
专属维度全 advisory·hard_gate 清单不随题材变（北极星⑤）。

用法：
  python scaffold_genre_packs.py --list              # 列 canonical genres + 有 pack 的
  python scaffold_genre_packs.py --pack romance      # 打印某题材包
  python scaffold_genre_packs.py verify              # 校验单一真理源 JSON 合法 + 结构完整
退出码：0 成功 / 2 缺失或损坏
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
try:
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from frozen_util import bundle_root as _bundle_root
    PACKS_FILE = _bundle_root() / "core" / "claude-home" / "templates" / "genre_dimension_packs.json"
except Exception:
    PACKS_FILE = ROOT / "core" / "claude-home" / "templates" / "genre_dimension_packs.json"

_CACHE: dict | None = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(PACKS_FILE.read_text(encoding="utf-8"))
    return _CACHE


def canonical_genres() -> list:
    return list(_load().get("_canonical_genres", []))


def get_pack(genre: str | None) -> dict:
    """题材包单一入口。无 genre / unknown / 无对应包 → {}（退化纯通用池·向后兼容·零回归）。"""
    if not genre:
        return {}
    try:
        return _load().get("packs", {}).get(str(genre).strip().lower(), {}) or {}
    except Exception:
        return {}


def get_judge_dims(genre: str | None) -> dict:
    """题材专属 judge 提问维度（声明 genre 时拼进 judge prompt）。"""
    return get_pack(genre).get("judge_dims", {}) or {}


def get_scanner(genre: str | None) -> str | None:
    """题材专属 advisory scanner 脚本名（无则 None）。"""
    return get_pack(genre).get("scanner")


def get_writer_directives(genre: str | None) -> list:
    """题材专属 writer 工艺提示（advisory）。"""
    return get_pack(genre).get("writer_directives", []) or []


def _verify() -> int:
    try:
        d = _load()
    except (OSError, json.JSONDecodeError) as e:
        print(f"[X] genre_dimension_packs.json 损坏: {e}", file=sys.stderr)
        return 2
    cg = d.get("_canonical_genres")
    packs = d.get("packs")
    if not isinstance(cg, list) or not cg:
        print("[X] _canonical_genres 缺失/非列表", file=sys.stderr)
        return 2
    if not isinstance(packs, dict):
        print("[X] packs 缺失/非 dict", file=sys.stderr)
        return 2
    # 每个 pack 的 genre 必须在 canonical 内·结构完整
    bad = []
    for g, p in packs.items():
        if g not in cg:
            bad.append(f"{g} 不在 canonical_genres")
        if not isinstance(p.get("judge_dims"), dict):
            bad.append(f"{g} 缺 judge_dims")
    if bad:
        for b in bad:
            print(f"[X] {b}", file=sys.stderr)
        return 2
    print(f"[OK] genre_dimension_packs 合法 · {len(cg)} canonical genres · {len(packs)} 包: {list(packs)}")
    return 0


def main():
    args = sys.argv[1:]
    if "--list" in args:
        d = _load()
        print("canonical genres:", canonical_genres())
        print("有专属包:", list(d.get("packs", {})))
        return 0
    if "--pack" in args:
        g = args[args.index("--pack") + 1] if len(args) > args.index("--pack") + 1 else ""
        print(json.dumps(get_pack(g), ensure_ascii=False, indent=2))
        return 0
    if "verify" in args:
        return _verify()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
