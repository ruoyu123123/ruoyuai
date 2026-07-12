#!/usr/bin/env python3
"""build_manifest._build_controlling_idea_anchor 回归测试 — 🔴 2026-06-29 主控思想软注入（主题脊柱 P0）。

钉死（设计见 角色建模升级/主题_提案.json designs[0]·骑 _soften_convergence_anchor L1875 同轨降精）：
  · 读 大势卡.story_destiny.controlling_idea = {premise, controlling_idea, moral_argument, designing_principle}
  · 早期 cluster（idx/total < 0.5）→ soft_early：只给软方向（premise + designing_principle），
    不泄条件化论断 controlling_idea / 对立价值轴 moral_argument（北极星③不把大势收敛从软变硬）
  · 后半程（idx/total >= 0.5）→ explicit_late：显化全字段（补 controlling_idea + moral_argument）
  · 默认安全闸：无 controlling_idea（旧大势卡/旧书）→ None（零行为变化·向后兼容）
  · 全 advisory（北极星⑤顾问非法官）：gate_level=advisory·绝不 hard_gate
  · 真正接线进 full build_manifest()（防写了却没接线）
"""
import json
import sys
import tempfile
from pathlib import Path

from cluster_summary_fixtures import write_cluster_summary

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import build_manifest as bm  # noqa: E402


_CI = {
    "premise": "唯有放下复仇执念，弃儿才能真正自由",
    "controlling_idea": "if 主角执着复仇 then 失去最后亲人 because 仇恨吞噬施加者",
    "moral_argument": [{"value": "复仇", "counter_value": "宽恕"}],
    "designing_principle": "钟楼的钟声=被时间审判的回响",
}


def _mk_project(tmp: Path, *, story_destiny=None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    payload = {}
    if story_destiny is not None:
        payload["story_destiny"] = story_destiny
    (db / "大势卡.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp


def _scanner(tmp: Path, ch: int = 1):
    return bm.DatabaseScanner(tmp, ch)


# ---------- 默认安全闸（向后兼容·旧书零行为变化） ----------

def test_no_story_destiny_returns_none():
    """大势卡 无 story_destiny → None（旧大势卡·不注入·零行为变化）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))  # 不写 story_destiny
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is None


def test_no_controlling_idea_returns_none():
    """story_destiny 仅有 final_image/thematic_resolution（旧 schema）→ None（默认安全）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={
            "final_image": "钟楼崩塌", "thematic_resolution": "弃儿归乡"})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is None


def test_empty_controlling_idea_returns_none():
    """controlling_idea 四层全空 → None（全空视为未填·默认安全）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": {
            "premise": "  ", "controlling_idea": "", "moral_argument": None,
            "designing_principle": ""}})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is None


def test_missing_dazhika_returns_none():
    """连 大势卡.json 都没有（更老的项目）→ None（不崩·默认安全）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is None


# ---------- 早期 cluster：软方向（北极星③软牵引降精） ----------

def test_early_cluster_soft_direction():
    """早期 cluster（cluster_001 / total=4 → 0.25 < 0.5）→ soft_early·只给软方向。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is not None
        assert r["_phase"] == "soft_early"
        # 软方向：给 premise + 主母题
        assert r["premise"] == _CI["premise"]
        assert r["designing_principle"] == _CI["designing_principle"]
        # 早期降精：绝不泄条件化论断 / 对立价值轴（北极星③不把大势收敛从软变硬）
        assert "controlling_idea" not in r
        assert "moral_argument" not in r
        assert "_softened_for_early_cluster" in r
        assert "软牵引" in r["directive"]


def test_position_unknown_defaults_soft():
    """无法判定位置（total<=1）→ 默认软方向（安全闸·不超额泄露终局价值落点）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 1)
        assert r["_phase"] == "soft_early"
        assert "moral_argument" not in r


def test_cluster_num_unparseable_defaults_soft():
    """cluster_id 无法解析序号（cluster_num None）→ 默认软方向。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "pending", 4)
        assert r["_phase"] == "soft_early"


# ---------- 后半程：显化（越近卷末越显化） ----------

def test_late_cluster_explicit():
    """后半程 cluster（cluster_003 / total=4 → 0.75 >= 0.5）→ explicit_late·显化全字段。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_003", 4)
        assert r["_phase"] == "explicit_late"
        assert r["premise"] == _CI["premise"]
        assert r["designing_principle"] == _CI["designing_principle"]
        # 显化：补条件化论断 + 对立价值轴
        assert r["controlling_idea"] == _CI["controlling_idea"]
        assert r["moral_argument"] == _CI["moral_argument"]
        assert "显化" in r["directive"]


