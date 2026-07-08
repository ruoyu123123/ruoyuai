#!/usr/bin/env python3
"""distill_surface_runner.py — phase-1 表层蒸馏综合器（synthesize-only）。

# 🔴 2026-06-28 移除exe/gen-model梳理方向
原本本脚本"自主把 novel-distill-analyzer 派 gen-model(judge_runner.run_judge)"逐 cluster
产 48 维 surface JSON。新架构下 48 维分析（梳理/判断）由**主代理 spawn Claude agent**
(novel-distill-analyzer) 完成并把 surface JSON 落盘，本脚本只做确定性综合：

  ① （主代理已完成）每 cluster surface JSON 落盘 蒸馏进度/cluster_{key}_surface.json
  ② 读盘取 surface（生产路径 judge_fn=None）；测试/旧注入路径可传 judge_fn 回调自产
  ③ 从 surface 的 continuity 段写 衔接分析/cluster_{key}_continuity.json（arc_aggregator 消费）
  ④ 逐章投影 蒸馏进度/ch{N}.json（word_count 从 chN_metrics·dims 引 cluster 级·arc_aggregator 读）
  ⑤ _aggregate_author_profile 确定性聚合全部 surface → 作者风格.json 初版

用法：
  python distill_surface_runner.py <project_root> [--overwrite] [--max-clusters N]
退出码：0 成功 / 1 cluster_index 缺失 / 3 注入 judge_fn 回调 block 失败
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import distill_prep_cluster_text as prep  # noqa: E402


# 阶段0 修复（聚合通路·已核实 bug）：judge 的 qualitative_dims 是**扁平** dim 键，而
# consolidate_author_profile.aggregate_narrative 读的是**嵌套** cross_chapter /
# narrative_craft / narrative_fingerprint。程序驱动管线只写扁平 qualitative_dims →
# consolidate 读空 → narrative_craft/narrative_fingerprint/cross_chapter_diversity 全空
# （实测《人生长恨》三段皆空）。下面确定性把扁平 dim 归位成消费者期望的嵌套结构
# （纯映射·弱模型不参与·键名与 judge schema 完全一致只是层级不同）。
_CONSUMER_DIM_NEST = {
    "cross_chapter": ("dim16_开头类型", "dim18_章末类型"),
    "narrative_craft": ("dim33_情绪节拍图", "dim34_叙事距离变化", "dim30_留白潜台词"),
    "narrative_fingerprint": ("dim42_叙事技巧指纹", "dim43_角色行为循环",
                              "dim46_场景结构质量", "dim47_人物丰满度"),
    # 阶段1 注：A1-A5 节奏组(dim49-53)是 cluster 级序列，逐章投影会重复污染聚合，
    # 由 consolidate.aggregate_rhythm 直接读 cluster_*_surface.json 聚合，不在此投影。
}


def _project_consumer_dims(dims: dict) -> dict:
    """把 judge 扁平 qualitative_dims 归位成 consolidate 读的嵌套结构（确定性·纯映射）。"""
    out: dict = {}
    if not isinstance(dims, dict):
        return out
    for nest, keys in _CONSUMER_DIM_NEST.items():
        sub = {k: dims[k] for k in keys
               if k in dims and dims[k] not in (None, "", [])}
        if sub:
            out[nest] = sub
    return out


def _chapter_wordcount(project_root: Path, ch: int) -> int:
    """从 chN_metrics.json 取字数（distill_chapter_metrics 已产·避免重算）。"""
    mp = project_root / "蒸馏进度" / f"ch{ch}_metrics.json"
    if mp.exists():
        try:
            prof = json.loads(mp.read_text(encoding="utf-8")).get("profile", {})
            for k in ("total_chars", "cjk_chars", "word_count", "char_count"):
                if isinstance(prof.get(k), (int, float)):
                    return int(prof[k])
        except (OSError, json.JSONDecodeError):
            pass
    return 0


def run(project_root: Path, *, overwrite: bool = False, max_clusters: int | None = None,
        judge_fn=None) -> int:
    """phase-1 表层蒸馏综合（synthesize-only · 2026-06-28 移除 gen-model 自主派单）。

    judge_fn：可注入回调（测试/旧注入路径自产 surface）。**生产路径 judge_fn=None**——
    主代理先 spawn novel-distill-analyzer(Claude) 把每 cluster 48 维 surface JSON 落盘
    蒸馏进度/cluster_{key}_surface.json，本函数读盘做衔接(③)+逐章投影(④)+聚合(⑤)。
    judge_fn 为 None 且某 cluster 无 surface → 跳过该 cluster（绝不自调 gen-model）。
    """
    idx_path = project_root / "cluster_index.json"
    if not idx_path.exists():
        print(f"[surface_runner] cluster_index.json 不存在: {idx_path}", file=sys.stderr)
        return 1
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    clusters = index.get("clusters") if isinstance(index, dict) else index
    if not isinstance(clusters, list) or not clusters:
        print("[surface_runner] cluster_index 无 clusters", file=sys.stderr)
        return 1
    dist = project_root / "蒸馏进度"
    cont_dir = project_root / "衔接分析"
    dist.mkdir(parents=True, exist_ok=True)
    cont_dir.mkdir(parents=True, exist_ok=True)
    wal = dist / ".wal"
    wal.mkdir(parents=True, exist_ok=True)

    # 阶段3：题材路由——风格库声明/推断 genre 时，把题材专属 judge 维度传给 judge
    # （通用 48 维 + A1-C3 骨 always-on·题材层按 genre 激活·unknown 则纯通用）。
    genre = "unknown"
    genre_dims_note = ""
    try:
        import scaffold_genre_packs as _gp
        g = index.get("genre") if isinstance(index, dict) else None
        if not g:
            import cluster_segmenter as _cs
            g = _cs._infer_genre_from_naming(project_root, project_root.name)
        genre = g or "unknown"
        gdims = _gp.get_judge_dims(genre)
        if gdims:
            genre_dims_note = (f"本作题材={genre}·额外分析以下题材专属维度(进 qualitative_dims·"
                               f"非{genre}维度可略)：" + json.dumps(gdims, ensure_ascii=False))
    except Exception:
        pass

    done = 0
    for i, c in enumerate(clusters):
        if max_clusters and done >= max_clusters:
            break
        cid = str(c.get("cluster_id") or f"cluster_{i+1:03d}")
        key = "".join(ch for ch in cid if ch.isdigit()) or f"{i+1:03d}"
        surface_out = dist / f"cluster_{key}_surface.json"
        rng = c.get("chapter_range") or []
        if len(rng) != 2:
            continue
        start, end = int(rng[0]), int(rng[1])
        # —— 取本 cluster 48 维 surface ——
        data = None
        if surface_out.exists() and not overwrite:
            # 生产路径：surface 已由主代理 spawn novel-distill-analyzer(Claude) 落盘 → 读盘
            try:
                data = json.loads(surface_out.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                print(f"[surface_runner] {cid} surface 读取失败（跳过衔接/投影）",
                      file=sys.stderr)
                continue
        elif judge_fn is not None:
            # 注入路径（测试/旧 driver）：① 拼全章全文 → ② 调 48 维 judge 回调（block 冒泡）
            fulltext = wal / f"cluster_{key}_fulltext.txt"
            rc = prep.prep(project_root, cid, fulltext)
            if rc != 0:
                print(f"[surface_runner] {cid} 全文准备失败 rc={rc}（跳过）", file=sys.stderr)
                continue
            try:
                _params = {"CLUSTER_ID": cid, "CHAPTER_RANGE": f"ch{start}-ch{end}"}
                if genre_dims_note:                    # 阶段3：题材专属维度提示
                    _params["题材专属维度"] = genre_dims_note
                outcome = judge_fn(
                    "novel-distill-analyzer", project_root,
                    params=_params,
                    context_files=[("本 cluster 全章原文", fulltext)],
                    output_path=surface_out)
            except Exception as e:        # JudgeBlockedError 等 → block 级停
                print(f"[surface_runner] {cid} judge block 失败: {e}", file=sys.stderr)
                return 3
            data = outcome.data if hasattr(outcome, "data") else (outcome or {})
        else:
            # 生产路径但无 surface：主代理须先 spawn novel-distill-analyzer 落盘 surface
            print(f"[surface_runner] {cid} 无 surface JSON·未注入 judge_fn → 跳过"
                  f"（主代理须先 spawn novel-distill-analyzer 落盘 surface JSON）",
                  file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        # ③ continuity → 衔接分析/
        cont = data.get("continuity")
        if isinstance(cont, dict):
            (cont_dir / f"cluster_{key}_continuity.json").write_text(
                json.dumps(cont, ensure_ascii=False, indent=2), encoding="utf-8")
        # ④ 逐章投影 ch{N}.json（arc_aggregator 读 word_count + dims·cluster 级分析投到每章）
        dims = data.get("qualitative_dims") if isinstance(data, dict) else None
        nested = _project_consumer_dims(dims or {})   # 阶段0：嵌套投影供 consolidate
        for ch in range(start, end + 1):
            ch_record = {
                "chapter": ch, "cluster_id": cid,
                "word_count": _chapter_wordcount(project_root, ch),
                "cluster_surface_ref": surface_out.name,
                "qualitative_dims": dims or {},
            }
            ch_record.update(nested)   # cross_chapter/narrative_craft/narrative_fingerprint
            (dist / f"ch{ch}.json").write_text(
                json.dumps(ch_record, ensure_ascii=False, indent=2),
                encoding="utf-8")
        done += 1
    _aggregate_author_profile(project_root)
    print(f"[surface_runner] {done} cluster 表层蒸馏完成（surface + continuity + 逐章投影）")
    return 0


def _aggregate_author_profile(project_root: Path):
    """确定性聚合全部 cluster surface JSON → 作者风格.json 初版（创意字段层）。

    真 distill e2e 抓出的管线 gap：consolidate_author_profile 只**规整已存在**的
    作者风格.json（旧 Claude 流程由综合 agent 先写创意字段·程序驱动无此角色）。
    此处零 LLM 聚合：golden 每类取首个非空·anti_patterns 并集·dims 取第一 cluster +
    各 cluster free_notes 收集。consolidate 随后覆盖/补全 consumer 数值字段。"""
    dist = project_root / "蒸馏进度"
    surfaces = sorted(dist.glob("cluster_*_surface.json"))
    if not surfaces:
        return
    golden: dict = {}
    anti: dict = {}
    vs_ai: dict = {}
    dims: dict = {}
    notes: list = []
    # 阶段2：作者思维/人物刻画骨·收集**全 cluster** 原始观察(不取首个·不堆叠)→reflect 归一
    raw_decisions: list = []      # author_decisions 各 cluster 观察
    raw_character: list = []      # characterization 各 cluster 观察
    vs_ai_all: list = []          # 全 cluster vs_ai(阶段2 提炼·替代只取首个)
    for sp in surfaces:
        try:
            d = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for k, v in (d.get("golden_paragraphs") or {}).items():
            if v and not golden.get(k):
                golden[k] = v
        for k, v in (d.get("anti_patterns") or {}).items():
            if isinstance(v, list):
                anti.setdefault(k, [])
                anti[k] = sorted(set(anti[k]) | set(map(str, v)))
        if not vs_ai and isinstance(d.get("vs_ai"), dict):
            vs_ai = d["vs_ai"]
        if isinstance(d.get("vs_ai"), dict):
            vs_ai_all.append({"cluster": d.get("cluster_id"), "vs_ai": d["vs_ai"]})
        if not dims and isinstance(d.get("qualitative_dims"), dict):
            dims = d["qualitative_dims"]
        if isinstance(d.get("author_decisions"), dict):
            raw_decisions.append({"cluster": d.get("cluster_id"),
                                  "observations": d["author_decisions"]})
        if isinstance(d.get("characterization"), dict):
            raw_character.append({"cluster": d.get("cluster_id"),
                                  "observations": d["characterization"]})
        fn = d.get("free_notes")
        if fn:
            notes.append(str(fn))
    profile_path = project_root / "作者风格.json"
    existing = {}
    if profile_path.exists():
        try:
            existing = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update({
        "_source": "distill_surface_runner 聚合（创意层）+ consolidate（数值层）",
        "clusters_analyzed": len(surfaces),
        "qualitative_dims": dims or existing.get("qualitative_dims", {}),
        "golden_paragraphs": golden or existing.get("golden_paragraphs", {}),
        "anti_patterns": anti or existing.get("anti_patterns", {}),
        "vs_ai": vs_ai or existing.get("vs_ai", {}),
        "free_notes": notes or existing.get("free_notes", []),
    })
    # 阶段2：全 cluster 原始观察临时区（reflect 步 LISA 归一成决策原则清单·绝不在此堆叠）
    if raw_decisions:
        existing["_raw_decisions_observations"] = raw_decisions
    if raw_character:
        existing["_raw_characterization_observations"] = raw_character
    if vs_ai_all:
        existing["_raw_vs_ai_observations"] = vs_ai_all
    profile_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"[surface_runner] 作者风格.json 初版聚合（{len(surfaces)} surface）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--max-clusters", type=int, default=None)
    ap.add_argument("--plan-id", default=None, help="（plan 集成预留·当前未用）")
    args = ap.parse_args()
    sys.exit(run(Path(args.project_root), overwrite=args.overwrite,
                 max_clusters=args.max_clusters))


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
