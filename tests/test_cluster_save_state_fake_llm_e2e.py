#!/usr/bin/env python3
"""cluster-save-state 状态保存主轨 fake-LLM 端到端集成测试（2026-06-17 · P1）。

延续 P0（tests/test_cluster_write_fake_llm_e2e.py）：先 fake-LLM 跑 cluster-write 产出
draft+changes，再 fake-LLM 跑 cluster-save-state 14 步——验证状态保存主轨的确定性骨架
（db_schema_validate / cluster_changes apply / archivist 回库 / cross_cluster aggregate /
emergence 涌现下个 cluster 走向卡 / wal 生命周期）。

🔴 2026-06-28：archivist+apply-archive（step5/6）插入后原 12 步顺延为 14（emergence 走向卡
由 step11→step13）。

安全性已实地核实（2026-06-17）：cluster-save-state step10/11 经 adaptive_runner 派 subprocess
跑的脚本（evolution_orchestrator/skill_evolver/maybe_judge_consensus/learning_loop/…）**全部
纯确定性·零 gen-model 调用**，故子进程无真 API 花钱风险；LLM 仅在 step5/7/8/9/13 的 judge
（archivist/summarizer/foreshadower/reflector/outline-planner），全走 orchestrator in-process
judge_dispatch → jr.lt.generate（已被 fake 覆盖）。叠加 _network_hard_block 兜底 + _frozen_environ 隔离。

复用 P0 夹具（_Sandbox/_seed_min_subsystems/三-seam fake/网络兜底/环境隔离/in-process runner），
避免重复。走向卡停顿点用 auto_pilot=True 取 emergence 第一候选。只断言确定性骨架。
"""
import json
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS))                       # 让 zero-dep runner 也能 import 兄弟夹具
sys.path.insert(0, str(_TESTS.parent / "core" / "scripts"))

import orchestrator as orc  # noqa: E402

# 复用 P0 夹具（只读·不改 P0 文件）
from test_cluster_write_fake_llm_e2e import (  # noqa: E402
    _ROOT, _Sandbox, _seed_min_subsystems, _network_hard_block,
    _frozen_environ, _utf8_io, _inprocess_runner, _writer_judge_fakes,
)


def _run(command, judge_log):
    """fake-LLM 跑一个命令（三-seam + 网络兜底 + 环境隔离 + in-process + auto_pilot）。"""
    with _frozen_environ(), _utf8_io(), _network_hard_block(), \
            _writer_judge_fakes(judge_log):
        return orc.run_command(
            command, "冒烟书", key="001",
            script_runner=_inprocess_runner, judge_dispatch=None,
            pause_handler=None, auto_pilot=True,        # 走向卡取第一候选·不阻塞
            repo_root=_ROOT)


def test_cluster_save_state_after_write_fake_llm():
    """cluster-write → cluster-save-state 全链 fake-LLM：状态保存 14 步确定性骨架。"""
    judge_log = []
    with _Sandbox() as sb:
        _seed_min_subsystems(sb.proj)
        # 1) 先产出 draft+changes（cluster-write·P0 已证可跑通）
        w = _run("cluster-write", judge_log)
        assert w.end_report.get("ok"), f"前置 cluster-write 应过: {w.end_report}"

        # 2) cluster-save-state 14 步
        s = _run("cluster-save-state", judge_log)
        assert s.end_report.get("ok"), f"save-state end_report 应 ok: {s.end_report}"
        done = [o for o in s.completed if o.status == "completed"]
        assert len(done) >= 12, f"save-state 应基本走完 14 步·实际完成 {len(done)}: " \
            f"{[(o.n, o.status) for o in s.completed]}"
        # archivist judge 真被派发（factual 权威源·非 writer 自报）
        assert "novel-archivist" in judge_log, \
            f"archivist 未派发（factual 回库链断）: {judge_log}"

        db = sb.proj / "_数据库"
        # —— 骨架断言：故事块摘要.json 被 build-cluster-summary 真更新（step8）——
        ledger = db / "故事块摘要.json"
        assert ledger.exists(), "save_state build-cluster-summary 未产 故事块摘要.json"
        lj = json.loads(ledger.read_text(encoding="utf-8"))
        assert lj.get("clusters"), f"故事块摘要 clusters 应非空: {list(lj)}"

        # —— 骨架断言：emergence 真涌现下个 cluster 写回 事件簇.json（step13·走向卡）——
        shijianji = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        clusters = shijianji.get("clusters", [])
        # auto_pilot 选定第一候选后应写入 clusters[1]（下个 cluster brief）·或完本短路
        bc = db / ".book_complete.json"
        assert len(clusters) >= 2 or bc.exists(), \
            f"emergence 应涌现下个 cluster 或标完本·clusters={len(clusters)} book_complete={bc.exists()}"


