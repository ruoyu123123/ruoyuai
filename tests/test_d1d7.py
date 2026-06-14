"""D1+D7 作者决策 rationale 链 + cheat-sheet 测试（2026-06-14·主题A 画骨核心）。

D7-2 守护（确定性压缩·非 LLM·照顾弱模型）：
  1. build_decision_cheat_sheet 按 decision_type 聚类·每类取 the_why 最长且 gap_filled 非空的 1 条；
  2. gap_filled 全空的 type 不产条目；
  3. 无 per_scene_rationale(旧档/None)→[]（零回归）；三键 situation/author_choice/vs_generic。
D1-1 守护：analyzer.md author_decisions 含 per_scene_rationale schema（5 键）。
零依赖（stdlib）。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


def test_cheat_sheet_clusters_by_type():
    """按 decision_type 聚类·每类 1 条·取 the_why 最长且 gap_filled 非空的·三键。"""
    dec = {"per_scene_rationale": [
        {"decision_type": "留白冰山", "scene": "段3", "the_what": "洗碗三遍",
         "the_why": "角色隐忍·直陈破人设故用动作侧写", "gap_filled": "AI写心如刀绞·作者用动作侧写"},
        {"decision_type": "留白冰山", "scene": "段7", "the_what": "短", "the_why": "短", "gap_filled": "x"},
        {"decision_type": "情绪节拍", "scene": "段5", "the_what": "停顿",
         "the_why": "节奏控制·此处缓冲让读者喘息", "gap_filled": "AI直接推进·作者留白"},
    ]}
    sheet = cap.build_decision_cheat_sheet(dec)
    types = {s["situation"].split(":")[0] for s in sheet}
    assert types == {"留白冰山", "情绪节拍"}, types
    liubai = [s for s in sheet if s["situation"].startswith("留白冰山")][0]
    assert "隐忍" in liubai["author_choice"]  # 取了 the_why 最长的段3
    assert all(set(s.keys()) == {"situation", "author_choice", "vs_generic"} for s in sheet)
    assert len(sheet) <= 7  # ≤ decision_type 枚举数


def test_cheat_sheet_skips_no_gap_filled():
    """gap_filled 全空的 decision_type 不产条目。"""
    dec = {"per_scene_rationale": [
        {"decision_type": "心理距离", "scene": "段1", "the_why": "近", "gap_filled": ""},
    ]}
    assert cap.build_decision_cheat_sheet(dec) == []


def test_cheat_sheet_empty_when_no_rationale():
    """无 per_scene_rationale(旧档/None)→[]（零回归）。"""
    assert cap.build_decision_cheat_sheet({}) == []
    assert cap.build_decision_cheat_sheet({"B1_道德滤镜": {}}) == []
    assert cap.build_decision_cheat_sheet(None) == []


def test_analyzer_has_per_scene_rationale():
    """D1-1：analyzer.md author_decisions 含 per_scene_rationale schema（5 键）。"""
    md = (_ROOT / ".claude" / "agents" / "novel-distill-analyzer.md").read_text(encoding="utf-8")
    assert "per_scene_rationale" in md
    for key in ("decision_type", "the_what", "the_why", "gap_filled"):
        assert key in md, key


# ── D7-3 / D7-4 注入端测试（import build_manifest/gen_writer·随全套跑）──────────────
def test_decision_payload_cheat_sheet_size_budget():
    """D7-3：cheat-sheet 按 vs_generic 信息量排序·累计字数到 DECISION_TOKEN_CAP 截断。"""
    import os
    import tempfile
    import build_manifest as bm

    class _S:
        def __init__(self, prof, db):
            self._p = prof
            self.db = db
            self.ch = 1

        def has_style_profile(self):
            return True

        def load(self, name, default=None):
            return self._p if name == "作者风格" else default

    prof = {"author_decision_principles": {"B1": {"x": "y"}}, "characterization_craft": {},
            "author_decision_cheat_sheet": [
                {"situation": "留白:段3", "author_choice": "用动作侧写", "vs_generic": "AI写心如刀绞而非动作"},
                {"situation": "心理:段1", "author_choice": "贴近", "vs_generic": "短"},
            ]}
    bak = {k: os.environ.get(k) for k in ("DECISION_TOKEN_CAP", "DECISION_INJECT_MODE")}
    os.environ["DECISION_TOKEN_CAP"] = "25"
    os.environ["DECISION_INJECT_MODE"] = "active"
    try:
        with tempfile.TemporaryDirectory() as td:
            p = bm._collect_author_decision_principles(_S(prof, Path(td)))
        bc = p["author_decision_cheat_sheet"]
        assert len(bc) == 1 and "留白" in bc[0]["situation"], bc  # 信息量高排前·第二条超 cap 截断
    finally:
        for k, v in bak.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_decision_section_renders_cheat_sheet_plain_text():
    """D7-4③：cheat-sheet 走纯文本分支·绝不 json.dumps blob。"""
    import json as _json
    import tempfile
    import gen_writer as gw
    with tempfile.TemporaryDirectory() as td:
        mp = Path(td) / "manifest.json"
        mp.write_text(_json.dumps({"author_decision_principles": {
            "author_decision_principles": {"B1_道德滤镜": {"母题": "天才孤独"}},
            "characterization_craft": {},
            "author_decision_cheat_sheet": [
                {"situation": "留白冰山:段3", "author_choice": "用动作侧写", "vs_generic": "AI写心如刀绞"}
            ],
        }}, ensure_ascii=False), encoding="utf-8")
        section = gw._build_decision_principles_section(mp)
        assert "cheat-sheet" in section
        assert "留白冰山:段3" in section and "用动作侧写" in section
        cheat_part = section.split("cheat-sheet")[-1]
        assert "{" not in cheat_part and "}" not in cheat_part  # 纯文本·未 json.dumps
