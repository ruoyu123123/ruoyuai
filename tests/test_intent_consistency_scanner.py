#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D5 intent_consistency_scanner 单测（2026-06-14）。

锚点：
- 角色动作无动机词 draft → emit INTENT_ANCHOR_MISSING advisory
- 有动机交代（因为/为了）draft → 不 emit
- INTENT_SCAN_MODE=shadow（默认）→ issues 空
- INTENT_ANCHOR_MISSING not in audit_hub.HARD_GATE_CODES 且 _gate_level_for 返 advisory（回归护栏）
"""
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "core" / "scripts"
for _p in (str(_SCRIPTS), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import intent_consistency_scanner as ic  # noqa: E402

# 动作密集 + 全程无动机交代（疑似降智/动作流水账）。每段 ≥3 个动作信号。
DRAFT_NO_MOTIV = (
    "他转身，伸手，握住门把。\n\n"
    "她抬手，推开椅子，扑过去，抓住对方的领口。\n\n"
    "男人甩开她，后退，转身就跑。\n\n"
    "他冲上去，扑倒，按住对方的肩膀，攥紧拳头。\n\n"
    "两人扭打，翻身，压住，掐住脖子，又被踢开。\n\n"
)

# 动作密集 + 每段都有动机交代（因为/为了/打算/决定）。
DRAFT_WITH_MOTIV = (
    "因为门后传来惨叫，他转身，伸手，握住门把。\n\n"
    "为了护住孩子，她抬手，推开椅子，扑过去，抓住对方的领口。\n\n"
    "男人打算逃命，于是甩开她，后退，转身就跑。\n\n"
    "他决定不能让对方得手，冲上去，扑倒，按住肩膀，攥紧拳头。\n\n"
    "她担心来不及，扭身，翻起，压住，掐住脖子，又被踢开。\n\n"
)


def _write(tmp_name: str, content: str) -> str:
    d = _HERE / "_tmp_intent"
    d.mkdir(exist_ok=True)
    f = d / tmp_name
    f.write_text(content, encoding="utf-8")
    return str(f)


def test_no_motivation_emits_advisory():
    """角色动作无动机词 draft → emit INTENT_ANCHOR_MISSING advisory。"""
    r = ic.scan_intent_consistency(DRAFT_NO_MOTIV)
    assert r["issues"], f"无动机动作密集 draft 应 emit·got {r}"
    codes = {i["code"] for i in r["issues"]}
    assert ic.INTENT_ANCHOR_MISSING in codes, f"应含 INTENT_ANCHOR_MISSING·got {codes}"
    for iss in r["issues"]:
        if iss["code"] == ic.INTENT_ANCHOR_MISSING:
            assert iss["gate_level"] == "advisory", f"必须 advisory·got {iss['gate_level']}"
            assert iss["severity"] == "warning"


def test_with_motivation_no_emit():
    """有动机交代（因为/为了/打算/决定）draft → 不 emit。"""
    r = ic.scan_intent_consistency(DRAFT_WITH_MOTIV)
    codes = {i["code"] for i in r["issues"]}
    assert ic.INTENT_ANCHOR_MISSING not in codes, f"有动机交代不应 emit·got {r}"
    assert r["anchored"] >= 1, f"应有 ≥1 段被动机锚定·got {r}"


def test_shadow_mode_default_empty_issues():
    """INTENT_SCAN_MODE=shadow（默认）→ main 落盘但 issues 空。"""
    import json
    import subprocess
    draft_path = _write("no_motiv.txt", DRAFT_NO_MOTIV)
    env = dict(os.environ)
    env.pop("INTENT_SCAN_MODE", None)  # 不设 = 默认 shadow
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "intent_consistency_scanner.py"), draft_path],
        capture_output=True, text=True, env=env, encoding="utf-8",
    )
    assert out.returncode == 0, f"shadow 默认应 exit0·got {out.returncode}·{out.stderr}"
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data.get("_shadow") is True, f"默认应标 _shadow·got {data}"
    assert data["issues"] == [], f"shadow 模式 issues 必须空·got {data['issues']}"


def test_active_mode_surfaces_issues():
    """INTENT_SCAN_MODE=active → issues 上报（确认 shadow 是默认而非永久吞）。"""
    import json
    import subprocess
    draft_path = _write("no_motiv_active.txt", DRAFT_NO_MOTIV)
    env = dict(os.environ)
    env["INTENT_SCAN_MODE"] = "active"
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "intent_consistency_scanner.py"), draft_path],
        capture_output=True, text=True, env=env, encoding="utf-8",
    )
    assert out.returncode == 0, f"active 应 exit0·got {out.returncode}·{out.stderr}"
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["issues"], f"active 模式应上报 issues·got {data}"
    assert all(i["gate_level"] == "advisory" for i in data["issues"])


def test_short_text_graceful():
    """段落不足（<2 段）→ 不崩·issues 空。"""
    r = ic.scan_intent_consistency("他转身。")
    assert r["issues"] == []
    assert "note" in r


def test_missing_draft_exit0():
    """draft 不存在 → main exit0 不抛错（_skip）。"""
    import json
    import subprocess
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "intent_consistency_scanner.py"),
         str(_HERE / "_tmp_intent" / "__nope__.txt")],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert out.returncode == 0, f"缺 draft 应 exit0·got {out.returncode}"
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["issues"] == [] and "_skip" in data


def test_hard_gate_boundary():
    """🔴 回归护栏：INTENT_ANCHOR_MISSING 绝不进 HARD_GATE_CODES·_gate_level_for 返 advisory。"""
    import audit_hub
    assert "INTENT_ANCHOR_MISSING" not in audit_hub.HARD_GATE_CODES, \
        "INTENT_ANCHOR_MISSING 不得进 HARD_GATE_CODES（北极星⑤）"
    assert audit_hub._gate_level_for("INTENT_ANCHOR_MISSING", "error") == "advisory", \
        "_gate_level_for 必须返 advisory"
    # 即便以 fatal 报也是 advisory（非 STYLE_单段超长 那类特例）。
    assert audit_hub._gate_level_for("INTENT_ANCHOR_MISSING", "fatal") == "advisory"
