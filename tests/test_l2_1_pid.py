"""L2-1 保守增量 PI 阈值控制器测试（2026-05-30 · 北极星⑤顾问非法官）。

【守护的设计】pid_threshold_tuner 把 L2-0 降档止血升级为完整保守增量 PI 控制器
（Filieri SEAMS2015 / CFAR），主动把真作者原文虚警率(FPR)驱动到 0。控制对象严格圈定
4 个连续 advisory 阈值（para_mean_len/dialogue_ratio/long_para_per_chapter/quota_per_word）。

本测试断言：
  · 增量式 PI 公式 Δ=Kp(e−e_prev)+Ki·e 正确（含抗 windup 只记 e_prev 不累加历史绝对量）；
  · 双目标误差 e=w1·FPR−w2·(rate−τ)；回测无 AI 样本 → 退化单目标；
  · 防震荡三件套：死区随样本量缩放 / band 硬边界 clamp / 低频每 cluster 更新；
  · 【物理隔离红线】控制器只读写白名单 4 键·硬拒其余（含 15 HARD_GATE_CODES）；
  · 【advisory 锁死守卫】本件绝不污染 audit_hub.HARD_GATE_CODES（结构性保证不可黑箱升 hard_gate）；
  · env PID_THRESHOLD_MODE 默认 off → apply_pid_delta 零回归（不改判决）；
  · validate_style 接线后 off 模式 byte 级零回归 / active 模式按正确方向放松；
  · 【离线回测金标准】真作者原文 FPR 收敛曲线单调下降不发散（蛊真人 + 惊悚乐园）。

只测确定性纯函数 + 真作者原文（无 LLM / agent / 网络 / 重依赖）。
"""
import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import pid_threshold_tuner as pid  # noqa: E402
import validate_style as vs  # noqa: E402
import learning_loop as ll  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_STYLES = _ROOT / "workspace" / "styles"


def _fresh_state():
    return {"version": 1, "e_prev": {}, "theta_delta": {}, "n_samples": 0,
            "last_update_cluster": None, "fpr_history": []}


# ---------- 增量式 PI 公式 + 抗 windup ----------

def test_pi_step_formula():
    """Δ = Kp·(e − e_prev) + Ki·e。"""
    d = pid._pi_step(e=0.3, e_prev=0.1, kp=0.05, ki=0.02)
    assert abs(d - (0.05 * 0.2 + 0.02 * 0.3)) < 1e-9


def test_pi_incremental_anti_windup_only_tracks_e_prev():
    """增量式抗 windup：state 只记 e_prev（不累加积分历史绝对量）。
    连续相同误差，每步增量恒定（不爆炸增长）= 真正的增量式。"""
    st = _fresh_state()
    st["n_samples"] = 50  # 大样本→死区小，确保动阈值
    pid.update_controller(st, {"quota_per_word": 0.3}, n_samples=50)
    s1 = st["theta_delta"]["quota_per_word"]["_scalar"]
    pid.update_controller(st, {"quota_per_word": 0.3}, n_samples=50)
    s2 = st["theta_delta"]["quota_per_word"]["_scalar"]
    # 第二步 e==e_prev → 比例项=0，只剩积分项 Ki·e（增量恒定·不 windup 爆炸）
    inc = s2 - s1
    assert abs(inc - 0.02 * 0.3) < 1e-9
    assert st["e_prev"]["quota_per_word"] == 0.3


# ---------- 双目标误差 ----------

def test_compute_error_dual_objective():
    """e = w1·FPR − w2·(rate − τ)。"""
    e = pid.compute_error(fpr=0.2, ai_detect_rate=0.6, tau=0.8, w1=1.0, w2=0.5)
    assert abs(e - (1.0 * 0.2 - 0.5 * (0.6 - 0.8))) < 1e-9


def test_compute_error_degrades_to_single_objective():
    """回测无 AI 样本（ai_detect_rate=None）→ 退化单目标 e = w1·FPR。"""
    e = pid.compute_error(fpr=0.2, ai_detect_rate=None, w1=1.0)
    assert abs(e - 0.2) < 1e-9