def test_boundary_exactly_half_is_late():
    """边界：idx/total == 0.5（cluster_002 / total=4）→ 后半程显化（>= 0.5·与 _soften_convergence_anchor 同款阈值）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_002", 4)
        assert r["_phase"] == "explicit_late"


# ---------- 北极星⑤：全 advisory·绝不 hard_gate ----------

def test_advisory_never_hard_gate():
    """gate_level=advisory·card 内绝不出现 hard_gate 裁决（北极星⑤顾问非法官）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={"controlling_idea": _CI})
        for cid in ("cluster_001", "cluster_003"):
            r = bm._build_controlling_idea_anchor(_scanner(tmp), cid, 4)
            assert r["gate_level"] == "advisory"
            # 不引入新 hard_gate code（不进 audit_hub.HARD_GATE_CODES）
            assert '"gate_level": "hard_gate"' not in json.dumps(r, ensure_ascii=False)


def test_not_in_hard_gate_codes():
    """controlling_idea_anchor 绝不进 audit_hub.HARD_GATE_CODES（不引入新硬门禁）。"""
    import audit_hub  # noqa: E402
    codes = {c.upper() for c in audit_hub.HARD_GATE_CODES}
    assert "CONTROLLING_IDEA" not in codes
    assert "CONTROLLING_IDEA_ANCHOR" not in codes
    assert not any("CONTROLLING_IDEA" in c for c in codes)


# ---------- 极简档（爽文可极简·作者档第一权威） ----------

def test_minimal_premise_only():
    """爽文极简：只填 premise（无 controlling_idea/moral/principle）→ 仍注入软方向。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), story_destiny={
            "controlling_idea": {"premise": "弱者逆袭爽"}})
        r = bm._build_controlling_idea_anchor(_scanner(tmp), "cluster_001", 4)
        assert r is not None
        assert r["premise"] == "弱者逆袭爽"
        assert "designing_principle" not in r  # 未填字段不塞空


# ---------- 接线进 full build_manifest() ----------

def _wiring_project(tmp: Path, *, with_ci: bool) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    write_cluster_summary(tmp, [])
    clusters = [{
        "cluster_id": "cluster_001", "status": "in_progress",
        "chapter_range": [1, 4], "scope_summary": "弃儿夜入钟楼",
        "scene_storyboard": [{"summary": "钟声响起", "key_events": ["开局"]}],
    }]
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(
        json.dumps({"characters": [{"id": "qi", "name": "祁安", "role": "主角"}]},
                   ensure_ascii=False), encoding="utf-8")
    prog = {
        "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 8]}],
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [1, 4],
                "scene_storyboard": [
                    {"ch": 1, "characters": ["祁安"], "key_events": ["开局"],
                     "scene_type": ["悬疑"], "summary": "祁安夜入钟楼"}
                ],
            }
        },
    }
    (db / "进度.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    sd = {"controlling_idea": _CI} if with_ci else {
        "final_image": "钟楼崩塌", "thematic_resolution": "弃儿归乡"}
    (db / "大势卡.json").write_text(
        json.dumps({"story_destiny": sd, "major_events": []}, ensure_ascii=False),
        encoding="utf-8")
    return tmp


def test_wired_into_final_manifest():
    """controlling_idea_anchor 真出现在 full build_manifest() 的 event_cluster_context brief（防没接线）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _wiring_project(Path(d), with_ci=True)
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        brief = m["event_cluster_context"]
        assert brief["mode"] == "on", brief
        anchor = brief["controlling_idea_anchor"]
        assert anchor is not None
        # 全 manifest 只 1 cluster（total=1）→ soft_early（位置无法判定 → 默认软方向）
        assert anchor["_phase"] == "soft_early"
        assert anchor["premise"] == _CI["premise"]
        assert anchor["gate_level"] == "advisory"


def test_wired_default_safe_no_controlling_idea():
    """旧大势卡（无 controlling_idea）→ full manifest 中 controlling_idea_anchor=None（默认安全）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _wiring_project(Path(d), with_ci=False)
        m = bm.build_manifest(tmp, 1)
        assert m["preflight"]["passed"], m["preflight"]
        brief = m["event_cluster_context"]
        assert brief["mode"] == "on"
        assert brief["controlling_idea_anchor"] is None


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
