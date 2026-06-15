#!/usr/bin/env python3
"""gen_creative distill_reflect mode 测试（phase-3·产 skill markdown·must_fix#5 关 JSON parse）。"""
import json
import sys
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import gen_creative as gc  # noqa: E402
import llm_transport as lt  # noqa: E402

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


class _Args:
    def __init__(self, tmp, gap, **kw):
        self.project = str(tmp)
        self.gap_report = str(gap)
        self.current_skill = None
        self.skill_version = 2
        self.style_ref = None
        self.dry_run = False
        self.out = None
        for k, v in kw.items():
            setattr(self, k, v)


def _setup():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True)
    gap = tmp / "gap.json"
    gap.write_text(json.dumps({"dimensions": [{"name": "句长", "gap": "复刻16 vs作者31"}]},
                              ensure_ascii=False), encoding="utf-8")
    return tmp, gap


def _patch_generate(monkey_text):
    orig = lt.generate

    def fake(loader, system, user, **kw):
        # must_fix#5：reflect 绝不传 response_format_json（markdown 输出）
        assert "response_format_json" not in kw or kw["response_format_json"] is False
        return types.SimpleNamespace(text=monkey_text)
    lt.generate = fake
    return orig


def _patch_generate_sequence(*texts):
    """序列返回 text（末个之后复用末个）·验 reflect retry 自愈。返回 (orig, calls)。"""
    orig = lt.generate
    calls = {"n": 0}

    def fake(loader, system, user, **kw):
        assert "response_format_json" not in kw or kw["response_format_json"] is False
        i = min(calls["n"], len(texts) - 1)
        calls["n"] += 1
        return types.SimpleNamespace(text=texts[i])
    lt.generate = fake
    return orig, calls


def test_reflect_writes_skill_markdown():
    tmp, gap = _setup()
    orig = _patch_generate(_GOOD_MD)
    try:
        rc = gc._run_distill_reflect(_Args(tmp, gap))
        assert rc == 0
        skill = tmp / "skill_v2.md"
        assert skill.exists()
        md = skill.read_text(encoding="utf-8")
        assert "## 句式与节奏" in md and "## 反模式" in md
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_block_on_empty_output():
    tmp, gap = _setup()
    orig = _patch_generate("太短")   # < 200 字 → 结构破损 block
    try:
        assert gc._run_distill_reflect(_Args(tmp, gap)) == 1
        assert not (tmp / "skill_v2.md").exists()
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_block_on_missing_sections():
    tmp, gap = _setup()
    # 长但缺过半必备小节 → block
    bad = "# skill\n\n" + ("一些内容。\n" * 60)
    orig = _patch_generate(bad)
    try:
        assert gc._run_distill_reflect(_Args(tmp, gap)) == 1
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_retry_recovers_from_transient_break():
    """🔴 真机 e2e 同类加固 2026-06-15：reflect 与 volume_arc 同根——单点 gen-model 调用偶发
    空/缺小节(限速/抖动)直接 block 逼用户 --resume(GUI 蒸馏致命)。验 retry 自愈：第一次太短
    (破损)·第二次合法 skill → rc==0 + 落盘(不 block)。"""
    tmp, gap = _setup()
    orig, calls = _patch_generate_sequence("太短", _GOOD_MD)  # 破损 → 合法
    try:
        rc = gc._run_distill_reflect(_Args(tmp, gap))
        assert rc == 0, "第二次合法应自愈 rc==0(不 block)"
        assert calls["n"] == 2, "应重试 1 次(共调 2 次)"
        assert (tmp / "skill_v2.md").exists(), "自愈后应落盘"
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_retry_exhausted_still_blocks():
    """3 次全破损 → block exit 1(确定性破损不无限重试·不静默吞断链)。"""
    tmp, gap = _setup()
    orig, calls = _patch_generate_sequence("太短")  # 每次都破损
    try:
        rc = gc._run_distill_reflect(_Args(tmp, gap))
        assert rc == 1, "3 次全破损应 block 非零退出"
        assert calls["n"] == 3, "应尝试满 3 次(MAX_REFLECT_TRIES)"
        assert not (tmp / "skill_v2.md").exists(), "破损不落盘"
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_v0_no_gap_generates_initial_skill():
    """gap-report 空 → 首版 v0 生成（破 chicken-egg：复刻需 skill_v0·SFS 需复刻）。"""
    tmp, _ = _setup()
    orig = _patch_generate(_GOOD_MD)
    try:
        a = _Args(tmp, tmp / "nonexist.json", skill_version=0,
                  out=str(tmp / "skill_v0.md"))
        a.gap_report = None
        assert gc._run_distill_reflect(a) == 0
        assert (tmp / "skill_v0.md").exists()
        # v0 prompt 走「从作者档提炼」分支
        s, u = gc.build_distill_reflect_prompt(
            gap_text="", current_skill="", author_block="作者档", version=0)
        assert "首版" in s and "首版 skill" in u
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_reflect_with_gap_uses_refine_branch():
    s, u = gc.build_distill_reflect_prompt(
        gap_text="句长差距大", current_skill="旧skill", author_block="作者档", version=2)
    assert "精化版" in s
    assert "SFS 复刻差距报告" in u


def test_reflect_custom_out_path():
    tmp, gap = _setup()
    orig = _patch_generate(_GOOD_MD)
    try:
        out = tmp / "styles" / "myskill.md"
        rc = gc._run_distill_reflect(_Args(tmp, gap, out=str(out)))
        assert rc == 0 and out.exists()
    finally:
        lt.generate = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


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
