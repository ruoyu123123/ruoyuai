#!/usr/bin/env python3
"""状态保存流水线的确定性入口。

CLI 子命令：
  --apply-cluster-changes <key>     校验 cluster writer self_eval 并写收据
  --apply-foreshadow-state <key>    注册本块伏笔、应用 payoff 并写收据
  --git-commit-cluster <key>        1 cluster 1 commit
  --auto-post-reflect-cluster <key> cluster 级 learning_loop 三步链
  --build-cluster-summary <key>     汇总 cluster required 产物并写入严格故事块摘要账本
  --detect-volume-boundary <key>    卷边界确定性检测（基于 ME 信号，仅报告）
  --apply-volume-summary <N>        卷级摘要回库（校验 source，幂等 upsert）

所有子命令都幂等（重复执行不产生副作用或破坏数据）。
确定性逻辑集中在此，AI 只负责：故事块摘要/写作反思/走向卡片。
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from frozen_util import child_python, scripts_dir  # frozen-aware path helpers
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cluster_lookup
import cluster_state_delta as _state_contract
from log_util import get_logger
from proc_utils import run_utf8
from save_state_common import (
    TERMINAL_STATE_VALUES,
    load_json,
    record_terminal_contract,
    require_cluster_id as _require_cluster_id,
    save_json,
    strip_terminal_state_payload,
    wal_dir as _wal_dir,
)
from save_state_volume import (
    closed_volumes as _closed_volumes,
    cmd_apply_volume_summary,
    cmd_detect_volume_boundary,
    me_volume_of as _me_volume_of,
)

logger = get_logger(__name__)


def _run_required(args: list[str], *, cwd: Path | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    """运行 required 子命令；任何非 0、超时或启动异常都抛错。"""
    try:
        r = run_utf8(args, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"required command timeout after {timeout}s: {' '.join(args)}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"required command failed to start: {' '.join(args)} :: {exc}") from exc
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-500:]
        raise RuntimeError(f"required command rc={r.returncode}: {' '.join(args)} :: {tail}")
    return r


def _git_commit_marker_payload(norm_cid: str, chapters: list[int], msg: str) -> dict:
    return {
        "schema_version": 1,
        "cluster_id": norm_cid,
        "chapters": chapters,
        "commit_message": msg,
        "committed": True,
    }


# ============ CLI ============

def _get_cluster_chapter_range(project_root, cluster_key):
    """读取 splitter 已确定的物理章节范围，供提交信息使用。"""
    cid = _require_cluster_id(cluster_key)
    cr = cluster_lookup.cluster_id_to_range(project_root, cid)
    if cr and len(cr) == 2:
        return list(range(int(cr[0]), int(cr[1]) + 1))
    return []


def _register_brief_foreshadowings(root, cluster_key):
    """把当前 cluster brief 中规划的伏笔幂等注册为 open promise。"""
    try:
        db = root / "_数据库"
        ec_path = db / "事件簇.json"
        if not ec_path.exists():
            raise RuntimeError("事件簇.json 不存在")
        cid = _require_cluster_id(cluster_key)
        ec = load_json(ec_path, None)
        if not isinstance(ec, dict):
            raise RuntimeError("事件簇.json 缺失或损坏")
        clusters = ec.get("clusters")
        if not isinstance(clusters, list):
            raise RuntimeError("事件簇.json.clusters 不是 list")
        cluster = next((c for c in clusters
                        if isinstance(c, dict) and c.get("cluster_id") == cid), None)
        if not cluster:
            raise RuntimeError(f"事件簇.json 找不到 {cid}")
        ftp = cluster.get("foreshadowing_to_plant", [])
        if not isinstance(ftp, list):
            raise RuntimeError(f"{cid}.foreshadowing_to_plant 不是 list")
        fs_path = db / "伏笔表.json"
        fs = load_json(fs_path, None)
        if not isinstance(fs, dict):
            raise RuntimeError("伏笔表.json 缺失或损坏")
        promises = fs.setdefault("promises", [])
        if not isinstance(promises, list):
            raise RuntimeError("伏笔表.json.promises 不是 list")
        existing = {p.get("id") for p in promises if isinstance(p, dict)}
        added = 0
        for f in ftp:
            if not isinstance(f, dict):
                raise RuntimeError("foreshadowing_to_plant 条目不是 object")
            fid = f.get("id")
            if not isinstance(fid, str) or not fid.strip():
                raise RuntimeError("foreshadowing_to_plant 条目缺稳定 id")
            if fid in existing:
                continue
            entry = {
                "id": fid, "setup_cluster": cid, "tier": f.get("tier", 3),
                "description": f.get("desc") or f.get("description") or "",
                "due_by_cluster": None, "status": "open", "owner": "writer",
                "due_by_pending_resolution": True, "_source": "brief",
            }
            entry["payoff_scope"] = str(f.get("payoff_scope") or "")
            promises.append(entry)
            existing.add(fid)
            added += 1
        if added:
            save_json(fs_path, fs)
            logger.info(f"[brief-foreshadow] {cid} 注册 {added} 条 brief 规划伏笔 → 伏笔表")
        return {"planned": len(ftp), "registered": added}
    except Exception as e:
        raise RuntimeError(f"brief 规划伏笔注册失败: {type(e).__name__}: {str(e)[:120]}") from e


def _apply_foreshadower_payoffs(root, cluster_key):
    """依据 required foreshadower 报告更新伏笔 payoff 状态。"""
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        rpt = db / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            raise RuntimeError(f"{rpt.name} 不存在")
        report = load_json(rpt, None)
        if not isinstance(report, dict):
            raise RuntimeError(f"{rpt.name} 缺失或损坏")
        scores = (report.get("specific_findings") or {}).get("payoff_scores", []) or []
        if not isinstance(scores, list):
            raise RuntimeError("foreshadower payoff_scores 不是 list")
        if not scores:
            result = {"checked": 0, "terminal_applied": 0,
                      "progressive_recorded": 0, "rejections": []}
            record_terminal_contract(db, cid, "foreshadower_payoff", result)
            return result
        fs_path = db / "伏笔表.json"
        fs = load_json(fs_path, None)
        if not isinstance(fs, dict):
            raise RuntimeError("伏笔表.json 缺失或损坏")
        by_id = {p.get("id"): p for p in fs.get("promises", []) if isinstance(p, dict)}
        changed = 0
        terminal_applied = 0
        progressive_recorded = 0
        rejections = []

        def _reject(fs_id, code, note):
            """拒绝一条不满足终态合同的转移并继续检查其余条目。"""
            rejections.append({"fs_id": fs_id, "code": code, "note": note})
            sys.stderr.write(f"[terminal-contract] WARNING: {cid} payoff→{fs_id} 拒绝终态转移"
                             f"（{code}·{note}·该条不落库·其余条目照常）\n")
            sys.stderr.flush()

        for it in scores:
            if not isinstance(it, dict):
                raise RuntimeError("payoff_scores 条目不是 object")
            fs_id = it.get("fs_id")
            if not isinstance(fs_id, str) or not fs_id.strip():
                raise RuntimeError("payoff_scores 条目缺 fs_id")
            p = by_id.get(fs_id)
            try:
                score = int(it.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            if score <= 0:
                continue
            verdict = str(it.get("verdict", ""))
            if it.get("terminal") is True or verdict == "paid_terminal":
                if p is None:
                    _reject(fs_id, "target_missing", "伏笔表不存在该条目（孤儿 payoff）")
                    continue
                if p.get("status") == "consumed":
                    continue
                status = p.get("status")
                if status not in ("open", "suspended"):
                    _reject(fs_id, "target_not_open",
                            f"status={status!r} 非 open/suspended")
                    continue
                evidence = str(it.get("evidence") or it.get("span") or it.get("reason") or "").strip()
                if not evidence:
                    _reject(fs_id, "evidence_missing", "缺 evidence/span/reason 正文证据")
                    continue
                if status == "suspended":
                    logger.warning(
                        f"[foreshadower-payoff] {p.get('id')} 处于 suspended 却被 terminal 回收"
                        "（回收动作指向非 open 条目·可能是状态漂移·advisory·照常落账）")
                p["status"] = "consumed"
                p["consumed_at_cluster"] = cid
                p["_consumed_by"] = "foreshadower"
                changed += 1
                terminal_applied += 1
            elif "progressive" in verdict or it.get("terminal") is False:
                if p is None:
                    _reject(fs_id, "target_missing", "伏笔表不存在该条目")
                    continue
                prog = p.setdefault("payoff_progress", [])
                if cid not in prog:
                    prog.append(cid)
                    changed += 1
                    progressive_recorded += 1
        if changed:
            save_json(fs_path, fs)
            logger.info(f"[foreshadower-payoff] {cid} 桥接 {changed} 条 payoff → 伏笔表"
                        "（terminal→consumed / progressive→progress）")
        result = {"checked": len(scores), "terminal_applied": terminal_applied,
                  "progressive_recorded": progressive_recorded, "rejections": rejections}
        record_terminal_contract(db, cid, "foreshadower_payoff", result)
        return result
    except Exception as e:
        raise RuntimeError(f"foreshadower payoff 回库失败: {type(e).__name__}: {str(e)[:120]}") from e


def cmd_apply_foreshadow_state(root: Path, cluster_key) -> int:
    """执行伏笔注册与 payoff 回库，并写本次 required completion receipt。"""
    try:
        cid = _require_cluster_id(cluster_key)
        report = root / "_数据库" / ".judge_reports" / f"{cid}_foreshadower.json"
        if not report.is_file():
            raise RuntimeError(f"required foreshadower 报告不存在: {report}")
        registration = _register_brief_foreshadowings(root, cid)
        payoff = _apply_foreshadower_payoffs(root, cid)
        receipt = {
            "schema_version": 1,
            "cluster_id": cid,
            "contract": "cluster_foreshadow_state",
            "completed": True,
            "brief_planned": registration["planned"],
            "brief_registered": registration["registered"],
            "payoff_checked": payoff["checked"],
            "terminal_applied": payoff["terminal_applied"],
            "progressive_recorded": payoff["progressive_recorded"],
            "rejection_count": len(payoff["rejections"]),
            "sources": [
                f"_数据库/事件簇.json#{cid}.foreshadowing_to_plant",
                f"_数据库/.judge_reports/{cid}_foreshadower.json",
            ],
        }
        out = _wal_dir(root) / f"{cid}_foreshadow_state_receipt.json"
        save_json(out, receipt)
        logger.info(f"[OK] 伏笔状态完成收据: {out.name}")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[foreshadow-state] FATAL: {type(exc).__name__}: {exc}")
        return 2


def cmd_apply_appraisal_beats(root, cluster_key):
    """把 summarizer 的场景评价链按 cluster/scene/角色幂等写入叙事节拍器。"""
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        summary_path = db / ".wal" / f"{cid}_summary.json"
        if not summary_path.is_file():
            logger.info(f"[appraisal-beats] {cid} summary.json 不存在（summarizer required 产物缺失）")
            return 2
        summ = load_json(summary_path, {})
        beats = summ.get("appraisal_beats") if isinstance(summ, dict) else None
        if not isinstance(beats, list):
            logger.info(f"[appraisal-beats] {cid} summary 缺 appraisal_beats list（required 字段缺失）")
            return 2
        if not beats:
            logger.info(f"[appraisal-beats] {cid} appraisal_beats=[]（生产者明确无情绪拍·幂等通过）")
            return 0

        # 启用 NN VAD 时，V/A 由模型重算，D 保留 summarizer 结果。
        nn_vad_by_id, _vad_bridge = {}, None
        import os as _os_vad
        if _os_vad.environ.get("RUOYU_NN_VAD") == "1":
            try:
                import nn_vad_bridge as _vad_bridge
                _bd = [b for b in beats if isinstance(b, dict)]
                _txts = [" ".join(str(b.get(k, "")) for k in
                                  ("trigger_event", "derived_emotion", "behavior_externalization")).strip()
                         for b in _bd]
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "feature_store"))
                from feature_cache import FeatureStore, enabled as _fs_enabled
                _preds = (FeatureStore.get().compute_vad_batch(_txts)
                          if _fs_enabled() else _vad_bridge.predict_batch(_txts))
                for b, pr in zip(_bd, _preds):
                    if pr and pr.get("valence") is not None:
                        nn_vad_by_id[id(b)] = pr
                if nn_vad_by_id:
                    logger.info(f"[appraisal-beats] {cid} NN VAD 重算 {len(nn_vad_by_id)}/{len(_bd)} 拍 "
                                "vad_bin.V/A（D 保留 summarizer）")
            except Exception as e:  # noqa: BLE001
                logger.info(f"[appraisal-beats] NN VAD FATAL: {type(e).__name__}: {str(e)[:80]}")
                return 2

        pacer_path = db / "叙事节拍器.json"
        pacer = load_json(pacer_path, None)
        if not isinstance(pacer, dict):
            logger.info(f"[appraisal-beats] {cid} 叙事节拍器.json 缺/坏（required 状态库不可用）")
            return 2
        existing = pacer.get("appraisal_beats")
        if not isinstance(existing, list):
            raise RuntimeError("叙事节拍器.appraisal_beats 不是 list")
        seen = {(b.get("cluster_id"), b.get("scene_idx"), b.get("focal_character"))
                for b in existing if isinstance(b, dict)}
        added = 0
        for b in beats:
            if not isinstance(b, dict):
                raise RuntimeError("appraisal_beats 条目不是 object")
            b_cid_raw = b.get("cluster_id")
            if b_cid_raw != cid:
                raise RuntimeError(f"appraisal beat.cluster_id 必须等于 {cid}")
            rec = dict(b)
            scene_idx = b.get("scene_idx")
            if isinstance(scene_idx, bool) or not isinstance(scene_idx, int) or scene_idx < 0:
                raise RuntimeError("appraisal beat.scene_idx 必须是非负整数")
            rec["scene_idx"] = scene_idx
            focal = rec.get("focal_character")
            if not isinstance(focal, str) or not focal.strip():
                raise RuntimeError("appraisal beat.focal_character 不能为空")
            pr = nn_vad_by_id.get(id(b))
            if pr is not None and _vad_bridge is not None:
                _old_bin = rec.get("vad_bin") if isinstance(rec.get("vad_bin"), dict) else {}
                _nb = _vad_bridge.to_vad_bin(pr.get("valence"), pr.get("arousal"))
                rec["vad_bin"] = {"valence": _nb["valence"], "arousal": _nb["arousal"],
                                  "dominance": _old_bin.get("dominance"),
                                  "_source": "model_va+summarizer_d"}
            key = (cid, rec["scene_idx"], focal)
            if key in seen:
                continue
            existing.append(rec)
            seen.add(key)
            added += 1
        if added:
            save_json(pacer_path, pacer)
            logger.info(f"[appraisal-beats] {cid} 回填 {added} 拍 → 叙事节拍器.appraisal_beats"
                        "（chain-of-emotion·结构化 STATE）")
        else:
            logger.info(f"[appraisal-beats] {cid} 无新增（全已存在或非本 cluster·幂等）")
        return 0
    except Exception as e:
        logger.info(f"[appraisal-beats] FATAL: {type(e).__name__}: {str(e)[:120]}")
        return 2


def cmd_apply_dramatic_questions(root, cluster_key):
    """把 foreshadower 的 raised/answered 戏剧问题幂等写入当前 cluster 账本。"""
    try:
        db = root / "_数据库"
        cid = _require_cluster_id(cluster_key)
        rpt = db / ".judge_reports" / f"{cid}_foreshadower.json"
        if not rpt.is_file():
            logger.info(f"[dramatic-questions] {cid} foreshadower JudgeReport 不存在（required 产物缺失）")
            return 2
        sf = (load_json(rpt, {}) or {}).get("specific_findings") or {}
        dq = sf.get("dramatic_questions")
        if not isinstance(dq, dict):
            logger.info(f"[dramatic-questions] {cid} 缺 dramatic_questions dict（required 字段缺失）")
            return 2
        raised = dq.get("raised") if isinstance(dq.get("raised"), list) else []
        answered = dq.get("answered") if isinstance(dq.get("answered"), list) else []
        if not raised and not answered:
            ledger_path = db / "戏剧问题账本.json"
            ledger = load_json(ledger_path, None)
            if not isinstance(ledger, dict):
                raise RuntimeError("戏剧问题账本.json 缺失或损坏")
            clusters = ledger.get("clusters")
            if not isinstance(clusters, dict):
                raise RuntimeError("戏剧问题账本.json.clusters 不是 object")
            clusters.setdefault(cid, {"raised": [], "answered": []})
            save_json(ledger_path, ledger)
            logger.info(f"[dramatic-questions] {cid} raised/answered 均空（生产者明确无戏剧问题变更·幂等通过）")
            return 0

        ledger_path = db / "戏剧问题账本.json"
        ledger = load_json(ledger_path, None)
        if not isinstance(ledger, dict):
            raise RuntimeError("戏剧问题账本.json 缺失或损坏")
        clusters = ledger.get("clusters")
        if not isinstance(clusters, dict):
            raise RuntimeError("戏剧问题账本.json.clusters 不是 object")
        entry = clusters.get(cid)
        if not isinstance(entry, dict):
            entry = clusters[cid] = {"raised": [], "answered": []}
        e_raised = entry.get("raised")
        if not isinstance(e_raised, list):
            e_raised = entry["raised"] = []
        e_answered = entry.get("answered")
        if not isinstance(e_answered, list):
            e_answered = entry["answered"] = []

        seen_raised = {r.get("qid") for r in e_raised if isinstance(r, dict)}
        seen_answered = {a.get("qid") for a in e_answered if isinstance(a, dict)}
        # 其他 cluster 已闭合的 qid 用于拒绝重复终态转移。
        answered_elsewhere = {}
        for _ocid, _oent in clusters.items():
            if _ocid == cid or not isinstance(_oent, dict):
                continue
            for _a in _oent.get("answered") or []:
                if isinstance(_a, dict) and _a.get("qid"):
                    answered_elsewhere.setdefault(_a["qid"], _ocid)
        dq_rejections = []
        added_r = added_a = 0
        for r in raised:
            if not isinstance(r, dict):
                raise RuntimeError("dramatic_questions.raised 条目不是 object")
            qid = r.get("qid")
            if not qid:
                raise RuntimeError("dramatic_questions.raised 条目缺 qid")
            if qid in seen_raised:
                continue
            scope = r.get("scope", "cluster")
            if scope not in ("cluster", "volume", "series"):
                raise RuntimeError(f"dramatic question {qid}.scope 无效")
            ras = r.get("raised_at_scene")
            if ras is not None and (isinstance(ras, bool) or not isinstance(ras, int) or ras < 0):
                raise RuntimeError(f"dramatic question {qid}.raised_at_scene 无效")
            gap_type = r.get("gap_type")
            if gap_type is not None and gap_type not in ("suspense", "curiosity", "surprise"):
                raise RuntimeError(f"dramatic question {qid}.gap_type 无效")
            e_raised.append({
                "qid": qid,
                "question": r.get("question") or "",
                "scope": scope,
                "raised_at_scene": ras,
                "expected_payoff_window": r.get("expected_payoff_window") or "",
                "gap_type": gap_type,
            })
            seen_raised.add(qid)
            added_r += 1
        for a in answered:
            if not isinstance(a, dict):
                raise RuntimeError("dramatic_questions.answered 条目不是 object")
            qid = a.get("qid")
            if not qid:
                raise RuntimeError("dramatic_questions.answered 条目缺 qid")
            if qid in seen_answered:
                continue
            if qid in answered_elsewhere:
                dq_rejections.append({"qid": qid, "code": "target_not_open",
                                      "note": f"已在 {answered_elsewhere[qid]} 闭合·拒绝重复终态转移"})
                sys.stderr.write(f"[terminal-contract] WARNING: {cid} 戏剧问题 {qid} 已在 "
                                 f"{answered_elsewhere[qid]} 闭合——拒绝重复 answered（该条不落库·其余照常）\n")
                sys.stderr.flush()
                continue
            aas = a.get("answered_at_scene")
            if aas is not None and (isinstance(aas, bool) or not isinstance(aas, int) or aas < 0):
                raise RuntimeError(f"dramatic question {qid}.answered_at_scene 无效")
            if aas is None:
                dq_rejections.append({"qid": qid, "code": "evidence_missing",
                                      "note": "缺 answered_at_scene 正文位置证据"})
                sys.stderr.write(f"[terminal-contract] WARNING: {cid} 戏剧问题 {qid} answered 缺 "
                                 f"answered_at_scene 正文证据——拒绝终态转移（该条不落库·其余照常）\n")
                sys.stderr.flush()
                continue
            e_answered.append({"qid": qid, "answered_at_scene": aas})
            seen_answered.add(qid)
            added_a += 1

        record_terminal_contract(db, cid, "dramatic_questions",
                                 {"raised_applied": added_r, "answered_applied": added_a,
                                  "rejections": dq_rejections})

        if added_r or added_a:
            ledger.setdefault("schema_version", 1)
            save_json(ledger_path, ledger)
            logger.info(f"[dramatic-questions] {cid} 回库 raised+{added_r} / answered+{added_a}"
                        " → 戏剧问题账本（PITQ/MDQ·读者粘性 STATE）")
        else:
            logger.info(f"[dramatic-questions] {cid} 无新增（全已存在·幂等）")
        return 0
    except Exception as e:
        logger.info(f"[dramatic-questions] FATAL: {type(e).__name__}: {str(e)[:120]}")
        return 2


def cmd_apply_cluster_changes(root, cluster_key):
    """校验 cluster writer 自评并写入确定性收据，不修改客观状态。"""
    try:
        cid = _require_cluster_id(cluster_key)
        changes_path = root / "章节" / f"{cid}_draft" / f"{cid}_changes.json"
        changes = json.loads(changes_path.read_text(encoding="utf-8"))
        schema_path = (Path(__file__).resolve().parents[1] / "claude-home" / "schemas"
                       / "changes_schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        _state_contract.validate_schema(changes, schema)
        self_eval = changes["self_eval"]
        receipt = {
            "cluster_id": cid,
            "source": str(changes_path.relative_to(root)).replace("\\", "/"),
            "validated_at": datetime.now().isoformat(timespec="seconds"),
            "contract": "cluster_writer_self_eval",
            "waiver_count": len(self_eval["waivers"]),
            "self_eval_fields": sorted(self_eval),
            "objective_state_applied": False,
        }
        out = root / "_数据库" / ".wal" / f"{cid}_apply_cluster.json"
        save_json(out, receipt)
        logger.info(f"[OK] writer self-eval validated: {out.name}")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.info(f"[apply-cluster] FATAL: {type(exc).__name__}: {exc}")
        return 2


def cmd_git_commit_cluster(root, cluster_key):
    """提交一个完整 cluster 及其 splitter 输出。"""
    norm_cid = _require_cluster_id(cluster_key)
    chapters = _get_cluster_chapter_range(root, cluster_key)
    if not chapters:
        sys.stderr.write(f"[FATAL save_state] cluster {cluster_key} 未找到 chapter_range\n")
        sys.stderr.flush()
        return 2
    marker = _wal_dir(root) / f"{norm_cid}_git_commit.json"
    import subprocess as _sp
    _cnum = f"{cluster_lookup.cluster_num(norm_cid):03d}"
    msg = f"feat(cluster-{_cnum}): {len(chapters)} 章 (ch{chapters[0]}-{chapters[-1]})"
    marker_payload = _git_commit_marker_payload(norm_cid, chapters, msg)
    try:
        _sp.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True, timeout=30)
        r = _sp.run(["git", "-C", str(root), "commit", "-m", msg],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode == 0:
            sha_r = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            check=True, timeout=10)
            sha = (sha_r.stdout or "").strip()
            save_json(marker, marker_payload)
            # marker 落在 .wal/（项目 .gitignore 惯例忽略 WAL 目录），但它是本 cluster 提交的
            # provenance 记录·代码本意就是要 amend 进提交 → 必须 -f 强制越过 gitignore（否则
            # 凡 .gitignore 含 _数据库/.wal/ 的书首次 cluster 提交必崩·数据提交已成功却 exit 2）。
            _sp.run(["git", "-C", str(root), "add", "-f", str(marker)], check=True, capture_output=True, timeout=30)
            amend = _sp.run(["git", "-C", str(root), "commit", "--amend", "--no-edit"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if amend.returncode != 0:
                raise RuntimeError((amend.stderr or amend.stdout or "")[:300])
            sha2 = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           check=True, timeout=10).stdout.strip()
            logger.info(f"[GIT] cluster_{cluster_key} 快照 {sha}: {msg}")
            logger.info(f"[GIT] marker → {marker.name} · amended HEAD {sha2}")
        else:
            combined = (r.stderr or r.stdout or "")
            if "nothing to commit" in combined.lower() or "working tree clean" in combined.lower():
                head_msg = _sp.run(["git", "-C", str(root), "log", "-1", "--pretty=%s"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   check=True, timeout=10).stdout.strip()
                if msg not in head_msg and f"cluster-{_cnum}" not in head_msg:
                    raise RuntimeError(f"无可提交内容，但 HEAD 不是本 cluster 提交: {head_msg}")
                sha = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=True, timeout=10).stdout.strip()
                existing_marker = load_json(marker, None)
                if existing_marker != marker_payload:
                    save_json(marker, marker_payload)
                    # 同上：.wal/ 被 gitignore，provenance marker 须 -f 强制入库
                    _sp.run(["git", "-C", str(root), "add", "-f", str(marker)], check=True, capture_output=True, timeout=30)
                    amend = _sp.run(["git", "-C", str(root), "commit", "--amend", "--no-edit"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                    if amend.returncode != 0:
                        raise RuntimeError((amend.stderr or amend.stdout or "")[:300])
                    sha = _sp.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  check=True, timeout=10).stdout.strip()
                logger.info(f"[GIT] {norm_cid} 已提交且工作区干净，幂等通过: {sha}")
            else:
                raise RuntimeError(combined[:300])
    except _sp.TimeoutExpired:
        logger.info(f"[GIT] cluster_{cluster_key} commit 超时 (>30s)")
        return 2
    except Exception as e:
        logger.info(f"[GIT] {e}")
        return 2
    return 0


def cmd_auto_post_reflect_cluster(root, cluster_key):
    """运行 cluster 级 learning_loop 三步链：
      1. learning_loop --merge-reflection .wal/<cluster_key>_reflection.json
      2. learning_loop --ingest .audit/cluster_<key>_audit.json（见 audit_hub.py:1399）
      3. learning_loop --scan-recurring
    退出码语义：返回 0 成功；reflection/audit/learning_loop/data_flywheel 是主链必需步骤，
    缺失或失败返回 2。
    """
    db = root / "_数据库"
    norm_cid = _require_cluster_id(cluster_key)

    learning_loop = scripts_dir() / "learning_loop.py"  # frozen-aware（狩猎修·exe下学习闭环静默不跑）
    if not learning_loop.is_file():
        logger.info(f"[auto-post-reflect-cluster] learning_loop.py 不存在")
        return 2

    refl_path = db / ".wal" / f"{norm_cid}_reflection.json"
    audit_path = db / ".audit" / f"{norm_cid}_audit.json"

    project_str = str(root)
    steps_ran = 0
    failures: list[str] = []

    # Step 1: merge cluster reflection → 写作经验.success/failure_patterns
    if refl_path.is_file():
        try:
            _run_required(
                [child_python(), str(learning_loop), project_str, "--merge-reflection",
                 refl_path.relative_to(root).as_posix()],
                timeout=180,
            )
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection OK ({refl_path.name})")
            steps_ran += 1
        except Exception as e:
            failures.append(f"merge-reflection: {e}")
            logger.info(f"[auto-post-reflect-cluster] step 1/3 merge-reflection FAIL: {e}")
    else:
        failures.append("cluster reflection 报告不存在")
        logger.info(f"[auto-post-reflect-cluster] step 1/3 缺失：cluster reflection 报告不存在"
              f"（找过 {norm_cid}_reflection.json 等）")

    # Step 2: ingest cluster audit → _recurrence_tracker / _waiver_tracker
    if audit_path.is_file():
        try:
            r = run_utf8(
                [child_python(), str(learning_loop), project_str, "--ingest",
                 audit_path.relative_to(root).as_posix()],
                timeout=180,
            )
            if r.returncode in (0, 1):  # 1 = 检测到复发问题，不是错
                logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest OK ({audit_path.name}, rc={r.returncode})")
                steps_ran += 1
            else:
                raise RuntimeError((r.stderr or r.stdout or "")[:300])
        except Exception as e:
            failures.append(f"ingest: {e}")
            logger.info(f"[auto-post-reflect-cluster] step 2/3 ingest FAIL: {e}")
    else:
        failures.append("cluster audit 报告不存在")
        logger.info(f"[auto-post-reflect-cluster] step 2/3 缺失：cluster audit 报告不存在"
              f"（找过 {norm_cid}_audit.json 等）")

    # Step 3: scan-recurring → 跨 cluster 复发追踪 + tool_calibration_suggestions
    try:
        r = run_utf8(
            [child_python(), str(learning_loop), project_str, "--scan-recurring"],
            timeout=180,
        )
        if r.returncode in (0, 1):
            logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring OK (rc={r.returncode})")
            steps_ran += 1
        else:
            raise RuntimeError((r.stderr or r.stdout or "")[:300])
    except Exception as e:
        logger.info(f"[auto-post-reflect-cluster] step 3/3 scan-recurring FAIL: {e}")
        failures.append(f"scan-recurring: {e}")

    # Step 4: DataFlywheel → cluster 训练样本池（paragraph/fix_pair/weak/strong/judge/checker/fixer/repair/reading/audit-meta labels）。
    # 创作入口默认打开 RUOYU_DATA_FLYWHEEL；主链里关闭即失败，避免 required 学习链变空跑。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "flywheel"))
        import data_collector as _data_collector
        fly = _data_collector.ClusterDataCollector(project_str, norm_cid).collect()
        if fly.get("skipped"):
            failures.append(f"data-flywheel skipped: {fly.get('reason')}")
            logger.info(f"[data-flywheel] {norm_cid} 缺失：{fly.get('reason')}")
        else:
            logger.info(f"[data-flywheel] {norm_cid} 训练样本收集 total={fly.get('total', 0)} "
                        f"paragraphs={fly.get('paragraphs', 0)} weak={fly.get('weak_labels', 0)} "
                        f"strong={fly.get('strong_labels', 0)} fixes={fly.get('fix_pairs', 0)} "
                        f"judge_reports={fly.get('judge_reports', 0)} "
                        f"checker_briefs={fly.get('checker_briefs', 0)} "
                        f"fixer_reports={fly.get('fixer_reports', 0)} "
                        f"repair_reports={fly.get('repair_reports', 0)} "
                        f"reading_reflections={fly.get('reading_reflections', 0)} "
                        f"audit_metadata={fly.get('audit_metadata', 0)}")
            steps_ran += 1
    except Exception as e:  # noqa: BLE001
        logger.info(f"[data-flywheel] {norm_cid} 收集失败: {type(e).__name__}: {str(e)[:160]}")
        failures.append(f"data-flywheel: {type(e).__name__}: {str(e)[:160]}")

    marker = _wal_dir(root) / f"{norm_cid}_post_reflect.json"
    save_json(marker, {
        "cluster_id": norm_cid,
        "steps_ran": steps_ran,
        "required_steps": 4,
        "failures": failures,
        "completed": not failures and steps_ran == 4,
    })
    if failures or steps_ran != 4:
        logger.info(f"[auto-post-reflect-cluster] FATAL {cluster_key}: 完成 {steps_ran}/4，失败 {failures}")
        return 2
    logger.info(f"[auto-post-reflect-cluster] {cluster_key} 完成 {steps_ran}/4 步 → {marker.name}")
    return 0


def main():
    # 公共 CLI 只暴露 cluster 级子命令；章级函数由 cluster 命令内部复用。
    import sys as _sys_nn
    import nn_runtime_defaults
    _nn_on = nn_runtime_defaults.enable_creative_nn_defaults()
    if _nn_on:
        print(f"[nn] 创作 save_state 默认开启模型/可成长门控: {', '.join(_nn_on)}", file=_sys_nn.stderr)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "registry"))
        import model_registry as _model_registry
        _reg = _model_registry.sync_runtime_models()
        if _reg.get("count"):
            print(f"[model-registry] 同步运行模型 { _reg['count'] } 个", file=_sys_nn.stderr)
    except Exception as _e:  # noqa: BLE001
        print(f"[model-registry] FATAL: {type(_e).__name__}: {str(_e)[:120]}", file=_sys_nn.stderr)
        sys.exit(2)
    ap = argparse.ArgumentParser(description="save_state.py · cluster-only CLI")
    ap.add_argument("project", help="项目路径")
    # cluster-level subcommands（唯一 CLI 入口）
    ap.add_argument("--apply-cluster-changes", type=str, metavar="CLUSTER_KEY",
                    help="校验本 cluster writer self_eval 并写 completion receipt")
    ap.add_argument("--apply-foreshadow-state", type=str, metavar="CLUSTER_KEY",
                    help="注册 brief 伏笔、应用 foreshadower payoff 并写 completion receipt")
    ap.add_argument("--git-commit-cluster", type=str, metavar="CLUSTER_KEY",
                    help="1 cluster 1 commit · msg = feat(cluster-NNN): N 章 (chX-chY)")
    ap.add_argument("--auto-post-reflect-cluster", type=str, metavar="CLUSTER_KEY",
                    help="cluster 级 learning_loop")
    ap.add_argument("--build-cluster-summary", type=str, metavar="CLUSTER_KEY",
                    help="把整 cluster 的正文、状态与评估产物写入 故事块摘要.json")
    ap.add_argument("--apply-appraisal-beats", type=str, metavar="CLUSTER_KEY",
                    help="把 summarizer 产出的 appraisal_beats 确定性回填 叙事节拍器.json"
                         "（chain-of-emotion·只 active cluster·幂等·required STATE·未产则 exit2）")
    ap.add_argument("--apply-dramatic-questions", type=str, metavar="CLUSTER_KEY",
                    help="把 foreshadower JudgeReport 的 dramatic_questions 确定性回库"
                         " 戏剧问题账本.json（PITQ/MDQ·读者粘性·只 active cluster·按 qid 幂等去重·"
                         "required STATE·未产则 exit2）")
    ap.add_argument("--detect-volume-boundary", type=str, metavar="CLUSTER_KEY",
                    help="卷边界确定性检测（某卷 ME 池非空且全部 status=completed"
                         " 且 故事块摘要.volume_summaries 尚无该卷）→ 写 .wal/<cid>_volume_boundary"
                         ".json 供主代理条件 spawn novel-summarizer MODE=volume·无边界=零行为变化")
    ap.add_argument("--apply-volume-summary", type=str, metavar="VOLUME_N",
                    help="卷级摘要回库入口——读 .wal/volume_<N>_summary.json 校验"
                         "（结构键钉死 + source 必须恰好覆盖本卷全部 cluster_ids + 卷真闭合）"
                         "→ 幂等 upsert 故事块摘要.volume_summaries[]·不合格 exit2 零写入")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    if not root.exists():
        logger.info(f"项目路径不存在: {root}")
        sys.exit(2)

    # 子命令返回码原样传播给调用方。
    rc = 0
    if args.apply_cluster_changes:
        rc = cmd_apply_cluster_changes(root, args.apply_cluster_changes)
    elif args.apply_foreshadow_state:
        rc = cmd_apply_foreshadow_state(root, args.apply_foreshadow_state)
    elif args.git_commit_cluster:
        rc = cmd_git_commit_cluster(root, args.git_commit_cluster)
    elif args.auto_post_reflect_cluster:
        rc = cmd_auto_post_reflect_cluster(root, args.auto_post_reflect_cluster)
    elif args.apply_appraisal_beats:
        rc = cmd_apply_appraisal_beats(root, args.apply_appraisal_beats)
    elif args.apply_dramatic_questions:
        rc = cmd_apply_dramatic_questions(root, args.apply_dramatic_questions)
    elif args.detect_volume_boundary:
        rc = cmd_detect_volume_boundary(root, args.detect_volume_boundary)
    elif args.apply_volume_summary:
        rc = cmd_apply_volume_summary(root, args.apply_volume_summary)
    elif args.build_cluster_summary:
        import cluster_summary_builder
        _res = cluster_summary_builder.build_cluster_summary(root, args.build_cluster_summary)
        if not _res.get("ok"):
            logger.info(f"[FAIL] build-cluster-summary {_res.get('cluster_id')} :: {_res.get('error')}")
            sys.exit(1)
        logger.info(f"[OK] 账本写入 {_res['cluster_id']} · 整块 CJK {_res['word_count']} · "
                    f"非空字段 {_res['cluster_rollup_fields']}")
        rc = 0
    else:
        ap.print_help()
        sys.exit(1)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
