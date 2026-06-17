#!/usr/bin/env python3
"""完整真实写作循环 真 gen-model 端到端（2026-06-17 · 写作主轨真 API 收官）。

cluster-write（真 writer + 真 judge + 真 splitter）→ cluster-save-state（真 summarizer/
foreshadower/reflector + 真 outline-planner 涌现走向卡 + 真确定性 step8/9）。这是唯一覆盖
**完整真实写作循环 + outline-planner judge 真模型生成走向卡**的测试（M2 测了其余 5 judge·
本测试补 outline-planner + 全循环真模型集成）。

互补：fake-LLM P0/P1 验确定性骨架·M2 验 judge 字段完整性·本测试验**真模型跑通整个 write→
save→emerge 循环**（抓 fake 掩盖的真集成 bug）。

隔离/运行：tests/real_api/ + RUOYU_RUN_REAL_API=1 门控。elysiver 故障 → 默认 pie-xian。
~20-30 个真 API 调用·GEN_MIN_INTERVAL_S 限流防 520。max_tokens 满值。
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_write_save_loop.py
"""
import json
import os
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TESTS))
sys.path.insert(0, str(_TESTS.parent / "core" / "scripts"))

import orchestrator as orc  # noqa: E402

from test_cluster_write_fake_llm_e2e import (  # noqa: E402
    _ROOT, _Sandbox, _seed_min_subsystems, _utf8_io, _frozen_environ,
    _inprocess_runner,
)
from test_cluster_save_state_fake_llm_e2e import _seed_fate_pool  # noqa: E402
from test_real_cluster_write_e2e import _force_profile  # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
PROFILE_NAME = os.environ.get("RUOYU_REAL_API_PROFILE", "gemini_pro_preview")

# 真风格档源（惊悚乐园·恐怖向·配 育新中学规则诡谈 scope）。
_STYLE_SRC = _TESTS.parent / "workspace" / "styles" / "惊悚乐园"


def _seed_real_style(proj):
    """覆盖空脚手架作者风格档为**真实风格**（惊悚乐园 44K + skill）→ writer 有真 voice + 厚
    manifest → 产健康长度稿（12000-25000 CJK·多章）。

    🔴 关键（2026-06-17 用户定调）：0 章 cluster 设计上不该发生（每块 ≥13000 字）。之前用
    空脚手架风格档种子 → 模型无素材 → 短稿 2580 → 0 章·是**测试种子不真实造的假象非真 bug**。
    种真风格档让测试反映真实生产（writer freestyle min_cjk=12000 软下限在有料时自然达标）。"""
    import shutil
    db = proj / "_数据库"
    src = _STYLE_SRC / "作者风格.json"
    if src.exists():
        shutil.copy2(src, db / "作者风格.json")
    skill = _STYLE_SRC / "skill_FINAL.md"
    if skill.exists():
        shutil.copy2(skill, db / "作者风格_skill.md")


def _cjk(s: str) -> int:
    return sum(1 for ch in s if "一" <= ch <= "鿿")


def _run(command, sb):
    with _frozen_environ(), _utf8_io():
        os.environ.setdefault("GEN_MIN_INTERVAL_S", "4.5")
        os.environ["BEST_OF_N"] = "1"
        return orc.run_command(
            command, "冒烟书", key="001",
            script_runner=_inprocess_runner, judge_dispatch=None,
            pause_handler=None, auto_pilot=True, repo_root=_ROOT)


def test_real_full_write_save_loop():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过完整真实写作循环测试")
        return
    with _Sandbox() as sb, _force_profile(PROFILE_NAME):
        _seed_min_subsystems(sb.proj)
        _seed_real_style(sb.proj)             # 真风格档 → writer 有料产健康稿（避免空种子→短稿→0章假象）
        _seed_fate_pool(sb.proj)              # 注入未完成 ME 池 → emergence 涌现走向卡（测 outline-planner）
        db = sb.proj / "_数据库"

        # 1) 真 cluster-write
        w = _run("cluster-write", sb)
        assert w.end_report.get("ok"), f"真 cluster-write 应跑通: {w.end_report}"
        draft = sb.proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"
        assert draft.exists(), "真 writer 未产 draft"
        n = _cjk(draft.read_text(encoding="utf-8"))
        print(f"\n[REAL-LOOP] 真 writer {n} CJK")
        assert n > 5000, f"真模型正文偏短 {n} CJK"

        # 2) 真 cluster-save-state（真 judge + 真 outline-planner 涌现走向卡）
        s = _run("cluster-save-state", sb)
        assert s.end_report.get("ok"), f"真 save-state 应跑通: {s.end_report}"
        done = [o for o in s.completed if o.status == "completed"]
        assert len(done) >= 10, f"save-state 应基本走完·完成 {len(done)}"

        # —— 真 build-cluster-summary ——
        ledger = json.loads((db / "故事块摘要.json").read_text(encoding="utf-8"))
        assert ledger.get("clusters"), "真 save-state 未更新 故事块摘要"

        # —— 真 outline-planner 涌现走向卡 → clusters[1]（ME 池有余·非完本）——
        bc = db / ".book_complete.json"
        assert not bc.exists(), "ME 池有余·不该判完本"
        shijianji = json.loads((db / "事件簇.json").read_text(encoding="utf-8"))
        clusters = shijianji.get("clusters", [])
        assert len(clusters) >= 2, \
            f"真 outline-planner 应涌现下个 cluster: {[c.get('cluster_id') for c in clusters]}"
        nxt = clusters[1]
        assert nxt.get("scope_summary"), f"真涌现的走向卡应有 scope_summary: {nxt}"
        print(f"[REAL-LOOP] 真 emergence 涌现下个 cluster: "
              f"{nxt.get('cluster_id')} scope={str(nxt.get('scope_summary'))[:60]}")
        # ASCII-only（Windows GBK 控制台·这些 print 在 _utf8_io 上下文之外·勿用非 GBK 字符）
        print("[REAL-LOOP] [OK] 完整真实写作循环 write-save-emerge 真模型跑通")


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
