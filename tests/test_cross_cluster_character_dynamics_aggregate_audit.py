"""cross_cluster_character_dynamics_aggregate 健壮性回归（2026-06-16 · triage worth_fixing 4 修）。

守护「角色行为动态扫」(CCR5+7+8) 的确定性数据投影健壮性。这些 scanner 读 writer 自由
产出的 _changes.json self_eval + 主角压力档.json（弱 LLM / 可选子系统，皆不受 schema
强约束）。triage 抓出 4 处真 bug（全 advisory/warning · 北极星⑤：不碰创作判断 · 不升
hard_gate）：

  · L305  scan_position_effect（磁盘）：position / effect 是两个独立分布，却共用
           total_evals 当分母。只有 effect 无 position 的 eval 合法可达 → eff_dist
           百分比可 >100% → 虚报 EFFECT_TOO_GREAT / EFFECT_TOO_LIMITED；反向（只有
           position 无 effect）则分母虚高稀释 → 漏报。修：新增 total_effects 独立分母。
  · L524  scan_position_effect_ledger（账本·cluster save-state step9 主路径）：同因
           同构的孪生 bug，且兼容两套键名风险面更大。同改。
  · L401  scan_stress_trend_ledger：缺『coping 是否被定义』前置守卫（磁盘版 L157
           `if high_chs and coping:` 有）。新书 skeleton 默认 coping_mechanisms: {}
           → 任何用了 stress 但没填 coping 行为的书开箱即误报 COPING_NEVER_TRIGGERED。
           修：加 coping_defined 守卫，与磁盘版对齐。
  · L189  scan_stress_trend（磁盘）：`b_card in text_dump`，b_card 缺/空串时
           `'' in 任意串` 恒 True → any_hit 恒 True → MENTAL_BREAK_FORGOTTEN 永久
           静默（假阴性）。修：`b_card and b_card in text_dump`，与账本版 L416 对齐。

测试零依赖：纯函数直调（ledger 版传 recs；磁盘版 tempfile 建假项目）。
__main__ 跑全部 test_*。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import cross_cluster_character_dynamics_aggregate as ccd  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 沙箱辅助：造假项目（章节/第NNN章/_changes.json[+.txt] + _数据库/主角压力档.json）
# ════════════════════════════════════════════════════════════════

def _make_proj(tmp: Path, *, per_ch_changes: dict, stress: dict | None = None,
               per_ch_text: dict | None = None) -> Path:
    """per_ch_changes: {ch_int: changes_dict}；可选 stress→主角压力档.json、per_ch_text→正文。"""
    proj = tmp / "proj"
    for ch, changes in per_ch_changes.items():
        ch_dir = proj / "章节" / f"第{ch:03d}章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / f"第{ch:03d}章_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False), encoding="utf-8")
        body = (per_ch_text or {}).get(ch, "正文内容。\n")
        (ch_dir / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")
    if stress is not None:
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "主角压力档.json").write_text(
            json.dumps(stress, ensure_ascii=False), encoding="utf-8")
    return proj


def _codes(findings: list[dict]) -> set:
    return {f["code"] for f in findings}


def _finding(findings: list[dict], code: str) -> dict | None:
    for f in findings:
        if f["code"] == code:
            return f
    return None


# ════════════════════════════════════════════════════════════════
# Bug L305 / L524：position / effect 独立分母（disk + ledger 孪生）
# ════════════════════════════════════════════════════════════════

def _evals_effect_only(effect: str, n: int) -> list[dict]:
    """n 条『只有 effect 无 position』的合法 eval（触发旧 bug 的分母错配）。"""
    return [{"evaluated_effect": effect} for _ in range(n)]


def test_ledger_effect_only_no_overcount_great():
    """账本版：4 条只有 effect=great、0 条 position → 旧 bug:total_evals=0→<3 直接 return（漏报）
    或若混入则分母虚高。修后 total_effects=4 → eff_dist['great']=1.0 → 正确报 EFFECT_TOO_GREAT，
    且百分比绝不 >100%。"""
    recs = [(i, {"self_eval": {"position_effect_evals": _evals_effect_only("great", 1)}})
            for i in range(1, 5)]  # 4 章各 1 条 effect-only
    findings = ccd.scan_position_effect_ledger(recs)
    f = _finding(findings, "EFFECT_TOO_GREAT")
    assert f is not None, f"4 条 great effect 应报 EFFECT_TOO_GREAT，实得 {_codes(findings)}"
    assert f["distribution"]["great"] == 1.0, f"分布须 = 1.0（独立分母），实得 {f['distribution']}"
    # 旧 bug 的核心症状：百分比 > 100%。确认绝无。
    for v in f["distribution"].values():
        assert v <= 1.0, f"分布百分比 > 100% = 分母错配未修: {f['distribution']}"


def test_ledger_effect_dist_not_diluted_by_position_only():
    """账本版：3 条 effect=great + 7 条只有 position（无 effect）。
    旧 bug:eff_dist 分母=total_evals=10 → great=0.3 → 漏报。
    修后 eff_dist 分母=total_effects=3 → great=1.0 → 正确报 EFFECT_TOO_GREAT。"""
    recs = []
    for i in range(1, 4):  # 3 章 effect-only great
        recs.append((i, {"self_eval": {"position_effect_evals": [{"evaluated_effect": "great"}]}}))
    for i in range(4, 11):  # 7 章 position-only controlled
        recs.append((i, {"self_eval": {"position_effect_evals": [{"evaluated_position": "controlled"}]}}))
    findings = ccd.scan_position_effect_ledger(recs)
    f = _finding(findings, "EFFECT_TOO_GREAT")
    assert f is not None, f"effect 分母被 position 稀释导致漏报（旧 bug），实得 {_codes(findings)}"
    assert f["distribution"]["great"] == 1.0


def test_ledger_position_only_still_reports_position():
    """账本版：6 条只有 position=controlled、0 effect → pos_dist['controlled']=1.0 报 POSITION_TOO_SAFE，
    eff_dist 为空不触发任何 EFFECT_* 码（独立分母互不干扰）。"""
    recs = [(i, {"self_eval": {"position_effect_evals": [{"evaluated_position": "controlled"}]}})
            for i in range(1, 7)]
    findings = ccd.scan_position_effect_ledger(recs)
    assert "POSITION_TOO_SAFE" in _codes(findings)
    assert not ({"EFFECT_TOO_GREAT", "EFFECT_TOO_LIMITED"} & _codes(findings)), \
        f"无 effect 样本不该触发 EFFECT_* 码: {_codes(findings)}"


def test_ledger_position_effect_below_sample_floor():
    """账本版：position 2 条 + effect 2 条（各 <3）→ 都不足样本 → 零 finding（早返回守卫）。"""
    recs = [
        (1, {"self_eval": {"position_effect_evals": [{"evaluated_position": "controlled", "evaluated_effect": "great"}]}}),
        (2, {"self_eval": {"position_effect_evals": [{"evaluated_position": "controlled", "evaluated_effect": "great"}]}}),
    ]
    findings = ccd.scan_position_effect_ledger(recs)
    assert findings == [], f"position/effect 各 2 条 (<3) 应零 finding，实得 {findings}"


def test_disk_effect_only_no_overcount():
    """磁盘版 scan_position_effect 同步修：4 章各 1 条 effect-only=great → EFFECT_TOO_GREAT，分布=1.0。"""
    with tempfile.TemporaryDirectory() as td:
        per_ch = {i: {"self_eval": {"position_effect_evals": [{"evaluated_effect": "great"}]}}
                  for i in range(1, 5)}
        proj = _make_proj(Path(td), per_ch_changes=per_ch)
        findings = ccd.scan_position_effect(proj, list(per_ch.keys()))
        f = _finding(findings, "EFFECT_TOO_GREAT")
        assert f is not None, f"磁盘版 4 条 great 应报，实得 {_codes(findings)}"
        assert f["distribution"]["great"] == 1.0
        for v in f["distribution"].values():
            assert v <= 1.0


def test_disk_position_effect_happy_balanced_no_finding():
    """磁盘控制组：position/effect 均衡分布 → 不越任何阈值 → 零 finding（守卫不误伤正常）。"""
    with tempfile.TemporaryDirectory() as td:
        # 每章 controlled+standard / risky+standard 交替，分布均衡
        per_ch = {}
        for i in range(1, 7):
            pos = "controlled" if i % 2 else "risky"
            per_ch[i] = {"self_eval": {"position_effect_evals": [
                {"evaluated_position": pos, "evaluated_effect": "standard"}]}}
        proj = _make_proj(Path(td), per_ch_changes=per_ch)
        findings = ccd.scan_position_effect(proj, list(per_ch.keys()))
        assert findings == [], f"均衡分布不应有 finding: {findings}"


# ════════════════════════════════════════════════════════════════
# Bug L401：scan_stress_trend_ledger coping_defined 守卫
# ════════════════════════════════════════════════════════════════

def _high_stress_recs(n: int, *, threshold: int = 8, coping_hit_chs=None) -> list:
    """n 个高压章（stress_total >= 0.75*threshold），coping_hit 默认全 False。"""
    coping_hit_chs = set(coping_hit_chs or [])
    high = int(threshold * 0.75) + 1
    return [(i, {"stress_total": high, "coping_hit": i in coping_hit_chs})
            for i in range(1, n + 1)]


def test_ledger_coping_undefined_suppresses_finding():
    """coping_mechanisms: {}（新书 skeleton 默认）+ 5 高压章 0 coping_hit →
    旧 bug:误报 COPING_NEVER_TRIGGERED。修后 coping_defined=False → 不报（与磁盘版对齐）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _make_proj(Path(td), per_ch_changes={1: {}},  # changes 不被 ledger 版用，占位
                          stress={"stress_threshold_break": 8, "coping_mechanisms": {}})
        recs = _high_stress_recs(5)
        findings = ccd.scan_stress_trend_ledger(proj, recs)
        assert "COPING_NEVER_TRIGGERED" not in _codes(findings), \
            f"coping 未定义不该报 COPING_NEVER_TRIGGERED（旧 bug），实得 {_codes(findings)}"


