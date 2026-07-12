#!/usr/bin/env python3
"""distill-character 的 Claude 草稿、gemini 润色与回灌验证测试。"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import distill_character_verify as dcv  # noqa: E402
import plan_tracker as pt            # noqa: E402
import voice_sample_polisher as vsp  # noqa: E402
import voice_pack_merger as vpm  # noqa: E402

PLAN_PATH = _ROOT / "core" / "claude-home" / "plans" / "distill-character.plan.json"


def _load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


# ════════════ plan 结构 ════════════

def test_plan_exists_7_steps():
    plan = _load_plan()
    assert plan["command"] == "distill-character"
    assert plan["total_steps"] == 7
    assert len(plan["steps"]) == 7
    assert plan["required_steps"] == [1, 2, 3, 4, 5, 6, 7]
    assert plan["optional_steps"] == []


def test_step7_git_snapshot_required_with_marker():
    plan = _load_plan()
    step7 = next(s for s in plan["steps"] if s["n"] == 7)
    assert step7["required"] is True
    assert step7["optional"] is False
    assert step7["skip_output_allowed"] is False
    scripts = step7.get("scripts", [])
    assert scripts and not any(s.lstrip().startswith("?") for s in scripts), \
        "git-snapshot 脚本禁用 `? ` 容错前缀（失败必须暴露）"
    blob = " ".join(scripts)
    assert "git_snapshot.py" in blob and "--marker" in blob
    outs = " ".join(step7["expected_outputs"])
    assert "distill_character_{key}_git_snapshot.json" in outs, \
        "marker 文件 = required step 的可验证产物（缺失 = step 失败）"


def test_voice_sample_steps_use_claude_then_gemini():
    plan = _load_plan()
    step3 = next(s for s in plan["steps"] if s["n"] == 3)
    assert "{key}_claude_drafts" in " ".join(step3["expected_outputs"])
    assert step3["must_spawn_agent"] == "novel-replica-writer"
    assert step3["agent_input"]["MODE"] == "voice-sample-draft"
    assert step3["agent_input"]["CHARACTER_ID"] == "{key}"
    assert step3["judge_report_path"].endswith("agent_report.json")
    step4 = next(s for s in plan["steps"] if s["n"] == 4)
    scripts = " ".join(step4.get("scripts", []))
    assert "voice_sample_polisher.py" in scripts
    assert "--claude-drafts-dir" in scripts
    assert "{key}_voice_samples.json" in " ".join(step4["expected_outputs"])


def test_step6_verify_advisory_exit_semantics():
    plan = _load_plan()
    step6 = next(s for s in plan["steps"] if s["n"] == 6)
    scripts = " ".join(step6.get("scripts", []))
    assert "distill_character_verify.py" in scripts
    assert "--strict" in scripts
    ec = step6["control_flow"]["exit_codes"]
    assert ec == {"0": "ok", "2": "fail"}
    assert "voice_verify_{key}.json" in " ".join(step6["expected_outputs"])


def test_step5_has_deterministic_merge_receipt():
    step5 = next(s for s in _load_plan()["steps"] if s["n"] == 5)
    scripts = " ".join(step5["scripts"])
    assert "voice_pack_merger.py" in scripts
    assert "--receipt" in scripts
    assert "{key}_voice_pack_merged.json" in " ".join(step5["expected_outputs"])


def test_plan_documents_process_integrity_and_provenance():
    """plan 文本须明确：唯一 hard=PROCESS-INTEGRITY·fidelity advisory·≥2章 provenance·同栈。"""
    blob = json.dumps(_load_plan(), ensure_ascii=False)
    for kw in ("PROCESS-INTEGRITY", "advisory", "provenance", "Claude", "gemini"):
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
    assert len(tpl["steps"]) == 7
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
        step4 = next(s for s in plan["steps"] if s["n"] == 4)
        assert "--character 李若渝" in " ".join(step4["scripts"])


def test_voice_sample_polisher_discovers_valid_claude_drafts():
    with tempfile.TemporaryDirectory() as tmp:
        drafts = Path(tmp)
        (drafts / "sample_001.txt").write_text(
            json.dumps({"kind": "style", "text": "滚开。", "from_clusters": ["cluster_003", "cluster_005"], "dim": "短句"}, ensure_ascii=False),
            encoding="utf-8",
        )
        found = vsp.discover_drafts(drafts)
        assert found[0][1]["kind"] == "style"


def test_voice_sample_polisher_writes_same_stack_provenance(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, payload in {
        "sample_001.txt": {"kind": "style", "text": "滚开。", "from_clusters": ["cluster_003", "cluster_005"], "dim": "短句"},
        "sample_002.txt": {"kind": "anti", "text": "请大家理性沟通。", "from_clusters": ["cluster_003", "cluster_007"], "violates": "示弱"},
    }.items():
        (drafts / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    material = tmp_path / "material.json"
    voice_dna = tmp_path / "voice.json"
    material.write_text("{}", encoding="utf-8")
    voice_dna.write_text("{}", encoding="utf-8")
    class Profile:
        model = "gemini-test"
        name = "gemini_profile"
    def fake_call(loader, system, user, **kwargs):
        draft = json.loads(user)["draft"]
        return json.dumps({"kind": draft["kind"], "text": draft["text"]}, ensure_ascii=False), Profile(), 0.1
    monkeypatch.setattr(vsp, "call_gen_model", fake_call)
    monkeypatch.setattr(vsp, "GenModelLoader", lambda: object())
    output = tmp_path / "voice_samples.json"
    result = vsp.polish_samples(
        drafts_dir=drafts, material_path=material, voice_dna_path=voice_dna,
        character="李若渝", output=output,
    )
    assert result["_meta"]["writer_mode"] == "claude_draft_gemini_polish_v29"
    assert result["_meta"]["generated_by_model"] == ["gemini-test"]
    assert output.exists()


# ════════════ distill_character_verify PROCESS-INTEGRITY（唯一 hard）════════════

def test_sample_provenance_ok():
    assert dcv.sample_provenance_ok({"text": "x", "from_clusters": ["cluster_003", "cluster_005"]}) is True
    assert dcv.sample_provenance_ok({"text": "x", "from_clusters": ["cluster_003"]}) is False
    assert dcv.sample_provenance_ok({"text": "x", "from_clusters": ["cluster_003", "cluster_003"]}) is False  # 不去重不算
    assert dcv.sample_provenance_ok("纯字符串无 provenance") is False
    assert dcv.sample_provenance_ok({"text": "x"}) is False


_VALID_VP = {
    "_gen_provenance": {
        "writer_mode": "claude_draft_gemini_polish_v29",
        "claude_drafts_dir": "C:/project/claude_drafts",
        "generated_by_model": ["gemini-3.1-pro-preview"],
        "generated_by_profile": ["gemini_pro_preview"],
    },
    "style_samples": [{"text": "滚开，我不需要。", "from_clusters": ["cluster_003", "cluster_005"]}],
    "anti_samples": [{"text": "好的，我会理性沟通。", "from_clusters": ["cluster_003", "cluster_007"]}],
    "banned_phrases": ["请", "麻烦您"],
    "catchphrases": ["滚开"],
}


def test_process_integrity_valid_pack_ok():
    ok, violations = dcv.check_process_integrity(_VALID_VP)
    assert ok is True, violations


def test_process_integrity_empty_samples_fail():
    vp = json.loads(json.dumps(_VALID_VP))
    vp["style_samples"] = []
    vp["anti_samples"] = []
    ok, violations = dcv.check_process_integrity(vp)
    assert ok is False
    assert any(item["code"] == "VOICE_SAMPLES_MISSING" for item in violations)


def test_voice_pack_merger_writes_receipt(tmp_path):
    project = tmp_path / "novel"
    database = project / "_数据库"
    database.mkdir(parents=True)
    cards = database / "人物卡.json"
    cards.write_text(json.dumps({"characters": [{"id": "char_li", "name": "李若渝"}]}, ensure_ascii=False), encoding="utf-8")
    voice_dna = tmp_path / "voice_dna.json"
    voice_dna.write_text(json.dumps({"voice_dna": {"layer_0": {"never_say": ["请"]}}}, ensure_ascii=False), encoding="utf-8")
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps({
        "style_samples": [{"text": "滚开。", "from_clusters": ["cluster_003", "cluster_005"]}],
        "anti_samples": [{"text": "请理性沟通。", "from_clusters": ["cluster_003", "cluster_007"]}],
        "_meta": {
            "writer_mode": "claude_draft_gemini_polish_v29",
            "claude_drafts_dir": "drafts",
            "generated_by_model": ["gemini-test"],
            "generated_by_profile": ["profile"],
        },
    }, ensure_ascii=False), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    receipt = vpm.merge_voice_pack(
        cards_path=cards, character="char_li", voice_dna_path=voice_dna,
        samples_path=samples, receipt_path=receipt_path,
    )
    merged = json.loads(cards.read_text(encoding="utf-8"))["characters"][0]
    assert merged["voice_pack"]["_gen_provenance"]["writer_mode"] == "claude_draft_gemini_polish_v29"
    assert receipt["style_sample_count"] == 1
    assert receipt_path.exists()


def test_process_integrity_missing_same_stack():
    vp = json.loads(json.dumps(_VALID_VP))
    del vp["_gen_provenance"]
    ok, violations = dcv.check_process_integrity(vp)
    assert ok is False
    assert any(v["code"] == "SAME_STACK_PROVENANCE_MISSING" for v in violations)


def test_process_integrity_insufficient_provenance():
    vp = json.loads(json.dumps(_VALID_VP))
    vp["style_samples"] = [{"text": "单故事块偶发用法", "from_clusters": ["cluster_003"]}]
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


def test_no_samples_is_required_failure():
    """required 角色蒸馏不得以空 voice_pack 完成。"""
    ok, violations = dcv.check_process_integrity({"banned_phrases": []})
    assert ok is False
    assert any(item["code"] == "VOICE_SAMPLES_MISSING" for item in violations)


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
           "--output", str(out)]
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
                                "from_clusters": ["cluster_003", "cluster_005"]}]
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
