#!/usr/bin/env python3
"""judge 金标准 M2 真 gen-model 验证（2026-06-17 · PROGRAM_DRIVEN.md「上线前必验」§1 里程碑）。

文档原话：「8 个 judge 的金标准对比（真作者原文当输入·对比 JudgeReport 字段完整性 +
作者档维度引用）尚未跑——M2 验证里程碑」。本测试用**真作者原文 + 真作者风格档 + 真 gen-model**
跑 judge_runner，验证：
  ① 字段完整性：每个 judge 在真实输入上产出满足 AGENT_SPECS.required_keys 的结构化 JSON
     （outcome.ok=True）——抓「gen-model judge 在真输入上截断/schema 破损」（fake 掩盖不了）。
  ② 作者档第一权威存续：needs_author_profile 的 judge 真注入了作者风格档
     （outcome.author_profile_missing=False）——绝非退回通用规则审稿（惊悚乐园流水账实证教训）。
  ③ 报告真落盘。

金标准输入：workspace/styles/惊悚乐园/（真作者档 44K + 原文/第001章.txt 真作者实际章节）。
「真作者原文当系统生成喂校验」= 测矫枉过正金标准（memory reference_system_validation_method）。

🔴 隔离/运行（见 memory feedback_real_api_tests_no_economize）：tests/real_api/ 子目录不被默认
runner glob + RUOYU_RUN_REAL_API=1 门控。elysiver 端点 2026-06-17 故障 → 默认用 pie-xian 的
gemini_pro_preview（可 RUOYU_REAL_API_PROFILE 覆盖）。max_tokens 用 spec/profile 满值不缩。

运行：
    RUOYU_RUN_REAL_API=1 python tests/real_api/test_real_judge_golden.py
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
# elysiver(.env active) 2026-06-17 端点故障 → 金标准默认用已验可用的 pie-xian gemini_pro_preview
PROFILE_NAME = os.environ.get("RUOYU_REAL_API_PROFILE", "gemini_pro_preview")

_STYLE_SRC = _TESTS.parent / "workspace" / "styles" / "惊悚乐园"

# 测哪些 judge（2 block 字段完整性 + 3 needs_author_profile 作者档引用）
_JUDGES = [
    ("novel-summarizer", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}, False),
    ("novel-foreshadower", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}, False),
    ("novel-voice-checker", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}, True),
    ("novel-validator-checker", {"CLUSTER_ID": "cluster_001", "MODE": "cluster"}, True),
    ("novel-reading-reflector",
     {"CLUSTER_ID": "cluster_001", "MODE": "cluster", "ROUND": "1"}, True),
]


def _seed_project(tmp: Path) -> tuple[Path, Path]:
    """种沙盒项目：真作者风格档 + skill + 真作者原文当 cluster 草稿。返回 (project_root, draft_path)。"""
    proj = tmp / "金标准书"
    db = proj / "_数据库"
    db.mkdir(parents=True)
    # 真作者风格档（第一权威）
    shutil.copy2(_STYLE_SRC / "作者风格.json", db / "作者风格.json")
    skill = _STYLE_SRC / "skill_FINAL.md"
    if skill.exists():
        shutil.copy2(skill, db / "作者风格_skill.md")
    # 真作者原文当输入（金标准：真作者原文喂 judge）
    src_txt = _STYLE_SRC / "原文" / "第001章.txt"
    draft = proj / "章节" / "cluster_001_draft" / "cluster_001_draft.txt"
    draft.parent.mkdir(parents=True)
    draft.write_text(src_txt.read_text(encoding="utf-8"), encoding="utf-8")
    return proj, draft


def test_judge_golden_standard_real_gen_model():
    if not GATE:
        print("[SKIP] 未设 RUOYU_RUN_REAL_API=1 → 跳过 judge 金标准真 API 测试")
        return
    assert _STYLE_SRC.exists(), f"金标准风格库缺失: {_STYLE_SRC}"

    profile = gml.GenModelLoader().get_profile(PROFILE_NAME)
    assert getattr(profile, "api_key", ""), f"profile {PROFILE_NAME} 无 api_key"

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proj, draft = _seed_project(tmp)
        cjk = sum(1 for ch in draft.read_text(encoding="utf-8") if "一" <= ch <= "鿿")
        print(f"\n[GOLDEN] 真作者原文 {cjk} CJK 喂 {len(_JUDGES)} judge · profile={PROFILE_NAME}\n")

        results = []
        for agent, params, needs_profile in _JUDGES:
            out_path = proj / "_数据库" / ".golden" / f"{agent}.json"
            try:
                outcome = jr.run_judge(
                    agent, proj,
                    params=params,
                    context_files=[("cluster 草稿（真作者原文）", draft)],
                    output_path=out_path,
                    loader=[profile])     # 真 gen-model（pie-xian）·max_tokens 用 spec 满值
            except jr.JudgeBlockedError as e:
                # block 级 judge 在真输入上结构破损/截断 = M2 抓到的真 bug
                results.append((agent, "BLOCKED", str(e)[:120]))
                print(f"  [BLOCKED] {agent}: {str(e)[:120]}")
                continue
            am = outcome.author_profile_missing
            results.append((agent, "ok" if outcome.ok else "degraded",
                            f"author_missing={am} written={outcome.output_path.exists() if outcome.output_path else False}"))
            print(f"  [{'OK' if outcome.ok else 'DEGRADED'}] {agent}: "
                  f"ok={outcome.ok} degraded={outcome.degraded} "
                  f"author_profile_missing={am} retries={outcome.retries}")
            # ② 作者档第一权威：needs_author_profile 的 judge 必须真注入作者档
            if needs_profile:
                assert am is False, \
                    f"{agent} 是 needs_author_profile·必须注入作者档（实际 author_profile_missing={am}·退回通用规则=违北极星⑤）"
            # ③ 报告真落盘 + 含 required_keys
            if outcome.ok:
                assert outcome.output_path and outcome.output_path.exists(), \
                    f"{agent} JudgeReport 未落盘"
                data = json.loads(outcome.output_path.read_text(encoding="utf-8"))
                spec = jr.AGENT_SPECS[agent]
                missing = [k for k in spec.required_keys if k not in data]
                assert not missing, f"{agent} JudgeReport 缺 required_keys: {missing}"

        # ① 字段完整性总闸：block 级 judge（summarizer/foreshadower）必须真产出（不可 BLOCKED）
        blocked_block = [a for a, st, _ in results
                         if st == "BLOCKED" and jr.AGENT_SPECS[a].failure_policy == "block"]
        assert not blocked_block, \
            f"block 级 judge 在真作者原文上结构破损（M2 真 bug·gen-model 截断/schema）: {blocked_block}"
        print(f"\n[GOLDEN] 汇总: " + " · ".join(f"{a}={st}" for a, st, _ in results))


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