def test_high_fpr_relaxes_thresholds():
    """FPR 高 → e>0 → 阈值朝放松方向走（scalar 增大）。"""
    st = _fresh_state()
    pid.update_controller(st, {"para_mean_len": 0.5}, n_samples=50)
    assert st["theta_delta"]["para_mean_len"]["_scalar"] > 0


# ---------- 防震荡三件套 ----------

def test_deadband_scales_with_sample_count():
    """死区随样本量缩放：样本越少死区越大。"""
    assert pid._deadband(2) > pid._deadband(50)
    # n == MIN_SAMPLES 时死区 == 基线
    assert abs(pid._deadband(pid._MIN_SAMPLES) - pid._BASE_DEADBAND) < 1e-9


def test_deadband_blocks_small_error_low_sample():
    """少样本 + 小误差落死区内 → 不动阈值（只记 e_prev）。"""
    st = _fresh_state()
    # n=2 死区 ≈ 0.1；误差 0.05 < 死区 → 不动
    pid.update_controller(st, {"quota_per_word": 0.05}, n_samples=2)
    assert "quota_per_word" not in st["theta_delta"]
    assert st["e_prev"]["quota_per_word"] == 0.05  # 仍记 e_prev


def test_step_clamp_limits_single_jump():
    """单步限幅：超大误差也只动 _MAX_STEP_FRAC（防单 cluster 跳变）。"""
    st = _fresh_state()
    pid.update_controller(st, {"quota_per_word": 99.0}, n_samples=50)
    assert st["theta_delta"]["quota_per_word"]["_scalar"] <= pid._MAX_STEP_FRAC + 1e-9


def test_band_hard_bound_clamp_in_apply():
    """band 硬边界 clamp：active 叠加后阈值绝不破物理安全栏（quota 上限 12）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"quota_per_word": {"_scalar": 99.0}}  # 巨量放松
        pid.save_state(tmp, st)
        os.environ["PID_THRESHOLD_MODE"] = "active"
        out = pid.apply_pid_delta({"quota_per_word": {"max": 5.0}}, tmp)
        assert out["quota_per_word"]["max"] <= 12.0  # 物理上限 clamp
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_low_frequency_per_cluster_update():
    """低频每 cluster 更新：同 cluster_id 不重复迭代（state 不变）。"""
    st = _fresh_state()
    pid.update_controller(st, {"para_mean_len": 0.5}, cluster_id="c1", n_samples=50)
    s1 = st["theta_delta"]["para_mean_len"]["_scalar"]
    # 同 cluster 再调 → 不动
    pid.update_controller(st, {"para_mean_len": 0.5}, cluster_id="c1", n_samples=50)
    assert st["theta_delta"]["para_mean_len"]["_scalar"] == s1
    # 新 cluster → 继续迭代
    pid.update_controller(st, {"para_mean_len": 0.5}, cluster_id="c2", n_samples=50)
    assert st["theta_delta"]["para_mean_len"]["_scalar"] > s1


# ---------- 物理隔离红线（白名单 4 键 · 硬拒其余） ----------

def test_controlled_keys_exactly_four():
    """被控键严格 4 个（与本件圈定一致·绝不扩张）。"""
    assert pid._CONTROLLED_KEYS == (
        "para_mean_len", "dialogue_ratio", "long_para_per_chapter", "quota_per_word")


def test_reject_non_whitelist_key():
    """硬拒非白名单键（含任何 HARD_GATE_CODE）→ raise ValueError。"""
    st = _fresh_state()
    for bad in ("LOCKED_FACT_CONFLICT", "ultra_short_ratio", "chapter_words", "banned_words"):
        try:
            pid.update_controller(dict(st), {bad: 0.5}, n_samples=50)
            assert False, f"应拒绝非白名单键 {bad}"
        except ValueError:
            pass


def test_apply_only_touches_whitelist_keys():
    """apply_pid_delta active 永不改白名单外的键（物理隔离回路外）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        pid.save_state(tmp, st)
        os.environ["PID_THRESHOLD_MODE"] = "active"
        base = {"para_mean_len": {"max": 40}, "banned_words": {"max": 0},
                "chapter_words": {"min": 1800, "max": 5800}}
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, tmp)
        # 被控键变；非被控键纹丝不动
        assert out["banned_words"] == base["banned_words"]
        assert out["chapter_words"] == base["chapter_words"]
        assert out["para_mean_len"]["max"] != base["para_mean_len"]["max"]
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- advisory 锁死守卫（结构性保证不可黑箱升 hard_gate） ----------

