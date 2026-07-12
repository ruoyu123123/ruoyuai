# -*- coding: utf-8 -*-
"""reasoning_effort 全链路回归网。

elysiver(new-api 中转)忽略 gemini 专有 thinking_level → thinking 暴走；认 OpenAI 标准
reasoning_effort。全链路统一「thinking_level OR reasoning_effort 都算 reasoning·都注入 extra_body」。

覆盖：
1. Profile 解析 reasoning_effort（真实调 loader·tmp .env）—— 防解析漂移
2. extra_body 注入逻辑（thinking_level/reasoning_effort 独立共存）—— 直接测单一真理源 gen_model_loader.reasoning_extra_body
3. reasoning 检测（thinking_level OR reasoning_effort）—— 锁 distill_replicate draft-refine auto-off 语义

零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_model_loader as gml  # noqa: E402


def _loader_for(td, body):
    env = Path(td) / ".env"
    env.write_text(body, encoding="utf-8")
    return gml.GenModelLoader(env_path=env)


_BASE = "GEN__{n}__MODEL=m\nGEN__{n}__BASE_URL=u\nGEN__{n}__API_KEY=k\n"


# ============ 1. Profile 解析（真实调 loader）============
def test_reasoning_effort_parsed_lowercased():
    """GEN__x__REASONING_EFFORT=LOW → profile.reasoning_effort='low'（strip+lower）。"""
    with tempfile.TemporaryDirectory() as td:
        loader = _loader_for(td, _BASE.format(n="x") + "GEN__x__REASONING_EFFORT=LOW\n")
        p = loader.get_profile("x")
        assert p.reasoning_effort == "low", p.reasoning_effort
        assert p.thinking_level is None


def test_thinking_level_independent_of_reasoning_effort():
    """只配 thinking_level → reasoning_effort=None（两者独立解析）。"""
    with tempfile.TemporaryDirectory() as td:
        loader = _loader_for(td, _BASE.format(n="y") + "GEN__y__THINKING_LEVEL=low\n")
        p = loader.get_profile("y")
        assert p.thinking_level == "LOW", p.thinking_level
        assert p.reasoning_effort is None


def test_both_parsed_independently():
    """同 profile 同时配 thinking_level + reasoning_effort → 都解析（独立字段）。"""
    with tempfile.TemporaryDirectory() as td:
        loader = _loader_for(td, _BASE.format(n="z")
                             + "GEN__z__THINKING_LEVEL=HIGH\nGEN__z__REASONING_EFFORT=medium\n")
        p = loader.get_profile("z")
        assert p.thinking_level == "HIGH"
        assert p.reasoning_effort == "medium"


def test_neither_for_non_reasoning_profile():
    """都不配（flash 等非 reasoning）→ 都 None。"""
    with tempfile.TemporaryDirectory() as td:
        loader = _loader_for(td, _BASE.format(n="f"))
        p = loader.get_profile("f")
        assert p.thinking_level is None
        assert p.reasoning_effort is None


# ============ 2. extra_body 注入逻辑（单一真理源 reasoning_extra_body）============
# 直接测真实代码。8 处 openai-path 调用（llm_transport/gen_writer/distill_replicate/
# av_judge/gen_creative/gen_fixer/gen_chapter_titles/gen_negatives）全用此。
_extra_body = gml.reasoning_extra_body


def _profile(**kw):
    base = dict(name="p", model="m", base_url="u", api_key="k", temperature=1.0, max_tokens=None)
    base.update(kw)
    return gml.Profile(**base)


def test_extra_body_reasoning_effort_only():
    """elysiver 形态：reasoning_effort=low / thinking_level None → 只注 reasoning_effort。"""
    assert _extra_body(_profile(reasoning_effort="low")) == {"reasoning_effort": "low"}


def test_extra_body_thinking_level_only():
    """pie-xian 形态：thinking_level=LOW / reasoning_effort None → 只注 thinking_level。"""
    assert _extra_body(_profile(thinking_level="LOW")) == {"thinking_level": "LOW"}


def test_extra_body_empty_for_flash():
    """flash 形态：都 None → 空 extra_body（不注入·零回归）。"""
    assert _extra_body(_profile()) == {}


def test_extra_body_both_coexist():
    """都配 → 都注入（互不覆盖·独立共存）。"""
    assert _extra_body(_profile(thinking_level="LOW", reasoning_effort="low")) == {
        "thinking_level": "LOW", "reasoning_effort": "low"}


# ============ 3. reasoning 检测（复现 distill_replicate draft-refine auto-off）============
def _is_reasoning(profile):
    """复现 reasoning 模型检测：thinking_level OR reasoning_effort 都算 reasoning。"""
    return bool(getattr(profile, "thinking_level", None) or getattr(profile, "reasoning_effort", None))


def test_is_reasoning_via_reasoning_effort():
    """🔴 关键：elysiver(reasoning_effort)必须被识别为 reasoning（否则 draft-refine 不 auto-off）。"""
    assert _is_reasoning(_profile(reasoning_effort="low")) is True


def test_is_reasoning_via_thinking_level():
    assert _is_reasoning(_profile(thinking_level="LOW")) is True


def test_is_reasoning_false_for_flash():
    assert _is_reasoning(_profile()) is False


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
