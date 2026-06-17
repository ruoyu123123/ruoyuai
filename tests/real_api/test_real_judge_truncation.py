#!/usr/bin/env python3
"""深 schema judge 长输入截断率 §3 真 gen-model 验证（2026-06-17）。

PROGRAM_DRIVEN.md「已知边界·上线前必验」§3：「深 schema judge（validator-checker 16 维 /
outline-planner 3 模式）在 reasoning 模型上的截断率需实测；必要时拆分多次调用」。

M2（test_real_judge_golden）用短输入（2739 CJK·retries=0 无截断）；本测试用**长输入**
（凿窍纪 cluster_001_draft 10669 CJK）喂深 schema judge，验证：长 draft → judge 输出更长 →
是否 finish=length 截断 → transport 续写循环能否兜住到 ok=True（不 degraded）。

判定：长输入上每个深 schema judge 仍 ok=True（字段完整）= 截断被 transport 续写正确处理；
若 degraded/BLOCKED = §3 真缺口（截断率超出续写能力·需拆分多次调用）。

隔离/运行：tests/real_api/ 子目录 + RUOYU_RUN_REAL_API=1 门控。elysiver 故障 → 默认 pie-xian。
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_judge_truncation.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TESTS.parent / "core" / "scripts"))

import judge_runner as jr  # noqa: E402
import gen_model_loader as gml  # noqa: E402

GATE = os.environ.get("RUOYU_RUN_REAL_API")
PROFILE_NAME = os.environ.get("RUOYU_REAL_API_PROFILE", "gemini_pro_preview")

_NOVEL = _TESTS.parent / "workspace" / "novels" / "凿窍纪"
_LONG_DRAFT = _NOVEL / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"

# 深 schema judge（输出可能很长 → 易截断）
_DEEP_JUDGES = [
    ("novel-validator-checker", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}),
    ("novel-voice-checker", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}),
    ("novel-reading-reflector",
     {"CLUSTER_ID": "cluster_001", "MODE": "cluster", "ROUND": "1"}),
]


def test_deep_judge_truncation_on_long_input():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 §3 截断率真 API 测试")
        return
    assert _LONG_DRAFT.exists(), f"长 draft 缺失: {_LONG_DRAFT}"

    profile = gml.GenModelLoader().get_profile(PROFILE_NAME)
    assert getattr(profile, "api_key", ""), f"profile {PROFILE_NAME} 无 api_key"

    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "截断测试书"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        # 真作者档（凿窍纪）+ 长 draft
        src_profile = _NOVEL / "_数据库" / "作者风格.json"
        if src_profile.exists():
            shutil.copy2(src_profile, db / "作者风格.json")
        draft = proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"
        draft.parent.mkdir(parents=True)
        txt = _LONG_DRAFT.read_text(encoding="utf-8")
        draft.write_text(txt, encoding="utf-8")
        cjk = sum(1 for ch in txt if "一" <= ch <= "鿿")
        print(f"\n[§3-TRUNC] 长 draft {cjk} CJK 喂 {len(_DEEP_JUDGES)} 深 schema judge · profile={PROFILE_NAME}\n")

        degraded_judges = []
        for agent, params in _DEEP_JUDGES:
            out_path = db / ".trunc" / f"{agent}.json"
            try:
                outcome = jr.run_judge(
                    agent, proj, params=params,
                    context_files=[("cluster 草稿（长输入）", draft)],
                    output_path=out_path, loader=[profile])
            except jr.JudgeBlockedError as e:
                degraded_judges.append((agent, "BLOCKED", str(e)[:100]))
                print(f"  [BLOCKED] {agent}: {str(e)[:100]}")
                continue
            status = "ok" if outcome.ok else "degraded"
            print(f"  [{status.upper()}] {agent}: ok={outcome.ok} degraded={outcome.degraded} "
                  f"retries={outcome.retries} author_missing={outcome.author_profile_missing}")
            if not outcome.ok:
                degraded_judges.append((agent, status, f"retries={outcome.retries}"))
            else:
                # 字段完整 → 截断（若有）被 transport 续写兜住
                data = json.loads(outcome.output_path.read_text(encoding="utf-8"))
                spec = jr.AGENT_SPECS[agent]
                missing = [k for k in spec.required_keys if k not in data]
                assert not missing, f"{agent} 缺 required_keys: {missing}"

        # §3 判定：长输入上深 schema judge 应全 ok（截断被续写兜住）。有 degraded/BLOCKED =
        # 截断率超出续写能力的真缺口——记录为失败让 §3 缺口显形（需拆分多次调用）。
        assert not degraded_judges, (
            f"§3 缺口：深 schema judge 在长输入({cjk} CJK)上截断未被续写兜住 → "
            f"{degraded_judges}（需拆分多次调用·见 PROGRAM_DRIVEN.md §3）")
        print(f"\n[§3-TRUNC] 全部深 schema judge 在 {cjk} CJK 长输入上 ok（截断被 transport 续写正确兜住）")


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