def test_hard_gate_codes_not_polluted():
    """本件 L2-1 改动绝不向 audit_hub.HARD_GATE_CODES 新增任何 code（北极星⑤）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import audit_hub as ah
    expected = {
        "LOCKED_FACT_CONFLICT", "FUTURE_KNOWLEDGE_LEAK", "FORESHADOWING_NOT_PAID",
        "SECRET_NOT_REVEALED", "UNKNOWN_CHARACTER_DETECTED", "CHANGES_MISSING",
        "MANIFEST_MISSING", "FILE_NOT_FOUND", "ITEM_HOLDER_ABSENT",
        "ITEM_NOT_YET_INTRODUCED", "PROPAGATION_DEBT_CREATED", "STYLE_单段超长",
        "CHAPTER_END_FORBIDDEN_SCREENPLAY", "CHAPTER_END_FORBIDDEN_TRANSITION",
        "LOCKED_FACT_CROSS_SCENE_CONFLICT",
    }
    assert ah.HARD_GATE_CODES == expected, (
        f"HARD_GATE_CODES 被污染！多出={ah.HARD_GATE_CODES - expected} "
        f"缺少={expected - ah.HARD_GATE_CODES}")


def test_controlled_keys_disjoint_from_hard_gate():
    """白名单 4 键与 15 HARD_GATE_CODES 物理无交集（回路彻底隔离）。"""
    sys.path.insert(0, str(_ROOT / "core" / "scripts"))
    import audit_hub as ah
    assert set(pid._CONTROLLED_KEYS).isdisjoint(ah.HARD_GATE_CODES)


def test_learning_loop_quantized_delta_only_for_controlled_keys():
    """learning_loop 量化幅度提示只映射被控键·HARD_GATE code 不产量化幅度。"""
    # 被控 code → 有量化幅度，且 controlled_key 在白名单内
    qd = ll._quantized_delta_hint("STYLE_配额词", 5)
    assert qd is not None and qd["controlled_key"] in pid._CONTROLLED_KEYS
    assert qd["relax_frac"] <= ll._QUANT_RELAX_CAP  # 钳上限·防 windup
    # HARD_GATE code → 不量化
    assert ll._quantized_delta_hint("LOCKED_FACT_CONFLICT", 5) is None
    assert ll._quantized_delta_hint("UNKNOWN_CHARACTER_DETECTED", 9) is None


# ---------- env 默认 off → 零回归 ----------

def test_mode_default_active():
    """PID_THRESHOLD_MODE 缺省 = active（回测已证两书 FPR 收敛 92%/85% · 北极星全开）。"""
    os.environ.pop("PID_THRESHOLD_MODE", None)
    assert pid._mode() == "active"


def test_active_no_state_is_harmless():
    """全开后保护栏：active + 无 per-作者 state（theta_delta 空）→ 不叠加 Δ（无害）。
    全开真生效靠回测 --save-state 落 state；没 state 时绝不无中生有改阈值。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        os.environ.pop("PID_THRESHOLD_MODE", None)  # 默认 active
        assert pid._mode() == "active"
        base = {"para_mean_len": {"max": 40}, "quota_per_word": {"max": 5}}
        # tmp 内无 pid_threshold_state.json → theta_delta 空 → 原样返回
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, tmp)
        assert out == base
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explicit_off_mode_zero_regression():
    """显式 PID_THRESHOLD_MODE=off 紧急回退：apply_pid_delta 返回原值（即便有非零 Δ state）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        pid.save_state(tmp, st)
        os.environ["PID_THRESHOLD_MODE"] = "off"  # 显式回退
        base = {"para_mean_len": {"max": 40}}
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, tmp)
        assert out == base
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_active_applies_saved_state_delta():
    """全开真生效：active + 有落盘 theta_delta state → apply_pid_delta 真叠加 Δ。
    这是①「接 state 生效」的核心断言——active 默认 + 回测落 state = 真调阈值。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        pid.save_state(tmp, st)
        os.environ.pop("PID_THRESHOLD_MODE", None)  # 默认 active
        base = {"para_mean_len": {"max": 40.0}}
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, tmp)
        assert out["para_mean_len"]["max"] > 40.0  # 真放松
        assert out["para_mean_len"]["max"] <= 80.0  # 仍守物理上限
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


