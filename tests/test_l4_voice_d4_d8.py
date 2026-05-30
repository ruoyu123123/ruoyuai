"""L4 D4 voice区分度 + D8 口癖一致性测试（2026-05-31 · 主代理手动实现回归）。

D4D8 两次 Workflow schema-nudge 失败后改主代理手动实现。
全 advisory · env VOICE_D4D8_MODE 默认 shadow（只挂字段不改 warning/exit · 回归0）。
零依赖纯统计（不用 embedding）· 砍掉项 D6情绪弧/D10情绪直陈不做。
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import cross_scene_voice_drift_scanner as vd


def _reload(mode):
    if mode is None:
        os.environ.pop("VOICE_D4D8_MODE", None)
    else:
        os.environ["VOICE_D4D8_MODE"] = mode
    importlib.reload(vd)
    return vd


# ════ [A] mode 解析 ════
def test_A_mode_default_active():
    m = _reload(None)   # 未设 → active(2026-05-31 放量·真作者13角色mean_dist=0.422已验证不误判)
    try:
        assert m._d4d8_mode() == "active"
    finally:
        _reload(None)


def test_A_mode_explicit_shadow_active_off():
    try:
        assert _reload("shadow")._d4d8_mode() == "shadow"
        assert _reload("active")._d4d8_mode() == "active"
        assert _reload("off")._d4d8_mode() == "off"
        assert _reload("ACTIVE")._d4d8_mode() == "active"
    finally:
        _reload(None)


def test_A_mode_garbage_falls_back_active():
    try:
        assert _reload("garbage")._d4d8_mode() == "active"   # 非法 → active(放量默认态)
    finally:
        _reload(None)


# ════ [B] voice 指纹 ════
def test_B_fingerprint_basic():
    fp = vd._voice_fingerprint(["你好啊", "走吧", "在吗？"])
    assert fp["_n"] == 3
    assert fp["avg_len"] > 0
    assert fp["question_ratio"] > 0      # "在吗？"
    assert fp["tic_rate"]["啊"] > 0       # "你好啊"
    assert fp["tic_rate"]["吧"] > 0       # "走吧"


def test_B_fingerprint_empty():
    assert vd._voice_fingerprint([]) is None


# ════ [C] 指纹距离 ════
def test_C_distance_identical_is_zero():
    fp = vd._voice_fingerprint(["你好啊", "走吧"])
    assert vd._fingerprint_distance(fp, fp) == 0.0


def test_C_distance_different_positive():
    a = vd._voice_fingerprint(["嗯。", "哦。", "走。"])                       # 短·无语气问号
    b = vd._voice_fingerprint(["这是一段很长很长的对白内容呢？？？",
                               "你倒是说句话啊到底行不行嘛？真的吗？"])      # 长·问号·语气词多
    assert vd._fingerprint_distance(a, b) > 0.1


# ════ [D] D4 区分度 ════
def test_D_d4_less_than_2_chars():
    r = vd.compute_d4_distinctiveness({"甲": ["你好啊", "走吧", "在吗"]})
    assert r["applicable"] is False


def test_D_d4_distinct_characters():
    chars = {
        "甲": ["嗯。", "哦。", "好。", "行。"],                                  # 极简短
        "乙": ["哎呀这可怎么办呀真是急死人了呢？", "你倒是说句话啊到底行不行嘛？",
               "我跟你讲这事儿没那么简单啦！"],                                 # 长·语气词多·问叹号
    }
    r = vd.compute_d4_distinctiveness(chars)
    assert r["applicable"] is True
    assert r["char_count"] == 2
    assert r["mean_pairwise_distance"] > 0.1          # 区分明显


def test_D_d4_homogeneous_characters():
    same = ["你好啊", "走吧呢", "在吗啊", "好的呢"]
    r = vd.compute_d4_distinctiveness({"甲": same, "乙": list(same), "丙": list(same)})
    assert r["applicable"] is True
    assert r["low_distinctiveness"] is True           # 完全相同 → 区分度低
    assert len(r["low_distinctiveness_pairs"]) >= 1


# ════ [E] D8 口癖一致性 ════
def test_E_d8_consistent_no_issue():
    tics = {"甲": [(0, {"啊": 1.5, "呢": 0.5}), (1, {"啊": 1.4, "呢": 0.6})]}
    assert vd.compute_d8_tic_consistency(tics)["count"] == 0


def test_E_d8_inconsistent_detected():
    # 场景0 "啊" 显著(3.0)·场景1 几乎消失(0.1) → 不一致
    tics = {"甲": [(0, {"啊": 3.0}), (1, {"啊": 0.1})]}
    r = vd.compute_d8_tic_consistency(tics)
    assert r["count"] >= 1
    assert r["inconsistent_tics"][0]["tic"] == "啊"


def test_E_d8_single_scene_skip():
    assert vd.compute_d8_tic_consistency({"甲": [(0, {"啊": 3.0})]})["count"] == 0


def test_E_d8_insignificant_tic_not_flagged():
    # 口癖率都很低(<2.0) → 不报（不显著的口癖波动不算漂移）
    tics = {"甲": [(0, {"啊": 0.5}), (1, {"啊": 0.05})]}
    assert vd.compute_d8_tic_consistency(tics)["count"] == 0


# ════ [F] scan 集成 + 影子回归 ════
def _make_draft(tmp, text):
    (tmp / "_数据库").mkdir(exist_ok=True)
    d = tmp / "draft.txt"
    d.write_text(text, encoding="utf-8")
    return d


_DRAFT = (
    "阿强说道：“你好啊，最近怎么样啊？”\n"
    "阿强问道：“真的吗啊？到底行不行啊？”\n"
    "阿强道：“我跟你讲啊，这事儿啊。”\n"
    "阿明说道：“还行吧。”\n"
    "阿明道：“好的呢，没问题呢。”\n"
    "阿明说：“就这样吧呢。”\n"
)


def test_F_scan_shadow_default_field_present_no_warning_pollution():
    vdmod = _reload("shadow")  # 显式 shadow(放量后默认 active·shadow 仍合法·测其回归行为)
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rep = vdmod.scan(tmp, _make_draft(tmp, _DRAFT))
            assert rep["d4d8_mode"] == "shadow"
            assert "d4_voice_distinctiveness" in rep
            assert "d8_tic_consistency" in rep
            # shadow：warning 绝不含 D4/D8 内容（回归0·只由原 drift_issues 决定）
            if rep["warning"]:
                assert "区分度" not in rep["warning"] and "口癖" not in rep["warning"]
    finally:
        _reload(None)


def test_F_scan_off_skips_d4d8():
    vdmod = _reload("off")
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rep = vdmod.scan(tmp, _make_draft(tmp, _DRAFT))
            assert rep["d4_voice_distinctiveness"] is None
            assert rep["d8_tic_consistency"] is None
    finally:
        _reload(None)


def test_F_scan_active_surfaces_homogeneous_d4():
    vdmod = _reload("active")
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # 三角色完全同质对白 → active 应升 warning
            text = (
                "阿强说道：“你好啊呢。”\n阿明说道：“你好啊呢。”\n阿丽说道：“你好啊呢。”\n"
                "阿强道：“走吧啊。”\n阿明道：“走吧啊。”\n阿丽道：“走吧啊。”\n"
                "阿强说：“在吗呢。”\n阿明说：“在吗呢。”\n阿丽说：“在吗呢。”\n"
            )
            rep = vdmod.scan(tmp, _make_draft(tmp, text))
            assert rep["d4d8_mode"] == "active"
            d4 = rep["d4_voice_distinctiveness"]
            assert d4 is not None
            if d4.get("applicable"):
                assert d4["low_distinctiveness"] is True   # 同质 → 区分度低
                assert rep["warning"] and "区分度" in rep["warning"]  # active 升 warning
    finally:
        _reload(None)


# ════ [G] advisory 锁死 + 不进 HARD_GATE_CODES ════
def test_G_gate_level_advisory():
    vdmod = _reload(None)
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rep = vdmod.scan(tmp, _make_draft(tmp, _DRAFT))
            assert rep["gate_level"] == "advisory"
    finally:
        _reload(None)


def test_G_no_new_hard_gate_code():
    import audit_hub
    hgc = set(audit_hub.HARD_GATE_CODES)
    assert "VOICE_DRIFT_CROSS_SCENE" not in hgc
    # D4/D8 无独立 code（挂 advisory report 字段）· 确认没污染 hard_gate
    for code in hgc:
        assert "DISTINCTIVENESS" not in code and "TIC" not in code
