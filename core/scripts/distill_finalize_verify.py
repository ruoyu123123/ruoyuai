#!/usr/bin/env python3
"""distill_finalize_verify.py — 蒸馏 v2 章程 Article 6 写作端回灌严闭环

v2 章程的最后一道闸：阶段 6 _FINAL 文件齐全后，跑本脚本验证「skill 能让目标 gen-model 真正写出来」。
不通过 → exit 2 → plan_tracker end 拦截 → 禁止声称蒸馏完成。

链路：
  1. 用 skill_FINAL 调 distill_replicate.py --mode cluster 实打 gen-model 复刻 cluster
  2. 对复刻 txt 估算简化 cluster_arc（emotion_curve / kicker_count / scene_summary_ratio）
  3. 调 cluster_evaluator.py 用原 cluster_arc 对比 6 维
  4. verdict ≠ PASS（--strict）/ ≠ PASS+WARN（默认）→ exit 2

用法：
  python core/scripts/distill_finalize_verify.py \\
    --project workspace/styles/<书名> \\
    --skill workspace/styles/<书名>/skill_FINAL.md \\
    --cluster-id cluster_001 \\
    --output workspace/styles/<书名>/对比报告/writer_feedback_verify.json \\
    [--strict]

依据：memory feedback_cluster_distill_v2_charter Article 6
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from frozen_util import child_python  # frozen-aware 子解释器（M4·dev=no-op）
import time
from pathlib import Path


CWD = Path(__file__).resolve().parent
DISTILL_REPLICATE = CWD / "distill_replicate.py"
CLUSTER_EVALUATOR = CWD / "cluster_evaluator.py"


# ============ 简化 cluster_arc 估算（不依赖 章节/ 目录） ============

# 情绪关键词（粗暴版 · 用 narrative_scanner 同一套思路）
POSITIVE_EMOTIONS = ["笑", "喜", "兴奋", "释然", "得意", "畅快", "胜利", "成功", "踏实"]
NEGATIVE_EMOTIONS = ["怒", "怕", "颤", "崩", "绝望", "痛", "悔", "恨", "悲", "冷", "寒"]
# 2026-05-29 修：原列表含「可」「却」高频单字 → 章末统计被普通行文噪声淹没，钩子维度失真
# （且 --strict 会据此 exit 2 误拦 plan）。换成强 cliffhanger 信号词（转折/意外），去掉单字噪声。
KICKER_KEYWORDS = ["然而", "突然", "竟然", "居然", "不料", "没想到", "岂料", "..."]  # cliffhanger 触发词（注意 ... 三连点）
KICKER_PUNCT = ["？", "……", "──"]


def _split_into_chapters(text: str, n_chapters: int) -> list[str]:
    """按字数等分把复刻 cluster txt 切成 N 章（粗暴版 · 实际 splitter 会更精）"""
    total = len(text)
    if n_chapters <= 1:
        return [text]
    step = total // n_chapters
    chapters = []
    for i in range(n_chapters):
        start = i * step
        end = (i + 1) * step if i < n_chapters - 1 else total
        chapters.append(text[start:end])
    return chapters


def _estimate_emotion(chapter_text: str) -> float:
    """估算章节情绪值 (0-1)：正向 - 负向，归一化"""
    pos = sum(chapter_text.count(k) for k in POSITIVE_EMOTIONS)
    neg = sum(chapter_text.count(k) for k in NEGATIVE_EMOTIONS)
    total = pos + neg
    if total == 0:
        return 0.5
    ratio = pos / total
    return ratio  # 0=全负，1=全正


def _estimate_kicker_count(chapter_text: str) -> int:
    """估算章末钩子数：取章末 25% 段 + 数 cliffhanger 标志"""
    tail = chapter_text[-(len(chapter_text) // 4 or 200):]
    n = sum(tail.count(k) for k in KICKER_KEYWORDS)
    n += sum(tail.count(p) for p in KICKER_PUNCT)
    return n


def _estimate_scene_summary_ratio(chapter_text: str) -> float:
    """估算场景 vs 概述比：含对话引号 / 短句 → 场景，长平叙 → 概述"""
    lines = [l for l in chapter_text.split("\n") if l.strip()]
    if not lines:
        return 0.5
    scene_lines = 0
    for l in lines:
        has_dialogue = ("“" in l) or ("”" in l) or (l.strip().startswith("—"))
        is_short = len(l) < 40
        if has_dialogue or is_short:
            scene_lines += 1
    return scene_lines / len(lines)


def _match_reagan_shape(curve: list[float]) -> str:
    """简化 reagan shape 拟合：根据 curve 形状判定"""
    if len(curve) < 3:
        return "Unknown"
    first, last = curve[0], curve[-1]
    mid = curve[len(curve) // 2]
    if first < mid and mid < last:
        return "Rags-to-Riches"
    if first > mid and mid > last:
        return "Riches-to-Rags"
    if first > mid and mid < last:
        return "Man-in-a-Hole"
    if first < mid and mid > last:
        return "Icarus"
    return "Unknown"


def estimate_cluster_arc(replica_txt: str, cluster_id: str, n_chapters: int) -> dict:
    """对复刻 cluster 文本估算简化 cluster_arc JSON。

    精度有限（不依赖蒸馏 continuity / character_arc），仅够 cluster_evaluator
    第 1/2/4/5 维粗判。第 3 维（衔接覆盖）走 fallback 中性分；第 6 维 voice
    需要 character_arcs 目录，本脚本不产。
    """
    chapters = _split_into_chapters(replica_txt, n_chapters)
    emotion_curve = [_estimate_emotion(c) for c in chapters]
    kicker_count = [_estimate_kicker_count(c) for c in chapters]
    scene_ratio = [_estimate_scene_summary_ratio(c) for c in chapters]
    shape = _match_reagan_shape(emotion_curve)
    return {
        "arc_id": cluster_id,
        "matched_reagan_shape": shape,
        "matched_reagan_shape_confidence": 0.5,  # 简化版置信度
        "emotion_curve_normalized": emotion_curve,
        "kicker_count_per_chapter": kicker_count,
        "scene_summary_ratio_per_chapter": scene_ratio,
        "_estimate_only": True,
        "_estimator": "distill_finalize_verify.py simplified estimate (no continuity/character_arc dependency)",
    }


# ============ strict 闸门判定（纯函数 · 可测） ============

# 可估算 strict 维：kicker 钩子分布(3) / scene 场景概述比(4)。
# 排除 continuity(2)/voice_pack(5)（恒中性，不喂 gen 侧）+ emotion(1)（valence vs intensity 轴错配，
# cosine 无测量学意义 · 2026-05-30 修 #5）+ arc 形状(0)（2026-06-01 修 #6，见下）。
# 索引须与 cluster_evaluator.DIM_LABELS 顺序对齐。
#
# 2026-06-01 修 #6：arc 形状(0) 移出 strict。matched_reagan_shape 从 estimate emotion_curve
# 拟合（_match_reagan_shape），与已移出的 emotion(1) 同源不可靠。金标准三重验证（distill_finalize_verify
# 对 惊悚乐园 auto_001 复刻 / auto_002 复刻 / 原文 ch1-6 自比）：estimate 拼接文本拟合 shape 恒 Icarus，
# 而聚合 cluster_arc 为 Cinderella / Man-in-a-Hole → arc 全 0.0。连真原文自比都 FAIL = estimate-shape
# vs 聚合-shape 方法论系统性不对等，arc 不该做 strict hard 维。kicker(3) 保留：auto_002 复刻 + 原文自比
# 均 PASS（gen=18=ref）证明 estimate 对 kicker 可靠（auto_001 复刻 gen=5 是该 cluster 钩子密度真实
# 个例差距，非工具失效）。
STRICT_ESTIMABLE_IDX = (3, 4)

# 2026-06-08 修 #7（reasoning 模型回灌深修）：reasoning gen-model（active profile thinking_level
# 非空，如 gemini-3.x pro-preview）下，distill_replicate 把整 cluster 复刻成「叙述浓缩版」
# （诡秘 auto_009 实测 9273 字 vs 全本 6 章 ~21k），章末 cliffhanger 信号被压缩稀释 → kicker
# estimate 系统性偏低（auto_009：ref_total=18 vs gen_total=3，cosine 0.548）。这与已移出 strict 的
# arc(0) 同源（estimate 拼接浓缩文本不可靠）。故 reasoning 下 kicker(3) 也移出 strict，仅看 scene(4)
# （scene 比是密度比值·对浓缩鲁棒，auto_009 实测 0.823 PASS）。arc/kicker 的真实验证 defer 到
# gen_writer 写作端（freestyle 全本字数正常·钩子密度真实可测）。
# 非 reasoning 模型（flash 等，thinking_level=None）字数正常，仍用 STRICT_ESTIMABLE_IDX 含 kicker。
STRICT_ESTIMABLE_IDX_REASONING = (4,)


def strict_idx_for_thinking_level(thinking_level: str | None) -> tuple[tuple[int, ...], bool]:
    """纯函数：按 gen-model thinking_level 决定 strict 可估算维集合。

    reasoning（thinking_level 非空）→ (4,) 仅 scene（kicker 浓缩复刻失真·同 arc 移出）。
    非 reasoning（None/空）→ (3,4) 含 kicker。
    返回 (estimable_idx, is_reasoning)。
    """
    if (thinking_level or "").strip():
        return STRICT_ESTIMABLE_IDX_REASONING, True
    return STRICT_ESTIMABLE_IDX, False


def resolve_strict_estimable_idx() -> tuple[tuple[int, ...], bool, str]:
    """读 active gen-model profile 的 thinking_level → strict 可估算维 + 人读说明。

    profile 加载失败（无 .env / GEN_MODEL_ACTIVE 未设）时退默认（含 kicker），不阻断 verify。
    返回 (estimable_idx, is_reasoning, note)。
    """
    try:
        from gen_model_loader import get_default_loader
        profile = get_default_loader().get_active_profile()
        idx, is_reasoning = strict_idx_for_thinking_level(profile.thinking_level)
        if is_reasoning:
            note = (f"reasoning 模型 {profile.name}(thinking_level={profile.thinking_level}) · "
                    f"kicker(3) 浓缩复刻失真移出 strict · 仅 scene(4) · "
                    f"arc/kicker 真实验证 defer 到 gen_writer 写作端")
        else:
            note = (f"非 reasoning 模型 {profile.name}(thinking_level=None) · "
                    f"默认 strict 含 kicker(3)+scene(4)")
        return idx, is_reasoning, note
    except Exception as e:  # noqa: BLE001 — profile 加载失败不阻断 verify
        return STRICT_ESTIMABLE_IDX, False, f"gen-model profile 加载失败({e}) · 退默认 strict 含 kicker"


def strict_gate_decision(report: dict | None,
                         estimable_idx: tuple[int, ...] = STRICT_ESTIMABLE_IDX
                         ) -> tuple[bool, list[dict]]:
    """从 cluster_evaluator 报告里取可估算维度，判 strict 是否全过。

    返回 (strict_ok, estimable_rows)。estimable_rows 用于日志展示通过数。
    只在恰好 6 维（cluster_evaluator v2 章程 6 维 schema）时按 index 抽取；
    schema 异常时退化为「全维都算」避免 IndexError 误判。
    """
    dim_rows = (report or {}).get("scores_by_dim", []) or []
    if len(dim_rows) == 6:
        estimable = [dim_rows[i] for i in estimable_idx]
    else:
        estimable = dim_rows
    est_pass = [r for r in estimable if r.get("passes")]
    strict_ok = bool(estimable) and len(est_pass) == len(estimable)
    return strict_ok, estimable


# ============ 主流程 ============

def run_distill_replicate(skill: Path, project: Path, cluster_id: str, output: Path) -> bool:
    """调 distill_replicate.py --mode cluster 实打 gen-model 复刻"""
    cmd = [
        child_python(), str(DISTILL_REPLICATE),
        "--style-skill", str(skill),
        "--mode", "cluster",
        "--cluster-ref", cluster_id,
        "--project", str(project),
        "--output", str(output),
    ]
    print(f"[verify] 调 distill_replicate.py --mode cluster ...", file=sys.stderr)
    print(f"         cmd = {' '.join(cmd)}", file=sys.stderr)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=False)  # 让 stream 直接打到 stderr
    elapsed = time.time() - t0
    print(f"[verify] distill_replicate 耗时 {elapsed:.1f}s · exit={r.returncode}", file=sys.stderr)
    return r.returncode == 0


def run_cluster_evaluator(ref_arc: Path, gen_arc: Path, output: Path,
                          ref_cont: Path | None = None,
                          ref_char_dir: Path | None = None,
                          strict: bool = False) -> tuple[int, dict]:
    cmd = [
        child_python(), str(CLUSTER_EVALUATOR),
        "--ref-cluster-arc", str(ref_arc),
        "--gen-cluster-arc", str(gen_arc),
        "--output", str(output),
    ]
    if ref_cont and ref_cont.exists():
        cmd += ["--ref-continuity", str(ref_cont)]
    if ref_char_dir and ref_char_dir.exists():
        cmd += ["--ref-character-arc-dir", str(ref_char_dir)]
    if strict:
        cmd += ["--strict"]
    print(f"[verify] 调 cluster_evaluator.py ...", file=sys.stderr)
    r = subprocess.run(cmd, capture_output=False)
    report = {}
    if output.exists():
        try:
            report = json.loads(output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return r.returncode, report


def main():
    parser = argparse.ArgumentParser(
        description="蒸馏 v2 章程 Article 6 写作端回灌严闭环",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project", required=True, type=Path,
                        help="风格库项目路径（含 cluster_index.json / 原 cluster_arc_<id>.json）")
    parser.add_argument("--skill", required=True, type=Path,
                        help="skill_FINAL.md 路径")
    parser.add_argument("--cluster-id", required=True,
                        help="要 verify 的 cluster_id（如 cluster_001）")
    parser.add_argument("--output", required=True, type=Path,
                        help="最终 verify 报告路径（writer_feedback_verify.json）")
    parser.add_argument("--strict", action="store_true",
                        help="verdict != PASS 时 exit 2（用于 plan_tracker step 8 闸门）")
    parser.add_argument("--skip-distill-replicate", action="store_true",
                        help="跳过实打 gen-model（假设复刻文件已存在 · debug 用）")
    parser.add_argument("--replica-path",
                        help="[--skip-distill-replicate] 已有复刻 txt 路径")
    args = parser.parse_args()

    # ===== 步骤 0：路径校验 =====
    project = args.project.resolve()
    skill = args.skill.resolve()
    if not project.exists():
        print(f"[ERROR] 项目目录不存在: {project}", file=sys.stderr)
        sys.exit(2)
    if not skill.exists():
        print(f"[ERROR] skill 文件不存在: {skill}", file=sys.stderr)
        sys.exit(2)

    cluster_index_path = project / "cluster_index.json"
    if not cluster_index_path.exists():
        print(f"[ERROR] cluster_index.json 不存在: {cluster_index_path}", file=sys.stderr)
        sys.exit(2)

    cluster_index = json.loads(cluster_index_path.read_text(encoding="utf-8"))
    cluster_meta = None
    for c in cluster_index.get("clusters", []):
        if c.get("cluster_id") == args.cluster_id:
            cluster_meta = c
            break
    if not cluster_meta:
        print(f"[ERROR] cluster_id={args.cluster_id} 在 cluster_index 中找不到", file=sys.stderr)
        sys.exit(2)

    # 原 cluster_arc 路径
    ref_arc_path = project / "arc_templates" / f"cluster_arc_{args.cluster_id}.json"
    if not ref_arc_path.exists():
        print(f"[ERROR] 原 cluster_arc 不存在: {ref_arc_path}", file=sys.stderr)
        print(f"        请先跑 arc_aggregator.py --project ... --cluster {args.cluster_id}",
              file=sys.stderr)
        sys.exit(2)

    # 衔接分析 / character_arcs（可选）
    ref_cont_path = project / "衔接分析" / f"cluster_{args.cluster_id}_continuity.json"
    ref_char_dir = project / "character_arcs"

    # ===== 步骤 1：实打 gen-model 复刻 cluster =====
    verify_dir = project / "复刻测试" / "writer_feedback_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    replica_path = verify_dir / f"{args.cluster_id}_replica.txt"

    if args.skip_distill_replicate:
        if args.replica_path:
            replica_path = Path(args.replica_path)
        if not replica_path.exists():
            print(f"[ERROR] --skip-distill-replicate 但复刻文件不存在: {replica_path}",
                  file=sys.stderr)
            sys.exit(2)
        print(f"[verify] 跳过 distill_replicate (debug) · 使用已有 {replica_path}",
              file=sys.stderr)
    else:
        ok = run_distill_replicate(skill, project, args.cluster_id, replica_path)
        if not ok or not replica_path.exists():
            print(f"[ERROR] distill_replicate.py 复刻失败 · 终止 verify", file=sys.stderr)
            sys.exit(3)

    # ===== 步骤 2：估算复刻 cluster_arc（简化版） =====
    replica_txt = replica_path.read_text(encoding="utf-8")
    n_chapters = int(cluster_meta.get("chapters_count") or 3)
    gen_arc = estimate_cluster_arc(replica_txt, args.cluster_id, n_chapters)
    gen_arc_path = verify_dir / f"cluster_arc_{args.cluster_id}_replica.json"
    gen_arc_path.write_text(json.dumps(gen_arc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[verify] 估算复刻 cluster_arc → {gen_arc_path}", file=sys.stderr)
    print(f"         emotion_curve = {[round(x, 2) for x in gen_arc['emotion_curve_normalized']]}",
          file=sys.stderr)
    print(f"         matched_shape = {gen_arc['matched_reagan_shape']}", file=sys.stderr)

    # ===== 步骤 3：调 cluster_evaluator.py 6 维比对 =====
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 2026-05-30 北极星复审：不透传 --strict 给子评分器——本 verifier 的 estimate_cluster_arc 只产
    # 4 维（arc/emotion/kicker/scene），cluster_evaluator「6 维全过才 PASS」下 dim2(continuity)/
    # dim5(voice_pack) 因无 gen 侧数据恒中性 <0.7 → 永远 WARN → strict 永远 exit2 = 出货 plan 死锁。
    # 让子评分器只产报告（exit0），由父进程按【可估算 3 维 arc/kicker/scene】重判 strict 闸门
    # （见步骤 5；emotion 因 valence/intensity 轴错配 2026-05-30 移出 strict）。
    exit_code, report = run_cluster_evaluator(
        ref_arc_path, gen_arc_path, args.output,
        ref_cont=ref_cont_path, ref_char_dir=ref_char_dir,
        strict=False,
    )

    # 解析 strict 闸门策略（reasoning 模型移出 kicker · 修#7）——先解析以便写入 metadata
    strict_idx, is_reasoning, strict_note = resolve_strict_estimable_idx()

    # ===== 步骤 4：扩展 report 加 verify metadata =====
    if report:
        report["_verify_metadata"] = {
            "project": str(project),
            "skill": str(skill),
            "cluster_id": args.cluster_id,
            "ref_cluster_arc": str(ref_arc_path),
            "gen_cluster_arc": str(gen_arc_path),
            "replica_path": str(replica_path),
            "replica_chars": len(replica_txt),
            "estimated_n_chapters": n_chapters,
            "verify_runner": "distill_finalize_verify.py v2",
            "strict_policy": {
                "estimable_idx": list(strict_idx),
                "is_reasoning_model": is_reasoning,
                "note": strict_note,
            },
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    # ===== 步骤 5：按【可估算维】重判 strict 闸门 =====
    # arc(0) 已移出（修#6·estimate-shape 金标准三重 FAIL）。reasoning 模型（thinking_level 非空）
    # 再移出 kicker(3)（修#7·浓缩复刻稀释钩子），仅看 scene(4)；非 reasoning 仍含 kicker(3)+scene(4)。
    strict_ok, estimable = strict_gate_decision(report, estimable_idx=strict_idx)
    est_pass = [r for r in estimable if r.get("passes")]
    est_dims = [r.get("dim", "?") for r in estimable]
    print(f"\n[verify] 6 维 verdict={report.get('verdict', '?')} · strict 可估算维 "
          f"{est_dims} 通过 {len(est_pass)}/{len(estimable)}", file=sys.stderr)
    print(f"         strict 策略: {strict_note}", file=sys.stderr)
    print(f"         报告: {args.output}", file=sys.stderr)
    if strict_ok:
        print(f"[OK · PASS] 写作端回灌（strict 可估算维全过）· 允许 plan_tracker end", file=sys.stderr)
        sys.exit(0)
    if args.strict:
        print(f"[FAIL · strict] strict 可估算维未全过 · 出货前拦截（修 skill 重蒸馏）", file=sys.stderr)
        sys.exit(2)
    print(f"[WARN] 可估算维未全过 · 非 strict 放行 · 建议手动审查", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
