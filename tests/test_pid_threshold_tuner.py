"""pid_threshold_tuner 专属聚焦回归测试（2026-06-17 · 北极星⑤顾问非法官）。

【与 tests/test_l2_1_pid.py 的分工】test_l2_1_pid 已锁：PI 公式 / 抗 windup /
compute_error 双目标 / 死区缩放 / apply_pid_delta 三模式 / save_backtest_state
收敛红线 / 真原文 FPR 收敛曲线。本件**聚焦尚未被覆盖**的确定性底层逻辑，不重复：

  · load_state 损坏/缺失文件回退 + setdefault 补字段（容错口径）；
  · save_state 原子写（.tmp → os.replace）+ 父目录自建 + UTF-8 中文落盘；
  · _clamp 纯函数边界；
  · _mode 非法值回退 active / off·shadow 接受 / 大小写空白归一；
  · _direction_unit 把标量 Δ 映射到每键「放松方向」（符号 + sub_key 选择 +
    long_para_per_chapter 的 max_ratio 优先于 max）；
  · _apply_state_delta（回测内联 Δ 叠加孪生体）含 _scalar 缺失/0/越界 clamp 分支；
  · update_controller 的 n_samples 回写 state + e_prev 首步默认 0.0 + 多步 scalar 累积。

只测确定性纯函数 + 临时文件 IO（无 LLM / agent / 网络 / 重依赖）。
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import pid_threshold_tuner as pid  # noqa: E402


def _fresh_state():
    return {"version": 1, "e_prev": {}, "theta_delta": {}, "n_samples": 0,
            "last_update_cluster": None, "fpr_history": []}


# ---------- load_state 容错 + setdefault 补字段 ----------

def test_load_state_missing_file_returns_default_shape():
    """缺文件 → 返回完整默认骨架（不抛·下游可直接用）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = pid.load_state(tmp)
        assert st["version"] == 1
        assert st["e_prev"] == {} and st["theta_delta"] == {}
        assert st["n_samples"] == 0
        assert st["last_update_cluster"] is None
        assert st["fpr_history"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_state_corrupt_json_falls_back():
    """损坏 JSON（非法语法）→ 回退默认骨架（容错·不崩流水线）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        pid._state_path(tmp).write_text("{ this is not json", encoding="utf-8")
        st = pid.load_state(tmp)
        # 回退到默认（绝不抛 json.JSONDecodeError）
        assert st["theta_delta"] == {} and st["e_prev"] == {}
        assert st["n_samples"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_state_non_dict_json_falls_back():
    """合法 JSON 但顶层非 dict（如 list）→ 回退默认骨架。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        pid._state_path(tmp).write_text("[1, 2, 3]", encoding="utf-8")
        st = pid.load_state(tmp)
        assert isinstance(st, dict)
        assert st["theta_delta"] == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_state_partial_dict_setdefaults_missing_keys():
    """已有 dict 但缺 e_prev/theta_delta/n_samples → setdefault 补齐（不覆盖已有）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        # 故意只写一个真实字段 + 一个已存在的 theta_delta，验证不覆盖
        partial = {"version": 7, "theta_delta": {"quota_per_word": {"_scalar": 0.3}}}
        pid._state_path(tmp).write_text(json.dumps(partial), encoding="utf-8")
        st = pid.load_state(tmp)
        # 缺的被补
        assert st["e_prev"] == {}
        assert st["n_samples"] == 0
        # 已有的不被覆盖
        assert st["version"] == 7
        assert st["theta_delta"]["quota_per_word"]["_scalar"] == 0.3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- save_state 原子写 + 自建目录 + UTF-8 ----------

def test_save_state_creates_parent_dir_and_roundtrips():
    """父目录不存在 → 自建·写入后 load 可还原（含中文 last_update_cluster）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        nested = tmp / "styles" / "蛊真人"  # 父目录尚不存在
        st = _fresh_state()
        st["theta_delta"] = {"para_mean_len": {"_scalar": 0.05}}
        st["last_update_cluster"] = "故事块_007"
        pid.save_state(nested, st)
        assert pid._state_path(nested).is_file()
        loaded = pid.load_state(nested)
        assert loaded["theta_delta"]["para_mean_len"]["_scalar"] == 0.05
        assert loaded["last_update_cluster"] == "故事块_007"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_state_leaves_no_tmp_file():
    """原子写：os.replace 后不残留 .json.tmp 临时文件。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        pid.save_state(tmp, _fresh_state())
        tmp_artifact = pid._state_path(tmp).with_suffix(".json.tmp")
        assert not tmp_artifact.exists(), "原子写后不应残留 .tmp"
        assert pid._state_path(tmp).is_file()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_save_state_overwrites_existing():
    """重复 save 覆盖旧内容（os.replace 语义·非追加）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st1 = _fresh_state()
        st1["n_samples"] = 11
        pid.save_state(tmp, st1)
        st2 = _fresh_state()
        st2["n_samples"] = 22
        pid.save_state(tmp, st2)
        assert pid.load_state(tmp)["n_samples"] == 22
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- _clamp 纯函数边界 ----------

def test_clamp_within_below_above():
    assert pid._clamp(5.0, 0.0, 10.0) == 5.0   # 区间内原样
    assert pid._clamp(-3.0, 0.0, 10.0) == 0.0  # 低于下界 → lo
    assert pid._clamp(99.0, 0.0, 10.0) == 10.0  # 超上界 → hi
    # 命中边界值不被改
    assert pid._clamp(0.0, 0.0, 10.0) == 0.0
    assert pid._clamp(10.0, 0.0, 10.0) == 10.0


# ---------- _mode 环境变量归一 ----------

def test_mode_invalid_value_falls_back_active():
    """非 {off,shadow,active} 的值 → 回退 active（容错默认全开）。"""
    old = os.environ.get("PID_THRESHOLD_MODE")
    try:
        os.environ["PID_THRESHOLD_MODE"] = "garbage_xyz"
        assert pid._mode() == "active"
    finally:
        if old is None:
            os.environ.pop("PID_THRESHOLD_MODE", None)
        else:
            os.environ["PID_THRESHOLD_MODE"] = old


def test_mode_accepts_off_and_shadow_with_case_and_whitespace():
    """off/shadow 大小写 + 前后空白归一（strip().lower()）。"""
    old = os.environ.get("PID_THRESHOLD_MODE")
    try:
        os.environ["PID_THRESHOLD_MODE"] = "  OFF  "
        assert pid._mode() == "off"
        os.environ["PID_THRESHOLD_MODE"] = "Shadow"
        assert pid._mode() == "shadow"
        os.environ["PID_THRESHOLD_MODE"] = "ACTIVE"
        assert pid._mode() == "active"
    finally:
        if old is None:
            os.environ.pop("PID_THRESHOLD_MODE", None)
        else:
            os.environ["PID_THRESHOLD_MODE"] = old


# ---------- _direction_unit 放松方向映射 ----------

def test_direction_unit_para_mean_len_relax_up():
    """para_mean_len 放松 = max 上调（符号 +1）。"""
    u = pid._direction_unit("para_mean_len", {"max": 40.0})
    assert u == {"max": (40.0, +1)}


def test_direction_unit_dialogue_ratio_both_bounds():
    """dialogue_ratio 放松 = min 下调(-1) + max 上调(+1)，两端都映射。"""
    u = pid._direction_unit("dialogue_ratio", {"min": 0.2, "max": 0.7})
    assert u["min"] == (0.2, -1)
    assert u["max"] == (0.7, +1)


def test_direction_unit_long_para_max_ratio_takes_precedence():
    """long_para_per_chapter：有 max_ratio 时优先用 max_ratio（不取 max）。"""
    both = pid._direction_unit("long_para_per_chapter",
                               {"max_ratio": 0.1, "max": 5})
    assert "max_ratio" in both and "max" not in both
    assert both["max_ratio"] == (0.1, +1)
    # 只有 max 时退回 max
    only_max = pid._direction_unit("long_para_per_chapter", {"max": 5})
    assert only_max == {"max": (5, +1)}


def test_direction_unit_quota_per_word_relax_up():
    u = pid._direction_unit("quota_per_word", {"max": 6.0})
    assert u == {"max": (6.0, +1)}


def test_direction_unit_empty_when_subkey_absent():
    """cfg 缺对应 sub_key → 空映射（不无中生有）。"""
    assert pid._direction_unit("para_mean_len", {}) == {}
    assert pid._direction_unit("dialogue_ratio", {}) == {}


# ---------- _apply_state_delta（回测内联孪生体） ----------

def test_apply_state_delta_relaxes_in_correct_direction():
    """正 scalar 放松：para_mean_len.max 上调，dialogue_ratio.min 下调。"""
    state = {"theta_delta": {"para_mean_len": {"_scalar": 0.1},
                             "dialogue_ratio": {"_scalar": 0.1}}}
    thr = {"para_mean_len": {"max": 40.0},
           "dialogue_ratio": {"min": 0.2, "max": 0.7}}
    out = pid._apply_state_delta(thr, state)
    # max 上调 = 40 + 0.1*40 = 44
    assert abs(out["para_mean_len"]["max"] - 44.0) < 1e-9
    # min 下调 = 0.2 - 0.1*0.2 = 0.18
    assert out["dialogue_ratio"]["min"] < 0.2
    # max 上调
    assert out["dialogue_ratio"]["max"] > 0.7


def test_apply_state_delta_clamps_to_phys_bounds():
    """巨量 scalar → 钳进 _PHYS_BOUNDS（quota 上限 12·绝不破物理栏）。"""
    state = {"theta_delta": {"quota_per_word": {"_scalar": 999.0}}}
    out = pid._apply_state_delta({"quota_per_word": {"max": 5.0}}, state)
    assert out["quota_per_word"]["max"] == 12.0  # _PHYS_BOUNDS quota max 上界


def test_apply_state_delta_skips_zero_scalar_and_missing():
    """_scalar==0 或键缺失 → 原样（不动·短路分支）。"""
    state = {"theta_delta": {"para_mean_len": {"_scalar": 0.0}}}
    thr = {"para_mean_len": {"max": 40.0}}
    out = pid._apply_state_delta({k: dict(v) for k, v in thr.items()}, state)
    assert out["para_mean_len"]["max"] == 40.0
    # 空 theta_delta → 原样
    out2 = pid._apply_state_delta({k: dict(v) for k, v in thr.items()}, {"theta_delta": {}})
    assert out2["para_mean_len"]["max"] == 40.0


def test_apply_state_delta_ignores_malformed_td():
    """theta_delta 项非 dict / 缺 _scalar → 跳过（容错·不崩）。"""
    state = {"theta_delta": {"para_mean_len": "not_a_dict",
                             "quota_per_word": {"no_scalar": 1}}}
    thr = {"para_mean_len": {"max": 40.0}, "quota_per_word": {"max": 5.0}}
    out = pid._apply_state_delta({k: dict(v) for k, v in thr.items()}, state)
    assert out["para_mean_len"]["max"] == 40.0
    assert out["quota_per_word"]["max"] == 5.0


# ---------- update_controller 细节：n_samples 回写 + 首步 e_prev 默认 + 多步累积 ----------

def test_update_controller_writes_n_samples_to_state():
    """显式传 n_samples → 回写 state['n_samples']（供后续死区缩放复用）。"""
    st = _fresh_state()
    pid.update_controller(st, {"para_mean_len": 0.5}, n_samples=42)
    assert st["n_samples"] == 42


def test_update_controller_first_step_uses_e_prev_zero():
    """首步 e_prev 缺省 0.0：Δ = Kp·(e−0) + Ki·e（无历史时比例项全量计入）。"""
    st = _fresh_state()
    e = 0.5
    pid.update_controller(st, {"para_mean_len": e}, n_samples=50,
                          kp=0.05, ki=0.02)
    expected = 0.05 * (e - 0.0) + 0.02 * e  # e_prev 默认 0
    # 未触发 _MAX_STEP_FRAC 限幅（0.035 < 0.10）
    got = st["theta_delta"]["para_mean_len"]["_scalar"]
    assert abs(got - expected) < 1e-9
    assert st["e_prev"]["para_mean_len"] == e


def test_update_controller_accumulates_scalar_across_steps():
    """多步（不同 cluster·非死区·非 windup）→ theta_delta 标量逐步累加（prev_d + delta）。"""
    st = _fresh_state()
    st["n_samples"] = 50
    pid.update_controller(st, {"quota_per_word": 0.3}, cluster_id="c1", n_samples=50)
    s1 = st["theta_delta"]["quota_per_word"]["_scalar"]
    pid.update_controller(st, {"quota_per_word": 0.6}, cluster_id="c2", n_samples=50)
    s2 = st["theta_delta"]["quota_per_word"]["_scalar"]
    # 第二步增量 = Kp*(0.6-0.3) + Ki*0.6 = 0.05*0.3 + 0.02*0.6 = 0.027
    assert abs((s2 - s1) - (0.05 * 0.3 + 0.02 * 0.6)) < 1e-9
    assert s2 > s1  # 持续放松累积


def test_update_controller_reject_non_whitelist_does_not_mutate():
    """硬拒非白名单键 raise 前不应已写入（白名单红线·结构性隔离）。"""
    st = _fresh_state()
    raised = False
    try:
        pid.update_controller(st, {"banned_words": 0.5}, n_samples=50)
    except ValueError:
        raised = True
    assert raised, "非白名单键必须 raise ValueError"
    assert "banned_words" not in st["theta_delta"]


# ---------- compute_error 默认参数 + 边界 ----------

def test_compute_error_defaults_and_rate_equals_tau():
    """缺省 tau=0.8/w1=1.0/w2=0.5；rate==tau 时 AI 项归零（e==w1·FPR）。"""
    e = pid.compute_error(fpr=0.2, ai_detect_rate=0.8)  # 全用默认
    assert abs(e - (1.0 * 0.2 - 0.5 * (0.8 - 0.8))) < 1e-9
    assert abs(e - 0.2) < 1e-9
