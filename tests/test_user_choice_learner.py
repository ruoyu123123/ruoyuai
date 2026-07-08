#!/usr/bin/env python3
"""test_user_choice_learner.py — 走向卡选择学习器回归测试。

覆盖两件事：
  1. 原有「逐维标量均值」逻辑（PREFERENCE_DIMS + inferred_behavior 累积）保持不变——
     显式锁定一个可预测场景的精确输出，并证明该输出不受新增 pairwise 门控状态影响。
  2. 新增「pairwise 偏好排序(BPR)」接线（RUOYU_PREF_RANKER=1 才生效）：
     - 门控关闭（默认）：用户偏好.json 不出现 pairwise_observations key（零回归）。
     - 门控开启：每次 choice 累积一条 pairwise_observations 记录，达到
       preference_ranker.COLD_START_MIN_OBSERVATIONS 后自动训练并落盘
       _数据库/.preference_ranker.json。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import user_choice_learner as ucl  # noqa: E402
import preference_ranker as pr  # noqa: E402

_CHOSEN = {
    "cluster_id": "cluster_002_candidate_1",
    "scope_summary": "A" * 100,
    "stakes_delta": "详细描述新增威胁",
    "is_volume_finale": True,
    "narrative_mode": "linear",
    "scene_storyboard": [{"a": 1}, {"b": 2}],
}
_REJECTED = [{
    "cluster_id": "cluster_002_candidate_2",
    "scope_summary": "B" * 10,
    "stakes_delta": "轻微",
    "is_volume_finale": False,
    "narrative_mode": "kishotenketsu_4act",
    "scene_storyboard": [{"c": 3}],
}]
_ALL_CANDIDATES = [_CHOSEN] + _REJECTED


def _new_project() -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
    return tmp


def _restore_env(flag, bak):
    if bak is None:
        os.environ.pop(flag, None)
    else:
        os.environ[flag] = bak


# ───────────────────── 1. 原逐维标量均值逻辑锁定（回归基线） ─────────────────────

def test_learn_from_choice_dim_mean_baseline_locked():
    """精确锁定一个可预测场景的输出——证明升级没有改动 PREFERENCE_DIMS 比较逻辑本身。"""
    root = _new_project()
    result = ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES, source_cluster="cluster_001")

    assert result["updated_dims"] == 5
    dims_seen = {s["dim"] for s in result["new_signals"]}
    assert dims_seen == {"stakes_delta", "is_volume_finale", "narrative_mode",
                          "scope_length", "scene_count"}

    pref = json.loads((root / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
    ib = pref["inferred_behavior"]
    assert ib["scope_length"]["observations"][0]["chosen_val"] == 100
    assert ib["scope_length"]["observations"][0]["rejected_avg"] == 10.0
    assert ib["scope_length"]["observations"][0]["direction"] == "higher"
    assert ib["scope_length"]["confidence"] == 0.1
    assert ib["scene_count"]["observations"][0]["chosen_val"] == 2
    assert ib["scene_count"]["observations"][0]["rejected_avg"] == 1.0
    assert ib["is_volume_finale"]["observations"][0]["direction"] == "higher"
    assert ib["narrative_mode"]["observations"][0]["rejected_vals"] == ["kishotenketsu_4act"]


def test_dim_mean_logic_identical_regardless_of_pref_ranker_gate():
    """核心零回归证据：同样输入，pairwise 门控 on/off 两种状态下，
    inferred_behavior 的产出必须完全相同——证明新逻辑是纯附加，没有改动旧路径。"""
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        outcomes = {}
        for gate in (None, "1"):
            _restore_env(pr.ENV_FLAG, gate)
            root = _new_project()
            r = ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES, source_cluster="cluster_001")
            pref = json.loads((root / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
            outcomes[gate] = (r["updated_dims"], r["new_signals"], pref["inferred_behavior"])
        assert outcomes[None] == outcomes["1"], "逐维标量均值逻辑不应受 pairwise 门控影响"
    finally:
        _restore_env(pr.ENV_FLAG, bak)


# ───────────────────── 2. 门控关闭：零回归（无 pairwise_observations key） ─────────────────────

def test_gate_off_no_pairwise_observations_key():
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ.pop(pr.ENV_FLAG, None)
        root = _new_project()
        result = ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES, source_cluster="cluster_001")
        assert "pairwise_observations_count" not in result
        assert "ranker_trained" not in result

        pref = json.loads((root / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        assert "pairwise_observations" not in pref
        assert not (root / "_数据库" / ".preference_ranker.json").exists()
    finally:
        _restore_env(pr.ENV_FLAG, bak)


def test_gate_off_with_env_explicitly_zero_is_also_off():
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ[pr.ENV_FLAG] = "0"
        root = _new_project()
        ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES, source_cluster="cluster_001")
        pref = json.loads((root / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        assert "pairwise_observations" not in pref
    finally:
        _restore_env(pr.ENV_FLAG, bak)


# ───────────────────── 3. 门控开启：累积 + 达阈值自动训练 ─────────────────────

def test_gate_on_accumulates_pairwise_observations():
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ[pr.ENV_FLAG] = "1"
        root = _new_project()
        for i in range(3):
            result = ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES,
                                            source_cluster=f"cluster_{i:03d}")
        assert result["pairwise_observations_count"] == 3
        assert result["ranker_trained"] is False  # 3 < COLD_START_MIN_OBSERVATIONS

        pref = json.loads((root / "_数据库" / "用户偏好.json").read_text(encoding="utf-8"))
        obs = pref["pairwise_observations"]
        assert len(obs) == 3
        rec = obs[0]
        assert set(rec.keys()) == {"chosen_features", "chosen_text", "rejected_features",
                                    "source_cluster", "ts"}
        assert isinstance(rec["chosen_features"], dict) and rec["chosen_features"]
        assert isinstance(rec["rejected_features"], list) and len(rec["rejected_features"]) == 1
        assert rec["source_cluster"] == "cluster_000"
        assert not (root / "_数据库" / ".preference_ranker.json").exists()
    finally:
        _restore_env(pr.ENV_FLAG, bak)


def test_gate_on_trains_at_cold_start_threshold():
    bak = os.environ.get(pr.ENV_FLAG)
    try:
        os.environ[pr.ENV_FLAG] = "1"
        root = _new_project()
        n = pr.COLD_START_MIN_OBSERVATIONS
        last_result = None
        for i in range(n):
            last_result = ucl.learn_from_choice(root, _CHOSEN, _ALL_CANDIDATES,
                                                source_cluster=f"cluster_{i:03d}")
            if i < n - 1:
                assert last_result["ranker_trained"] is False, f"第 {i+1} 次不应达到冷启动阈值"

        assert last_result["pairwise_observations_count"] == n
        assert last_result["ranker_trained"] is True
        weights_path = root / "_数据库" / ".preference_ranker.json"
        assert weights_path.exists()
        saved = json.loads(weights_path.read_text(encoding="utf-8"))
        assert saved["n_observations"] == n
        assert saved["_schema"] == pr.SCHEMA
    finally:
        _restore_env(pr.ENV_FLAG, bak)


# ───────────────────── 4. CLI smoke（子进程·创作入口默认门控语义） ─────────────────────

def test_cli_main_smoke_default_gate_on_via_creative_defaults():
    """2026-07-03 W3 集成：CLI main() 调 nn_runtime_defaults.enable_creative_nn_defaults()，
    未显式设置时 RUOYU_PREF_RANKER setdefault 为 1 → pairwise 观察默认捕获（否则数据永不积累）。"""
    root = _new_project()
    chosen_path = root / "choice.json"
    candidates_path = root / "candidates.json"
    chosen_path.write_text(json.dumps({"answer": _CHOSEN}, ensure_ascii=False), encoding="utf-8")
    candidates_path.write_text(json.dumps({"candidates": _ALL_CANDIDATES}, ensure_ascii=False),
                               encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop(pr.ENV_FLAG, None)   # 未显式设置 → 入口 setdefault 开
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "user_choice_learner.py"), str(root),
         "--chosen", str(chosen_path), "--candidates", str(candidates_path),
         "--source-cluster", "cluster_001"],
        capture_output=True, text=True, timeout=30, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    assert "学到 5 个偏好维度" in r.stdout
    assert "pairwise 观察数=1" in r.stdout  # 创作入口默认捕获


def test_cli_main_smoke_explicit_zero_stays_off():
    """setdefault 语义：显式 RUOYU_PREF_RANKER=0 → 入口默认不覆盖，pairwise 捕获保持关闭。"""
    root = _new_project()
    chosen_path = root / "choice.json"
    candidates_path = root / "candidates.json"
    chosen_path.write_text(json.dumps({"answer": _CHOSEN}, ensure_ascii=False), encoding="utf-8")
    candidates_path.write_text(json.dumps({"candidates": _ALL_CANDIDATES}, ensure_ascii=False),
                               encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", pr.ENV_FLAG: "0"}
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "user_choice_learner.py"), str(root),
         "--chosen", str(chosen_path), "--candidates", str(candidates_path),
         "--source-cluster", "cluster_001"],
        capture_output=True, text=True, timeout=30, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    assert "学到 5 个偏好维度" in r.stdout
    assert "pairwise" not in r.stdout  # 显式关 → 不捕获不打印


def test_cli_main_smoke_project_relative_paths():
    """🔴 2026-07-08 验证书 e2e 抓出：cluster-save-state.plan.json 的
    after_pause_scripts 用 project-relative 路径调用本脚本（与同一 after_pause_scripts
    里 cluster_choice_apply.py 的 --choice 同款约定），而非既有测试全用的绝对路径。
    此前 --chosen/--candidates 未做 project_root join，模板给的相对路径必
    FileNotFoundError（每本书 save-state 走到 step13 都会命中）。"""
    root = _new_project()
    (root / "_数据库" / ".wal").mkdir(parents=True, exist_ok=True)
    chosen_path = root / "_数据库" / ".wal" / "cluster_002_user_choice.json"
    candidates_path = root / "_数据库" / ".wal" / "cluster_002_brief_candidates.json"
    chosen_path.write_text(json.dumps({"answer": _CHOSEN}, ensure_ascii=False), encoding="utf-8")
    candidates_path.write_text(json.dumps({"candidates": _ALL_CANDIDATES}, ensure_ascii=False),
                               encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "user_choice_learner.py"), str(root),
         "--chosen", "_数据库/.wal/cluster_002_user_choice.json",
         "--candidates", "_数据库/.wal/cluster_002_brief_candidates.json",
         "--source-cluster", "cluster_001"],
        capture_output=True, text=True, timeout=30, encoding="utf-8", env=env,
        cwd=str(_ROOT))
    assert r.returncode == 0, r.stderr
    assert "学到" in r.stdout


def test_cli_empty_candidates_is_hard_error():
    root = _new_project()
    chosen_path = root / "choice.json"
    candidates_path = root / "candidates.json"
    chosen_path.write_text(json.dumps({"answer": _CHOSEN}, ensure_ascii=False), encoding="utf-8")
    candidates_path.write_text(json.dumps({"candidates": []}, ensure_ascii=False), encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [sys.executable, str(_SCRIPTS / "user_choice_learner.py"), str(root),
         "--chosen", str(chosen_path), "--candidates", str(candidates_path),
         "--source-cluster", "cluster_001"],
        capture_output=True, text=True, timeout=30, encoding="utf-8", env=env)
    assert r.returncode == 2
    assert "FATAL" in r.stderr


# ───────────────────── 零依赖 runner（与仓库其余 test_*_audit.py 同范式） ─────────────────────

def _run_all():
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"[OK] {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
