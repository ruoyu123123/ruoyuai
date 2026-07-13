#!/usr/bin/env python3
"""gen_creative distill_reflect --verify 测试（skill 由 novel-skill-author 亲笔·脚本只验收）。

验：五必备小节+≥200 字确定性验收门；验收不过 exit 2（主代理重 spawn agent）；
缺 --verify 的旧 LLM 生成入口已删（响亮拒绝 exit 1）。
"""
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_creative as gc  # noqa: E402

_GOOD_MD = """# 作者风格 skill v2

## 句式与节奏
- 句长均值 28-34 字，长短交替，紧张处短句 3-7 字连发，舒缓处长句 30+ 字铺展收束。
- 单句独行占比约一半，制造呼吸感，避免连续长段堆砌造成的窒息感。

## 段落与标点
- 段落 2-4 句，段间用动作或感官细节过渡，不用「与此同时」等连接词硬接。
- 破折号克制（每千字 0.3 次以下），省略号用于话语停顿而非滥用。

## 对话工艺
- 对话推进剧情而非解释设定，台词间穿插动作节拍，有潜台词和筹码交换。
- 角色语气可区分，不同角色的口癖、句长、用词习惯各异。

## 描写与情绪
- 情绪靠动作外显（攥拳、呼吸粗重），不直写「他很愤怒」这类情绪词。
- 环境描写两字锚点定场后单点深入，不平铺罗列五感清单。

## 反模式（绝不做）
- 绝不用「与此同时」「值得一提的是」「不仅如此」等 AI 套话。
- 绝不在章末写总结感悟，章末是钩子不是收束。
"""


def _args(**kw):
    a = types.SimpleNamespace(skill=None, verify=True)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _run_main(argv: list[str]) -> int | None:
    old_argv = sys.argv[:]
    sys.argv = argv
    try:
        gc.main()
        return None
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old_argv


def test_verify_passes_valid_skill():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skill_v2.md"
        p.write_text(_GOOD_MD, encoding="utf-8")
        assert gc._verify_distill_skill(_args(skill=str(p))) == 0


def test_verify_rejects_too_short():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skill_v2.md"
        p.write_text("太短", encoding="utf-8")
        assert gc._verify_distill_skill(_args(skill=str(p))) == 2


def test_verify_rejects_missing_sections():
    """长但缺必备小节 → exit 2（agent 合约要求五小节全齐·验收强于旧容忍口径）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skill_v2.md"
        p.write_text("# skill\n\n" + ("一些内容。\n" * 60), encoding="utf-8")
        assert gc._verify_distill_skill(_args(skill=str(p))) == 2


def test_verify_rejects_single_missing_section():
    """恰缺 1 节也不放行（五小节全 required·不容 2 节缺失的旧降级口径）。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skill_v2.md"
        p.write_text(_GOOD_MD.replace("## 反模式（绝不做）", "## 别的东西"),
                     encoding="utf-8")
        assert gc._verify_distill_skill(_args(skill=str(p))) == 2


def test_verify_missing_file_exit2_for_respawn():
    """skill 未落盘 = agent 未产出 → exit 2（主代理重 spawn novel-skill-author）。"""
    with tempfile.TemporaryDirectory() as td:
        assert gc._verify_distill_skill(_args(skill=str(Path(td) / "nope.md"))) == 2


def test_verify_missing_skill_arg_exit1():
    assert gc._verify_distill_skill(_args(skill=None)) == 1


def test_verify_pure_function_rules():
    assert gc.verify_distill_skill_text(_GOOD_MD) == []
    errs = gc.verify_distill_skill_text("短")
    assert errs and any("过短" in e for e in errs)
    errs2 = gc.verify_distill_skill_text("x" * 300)
    assert errs2 and any("缺必备小节" in e for e in errs2)


def test_cli_distill_reflect_requires_verify():
    """旧 LLM 生成入口已删：--mode distill_reflect 不带 --verify → 响亮 exit 1。"""
    code = _run_main(["gen_creative.py", "--mode", "distill_reflect",
                      "--skill", "whatever.md"])
    assert code == 1, f"缺 --verify 应响亮拒绝 exit 1，实得 {code}"


def test_cli_distill_reflect_verify_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skill_v0.md"
        p.write_text(_GOOD_MD, encoding="utf-8")
        code = _run_main(["gen_creative.py", "--mode", "distill_reflect",
                          "--verify", "--skill", str(p)])
        assert code in (None, 0), f"合法 skill 应验收通过，实得 {code}"


def test_no_llm_pipeline_left_in_module():
    """LLM 生成管线彻底清除（不兼容不降级·旧路径删干净）。"""
    for gone in ("call_gen_model", "build_distill_reflect_prompt",
                 "build_brainstorm_prompt", "_run_distill_reflect",
                 "resolve_max_tokens", "check_deps"):
        assert not hasattr(gc, gone), f"旧 LLM 管线残留: {gone}"
    src = (_ROOT / "core" / "scripts" / "gen_creative.py").read_text(encoding="utf-8")
    for token in ("llm_transport", "GenModelLoader", "openai", "OpenAI"):
        assert token not in src, f"gen_creative.py 残留 LLM 依赖: {token}"


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
