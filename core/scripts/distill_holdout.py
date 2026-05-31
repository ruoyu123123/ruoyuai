#!/usr/bin/env python3
"""distill_holdout.py — 蒸馏留出验证 · 防 metric overfit / in-context reward hacking
（2026-05-31 · 北极星①⑤⑥）

治什么病
--------
蒸馏闭环优化 skill 时（v0→v8…）若**只看 tuning cluster 的 SFS 分**，会过拟合：
skill 被一路调到「专门讨好那几个 tuning cluster 的特例」——tuning SFS 涨得很漂亮，
但换一个**没参与优化**的 cluster（holdout）就掉分。这就是经典的 metric overfitting /
in-context reward hacking：优化目标（tuning SFS）和真实目标（任意 cluster 都像作者）
脱钩，分数虚高、泛化崩。

机器学习里防这个的标准手法就是**留出验证集（holdout）**：把一部分样本锁起来不参与
调参，只在最后拿来量「泛化差距」。本脚本把这套搬到蒸馏闭环：
  · 蒸馏时把 cluster 池切成 **tuning 集**（参与 skill 优化）+ **holdout 集**（锁住不碰）；
  · 分别算 tuning 平均 SFS 与 holdout 平均 SFS；
  · **落差 = tuning_mean - holdout_mean**。落差大 = skill 过拟合到 tuning 特例 → advisory。

关键纪律（与任务书 + 北极星一致）
--------------------------------
  1. **advisory only**：落差大只 print 警示 + 写进 report，**绝不 hard_gate / 绝不 exit 非 0
     阻断**（北极星⑤·顾问非法官·不干涉模型创作判断）。
  2. **零模型推理 / 零网络 / 零新依赖**：纯 stdlib。SFS 分要么由调用方传入（已在别处跑出），
     要么本脚本**复用现有 style_evaluator.evaluate** 给 replica 打分——脚本自身不调
     gen-model / 不调 LLM（那些是上游 distill_replicate 的事）。
  3. **复用 SFS 评分**：不另造打分逻辑——直接 import style_evaluator.evaluate（与正式
     写作端、distill_track 同一把尺，避免「换一把尺量出假落差」）。
  4. **确定性留出切分**：split 用固定 seed 的洗牌，可复现、可单测（同一池同一 seed →
     同一切分）。也允许显式 --holdout-ref 钉死哪些 cluster 留出（IP 改编/指定场景）。
  5. **挂 distill_track**：record 跑完可把 {tuning_mean, holdout_mean, gap, overfit}
     塞进 distill_track 的 ledger（基线 ledger 加 holdout 列），与防打转件共用一个账本。
  6. **env HOLDOUT_SFS_MODE 默认 active**（用户：默认关闭写它干什么）。active=算落差+附
     advisory；shadow=只算只记录不提 advisory 顶层；off=完全不算（逃生口）。

用法
----
  # A) 已在别处跑出 SFS 分 → 直接传分算落差（最常用 · 零 IO）
  python core/scripts/distill_holdout.py record \\
    --project workspace/styles/蛊真人 --skill-version v8 \\
    --tuning-sfs 72.1 --tuning-sfs 70.5 \\
    --holdout-sfs 61.3 --holdout-sfs 59.8 \\
    --track   # 可选：同时写进 distill_track 基线 ledger 的 holdout 列

  # B) 从 style_evaluator 报告 JSON 读 sfs_quick（tuning / holdout 各若干份报告）
  python core/scripts/distill_holdout.py record --project ... --skill-version v8 \\
    --tuning-report t1.json --tuning-report t2.json \\
    --holdout-report h1.json

  # C) 给 replica + 参考原文，本脚本复用 style_evaluator 现打分再算落差
  python core/scripts/distill_holdout.py score-and-record --project ... --skill-version v8 \\
    --tuning-pair t1_replica.txt:t1_ref.txt --holdout-pair h1_replica.txt:h1_ref.txt

  # D) 确定性切分：把 cluster 池切成 tuning / holdout（选哪些留出 · 可复现）
  python core/scripts/distill_holdout.py split \\
    --cluster cluster_001 --cluster cluster_002 --cluster cluster_003 \\
    --cluster cluster_004 --holdout-frac 0.25 --seed 42
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path

# 复用同目录现有件（注入手法与 distill_track / distill_replicate 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import distill_track as _dt  # 复用统计纯函数 + ledger 读写（挂 holdout 列）
    _HAVE_DT = True
except Exception:  # pragma: no cover - 退化：自带 mean，仍可跑落差
    _dt = None
    _HAVE_DT = False


# ---- 过拟合判定常量（保守默认 · 与 distill_track 容差哲学同源：宁可漏报不误报）----
# SFS 百分制下 tuning−holdout 落差 < 此值视作噪声/正常波动，绝不报过拟合。
DEFAULT_GAP_FLOOR = 4.0
# 落差 / tuning 均值 的相对阈值兜底（绝对值 floor + 相对比例双卡，避免高分段误判）。
DEFAULT_GAP_REL = 0.08
# 最少 holdout 样本数：少于此值 holdout 均值不可信 → 不下过拟合结论（only 提示样本不足）。
MIN_HOLDOUT_N = 1


# ============================================================
# 纯函数层（全部可单测 · 零 IO / 零模型 / 零网络）
# ============================================================

def _mean(xs):
    """算数均值（复用 distill_track.mean；不可用时本地兜底）。空 → 0.0。"""
    if _HAVE_DT:
        return _dt.mean(xs)
    xs = [float(x) for x in xs]
    return sum(xs) / len(xs) if xs else 0.0


def split_clusters(cluster_ids, holdout_frac=0.25, holdout_n=None,
                   holdout_refs=None, seed=42):
    """把 cluster 池确定性切成 tuning / holdout 两组（纯函数 · 可复现 · 可单测）。

    优先级：
      1. holdout_refs（显式钉死哪些 cluster 留出）→ 按这个分（IP 改编/指定场景）。
         只保留确实在池子里的 ref（防拼写错），未命中的忽略。
      2. holdout_n（留出固定个数）。
      3. holdout_frac（留出比例 · 默认 0.25）→ n = round(frac × 池大小)，
         至少 1 个（池 ≥2 时）、至多「池大小 − 1」（tuning 不能空）。

    切分用固定 seed 的洗牌 → 同池同 seed 同切分（可复现 · 可单测）。
    返回 {"tuning": [...], "holdout": [...], "seed": seed, "strategy": str}。
    池大小 < 2 → 无法留出，holdout=[]（调用方据此判定不适用）。
    """
    ids = [str(c) for c in (cluster_ids or [])]
    # 去重保序（同一 cluster 重复传入不该既在 tuning 又在 holdout）
    seen = set()
    uniq = []
    for c in ids:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    n_total = len(uniq)

    if holdout_refs:
        hold = [c for c in uniq if c in set(str(x) for x in holdout_refs)]
        tune = [c for c in uniq if c not in set(hold)]
        return {"tuning": tune, "holdout": hold, "seed": seed,
                "strategy": "explicit_refs"}

    if n_total < 2:
        # 池太小：无法留出（留出后 tuning 会空 / holdout 不可信）。
        return {"tuning": list(uniq), "holdout": [], "seed": seed,
                "strategy": "too_small"}

    if holdout_n is not None:
        k = int(holdout_n)
    else:
        k = round(float(holdout_frac) * n_total)
    # 钳到 [1, n_total-1]：至少留 1 个 holdout、至少留 1 个 tuning。
    k = max(1, min(k, n_total - 1))

    shuffled = list(uniq)
    random.Random(seed).shuffle(shuffled)
    hold = sorted(shuffled[:k])
    tune = sorted(shuffled[k:])
    strat = "explicit_n" if holdout_n is not None else "frac"
    return {"tuning": tune, "holdout": hold, "seed": seed, "strategy": strat}


def compute_gap(tuning_scores, holdout_scores):
    """算 tuning 与 holdout 的 SFS 均值落差（纯函数）。

    gap = tuning_mean − holdout_mean。
      gap > 0 : holdout 比 tuning 低（过拟合方向——分调到 tuning 特例上了）。
      gap ≤ 0 : holdout 不低于 tuning（泛化良好 · 没有过拟合迹象）。

    返回 {tuning_mean, holdout_mean, gap, tuning_n, holdout_n}。
    任一组空 → 对应均值 None、gap None（调用方据此判定不可比）。
    """
    t = [float(x) for x in (tuning_scores or [])]
    h = [float(x) for x in (holdout_scores or [])]
    tm = _mean(t) if t else None
    hm = _mean(h) if h else None
    gap = (tm - hm) if (tm is not None and hm is not None) else None
    return {
        "tuning_mean": round(tm, 3) if tm is not None else None,
        "holdout_mean": round(hm, 3) if hm is not None else None,
        "gap": round(gap, 3) if gap is not None else None,
        "tuning_n": len(t),
        "holdout_n": len(h),
    }


def overfit_threshold(tuning_mean, gap_floor=DEFAULT_GAP_FLOOR,
                      gap_rel=DEFAULT_GAP_REL):
    """过拟合判定阈值（分数单位 · 绝对 floor 与相对比例取大者）。

    threshold = max(gap_floor, gap_rel × tuning_mean)
    高分段（tuning_mean 大）时相对比例放宽——80 分掉 5 分比 50 分掉 5 分更可能是噪声。
    tuning_mean 缺失 → 退到 gap_floor（保守）。
    """
    if tuning_mean is None:
        return float(gap_floor)
    return max(float(gap_floor), float(gap_rel) * float(tuning_mean))


def detect_overfit(tuning_scores, holdout_scores,
                   gap_floor=DEFAULT_GAP_FLOOR, gap_rel=DEFAULT_GAP_REL,
                   min_holdout_n=MIN_HOLDOUT_N):
    """纯函数：算落差并判定是否过拟合（落差超阈值 = 疑似过拟合 · advisory）。

    返回 dict：
      {
        "overfit": bool,          # gap > threshold 才 True（只朝 holdout 偏低方向）
        "applicable": bool,       # 两组都有分且 holdout 样本够 才可判
        "gap": float|None,        # tuning_mean − holdout_mean（正=holdout 偏低）
        "threshold": float,       # 本次过拟合阈值
        "tuning_mean": ..., "holdout_mean": ...,
        "tuning_n": int, "holdout_n": int,
        "verdict": "overfit"|"healthy"|"holdout_better"|"insufficient",
        "note": str,
      }

    holdout 样本不足（< min_holdout_n）或任一组空 → applicable=False，绝不下过拟合结论
    （样本不足时判过拟合 = 自己制造误报打转，违背「没调查没发言权」）。
    """
    g = compute_gap(tuning_scores, holdout_scores)
    threshold = overfit_threshold(g["tuning_mean"], gap_floor=gap_floor,
                                  gap_rel=gap_rel)
    base = {
        "gap": g["gap"], "threshold": round(threshold, 3),
        "tuning_mean": g["tuning_mean"], "holdout_mean": g["holdout_mean"],
        "tuning_n": g["tuning_n"], "holdout_n": g["holdout_n"],
    }

    if g["gap"] is None or g["holdout_n"] < int(min_holdout_n) or g["tuning_n"] < 1:
        base.update({
            "overfit": False, "applicable": False, "verdict": "insufficient",
            "note": (f"样本不足（tuning {g['tuning_n']} / holdout {g['holdout_n']}，"
                     f"需 holdout ≥ {min_holdout_n}）·不下过拟合结论（没调查没发言权）"),
        })
        return base

    gap = g["gap"]
    if gap <= 0:
        # holdout 不低于 tuning：泛化良好（或 holdout 反而更高）。
        verdict = "holdout_better" if gap < 0 else "healthy"
        note = (f"holdout 均值 {g['holdout_mean']} ≥ tuning {g['tuning_mean']}"
                f"（落差 {gap:+.2f}）·无过拟合迹象·泛化良好")
        return {**base, "overfit": False, "applicable": True,
                "verdict": verdict, "note": note}

    if gap > threshold:
        note = (f"holdout 比 tuning 低 {gap:.2f}（超阈值 {threshold:.2f}）"
                f"·疑似过拟合：skill 可能被调到讨好 tuning cluster 的特例"
                f"·advisory·不阻断")
        return {**base, "overfit": True, "applicable": True,
                "verdict": "overfit", "note": note}

    note = (f"holdout 比 tuning 低 {gap:.2f}（在阈值 {threshold:.2f} 内）"
            f"·视作正常波动·非过拟合")
    return {**base, "overfit": False, "applicable": True,
            "verdict": "healthy", "note": note}


# ============================================================
# env 模式（默认 active · 与 av_judge / L3a 同款 idiom）
# ============================================================

def holdout_mode() -> str:
    """HOLDOUT_SFS_MODE：默认 active（用户：默认关闭写它干什么 · 北极星纪律 2）。

    active：算落差 + 把过拟合 advisory 提到 report 顶层（消费方可见）。
    shadow：算落差但只记录（不提 advisory 顶层 · 校准期用）。
    off   ：完全不算（无 SFS / 离线逃生口）。
    空 / 非法值 → active。
    """
    m = (os.environ.get("HOLDOUT_SFS_MODE") or "active").strip().lower()
    return m if m in ("shadow", "active", "off") else "active"


def build_holdout_report(tuning_scores, holdout_scores, skill_version=None,
                         tuning_refs=None, holdout_refs=None,
                         gap_floor=DEFAULT_GAP_FLOOR, gap_rel=DEFAULT_GAP_REL,
                         min_holdout_n=MIN_HOLDOUT_N, mode=None) -> dict:
    """组装 holdout 落差 report（纯函数 · 供 record 写文件 / 挂 ledger / 测试共用）。

    mode=off → 仍返回结构但 advisory_issues 空、judged=False（不算判决）。
    """
    mode = mode or holdout_mode()
    det = detect_overfit(tuning_scores, holdout_scores, gap_floor=gap_floor,
                          gap_rel=gap_rel, min_holdout_n=min_holdout_n)
    advisory_issues = []
    if mode != "off" and det.get("applicable") and det.get("overfit"):
        advisory_issues.append({
            "code": "HOLDOUT_SFS_GAP_OVERFIT",
            "gate_level": "advisory",  # 永远 advisory（北极星⑤ · 顾问非法官）
            "message": det["note"],
            "gap": det["gap"], "threshold": det["threshold"],
            "tuning_mean": det["tuning_mean"], "holdout_mean": det["holdout_mean"],
        })
    return {
        "schema": "distill_holdout/1",
        "skill_version": skill_version,
        "mode": mode,
        "tuning_refs": list(tuning_refs) if tuning_refs else [],
        "holdout_refs": list(holdout_refs) if holdout_refs else [],
        "tuning_scores": [round(float(s), 3) for s in (tuning_scores or [])],
        "holdout_scores": [round(float(s), 3) for s in (holdout_scores or [])],
        "detection": det,
        # active 才把 advisory 提顶层；shadow 留空（只藏在 detection 里 · 校准期不打扰）。
        "advisory_issues": advisory_issues if mode == "active" else [],
        "judged": mode != "off",
        "note": "全 advisory · 不改 SFS 判决 · 不进 hard_gate（防 metric overfit · 顾问非法官）",
    }


# ============================================================
# IO 层（读分 / SFS 打分 / 写 report / 挂 ledger · 全 advisory 永不阻断）
# ============================================================

def _score_from_report(path) -> float | None:
    """从 style_evaluator 报告 JSON 抽 sfs_quick（兜底 programmatic_score.total）。

    与 distill_track._scores_from_report 同口径——同一把尺读分。
    """
    if _HAVE_DT:
        return _dt._scores_from_report(Path(path))
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 报告读不出 sfs({path}): {e}", file=sys.stderr)
        return None
    if isinstance(d.get("sfs_quick"), (int, float)):
        return float(d["sfs_quick"])
    ps = d.get("programmatic_score") or {}
    if isinstance(ps.get("total"), (int, float)):
        return float(ps["total"])
    print(f"[WARN] 报告无 sfs_quick/programmatic_score.total: {path}", file=sys.stderr)
    return None


def _score_pair(replica_path, ref_path) -> float | None:
    """复用 style_evaluator.evaluate 给一对 (replica, ref) 现打 SFS 分。

    脚本自身不调 gen-model / LLM——只调 style_evaluator 的**确定性程序化评分**
    （sfs_quick = programmatic_score.total）。LLM 评分段不在这里跑（保零网络纪律）。
    导入失败 / 文件缺失 → 返回 None（advisory 层鲁棒 · 不抛不阻断）。
    """
    try:
        import style_evaluator as _se
    except Exception as e:  # pragma: no cover - 环境缺 numpy/scipy 时降级
        print(f"[WARN] 无法 import style_evaluator（{e}）· 改用 --*-report / --*-sfs 传分",
              file=sys.stderr)
        return None
    try:
        ref_text = Path(ref_path).read_text(encoding="utf-8")
        gen_text = Path(replica_path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"[WARN] 读 pair 失败（{replica_path} / {ref_path}）: {e}", file=sys.stderr)
        return None
    try:
        report = _se.evaluate(ref_text, gen_text)
        v = report.get("sfs_quick")
        return float(v) if isinstance(v, (int, float)) else None
    except Exception as e:  # pragma: no cover - 评分内部异常不阻断
        print(f"[WARN] style_evaluator.evaluate 失败: {e}", file=sys.stderr)
        return None


def report_path(project, skill_version=None) -> Path:
    sub = Path(project) / _dt.LEDGER_SUBDIR if _HAVE_DT else Path(project) / "复刻测试"
    name = f"holdout_{skill_version}.json" if skill_version else "holdout_report.json"
    return sub / name


def save_report(project, skill_version, report) -> Path:
    p = report_path(project, skill_version)
    p.parent.mkdir(parents=True, exist_ok=True)
    if _HAVE_DT and getattr(_dt, "_HAVE_ATOMIC", False):
        import atomic_json
        atomic_json.atomic_write_json(p, report)
    else:
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return p


def _track_holdout(args, report) -> None:
    """可选：把 holdout 落差挂进 distill_track 的基线 ledger（加 holdout 列）。

    用 holdout 均值当该条 entry 的 SFS（holdout 才是泛化真分），并把过拟合信息塞进
    entry 的 holdout 字段。与防打转件共用一个账本——dashboard 一眼看见过拟合标记。
    永不阻断（distill_track 本身 advisory · 失败仅 warn）。
    """
    if not _HAVE_DT:
        print("[WARN] distill_track 不可用 · 跳过 --track（落差 report 仍已写出）",
              file=sys.stderr)
        return
    det = report.get("detection", {})
    holdout_scores = report.get("holdout_scores") or []
    if not holdout_scores:
        print("[WARN] 无 holdout 分 · 跳过挂 ledger", file=sys.stderr)
        return
    git_sha = (args.git_sha or _dt.current_git_sha(Path(args.project)))
    entry = _dt.build_entry(
        skill_version=args.skill_version,
        cluster_ref="holdout",  # 专列：与 tuning cluster 的 ref 区分开
        sfs_scores=holdout_scores,
        model=getattr(args, "model", None),
        git_sha=git_sha,
    )
    # 挂 holdout 落差信息（distill_track entry 是普通 dict · 加字段不破坏其 schema）。
    entry["holdout"] = {
        "tuning_mean": det.get("tuning_mean"),
        "holdout_mean": det.get("holdout_mean"),
        "gap": det.get("gap"),
        "overfit": det.get("overfit"),
        "verdict": det.get("verdict"),
    }
    ledger = _dt.load_ledger(Path(args.project))
    ledger = _dt.append_entry(ledger, entry)
    _dt.save_ledger(Path(args.project), ledger)
    print(f"     [TRACK] 已挂 distill_track ledger（holdout 列）: "
          f"{_dt.ledger_path(Path(args.project))}")


def _print_verdict(report) -> None:
    det = report.get("detection", {})
    mode = report.get("mode")
    if not det.get("applicable"):
        print(f"     [INSUFFICIENT] {det.get('note', '样本不足·不下结论')}")
        return
    tm, hm, gap = det.get("tuning_mean"), det.get("holdout_mean"), det.get("gap")
    print(f"     tuning 均值 {tm}（{det.get('tuning_n')} 样本） · "
          f"holdout 均值 {hm}（{det.get('holdout_n')} 样本） · 落差 {gap:+.2f}")
    if det.get("overfit") and mode != "off":
        print(f"     [ADVISORY · 疑似过拟合] {det.get('note')}")
        print("     [ADVISORY] holdout 掉得多 = skill 可能学到 tuning cluster 特例 ·"
              "别只盯 tuning SFS · 优化时把 holdout 一起看（防 reward hacking）。")
    elif det.get("verdict") == "holdout_better":
        print(f"     [↑ 泛化好] holdout 反而更高 · {det.get('note')}")
    else:
        print(f"     [≈ 健康] {det.get('note')}")


def _collect_scores(sfs_list, report_list):
    """合并 --*-sfs 直传分 + --*-report 读出分（顺序：先直传后报告）。"""
    scores = [float(x) for x in (sfs_list or [])]
    for rp in (report_list or []):
        v = _score_from_report(rp)
        if v is not None:
            scores.append(v)
    return scores


def cmd_record(args) -> int:
    mode = holdout_mode()
    tuning = _collect_scores(args.tuning_sfs, args.tuning_report)
    holdout = _collect_scores(args.holdout_sfs, args.holdout_report)

    if mode == "off":
        print("[NOTE] HOLDOUT_SFS_MODE=off · 跳过留出落差计算（逃生口）", file=sys.stderr)
        return 0
    if not tuning:
        print("[ERROR] 无 tuning SFS 分：用 --tuning-sfs X 或 --tuning-report t.json",
              file=sys.stderr)
        return 1  # 用法错（参数缺失 · 非门禁阻断）
    if not holdout:
        print("[NOTE] 无 holdout SFS 分 · 无法算泛化落差（先跑 holdout cluster 复刻再传分）",
              file=sys.stderr)

    report = build_holdout_report(
        tuning, holdout, skill_version=args.skill_version,
        tuning_refs=args.tuning_ref, holdout_refs=args.holdout_ref,
        gap_floor=args.gap_floor, gap_rel=args.gap_rel,
        min_holdout_n=args.min_holdout_n, mode=mode,
    )
    p = save_report(args.project, args.skill_version, report)
    print(f"[OK] 留出验证 {args.skill_version} @ {args.project}")
    print(f"     写入 {p}（mode={mode}）")
    _print_verdict(report)
    if args.track:
        _track_holdout(args, report)
    return 0  # 永远 0：advisory 层绝不阻断蒸馏闭环


def _parse_pair(spec):
    """解析 --*-pair 的 'replica.txt:ref.txt'（Windows 盘符 C: 用 rsplit 一刀切末段）。"""
    if ":" not in spec:
        raise argparse.ArgumentTypeError(
            f"pair 格式须为 replica.txt:ref.txt（收到 {spec!r}）")
    rep, ref = spec.rsplit(":", 1)
    return rep, ref


def cmd_score_and_record(args) -> int:
    mode = holdout_mode()
    if mode == "off":
        print("[NOTE] HOLDOUT_SFS_MODE=off · 跳过", file=sys.stderr)
        return 0
    tuning, holdout = [], []
    for rep, ref in (args.tuning_pair or []):
        v = _score_pair(rep, ref)
        if v is not None:
            tuning.append(v)
    for rep, ref in (args.holdout_pair or []):
        v = _score_pair(rep, ref)
        if v is not None:
            holdout.append(v)
    if not tuning:
        print("[ERROR] tuning pair 一个都没打出分（缺 numpy/scipy？文件缺失？）·"
              "改用 record + --tuning-sfs 直传分", file=sys.stderr)
        return 1
    report = build_holdout_report(
        tuning, holdout, skill_version=args.skill_version,
        tuning_refs=[r for _, r in (args.tuning_pair or [])],
        holdout_refs=[r for _, r in (args.holdout_pair or [])],
        gap_floor=args.gap_floor, gap_rel=args.gap_rel,
        min_holdout_n=args.min_holdout_n, mode=mode,
    )
    p = save_report(args.project, args.skill_version, report)
    print(f"[OK] 留出验证（现打分）{args.skill_version}")
    print(f"     写入 {p}（mode={mode}）")
    _print_verdict(report)
    if args.track:
        _track_holdout(args, report)
    return 0


def cmd_split(args) -> int:
    res = split_clusters(
        args.cluster, holdout_frac=args.holdout_frac,
        holdout_n=args.holdout_n, holdout_refs=args.holdout_ref, seed=args.seed,
    )
    print(f"[OK] 切分策略={res['strategy']} seed={res['seed']}")
    print(f"     tuning  ({len(res['tuning'])}): {res['tuning']}")
    print(f"     holdout ({len(res['holdout'])}): {res['holdout']}")
    if not res["holdout"]:
        print("     [NOTE] holdout 为空（池太小 / 无可留出）· 此时无法量泛化落差",
              file=sys.stderr)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="蒸馏留出验证·防 metric overfit / reward hacking"
                    "（advisory only·零模型零依赖·复用 SFS）")
    sub = p.add_subparsers(dest="cmd", required=True)

    # record：传分算落差
    pr = sub.add_parser("record", help="传 tuning/holdout SFS 分算泛化落差 + 过拟合 advisory")
    pr.add_argument("--project", required=True, help="风格项目目录 workspace/styles/<书>")
    pr.add_argument("--skill-version", required=True, help="skill 版本号 如 v8")
    pr.add_argument("--tuning-sfs", type=float, action="append", default=[],
                    help="tuning cluster 单趟 SFS 分（可多次）")
    pr.add_argument("--holdout-sfs", type=float, action="append", default=[],
                    help="holdout cluster 单趟 SFS 分（可多次）")
    pr.add_argument("--tuning-report", action="append", default=[],
                    help="tuning style_evaluator 报告 JSON（读 sfs_quick · 可多次）")
    pr.add_argument("--holdout-report", action="append", default=[],
                    help="holdout style_evaluator 报告 JSON（可多次）")
    pr.add_argument("--tuning-ref", action="append", default=[], help="tuning cluster_ref 标注（meta）")
    pr.add_argument("--holdout-ref", action="append", default=[], help="holdout cluster_ref 标注（meta）")
    pr.add_argument("--model", default=None, help="gen-model 名（meta · 挂 ledger 用）")
    pr.add_argument("--track", action="store_true", help="同时挂 distill_track 基线 ledger（holdout 列）")
    pr.add_argument("--git-sha", default=None, help="覆盖 git sha（默认自动取 · 挂 ledger 用）")
    pr.add_argument("--gap-floor", type=float, default=DEFAULT_GAP_FLOOR)
    pr.add_argument("--gap-rel", type=float, default=DEFAULT_GAP_REL)
    pr.add_argument("--min-holdout-n", type=int, default=MIN_HOLDOUT_N)
    pr.set_defaults(func=cmd_record)

    # score-and-record：给 replica+ref 现打分再算落差
    ps = sub.add_parser("score-and-record",
                        help="给 (replica, ref) pair 复用 style_evaluator 现打 SFS 分再算落差")
    ps.add_argument("--project", required=True)
    ps.add_argument("--skill-version", required=True)
    ps.add_argument("--tuning-pair", type=_parse_pair, action="append", default=[],
                    help="tuning 的 replica.txt:ref.txt（可多次）")
    ps.add_argument("--holdout-pair", type=_parse_pair, action="append", default=[],
                    help="holdout 的 replica.txt:ref.txt（可多次）")
    ps.add_argument("--track", action="store_true")
    ps.add_argument("--git-sha", default=None)
    ps.add_argument("--model", default=None)
    ps.add_argument("--gap-floor", type=float, default=DEFAULT_GAP_FLOOR)
    ps.add_argument("--gap-rel", type=float, default=DEFAULT_GAP_REL)
    ps.add_argument("--min-holdout-n", type=int, default=MIN_HOLDOUT_N)
    ps.set_defaults(func=cmd_score_and_record)

    # split：确定性切 tuning/holdout
    pp = sub.add_parser("split", help="把 cluster 池确定性切成 tuning/holdout（选哪些留出）")
    pp.add_argument("--cluster", action="append", default=[], required=True,
                    help="cluster id（可多次 → 整个池）")
    pp.add_argument("--holdout-frac", type=float, default=0.25, help="留出比例（默认 0.25）")
    pp.add_argument("--holdout-n", type=int, default=None, help="留出固定个数（覆盖 frac）")
    pp.add_argument("--holdout-ref", action="append", default=[],
                    help="显式钉死留出哪些 cluster（覆盖 frac/n）")
    pp.add_argument("--seed", type=int, default=42, help="洗牌 seed（确定性可复现）")
    pp.add_argument("--json", action="store_true", help="额外打 JSON 行（供脚本消费）")
    pp.set_defaults(func=cmd_split)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
