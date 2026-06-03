"""rhetoric_repetition_scanner 回归测试（2026-06-03 · AI 长文退化三连指纹检测闭环）。

钉死：gen_writer 元anti-slop 是 prompt 喊话，弱模型 freestyle 守不住——本 scanner 在
cluster 草稿层客观检测兜底。四探针：否定对照密度 / 破折号密度 / 比喻喻体复读 / 整段近重复。
北极星边界：全 advisory（顾问非法官），阈值以 cluster_001 优秀样本校准防矫枉过正。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import rhetoric_repetition_scanner as R  # noqa: E402

# 中性 filler（≈21 CJK/句），用于把样本撑到目标字数稀释/浓缩密度
_FILLER = "池迟走进教室找了个靠窗的位置坐下来看了看外面的天色\n"


def _pad(core: str, filler_lines: int) -> str:
    return core + "\n" + (_FILLER * filler_lines)


def _kinds(r):
    return {v["kind"] for v in r["violations"]}


def _by_kind(r, kind):
    return [v for v in r["violations"] if v["kind"] == kind]


# ── 探针 1：否定对照密度 ─────────────────────────────
def test_neg_contrast_overuse_major():
    """高密度「不是X——是Y」→ neg_contrast_overuse（密度 ≥4.0/kCJK → major）。"""
    core = "\n".join(f"那不是灯光{i}——是别的东西。" for i in range(12))
    r = R.scan(_pad(core, 2))  # 12 对照 / 小字数 → 高密度
    v = _by_kind(r, "neg_contrast_overuse")
    assert v, f"应报否定对照过用，实际 {_kinds(r)}"
    assert v[0]["severity"] == "major"
    assert v[0]["count"] >= 12


def test_neg_contrast_below_threshold_pass():
    """防矫枉过正：cluster_001 风格密度（~2/kCJK）→ 不报否定对照。"""
    core = "他不是没看见——是懒得理。" + "\n那不是风——是别的。"
    r = R.scan(_pad(core, 60))  # 2 对照 / ~1300 CJK ≈ 1.5/kCJK < 2.8
    assert "neg_contrast_overuse" not in _kinds(r)


# ── 探针 2：破折号密度 ───────────────────────────────
def test_dash_overuse_major():
    core = "\n".join(f"他停了一下——又往前走了第{i}步。" for i in range(30))
    r = R.scan(_pad(core, 1))
    v = _by_kind(r, "dash_overuse")
    assert v, f"应报破折号过用，实际 {_kinds(r)}"
    assert v[0]["severity"] == "major"


def test_dash_sparse_pass():
    """少量破折号（cluster_001 ~12.5/kCJK 量级）→ 不报。"""
    core = "他停了一下——又走了。"
    r = R.scan(_pad(core, 60))  # 1 破折 / ~1300 CJK
    assert "dash_overuse" not in _kinds(r)


def test_dash_tightened_by_author_baseline():
    """北极星⑤：作者几乎不用破折号(0.3/千) → 阈值收紧到 8/14，~10/kCJK 即报（通用 15/22 本不报）。"""
    core = "\n".join(f"他停了一下——第{i}次。" for i in range(13))  # 13 破折号
    text = _pad(core, 55)  # 稀释到约 10/kCJK（落在收紧 8 与通用 15 之间）
    r_generic = R.scan(text)                              # 无作者基线 → 通用 15/22
    r_tight = R.scan(text, author_dash_per_kcjk=0.3)      # 作者不用破折号 → 收紧 8/14
    assert "dash_overuse" not in _kinds(r_generic), "通用阈值下该密度不报"
    assert "dash_overuse" in _kinds(r_tight), "作者基线收紧后应报"


# ── 探针 3：比喻喻体复读 ─────────────────────────────
def test_simile_reuse_detected():
    """同一喻体复读 ≥5 次 → simile_reuse（≥8 major）。"""
    core = "\n".join(f"他的声音像放凉的粥，第{i}次响起。" for i in range(9))
    r = R.scan(_pad(core, 3))
    v = _by_kind(r, "simile_reuse")
    assert v, f"应报比喻复读，实际 {_kinds(r)}"
    assert any("放凉的粥" in x["vehicle"] for x in v)
    assert any(x["severity"] == "major" for x in v)  # 9 次 ≥8


def test_diverse_similes_pass():
    """不同喻体各用一次 → 不报（复读才是问题，比喻本身不是）。"""
    core = ("他的脸像石膏。\n声音像旧电台。\n天色像灯箱片。\n"
            "走廊像医院。\n校服像赶工的。")
    r = R.scan(_pad(core, 30))
    assert "simile_reuse" not in _kinds(r)


# ── 探针 4：整段近重复 ───────────────────────────────
def test_paragraph_exact_copy_major():
    """两个 CJK≥30 的逐字相同长段 → paragraph_near_dup major（ratio 1.0）。"""
    para = ("网线从他身边穿过但没有一条线碰到他每一条线在靠近他身体的时候都自动绕开了"
            "绕过他的肩膀绕过他的腰绕过他的腿")
    core = para + "\n" + _FILLER + para
    r = R.scan(_pad(core, 5))
    v = _by_kind(r, "paragraph_near_dup")
    assert v, f"应报整段近重复，实际 {_kinds(r)}"
    assert v[0]["severity"] == "major"
    assert v[0]["ratio"] >= 0.85


def test_short_paras_not_compared():
    """短段（CJK<30）即使相同也不比对（避免对话/独行短句误报）。"""
    core = "他点头。\n他点头。\n他点头。\n他点头。"
    r = R.scan(_pad(core, 20))
    assert "paragraph_near_dup" not in _kinds(r)


def test_length_diff_prefilter():
    """长度差 >25% 的两段不比对（性能预筛 + 防长短段误判）。"""
    long_a = "他站在香案前低头看着那本合着的册子封皮上烫金的大字在烛火下泛着微微的光"
    long_b = long_a + long_a + long_a  # 长度差极大 → 预筛跳过
    core = long_a + "\n" + _FILLER + long_b
    r = R.scan(_pad(core, 5))
    # 不应因长短悬殊配出 near_dup（即便子串高度重叠）
    for v in _by_kind(r, "paragraph_near_dup"):
        assert not (long_a[:20] in str(v.get("evidence")) and long_b[:20] in str(v.get("evidence")))


# ── 总体契约 ─────────────────────────────────────────
def test_clean_text_passes():
    core = ("池迟睁开眼前是一扇校门铁栅栏刷着绿漆漆皮翘起几块露出底下锈红的铁。\n"
            "天空灰白没有太阳也看不见云。\n他低头看了看自己穿着深蓝色的校服。")
    r = R.scan(_pad(core, 10))
    assert r["verdict"] == "PASS", f"干净文本应 PASS，violations={r['violations']}"


def test_gate_level_always_advisory_even_when_major():
    """北极星⑤：哪怕 FAIL_MAJOR，scanner 顶层 gate_level 恒 advisory（顾问非法官·可豁免）。"""
    core = "\n".join(f"那不是灯{i}——是别的——还是别的——又是别的。" for i in range(20))
    r = R.scan(_pad(core, 1))
    assert r["verdict"] == "FAIL_MAJOR"
    assert r["gate_level"] == "advisory"


def test_violations_schema_for_audit_hub():
    """每条 violation 必带 kind+severity(major/minor)，供 audit_hub _parse_violations_scanner 复用。"""
    core = "\n".join(f"那不是灯{i}——是别的东西。" for i in range(12))
    r = R.scan(_pad(core, 1))
    assert r["scanner"] == "rhetoric_repetition"
    for v in r["violations"]:
        assert "kind" in v
        assert v["severity"] in ("major", "minor")
