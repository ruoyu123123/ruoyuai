#!/usr/bin/env python3
"""knowledge_collector.py — 知识自动采集器（挂接蒸馏/调研/创作流水线）

三个采集入口：
  1. collect_from_distill(style_project, cluster_surface) → 蒸馏时提取题材/技法知识
  2. collect_from_research(research_cache_path) → 调研结果沉淀高价值 findings
  3. collect_from_writing(project, cluster_id, scanner_results) → 创作后成功/失败模式反馈

每个入口从已有数据中提取知识，调 knowledge_store.add() 写入本地库。
不调用任何 LLM——纯确定性提取（北极星⑤不干涉模型判断）。
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import knowledge_store as ks


def collect_from_distill(style_project: Path, cluster_id: str = None):
    """从蒸馏产物中提取知识。

    读取：
      - 作者风格_FINAL.json → 题材知识（genre_tags + 量化指纹）
      - 蒸馏进度/ch*.json → 描写技法（golden_passages + 签名手法）
      - cluster_index.json → 世界观模式（cluster 结构统计）
    """
    style_project = Path(style_project)
    book_name = style_project.name
    source = f"distill:{book_name}"

    # 1. 从作者风格档提取题材知识
    style_path = style_project / "作者风格_FINAL.json"
    if style_path.exists():
        try:
            style = json.loads(style_path.read_text(encoding="utf-8"))
            genre_tags = style.get("genre_tags", [])
            genre = genre_tags[0] if genre_tags else "unknown"

            # 提取量化风格指纹作为题材参考
            quant = style.get("quantitative", {})
            if quant:
                fingerprint_items = []
                for key in ["sentence_length", "paragraph_length_chars", "dialogue_ratio",
                            "exclamation_density", "question_density", "ellipsis_density",
                            "dash_density"]:
                    val = quant.get(key)
                    if isinstance(val, dict) and val.get("mean") is not None:
                        fingerprint_items.append(f"{key}: mean={val['mean']}")
                    elif val is not None:
                        fingerprint_items.append(f"{key}: {val}")
                if fingerprint_items:
                    ks.add("genre", genre,
                           f"[{book_name}] 量化风格指纹：" + " / ".join(fingerprint_items),
                           source=source, source_type="distill",
                           tags=genre_tags, quality=0.7)

            # 提取签名词/签名手法
            signature = style.get("signature_phrases") or style.get("signature_words")
            if isinstance(signature, list) and signature:
                ks.add("technique", "signature",
                       f"[{book_name}] 签名手法：" + "、".join(str(s) for s in signature[:10]),
                       source=source, source_type="distill",
                       tags=genre_tags + ["signature"], quality=0.6)

        except (json.JSONDecodeError, OSError):
            pass

    # 2. 从蒸馏进度提取描写技法（golden_passages）
    distill_dir = style_project / "蒸馏进度"
    if distill_dir.exists():
        golden_count = 0
        for ch_file in sorted(distill_dir.glob("ch*.json"))[:5]:
            try:
                ch_data = json.loads(ch_file.read_text(encoding="utf-8"))
                golden = ch_data.get("golden_passages") or ch_data.get("C_黄金段落")
                if isinstance(golden, list):
                    for g in golden[:2]:
                        text = g.get("text", "") if isinstance(g, dict) else str(g)
                        if len(text) > 30:
                            scene_type = g.get("scene_type", "general") if isinstance(g, dict) else "general"
                            ks.add("technique", "golden_passage",
                                   f"[{book_name}] {text[:200]}",
                                   source=source, source_type="distill",
                                   tags=[scene_type, "golden"], quality=0.8)
                            golden_count += 1
            except (json.JSONDecodeError, OSError):
                continue
        if golden_count:
            print(f"[knowledge_collector] distill: {book_name} → {golden_count} golden passages saved")

    # 3. 从 cluster_index 提取结构知识
    ci_path = style_project / "cluster_index.json"
    if ci_path.exists():
        try:
            ci = json.loads(ci_path.read_text(encoding="utf-8"))
            avg_ch = ci.get("average_chapters_per_cluster")
            avg_words = ci.get("average_words_per_cluster")
            total_ch = ci.get("total_chapters")
            if avg_ch and avg_words:
                ks.add("worldbuilding", "structure",
                       f"[{book_name}] 结构参考：{total_ch}章 / 平均{avg_ch:.1f}章/cluster / 平均{avg_words}字/cluster",
                       source=source, source_type="distill",
                       tags=[ci.get("genre", "unknown")], quality=0.5)
        except (json.JSONDecodeError, OSError):
            pass


def collect_from_research(cache_path: Path, genre: str = "unknown"):
    """从调研缓存中提取高价值 findings。

    读取 .research_cache/*.md 文件，提取有来源标注的段落。
    """
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return
    source = f"research:{cache_path.parent.name}"

    for md_file in cache_path.glob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # 提取有 URL 来源的段落（格式：- **xxx**: description [source](url)）
        findings = re.findall(r"[-*]\s*\*\*(.+?)\*\*[：:]\s*(.+?)(?:\n|$)", text)
        for title, desc in findings[:5]:
            if len(desc) > 20:
                ks.add("research", "findings",
                       f"[{title.strip()}] {desc.strip()[:300]}",
                       source=source, source_type="research",
                       tags=[genre], quality=0.6)


def collect_from_writing(project: Path, cluster_id: str, scanner_results: dict):
    """从创作后的 scanner 结果中提取模式反馈。

    成功模式：scanner PASS 的维度 → 记录什么参数组合能过
    失败模式：scanner FAIL 的维度 → 记录常见翻车点
    """
    project = Path(project)
    book_name = project.name
    source = f"writing:{book_name}_{cluster_id}"

    for scanner_name, result in scanner_results.items():
        if not isinstance(result, dict):
            continue
        verdict = result.get("verdict", "")
        if "FAIL" in verdict:
            violations = result.get("violations", [])
            for v in violations[:3]:
                msg = v.get("message", "")[:200]
                kind = v.get("kind", scanner_name)
                ks.add("technique", "failure_patterns",
                       f"[{book_name}/{cluster_id}] {kind}: {msg}",
                       source=source, source_type="writing",
                       tags=["failure", kind], quality=0.4)


def backfill_from_existing_styles():
    """回填：从已有的全部蒸馏产物中批量提取知识（一次性运行）。"""
    styles_dir = Path(__file__).resolve().parent.parent.parent / "workspace" / "styles"
    if not styles_dir.exists():
        print(f"[knowledge_collector] styles_dir not found: {styles_dir}")
        return
    count = 0
    for style_dir in sorted(styles_dir.iterdir()):
        if not style_dir.is_dir() or style_dir.name.startswith((".", "_")):
            continue
        if (style_dir / "作者风格_FINAL.json").exists() or (style_dir / "cluster_index.json").exists():
            collect_from_distill(style_dir)
            count += 1
    print(f"[knowledge_collector] backfill done: {count} styles processed")
    s = ks.stats()
    for k, v in s.items():
        print(f"  {k}: {v}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="知识自动采集器")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("backfill", help="从已有蒸馏产物回填知识库")
    d = sub.add_parser("distill", help="从指定蒸馏项目采集")
    d.add_argument("project")
    args = ap.parse_args()

    if args.cmd == "backfill":
        backfill_from_existing_styles()
    elif args.cmd == "distill":
        collect_from_distill(Path(args.project))
        s = ks.stats()
        print(f"Stats: {s}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
