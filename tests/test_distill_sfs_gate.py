"""C01 蒸馏出货 SFS 闸 + 收敛闸 确定性单测（镜像 strict_gate_decision 测法·零 API/subprocess）。

根因：distill_finalize_verify 此前只调 cluster_evaluator 不调 style_evaluator，
STRICT_ESTIMABLE_IDX_REASONING=(4,) 把 SFS 五维全移出 strict → SFS 完全不进闸·SFS=40
跑飞的 skill 也能正常出货污染全书。C01 补两道闸：
  ① distill_finalize_verify.sfs_gate_decision —— 出货前对 skill_FINAL 复刻测 SFS，灾难性坍缩拦。
  ② distill_convergence_gate.converge_decision —— v0→v1 收敛环（复用 validation_gate.decide）。

北极星护栏断言：
  · 绝不绝对 SFS≥80 hard-lock —— 55-80 band 恒 advisory（below_band·不拦）；
  · hard 档只在灾难性坍缩（<floor / grade==D）触发；
  · floor 用相对锚 max(55, v0×0.85)；收敛 floor 用 v0×0.97（validation_gate L6 地板）；
  · SFS 不可解析 → 不阻断（infra 失败不 punish·真闸在 cluster 维 / step7）。

只测确定性纯函数，不碰 LLM/subprocess/agent。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import distill_finalize_verify as dfv  # noqa: E402
import distill_convergence_gate as dcg  # noqa: E402
from skill_opt import validation_gate as vg  # noqa: E402


# ============ sfs_gate_decision 三档 ============

def test_catastrophic_below_absolute_floor():
    """SFS < 绝对地板 55 → catastrophic（hard_gate）。"""
    verdict, detail = dfv.sfs_gate_decision(40.0, "D", floor=55.0)
    assert verdict == "catastrophic", verdict
    assert detail["gate_level"] == "hard_gate"


def test_catastrophic_grade_d_even_if_sfs_above_floor():
    """grade==D 即 catastrophic（即便 SFS 数值 ≥ floor·grade 是独立坍缩信号）。"""
    verdict, detail = dfv.sfs_gate_decision(58.0, "D", floor=55.0)
    assert verdict == "catastrophic", verdict
    assert "grade==D" in detail["note"]


def test_catastrophic_below_relative_v0_floor():
    """相对锚：v0=88 → floor=74.8，SFS=70 虽 >55 绝对地板但 <74.8 → catastrophic。"""
    floor = dfv.sfs_catastrophic_floor(88.0)  # max(55, 74.8)=74.8
    verdict, _ = dfv.sfs_gate_decision(70.0, "C", floor=floor)
    assert verdict == "catastrophic", (floor, verdict)


def test_below_band_is_advisory_not_hard():
    """55 ≤ SFS < 80 → below_band·advisory 放行（绝不 hard-lock·北极星①）。
    诡秘 88.35/蛊真人 B 级正常出货——70 的 B 级不该被拦。"""
    verdict, detail = dfv.sfs_gate_decision(70.0, "C", floor=55.0)
    assert verdict == "below_band", verdict
    assert detail["gate_level"] == "advisory"


def test_below_band_boundary_just_above_floor():
    """SFS == floor → 不坍缩（严格小于才坍缩）→ below_band。"""
    verdict, _ = dfv.sfs_gate_decision(55.0, "D" if False else "C", floor=55.0)
    assert verdict == "below_band", verdict


def test_healthy_at_and_above_band():
    """SFS ≥ 80 → healthy（pass）。B 级 88.35 风格保真度健康。"""
    for sfs in (80.0, 88.35, 96.98):
        verdict, detail = dfv.sfs_gate_decision(sfs, "A", floor=55.0)
        assert verdict == "healthy", (sfs, verdict)
        assert detail["gate_level"] == "pass"


def test_unknown_when_sfs_none_does_not_block():
    """SFS 无法解析（None）→ unknown·不阻断（infra 失败不 punish·北极星⑤）。"""
    verdict, detail = dfv.sfs_gate_decision(None, None, floor=55.0)
    assert verdict == "unknown", verdict
    assert detail["gate_level"] != "hard_gate"


def test_grade_case_insensitive():
    """grade 大小写 / 空白不敏感：'d' / ' D ' 都判 catastrophic。"""
    for g in ("d", " D ", "D"):
        verdict, _ = dfv.sfs_gate_decision(85.0, g, floor=55.0)
        assert verdict == "catastrophic", (g, verdict)


# ============ sfs_catastrophic_floor 相对锚 ============

def test_floor_relative_anchor_high_author():
    """v0 高分作者：floor = v0×0.85（> 绝对地板 55）。"""
    assert dfv.sfs_catastrophic_floor(88.0) == round(88.0 * 0.85, 2)  # 74.8


def test_floor_absolute_when_v0_low():
    """v0 低分难仿作者（60）：v0×0.85=51 < 55 → 取绝对地板 55 兜底不误杀。"""
    assert dfv.sfs_catastrophic_floor(60.0) == 55.0


def test_floor_none_v0_uses_absolute():
    """无 v0 eval（None）→ 仅绝对地板 55。"""
    assert dfv.sfs_catastrophic_floor(None) == 55.0


# ============ parse_sfs_eval 口径（对齐 finalize_distill） ============

def test_parse_sfs_quick_top_level(tmp_path):
    p = tmp_path / "e.json"
    p.write_text('{"sfs_quick": 85.5, "grade": "B"}', encoding="utf-8")
    assert dfv.parse_sfs_eval(p) == (85.5, "B")


def test_parse_fallback_to_total(tmp_path):
    p = tmp_path / "e.json"
    p.write_text('{"total": 77.0}', encoding="utf-8")
    sfs, grade = dfv.parse_sfs_eval(p)
    assert sfs == 77.0 and grade is None


def test_parse_fallback_programmatic_score(tmp_path):
    """style_evaluator 真实输出：sfs_quick 顶层 + grade 在 programmatic_score。"""
    p = tmp_path / "e.json"
    p.write_text('{"sfs_quick": 90.0, "programmatic_score": {"total": 90.0, "grade": "A"}}',
                 encoding="utf-8")
    assert dfv.parse_sfs_eval(p) == (90.0, "A")


def test_parse_grade_only_in_programmatic(tmp_path):
    p = tmp_path / "e.json"
    p.write_text('{"sfs_quick": 70, "programmatic_score": {"grade": "C"}}', encoding="utf-8")
    assert dfv.parse_sfs_eval(p) == (70.0, "C")


def test_parse_missing_file_returns_none(tmp_path):
    assert dfv.parse_sfs_eval(tmp_path / "nope.json") == (None, None)


def test_parse_malformed_json_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert dfv.parse_sfs_eval(p) == (None, None)


# ============ converge_decision 收敛/floor（复用 validation_gate.decide） ============

def test_converge_accept_v1_strictly_better():
    """v1 SFS 严格优于 v0（且未跌破 floor）→ 采纳 v1。"""
    accept, label, detail = dcg.converge_decision(80.0, 86.0, floor_ratio=0.97)
    assert accept is True and label == "v1", detail
    assert detail["floor"] == round(80.0 * 0.97, 4)


def test_converge_reject_tie():
    """v1 == v0 平局 → 回退 v0（防 reward hacking 漂移·validation_gate 语义）。"""
    accept, label, _ = dcg.converge_decision(82.0, 82.0, floor_ratio=0.97)
    assert accept is False and label == "v0"


def test_converge_reject_worse():
    """v1 < v0 → 回退 v0。"""
    accept, label, _ = dcg.converge_decision(85.0, 80.0, floor_ratio=0.97)
    assert accept is False and label == "v0"


def test_converge_reject_below_floor_even_if_better_than_some():
    """v1 跌破 v0×0.97 floor → 回退 v0（L6 地板·即使 delta 计算前）。
    v0=90 → floor=87.3，v1=88 优于无意义对照但... 这里直接验 floor reject：
    v0=90, v1=85（85<87.3 floor 且 85<90）→ 必回退。"""
    accept, label, detail = dcg.converge_decision(90.0, 85.0, floor_ratio=0.97)
    assert accept is False and label == "v0"
    # floor 锚 = 87.3
    assert detail["floor"] == round(90.0 * 0.97, 4)


def test_converge_infra_skip_when_v1_none():
    """v1 SFS 不可解析（infra 失败）→ 出货 v1 改进版不回退（北极星⑤·不丢 reflect 工作）。"""
    accept, label, detail = dcg.converge_decision(80.0, None)
    assert accept is True and label == "v1"
    assert detail["infra_skip"] is True


def test_converge_infra_skip_when_v0_none():
    """v0 SFS 不可解析 → 同样 infra_skip 出货 v1。"""
    accept, label, detail = dcg.converge_decision(None, 85.0)
    assert accept is True and label == "v1"
    assert detail["infra_skip"] is True


def test_converge_decision_mirrors_validation_gate():
    """收敛判定与底层 validation_gate.decide 完全一致（复用·非另起炉灶）。"""
    v0, v1 = 80.0, 86.0
    floor = round(v0 * 0.97, 4)
    res = vg.decide(rewards_before=[v0], rewards_after=[v1], floor=floor)
    accept, _, detail = dcg.converge_decision(v0, v1, floor_ratio=0.97)
    assert accept == res.accepted
    assert detail["delta"] == res.delta


# ============ converge_decision → 文件选择（main 选 skill 落盘） ============

def test_convergence_gate_ships_v1_on_accept(tmp_path):
    """端到端（无 subprocess）：v1 优于 → ship-out = skill_v1 内容 + eval_ship = eval_v1。"""
    (tmp_path / "skill_v0.md").write_text("# v0 skill", encoding="utf-8")
    (tmp_path / "skill_v1.md").write_text("# v1 skill", encoding="utf-8")
    (tmp_path / "eval_v0.json").write_text('{"sfs_quick": 80, "grade": "B"}', encoding="utf-8")
    (tmp_path / "eval_v1.json").write_text('{"sfs_quick": 88, "grade": "B"}', encoding="utf-8")
    rc = dcg.main([
        "--before-eval", str(tmp_path / "eval_v0.json"),
        "--after-eval", str(tmp_path / "eval_v1.json"),
        "--skill-before", str(tmp_path / "skill_v0.md"),
        "--skill-after", str(tmp_path / "skill_v1.md"),
        "--ship-out", str(tmp_path / "skill_v2.md"),
        "--ship-sfs-out", str(tmp_path / "eval_ship.json"),
    ])
    assert rc == 0
    assert (tmp_path / "skill_v2.md").read_text(encoding="utf-8") == "# v1 skill"
    assert "88" in (tmp_path / "eval_ship.json").read_text(encoding="utf-8")


def test_convergence_gate_reverts_v0_on_reject(tmp_path):
    """v1 未优于（平局）→ ship-out = skill_v0 内容（回退）。"""
    (tmp_path / "skill_v0.md").write_text("# v0 skill", encoding="utf-8")
    (tmp_path / "skill_v1.md").write_text("# v1 skill", encoding="utf-8")
    (tmp_path / "eval_v0.json").write_text('{"sfs_quick": 82, "grade": "B"}', encoding="utf-8")
    (tmp_path / "eval_v1.json").write_text('{"sfs_quick": 82, "grade": "B"}', encoding="utf-8")
    rc = dcg.main([
        "--before-eval", str(tmp_path / "eval_v0.json"),
        "--after-eval", str(tmp_path / "eval_v1.json"),
        "--skill-before", str(tmp_path / "skill_v0.md"),
        "--skill-after", str(tmp_path / "skill_v1.md"),
        "--ship-out", str(tmp_path / "skill_v2.md"),
    ])
    assert rc == 0
    assert (tmp_path / "skill_v2.md").read_text(encoding="utf-8") == "# v0 skill"


def test_convergence_gate_always_exit0(tmp_path):
    """收敛闸只选 skill 不阻断 plan（北极星③）—— 即便 eval 缺失也 exit 0。"""
    (tmp_path / "skill_v0.md").write_text("# v0", encoding="utf-8")
    (tmp_path / "skill_v1.md").write_text("# v1", encoding="utf-8")
    rc = dcg.main([
        "--before-eval", str(tmp_path / "missing0.json"),
        "--after-eval", str(tmp_path / "missing1.json"),
        "--skill-before", str(tmp_path / "skill_v0.md"),
        "--skill-after", str(tmp_path / "skill_v1.md"),
        "--ship-out", str(tmp_path / "skill_v2.md"),
    ])
    assert rc == 0
    # infra_skip → 出货 v1
    assert (tmp_path / "skill_v2.md").read_text(encoding="utf-8") == "# v1"