def test_ledger_coping_defined_still_reports():
    """coping_mechanisms.high_stress_behaviors 非空 + 高压章 0 coping_hit →
    coping_defined=True → 正常报 COPING_NEVER_TRIGGERED（守卫不吞真问题）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _make_proj(Path(td), per_ch_changes={1: {}},
                          stress={"stress_threshold_break": 8,
                                  "coping_mechanisms": {"high_stress_behaviors": ["抽烟", "独处"]}})
        recs = _high_stress_recs(5)
        findings = ccd.scan_stress_trend_ledger(proj, recs)
        assert "COPING_NEVER_TRIGGERED" in _codes(findings), \
            f"coping 已定义且全程未命中应报，实得 {_codes(findings)}"


def test_ledger_coping_defined_but_hit_no_finding():
    """coping 已定义且高压章里命中过 coping_hit → 不报（正常消费）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = _make_proj(Path(td), per_ch_changes={1: {}},
                          stress={"stress_threshold_break": 8,
                                  "coping_mechanisms": {"high_stress_behaviors": ["抽烟"]}})
        recs = _high_stress_recs(5, coping_hit_chs=[2, 4])
        findings = ccd.scan_stress_trend_ledger(proj, recs)
        assert "COPING_NEVER_TRIGGERED" not in _codes(findings)


