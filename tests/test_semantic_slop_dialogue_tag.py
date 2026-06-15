#!/usr/bin/env python3
"""semantic_slop B+9 对话标签密度过用测试（reading-reflector 维度6·零依赖）。

与 B+8 同义词循环正交：B+8 查变体多（换花样说），B+9 查密度高（每句带标签=工艺单一）。
金标准防矫枉过正：真作者原文对话段标签密度不应误报。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import semantic_slop_scanner as S  # noqa: E402


def test_high_tag_density_flagged():
    """每句对话都带具名标签（密度高）→ warning。"""
    paras = [
        '“你来了，”他说。',
        '“我等很久了，”她道。',
        '“那就走吧，”他说道。',
        '“等等我，”她喊道。',
        '“好的，”他答道。',
        '“快点，”她问道。',
        '“知道了，”他应道。',
    ]
    r = S.scan_dialogue_tag_density(paras)
    assert r["warning"], f"高密度应报，density={r['tag_density']}"
    assert r["tag_density"] >= 0.65


def test_low_tag_density_no_flag():
    """对话靠动作/上下文带说话人（标签疏）→ 不报（防矫枉过正）。"""
    paras = [
        '“你来了。”他把伞收进墙角。',
        '“嗯。”她头也没抬。',
        '陆参拉开椅子坐下，“等很久了？”',
        '“还好。”窗外的雨还在下着。',
        '“那就开始吧。”桌上的茶已经凉透了。',
        '她终于抬起头，“我有件事要告诉你。”',
    ]
    r = S.scan_dialogue_tag_density(paras)
    assert not r["warning"], f"低密度不该报，density={r['tag_density']}"


def test_b8_b9_orthogonal():
    """B+8(变体多) vs B+9(密度高) 正交：单一标签「他说」高密度→B+9 报·B+8 变体少不报。"""
    paras = ['“某句话，”他说。' for _ in range(8)]
    body = "\n".join(paras)
    r9 = S.scan_dialogue_tag_density(paras)
    r8 = S.scan_tag_synonym_cycle(body)
    assert r9["warning"]                       # 密度高·B+9 报
    assert r8["distinct_variants"] < 6         # 变体少（全「他说」非「说道」变体）·B+8 不报


def test_dialogue_tag_density_no_dialogue():
    """无对话段 → 不崩·density=0。"""
    r = S.scan_dialogue_tag_density(["他走进了房间。", "天色暗了下来。"])
    assert r["tag_density"] == 0.0
    assert not r["warning"]


def test_b9_in_all_checks():
    """B+9 注册进 ALL_CHECKS（audit_hub 据此解析）。"""
    assert "dialogue_tag_density" in S.ALL_CHECKS


def test_real_author_dialogue_not_overflagged():
    """金标准防矫枉过正：真作者原文对话段标签密度不应误报（density<0.65 或对话段不足）。"""
    jroot = _SCRIPTS.parents[0] / "workspace" / "styles" / "惊悚乐园" / "原文"
    if not jroot.exists():
        return  # 文件不在则跳过（零依赖原则）
    import re
    chs = [jroot / f"第{c:03d}章.txt" for c in range(6, 14)]
    text = "\n\n".join(p.read_text(encoding="utf-8") for p in chs if p.exists())
    if not text:
        return
    paras = [p for p in re.split(r"\n\n+", text) if p.strip()]
    r = S.scan_dialogue_tag_density(paras)
    assert not r["warning"], \
        f"真作者不该误报对话标签密度过用，density={r['tag_density']} ({r['tagged_paras']}/{r['dialogue_paras']})"


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