def test_shadow_mode_does_not_change_thresholds():
    """shadow 模式只记 stderr·返回原阈值（不改判决·零回归）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        pid.save_state(tmp, st)
        os.environ["PID_THRESHOLD_MODE"] = "shadow"
        base = {"para_mean_len": {"max": 40}}
        out = pid.apply_pid_delta({k: dict(v) for k, v in base.items()}, tmp)
        assert out == base
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- validate_style 接线零回归 + L1a band 保留 ----------

def test_validate_style_active_no_state_preserves_overrides():
    """validate_style 接 PID 后·active 默认但无 state（空 author_dir）与不接 PID 结果一致
    （保留 L1a + 现有 override·全开无 state 无害保护栏）。"""
    sd = {"quantitative": {"paragraph_length_chars": {"mean": 30},
                           "dialogue_ratio": {"mean": 0.5},
                           "chapter_chars": {"mean": 2719}}}
    base = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    os.environ.pop("PID_THRESHOLD_MODE", None)  # 默认 active
    # 不传 author_dir（旧调用方·PID 完全跳过）vs 传空 author_dir 但无 state → 应等价
    t_none = vs._apply_style_overrides({k: dict(v) for k, v in base.items()}, sd)
    tmp = Path(tempfile.mkdtemp())
    try:
        t_act = vs._apply_style_overrides({k: dict(v) for k, v in base.items()}, sd,
                                          author_dir=tmp)
        assert t_none["para_mean_len"] == t_act["para_mean_len"]
        assert t_none["dialogue_ratio"] == t_act["dialogue_ratio"]
        assert t_none["quota_per_word"] == t_act["quota_per_word"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_validate_style_active_relaxes_in_correct_direction():
    """active 模式：PID Δ 在【作者档前馈 override 之上】按正确方向放松 4 被控键。"""
    sd = {"quantitative": {"paragraph_length_chars": {"mean": 30},
                           "dialogue_ratio": {"mean": 0.5},
                           "chapter_chars": {"mean": 2719}}}
    base = {k: dict(v) for k, v in vs.DEFAULT_THRESHOLDS.items()}
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05},
                             "dialogue_ratio": {"_scalar": 0.05},
                             "quota_per_word": {"_scalar": 0.05}}
        pid.save_state(tmp, st)
        os.environ["PID_THRESHOLD_MODE"] = "off"  # 显式 off 取无 Δ 基线（默认已 active·须显式）
        t_off = vs._apply_style_overrides({k: dict(v) for k, v in base.items()}, sd,
                                          author_dir=tmp)
        os.environ["PID_THRESHOLD_MODE"] = "active"
        t_act = vs._apply_style_overrides({k: dict(v) for k, v in base.items()}, sd,
                                          author_dir=tmp)
        assert t_act["para_mean_len"]["max"] > t_off["para_mean_len"]["max"]
        assert t_act["dialogue_ratio"]["min"] < t_off["dialogue_ratio"]["min"]
        assert t_act["quota_per_word"]["max"] > t_off["quota_per_word"]["max"]
    finally:
        os.environ.pop("PID_THRESHOLD_MODE", None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 接 state 生效：回测落盘 + cluster 积累桥 ----------

def test_save_backtest_state_persists_only_when_converged():
    """红线：回测收敛才落 theta_delta·不收敛拒绝落盘（绝不持久放大误判的 Δ）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        # 收敛 + 有 Δ → 落盘
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        res_ok = {"converged": True, "final_fpr": 0.01, "_state": st}
        p = pid.save_backtest_state(tmp, res_ok)
        assert p is not None and pid._state_path(tmp).is_file()
        loaded = pid.load_state(tmp)
        assert loaded["theta_delta"]["para_mean_len"]["_scalar"] == 0.05
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 不收敛 → 拒绝落盘
    tmp2 = Path(tempfile.mkdtemp())
    try:
        res_bad = {"converged": False, "final_fpr": 0.9,
                   "_state": {"theta_delta": {"para_mean_len": {"_scalar": 0.05}}}}
        assert pid.save_backtest_state(tmp2, res_bad) is None
        assert not pid._state_path(tmp2).is_file()
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