# ════════════════════════════════════════════════════════════════
# Bug L189：scan_stress_trend（磁盘）空 card_id 假阴性
# ════════════════════════════════════════════════════════════════

def _stress_log_with_break(break_ch: int, card_id, post_chs: list[int]) -> dict:
    """造 stress_log：break_ch 触发 mental_break（card_id 可为空），外加若干普通章撑出 chapters。"""
    log = [{"ch": break_ch, "trigger_type": "mental_break_triggered", "card_id": card_id}]
    # break 前后都要有非 break 的 stress 数据点（>=2 才进主逻辑），且 post_chs 是 break 后章
    log.append({"ch": break_ch, "trigger_type": "stress_gain", "new_total": 9})
    for c in post_chs:
        log.append({"ch": c, "trigger_type": "stress_gain", "new_total": 5})
    return {"stress_threshold_break": 8, "stress_log": log}


def test_disk_empty_card_id_still_reports_break_forgotten():
    """break 条 card_id="" + 后续章 _changes 不引用 →
    旧 bug:`'' in text_dump` 恒 True → any_hit 恒 True → 永久静默漏报。
    修后 `b_card and ...` → 空 card 时 any_hit 保持 False → 正常报 MENTAL_BREAK_FORGOTTEN。"""
    with tempfile.TemporaryDirectory() as td:
        break_ch, post = 1, [2, 3, 4]
        stress = _stress_log_with_break(break_ch, "", post)
        # 后续章 _changes 不含任何 card 引用
        per_ch = {c: {"factual": {"locked_facts": []}} for c in [break_ch] + post}
        proj = _make_proj(Path(td), per_ch_changes=per_ch, stress=stress)
        findings = ccd.scan_stress_trend(proj, sorted(per_ch.keys()))
        assert "MENTAL_BREAK_FORGOTTEN" in _codes(findings), \
            f"空 card_id 时旧 bug 会静默漏报，修后应报，实得 {_codes(findings)}"


