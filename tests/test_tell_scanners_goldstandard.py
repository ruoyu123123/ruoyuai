# -*- coding: utf-8 -*-
"""tell 类 scanner 转 active 金标准回归测试（2026-06-16·subtext/dramatic_irony/reveal_show）。

【背景】W1/W4/W5 三个 tell-vs-show 检测 scanner 此前默认 shadow（只记不判）。穷尽核查
Workflow + 主代理自验金标准（6 作者原文实测 subtext≤0.06/irony≤0.11/reveal≤0.07 per_1k·
远低于 floor 3.0/0.8/0.8）→ 切 active 放量。本测试是放量守护回归锁：

  1. 真作者原文 active → 零误报（verdict PASS·violations==[]）·防矫枉过正回归锁；
  2. 高密度合成 tell 草稿 active → FAIL（灵敏度不退·active 真上报非空转）；
  3. 3 code 绝不进 HARD_GATE_CODES（制度锁·北极星⑤·转 active 仍 advisory 可豁免）；
  4. env 未设 → 默认 active（本增量主交付）。

零依赖·仓库根 pytest 入口。真作者原文本地无（CI 干净环境）→ 金标准跳过不阻塞。
"""
import glob
import io
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import subtext_rescan_scanner as S  # noqa: E402
import dramatic_irony_scanner as D  # noqa: E402
import reveal_show_scanner as R  # noqa: E402

_MODE_ENVS = ("SUBTEXT_RESCAN_MODE", "DRAMATIC_IRONY_MODE", "REVEAL_SHOW_MODE")


def _set_modes(v):
    for k in _MODE_ENVS:
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _styles_search_roots():
    """真作者语料根（当前 repo + worktree 场景回退主检出 + env 覆盖）·仿 consistency 金标准。"""
    roots = [_ROOT]
    parts = _ROOT.parts
    if len(parts) >= 3 and parts[-3] == ".claude" and parts[-2] == "worktrees":
        roots.append(Path(*parts[:-3]))
    env = os.environ.get("RUOYUAI_MAIN_CHECKOUT")
    if env:
        roots.append(Path(env))
    return roots


def _discover_author_raws(limit_authors=6):
    """动态发现有 原文/*.txt 的作者目录（不硬编码作者名·目录名可变）·返回 [(author, [前6章])]。"""
    seen = {}
    for root in _styles_search_roots():
        for d in glob.glob(str(root / "workspace" / "styles" / "*" / "原文")):
            author = Path(d).parent.name
            if author in seen or "clitest" in author or "test" in author.lower():
                continue
            raws = sorted(glob.glob(os.path.join(d, "第0*章.txt")))[:6]
            if len(raws) >= 3:
                seen[author] = raws
    return list(seen.items())[:limit_authors]


def _write_tmp(text):
    tf = Path(tempfile.mkdtemp()) / "draft.txt"
    tf.write_text(text, encoding="utf-8")
    return str(tf)


def test_real_author_zero_false_positive_active():
    """🔴 金标准：真作者原文前 6 章 active 喂 3 scanner → 零误报（防矫枉过正回归锁·北极星⑤）。"""
    authors = _discover_author_raws()
    if not authors:
        print("[SKIP] 真作者原文池本地无（CI 干净环境）·金标准跳过（不阻塞确定性合入）")
        return
    _set_modes("active")
    ran = 0
    try:
        for author, raws in authors:
            text = "".join(io.open(f, encoding="utf-8").read() for f in raws)
            tf = _write_tmp(text)
            for name, mod in (("subtext", S), ("dramatic_irony", D), ("reveal_show", R)):
                rep = mod.scan(tf)
                assert rep["verdict"] == "PASS", (author, name, rep.get("verdict"), rep)
                assert rep["violations"] == [], (author, name, rep["violations"])
            ran += 1
    finally:
        _set_modes(None)
    assert ran >= 1


def test_high_density_synthetic_fails_active():
    """灵敏度不退：高密度 tell 合成草稿 active → FAIL（active 真上报·非空转·验证转 active 有效）。"""
    _set_modes("active")
    try:
        on = "他感到愤怒。她心中充满了悲伤。我觉得绝望。他内心涌起恐惧。" * 60
        rs = S.scan(_write_tmp(on))
        assert "FAIL" in rs["verdict"], rs
        assert rs["violations"], rs   # active 真上报 violation
        ir = "他殊不知危险将至。她浑然不知真相。他却不知道这一切。" * 60
        rd = D.scan(_write_tmp(ir))
        assert "FAIL" in rd["verdict"], rd
        rv = "真相大白了。他恍然大悟。原来如此。这才明白过来。谜底揭晓。" * 60
        rr = R.scan(_write_tmp(rv))
        assert "FAIL" in rr["verdict"], rr
    finally:
        _set_modes(None)


def test_tell_codes_never_hard_gate():
    """制度锁：3 个 tell code 绝不进 HARD_GATE_CODES（北极星⑤·转 active 仍 advisory 可豁免）。"""
    import audit_hub
    for code in ("ON_THE_NOSE_EMOTION_DENSITY", "DRAMATIC_IRONY_DRIFT", "REVEAL_TELL_OVERUSE"):
        assert code not in audit_hub.HARD_GATE_CODES, code
        assert audit_hub._gate_level_for(code, "error") == "advisory", code


def test_default_mode_is_active_after_flip():
    """env 未设 → 默认 active（2026-06-16 放量·本增量主交付·shadow/off 仍可显式回退）。"""
    _set_modes(None)
    try:
        assert S._mode() == "active"
        assert D._mode() == "active"
        assert R._mode() == "active"
        os.environ["SUBTEXT_RESCAN_MODE"] = "shadow"
        assert S._mode() == "shadow"   # 显式 shadow 仍生效（放量非锁死）
    finally:
        _set_modes(None)
