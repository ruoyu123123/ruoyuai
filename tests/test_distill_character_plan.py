#!/usr/bin/env python3
"""distill-character 纳 plan 强制规划 + 同栈 voice_sample + 回灌 verify 测试（🔴 2026-06-27 C13）。

零依赖范式（文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]）。覆盖：
  · plan 6 步结构 + 同栈 gen_creative voice_sample 落点 + verify advisory exit 语义 + provenance 硬项
  · plan_tracker 注册（KNOWN_COMMANDS + load_template + {key} 替换）
  · gen_creative --mode voice_sample 消除 NotImplementedError（同栈·喂历史 few-shot）
  · distill_character_verify PROCESS-INTEGRITY 硬（同栈/≥2章 provenance）· fidelity 永 advisory
  · CLI exit：valid→0 / 破损+strict→2 / 破损无strict→0(advisory) / fidelity 低→0(永不据此拦)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gen_creative as gc            # noqa: E402
import distill_character_verify as dcv  # noqa: E402
import plan_tracker as pt            # noqa: E402

PLAN_PATH = _ROOT / "core" / "claude-home" / "plans" / "distill-character.plan.json"


def _load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


# ════════════ plan 结构 ════════════

def test_plan_exists_6_steps():
    """6 步全 required（2026-07 收口：step6 git-snapshot 从 optional 升 required）。

    历史契约 required=[1..5] / optional=[6]（git 失败仅记录不阻断）已废——
    对齐 CLAUDE.md Git 快照纪律「init/commit 失败或 marker 缺失 = 当前
    required step 失败」+ 不降级规则（必需步骤设 required 不留 advisory 兜底）。
    """
    plan = _load_plan()
    assert plan["command"] == "distill-character"
    assert plan["total_steps"] == 6
    assert len(plan["steps"]) == 6
    assert plan["required_steps"] == [1, 2, 3, 4, 5, 6]
    assert plan["optional_steps"] == []


def test_step6_git_snapshot_required_with_marker():
    """回归锁：step6 git-snapshot 必须 required + marker 产物（禁回退 optional/`? ` 前缀）。"""
    plan = _load_plan()
    step6 = next(s for s in plan["steps"] if s["n"] == 6)
    assert step6["required"] is True
    assert step6["optional"] is False
    assert step6["skip_output_allowed"] is False
    scripts = step6.get("scripts", [])
    assert scripts and not any(s.lstrip().startswith("?") for s in scripts), \
        "git-snapshot 脚本禁用 `? ` 容错前缀（失败必须暴露）"
    blob = " ".join(scripts)
    assert "git_snapshot.py" in blob and "--marker" in blob
    outs = " ".join(step6["expected_outputs"])
    assert "distill_character_{key}_git_snapshot.json" in outs, \
        "marker 文件 = required step 的可验证产物（缺失 = step 失败）"


def test_step3_voice_sample_is_genmodel_same_stack():
    """step3 必须走 gen_creative.py --mode voice_sample（同栈 gen-model·非 Claude 编）。"""
    plan = _load_plan()
    step3 = next(s for s in plan["steps"] if s["n"] == 3)
    scripts = " ".join(step3.get("scripts", []))
    assert "gen_creative.py" in scripts
    assert "--mode voice_sample" in scripts
    assert "--history" in scripts          # 喂角色历史真实对白 few-shot（同栈实战 voice 锚点）
    assert "{key}_voice_samples.json" in " ".join(step3["expected_outputs"])


def test_step5_verify_advisory_exit_semantics():
    """step5 = distill_character_verify.py --strict · exit_codes {0:ok,2:fail}。"""
    plan = _load_plan()
    step5 = next(s for s in plan["steps"] if s["n"] == 5)
    scripts = " ".join(step5.get("scripts", []))
    assert "distill_character_verify.py" in scripts
    assert "--strict" in scripts
    ec = step5["control_flow"]["exit_codes"]
    assert ec == {"0": "ok", "2": "fail"}
    assert "voice_verify_{key}.json" in " ".join(step5["expected_outputs"])


def test_plan_documents_process_integrity_and_provenance():
    """plan 文本须明确：唯一 hard=PROCESS-INTEGRITY·fidelity advisory·≥2章 provenance·同栈。"""
    blob = json.dumps(_load_plan(), ensure_ascii=False)
    for kw in ("PROCESS-INTEGRITY", "advisory", "provenance", "同栈", "gen-model"):
        assert kw in blob, f"plan 缺关键约束语义: {kw}"


def test_plan_referenced_scripts_exist():
    """plan 引用的 core/scripts/*.py 必须真实存在（防幽灵引用）。"""
    plan = _load_plan()
    import re
    ref_re = re.compile(r"core[/\\]scripts[/\\][A-Za-z0-9_]+\.py")
    for step in plan["steps"]:
        for line in step.get("scripts", []):
            for m in ref_re.findall(line):
                assert (_ROOT / m.replace("\\", "/")).exists(), f"幽灵引用: {m}"


# ════════════ plan_tracker 注册 ════════════

def test_registered_in_known_commands():
    assert "distill-character" in pt.KNOWN_COMMANDS


def test_load_template_and_key_substitution():
    """cluster-only 契约（2026-07 收口）：_substitute(text, project, key) 三参。

    历史签名 _substitute(text, project, chapter, key) 的章号参数已随
    cluster-only 架构整体拆除（plan 模板禁用 {ch...} 占位符·遇到即抛错，
    防止旧单章路径被静默替换为空）。
    """
    tpl = pt.load_template("distill-character")
    assert len(tpl["steps"]) == 6
    # {key} 替换：角色 id 注入 expected_outputs / scripts
    out = pt._substitute("_数据库/.distill_character/{key}_material.json",
                         "书名", "李若渝")
    assert out == "_数据库/.distill_character/李若渝_material.json"
    # 回归锁：模板残留旧章号占位符 = 直接报错（禁静默替空）
    import pytest
    with pytest.raises(ValueError):
        pt._substitute("章节/ch_{ch:03d}.txt", "书名", "李若渝")


def test_create_plan_substitutes_character_key():
    """端到端：create 一个 distill-character plan（隔离 tmp 项目）·验 {key} 落实。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "_数据库").mkdir(parents=True)
        plan_id = pt.create_plan("distill-character", str(proj), key="李若渝")
        assert plan_id
        plan = pt.get_plan(plan_id)
        step1 = next(s for s in plan["steps"] if s["n"] == 1)
        assert "李若渝_material.json" in step1["expected_outputs"][0]
        step3 = next(s for s in plan["steps"] if s["n"] == 3)
        assert "--character 李若渝" in " ".join(step3["scripts"])


# ════════════ gen_creative voice_sample（同栈·消 NotImplementedError）════════════

def test_voice_sample_prompt_no_longer_raises():
    system, user = gc.build_voice_sample_prompt(
        character_id="char_li", character_name="李若渝",
        history_quotes="- 「滚开。」（ch3）\n- 「我不需要谁帮我。」（ch5）",
        voice_dna_text="layer_0: 绝不示弱", count=4)
    assert "李若渝" in user
    assert "滚开" in user                         # 历史对白 few-shot 注入
    assert "style_samples" in system and "anti_samples" in system
    assert "JSON" in system


def test_voice_sample_history_text_from_material():
    with tempfile.TemporaryDirectory() as tmp:
        mat = Path(tmp) / "m.json"
        mat.write_text(json.dumps({
            "character": "李若渝",
            "dialogue_quotes": [{"text": "滚开。", "from_chapter": 3},
                                {"text": "我自己来。", "from_chapter": 5}],
        }, ensure_ascii=False), encoding="utf-8")
        txt = gc._voice_sample_history_text(mat)
        assert "滚开。" in txt and "ch3" in txt
        assert "我自己来。" in txt


def test_voice_sample_parse_loose():
    d = gc.parse_voice_sample_output('```json\n{"style_samples":[{"text":"x"}]}\n```')
    assert d["style_samples"][0]["text"] == "x"
    # 垃圾输入 → 空骨架（不崩）
    d2 = gc.parse_voice_sample_output("not json at all")
    assert d2["style_samples"] == [] and "_raw" in d2


# ════════════ distill_character_verify PROCESS-INTEGRITY（唯一 hard）════════════

def test_sample_provenance_ok():
    assert dcv.sample_provenance_ok({"text": "x", "from_chapters": [3, 5]}) is True
    assert dcv.sample_provenance_ok({"text": "x", "from_chapters": [3]}) is False   # <2 章
    assert dcv.sample_provenance_ok({"text": "x", "from_chapters": [3, 3]}) is False  # 不去重不算
    assert dcv.sample_provenance_ok("纯字符串无 provenance") is False
    assert dcv.sample_provenance_ok({"text": "x"}) is False


_VALID_VP = {
    "_gen_provenance": {"generated_by_model": "gemini-3.1-pro-preview",
                        "generated_by_profile": "gemini_pro_preview"},
    "style_samples": [{"text": "滚开，我不需要。", "from_chapters": [3, 5]}],
    "anti_samples": [{"text": "好的，我会理性沟通。", "from_chapters": [3, 7]}],
    "banned_phrases": ["请", "麻烦您"],
    "catchphrases": ["滚开"],
}


def test_process_integrity_valid_pack_ok():
    ok, violations = dcv.check_process_integrity(_VALID_VP)
    assert ok is True, violations


def test_process_integrity_missing_same_stack():
    vp = json.loads(json.dumps(_VALID_VP))
    del vp["_gen_provenance"]
    ok, violations = dcv.check_process_integrity(vp)
    assert ok is False
    assert any(v["code"] == "SAME_STACK_PROVENANCE_MISSING" for v in violations)


def test_process_integrity_insufficient_provenance():
    vp = json.loads(json.dumps(_VALID_VP))
    vp["style_samples"] = [{"text": "单章特色用法", "from_chapters": [3]}]
    ok, violations = dcv.check_process_integrity(vp)
    assert ok is False
    assert any(v["code"] == "INSUFFICIENT_PROVENANCE" for v in violations)


def test_process_integrity_banned_phrases_first_explicit_ok():
    """banned_phrases 首次明确即可入列（不要求 ≥2 源·单元素 list 合法）。"""
    vp = json.loads(json.dumps(_VALID_VP))
    vp["banned_phrases"] = ["唯一一次出现的禁说词"]
    ok, violations = dcv.check_process_integrity(vp)
    assert ok is True, violations
    # 但 banned_phrases 结构破损（非 list）→ 违规
    vp["banned_phrases"] = "不是列表"
    ok2, violations2 = dcv.check_process_integrity(vp)
    assert ok2 is False
    assert any(v["code"] == "BANNED_PHRASES_MALFORMED" for v in violations2)


def test_no_samples_is_vacuously_ok():
    """无样本（没产/没并）= 非破损·vacuous ok（不误把空 voice_pack 当契约破损）。"""
    ok, violations = dcv.check_process_integrity({"banned_phrases": []})
    assert ok is True and violations == []


def test_fidelity_always_advisory():
    fid = dcv.voice_fidelity_estimate(
        ["短句。", "他摔了杯子。"], ["滚开。", "我自己来。"], _VALID_VP)
    assert fid["gate_level"] == "advisory"
    assert fid["verdict"] == "advisory"


def test_build_report_integrity_drives_ok():
    report, ok = dcv.build_report(_VALID_VP, None, "李若渝")
    assert ok is True
    assert report["process_integrity"]["gate_level"] == "hard_gate"
    assert report["voice_fidelity"]["gate_level"] == "advisory"


# ════════════ CLI exit 语义（contract/provenance 破损非 fidelity delta）════════════

def _make_project(tmp: Path, voice_pack: dict) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True)
    doc = {"characters": [{"id": "李若渝", "name": "李若渝", "voice_pack": voice_pack}]}
    (proj / "_数据库" / "人物卡.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return proj


def _run_verify(proj: Path, strict: bool) -> tuple[int, Path]:
    out = proj / "对比报告" / "voice_verify_李若渝.json"
    cmd = [sys.executable, str(_SCRIPTS / "distill_character_verify.py"),
           "--project", str(proj), "--character", "李若渝",
           "--output", str(out), "--skip-genmodel"]
    if strict:
        cmd.append("--strict")
    # 子进程 stderr 是 UTF-8 中文（脚本 reconfigure）·父进程显式 utf-8 解码（Windows 默认 GBK 会炸）
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode, out


def test_cli_valid_exit0_and_report_written():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_project(Path(tmp), _VALID_VP)
        rc, out = _run_verify(proj, strict=True)
        assert rc == 0, rc
        assert out.exists(), "报告必须落盘"
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["process_integrity"]["ok"] is True


def test_cli_broken_provenance_strict_exit2():
    """provenance 破损 + --strict → exit 2（拦在出货前）·报告仍落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        bad = json.loads(json.dumps(_VALID_VP))
        del bad["_gen_provenance"]            # 同栈证据缺失 = 契约破损
        proj = _make_project(Path(tmp), bad)
        rc, out = _run_verify(proj, strict=True)
        assert rc == 2, rc
        assert out.exists()


def test_cli_broken_provenance_no_strict_exit0():
    """同样破损但无 --strict → exit 0（advisory 放行·不阻断中途校准）。"""
    with tempfile.TemporaryDirectory() as tmp:
        bad = json.loads(json.dumps(_VALID_VP))
        del bad["_gen_provenance"]
        proj = _make_project(Path(tmp), bad)
        rc, _ = _run_verify(proj, strict=False)
        assert rc == 0, rc


def test_cli_low_fidelity_still_exit0():
    """fidelity 差（句长/AI 套话）但 PROCESS-INTEGRITY 过 → exit 0（绝不据 fidelity delta 拦）。"""
    with tempfile.TemporaryDirectory() as tmp:
        vp = json.loads(json.dumps(_VALID_VP))
        # 样本塞 AI 套话 + 句长偏离·但 provenance/同栈完好
        vp["style_samples"] = [{"text": "与此同时，他淡淡地说，微微挑眉，"
                                "嘴角勾起一抹意味深长的弧度，仿佛一切尽在掌握。",
                                "from_chapters": [3, 5]}]
        proj = _make_project(Path(tmp), vp)
        rc, out = _run_verify(proj, strict=True)
        assert rc == 0, "fidelity 低永不拦截（只 PROCESS-INTEGRITY 硬）"
        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["voice_fidelity"]["ai_slop_leaks"], "应捕获 AI 套话泄漏（advisory）"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