def test_disk_real_card_referenced_no_false_positive():
    """break 条 card_id 非空且后续章 _changes 真引用该 card → any_hit=True → 不报（守卫不误伤）。"""
    with tempfile.TemporaryDirectory() as td:
        break_ch, post = 1, [2, 3, 4]
        stress = _stress_log_with_break(break_ch, "break_card_X", post)
        per_ch = {break_ch: {"factual": {"locked_facts": []}}}
        # ch2 引用了该 card_id → 应命中、不报
        per_ch[2] = {"factual": {"locked_facts": ["break_card_X 的后续影响"]}}
        per_ch[3] = {"factual": {"locked_facts": []}}
        per_ch[4] = {"factual": {"locked_facts": []}}
        proj = _make_proj(Path(td), per_ch_changes=per_ch, stress=stress)
        findings = ccd.scan_stress_trend(proj, sorted(per_ch.keys()))
        assert "MENTAL_BREAK_FORGOTTEN" not in _codes(findings), \
            f"真 card 被引用不该报: {_codes(findings)}"


def test_disk_real_card_never_referenced_reports():
    """break 条 card_id 非空但后续章从不引用 → 正常报 MENTAL_BREAK_FORGOTTEN（确认非空路径仍工作）。"""
    with tempfile.TemporaryDirectory() as td:
        break_ch, post = 1, [2, 3, 4]
        stress = _stress_log_with_break(break_ch, "break_card_Y", post)
        per_ch = {c: {"factual": {"locked_facts": []}} for c in [break_ch] + post}
        proj = _make_proj(Path(td), per_ch_changes=per_ch, stress=stress)
        findings = ccd.scan_stress_trend(proj, sorted(per_ch.keys()))
        assert "MENTAL_BREAK_FORGOTTEN" in _codes(findings)


# ════════════════════════════════════════════════════════════════
# import 守卫
# ════════════════════════════════════════════════════════════════

def test_module_imports():
    """模块可 import（4 修后无语法/引用错），关键函数在位。"""
    assert hasattr(ccd, "scan_position_effect")
    assert hasattr(ccd, "scan_position_effect_ledger")
    assert hasattr(ccd, "scan_stress_trend")
    assert hasattr(ccd, "scan_stress_trend_ledger")
    assert hasattr(ccd, "main")


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
    print(f"\n{'ALL OK' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