def _seed_fate_pool(proj):
    """覆写 大势卡.json 给 ME 池注入未完成 ME（让 emergence 涌现候选而非完本短路）。

    find_remaining_mes 读 major_events_pool·滤掉 status=='completed'→remaining；有 remaining
    则 emerge 涌现候选（打分全 0 也有 remaining[:3] fallback）。volume:1 + 末个 is_volume_finale。"""
    import json as _json
    pool = {"major_events_pool": [
        {"id": "ME_001", "title": "规则觉醒", "volume": 1, "status": "scheduled",
         "description": "主角识破第一条隐藏规则，被卷入更深的诡异体系。"},
        {"id": "ME_002", "title": "同盟反目", "volume": 1, "status": "scheduled",
         "description": "看似可靠的同伴暴露立场，信任崩塌。"},
        {"id": "ME_003", "title": "阶段终局", "volume": 1, "status": "scheduled",
         "is_volume_finale": True, "stakes_delta": "+强",
         "description": "本阶段核心反派现身，主角力量/身份跃迁。"},
    ]}
    (proj / "_数据库" / "大势卡.json").write_text(
        _json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def test_save_state_emergence_emerges_next_cluster():
    """save-state step13 涌现路径（非完本短路）：ME 池有余 → emerge 出候选 →
    outline-planner judge(fake) → 走向卡 auto_pilot 取第一候选 → cluster_choice_apply
    写回 事件簇.json.clusters[1]。覆盖产品核心涟漪/涌现 + 走向卡选定写回链路。"""
    judge_log = []
    with _Sandbox() as sb:
        _seed_min_subsystems(sb.proj)
        _seed_fate_pool(sb.proj)                 # ← 关键：注入未完成 ME 池
        w = _run("cluster-write", judge_log)
        assert w.end_report.get("ok"), f"前置 cluster-write 应过: {w.end_report}"
        s = _run("cluster-save-state", judge_log)
        assert s.end_report.get("ok"), f"save-state end_report 应 ok: {s.end_report}"

        db = sb.proj / "_数据库"
        bc = db / ".book_complete.json"
        assert not bc.exists(), "ME 池有余·不该判完本（应涌现候选）"
        shijianji = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        clusters = shijianji.get("clusters", [])
        assert len(clusters) >= 2, \
            f"涌现应写回下个 cluster·clusters={[c.get('cluster_id') for c in clusters]}"
        nxt = clusters[1]
        # 走向卡 auto_pilot 选定后·cluster_choice_apply 应把候选落为下个 cluster brief
        assert nxt.get("scope_summary") or nxt.get("status"), \
            f"下个 cluster 应有 scope_summary/status: {nxt}"
        # outline-planner judge 真被派发（非完本短路时才走 judge）
        assert "novel-outline-planner" in judge_log, \
            f"涌现路径应派 outline-planner judge: {judge_log}"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[nm]()
            print(f"  [OK] {nm}")
        except Exception as e:
            fails += 1
            import traceback
            print(f"  [FAIL] {nm}: {e}")
            traceback.print_exc()
    sys.exit(1 if fails else 0)
