"""writer system prompt 体量回归守卫（G4 · 2026-06-23）。

钉死 G3 真 API e2e 抓出的卡死根因：211 轮 R7-R29 upgrade 把流程/基建/蒸馏/测试/合规类
feedback lesson 无差别灌进 writer system prompt，system 从 ~20k 撑到 67127 chars →
超 profile max_prompt_chars（时值 elysiver 渠道·已删）直接跳过不调用 → cluster-write step 2 writer exit 3。

守护点：
  · _collect_feedback_rules() 注入的 system 段只含写作工艺类 feedback（白名单 ∪ writer_relevant）
  · 真实 home memory 注入体量有界（< 12000 chars）——防新流程 lesson 再把它撑爆
"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "scripts"))
import gen_writer as gw  # noqa: E402

# 写作工艺类 feedback 注入体量软上限（7 文件 × ≤2500 截断 + header ≈ ≤9k·留余量到 12k）
_FEEDBACK_INJECT_CEIL = 12000


@contextlib.contextmanager
def _fake_home(path: Path):
    saved = {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}
    os.environ["USERPROFILE"] = str(path)
    os.environ["HOME"] = str(path)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mk_feedback(mem: Path, fname: str, body: str, writer_relevant: bool = False):
    extra = "  writer_relevant: true\n" if writer_relevant else ""
    (mem / fname).write_text(
        "---\nname: x\nmetadata: \n  node_type: memory\n  type: feedback\n"
        + extra + "---\n\n" + body + "\n", encoding="utf-8")


def test_feedback_inject_only_craft_and_bounded():
    """混合 home memory（5 工艺 + 6 流程/测试类）→ 注入段只含工艺类·体量有界。"""
    craft = [
        "feedback_no_screenplay_stage_directions_in_novels",
        "feedback_one_sentence_per_paragraph",
        "feedback_dialogue_quote_unicode_distinction",
        "feedback_smart_side_characters_no_dumbing_down",
        "feedback_author_goldstandard_comparison_gate",
    ]
    process = [
        "feedback_default_no_step_skipping_for_new_books",
        "feedback_verify_stderr_not_exitcode",
        "feedback_real_api_tests_no_economize",
        "feedback_runtime_self_learning_mape_k",
        "feedback_distill_sfs_multi_ref",
        "feedback_reader_growth_compliance_redline",
    ]
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        mem = home / ".claude" / "projects" / "D--Desktop-ruoyuai" / "memory"
        mem.mkdir(parents=True)
        for nm in craft:
            _mk_feedback(mem, nm + ".md", f"CRAFT_BODY_{nm} " + "工艺规则正文。" * 200)
        for nm in process:
            _mk_feedback(mem, nm + ".md", f"PROCESS_BODY_{nm} " + "流程规则正文。" * 200)
        with _fake_home(home):
            rules = gw._collect_feedback_rules()
    # 工艺类全在
    for nm in craft:
        assert nm in rules, f"工艺类 {nm} 被误删"
    # 流程/测试类一个都不在
    for nm in process:
        assert nm not in rules, f"流程/测试类 {nm} 未被过滤（G4 瘦身失效·67k 膨胀根因复发）"
    assert "PROCESS_BODY_" not in rules
    # 体量有界
    assert len(rules) < _FEEDBACK_INJECT_CEIL, (
        f"feedback 注入段 {len(rules)} chars 超软上限 {_FEEDBACK_INJECT_CEIL}")


def test_writer_relevant_flag_only_read_from_frontmatter():
    """🔴 2026-06-23 fix：`writer_relevant: true` 只在 frontmatter 生效，正文里的同名
    文字（文档解释 opt-in 机制）不得触发误注入。

    回归 e2e bug：feedback_writer_prompt_bloat_feedback_whitelist（frontmatter
    writer_relevant: false，但正文写了 `writer_relevant: true` opt-in 字样）被
    旧版整文 re.search 误判 writer-relevant → 流程 lesson 反灌 writer prompt。
    """
    # frontmatter=false 但正文含 "writer_relevant: true" 文字 → 应 False
    tricky = (
        "---\nname: x\nmetadata: \n  type: feedback\n  writer_relevant: false\n---\n\n"
        "本 lesson 解释：带 frontmatter `writer_relevant: true` 的文件会被注入。\n"
        "default_no_step_skipping 这类流程指纹不该进 writer prompt。\n")
    assert not gw._is_writer_relevant_feedback("feedback_some_process_lesson", tricky), \
        "正文里的 writer_relevant: true 文字不该触发注入（误判=膨胀复发）"
    # frontmatter=true → 应 True
    real = ("---\nname: y\nmetadata: \n  type: feedback\n  writer_relevant: true\n---\n\n"
            "写作工艺正文。\n")
    assert gw._is_writer_relevant_feedback("feedback_new_craft_lesson", real), \
        "frontmatter writer_relevant: true 应判 writer-relevant"
    # 白名单命中（即便无 frontmatter flag）→ True
    assert gw._is_writer_relevant_feedback("feedback_one_sentence_per_paragraph", "")


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            fn = globals()[nm]
            mark = getattr(fn, "pytestmark", [])
            skip = any(getattr(m, "name", "") == "skipif" and m.args and m.args[0]
                       for m in (mark if isinstance(mark, list) else [mark]))
            if skip:
                print(f"  [SKIP] {nm}")
                continue
            try:
                fn()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