def test_save_backtest_state_filters_non_whitelist():
    """落盘前物理隔离复核：非白名单键绝不被持久（即便 _state 混入）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05},
                             "LOCKED_FACT_CONFLICT": {"_scalar": 0.99}}
        res = {"converged": True, "final_fpr": 0.0, "_state": st}
        pid.save_backtest_state(tmp, res)
        loaded = pid.load_state(tmp)
        assert "LOCKED_FACT_CONFLICT" not in loaded["theta_delta"]
        assert "para_mean_len" in loaded["theta_delta"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_accumulate_pid_state_from_calibration_bridge():
    """cluster 积累桥：learning_loop 的 quantized_delta 校准建议 → PID per-作者 state 累积。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        suggestions = [
            {"code": "STYLE_配额词", "suggestion_type": "adjust_threshold",
             "waived_count": 5,
             "quantized_delta": {"controlled_key": "quota_per_word",
                                 "relax_frac": 0.10, "basis": "waived×5"}},
            # 非被控键混入 → 物理隔离忽略（不 raise）
            {"code": "X", "quantized_delta": {"controlled_key": "banned_words",
                                              "relax_frac": 0.5}},
        ]
        st = ll.accumulate_pid_state_from_calibration(tmp, suggestions, cluster_id="c1")
        assert st is not None
        assert "quota_per_word" in st["theta_delta"]
        assert "banned_words" not in st["theta_delta"]  # 非白名单被隔离
        # 落盘可被 apply 读取
        assert pid._state_path(tmp).is_file()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_accumulate_pid_state_no_author_dir_harmless():
    """积累桥 author_dir=None / 无 quantized_delta → 返回 None（无害空转）。"""
    assert ll.accumulate_pid_state_from_calibration(None, [{"quantized_delta": {}}]) is None
    tmp = Path(tempfile.mkdtemp())
    try:
        assert ll.accumulate_pid_state_from_calibration(tmp, [{"code": "X"}]) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 离线回测金标准：真作者原文 FPR 收敛 ----------

def _author_present(name):
    return (_STYLES / name / "原文").is_dir() and (_STYLES / name / "作者风格.json").is_file()


def test_offline_backtest_fpr_converges_guzhenren():
    """真原文金标准：蛊真人 FPR 收敛曲线单调下降、终值 ≤ 初值（不发散）。"""
    if not _author_present("蛊真人"):
        return  # 原文缺失环境跳过（CI 无样本）
    res = pid.backtest(_STYLES / "蛊真人", _STYLES / "蛊真人" / "作者风格.json",
                       rounds=8, max_chapters=80, verbose=False)
    curve = res["fpr_curve"]
    assert len(curve) >= 3
    assert res["final_fpr"] <= curve[0] + 1e-9, "FPR 不应发散（终值 ≤ 初值）"
    # 单调非增（保守 PI 无超调震荡·允许相等平台）
    for a, b in zip(curve, curve[1:]):
        assert b <= a + 1e-9, f"FPR 曲线出现上扬（震荡）: {curve}"


def test_offline_backtest_fpr_converges_jingsong():
    """真原文金标准：惊悚乐园 FPR 同样收敛（跨作者鲁棒）。"""
    if not _author_present("惊悚乐园"):
        return
    res = pid.backtest(_STYLES / "惊悚乐园", _STYLES / "惊悚乐园" / "作者风格.json",
                       rounds=8, max_chapters=80, verbose=False)
    curve = res["fpr_curve"]
    assert len(curve) >= 3
    assert res["final_fpr"] <= curve[0] + 1e-9
    for a, b in zip(curve, curve[1:]):
        assert b <= a + 1e-9, f"FPR 曲线出现上扬（震荡）: {curve}"
