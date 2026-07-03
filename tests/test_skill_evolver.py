"""skill_evolver.py 确定性回归测试（零 LLM / 零联网）。

已有间接覆盖（不重复）：
  · tests/test_transfer_scope.py —— classify_transfer_scope / _transfer_scope_filter_active /
    promote（universal 跨 pool / local 锁本地 / locked 不重复评估 / filter=0 回退）全覆盖。
  · tests/test_experience_atomic_write.py —— save_json/load_json 往返 + atomic_json 接线。
  · tests/test_plan_script_contract.py —— 仅 CLI 字符串路径抽取（非行为）。
  · tests/test_evolution_orchestrator.py —— 明确「不测 skill_evolver」（subprocess 调用）。

本组聚焦尚未覆盖的核心确定性逻辑：
  · jaccard —— 字符集合相似度（边界：空串 / 全同 / 无交集）。
  · upgrade_to_versioned —— flat → versioned 升级 + 幂等（已升级原样返回）。
  · evolve —— Jaccard>0.6 合并 + version+1 + confidence 累进 + evolution_log 落账 + 50 条截断。
  · retire（chapter 语义）—— current_ch - last > threshold 标 retired；已 retired 不重标。
  · retire_by_cluster —— 显式 cluster 来源字段 → 序号差 > 阈值 retire；反查不到 → 保守不淘汰。
  · _cluster_to_end_ch —— 无 DB 时回退 cluster 序号 / 纯数字 key。
  · dashboard —— active/retired/promoted/versioned 计数 + by_version 聚合。
  · main CLI —— evolve/dashboard 退出码恒 0、stdout 为合法 JSON（subprocess 真 argparse）。

全程真 import 真调用被测函数，断言真实行为，绝不 mock 被测逻辑。
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
import skill_evolver as mod  # noqa: E402

_TARGET = _SCRIPTS / "skill_evolver.py"


# ── 工具：临时项目 + 写作经验.json ────────────────────────────────────────────

def _mk_project(payload: dict) -> tuple:
    """造一个临时项目目录 + _数据库/写作经验.json。返回 (tmp_dir_obj, project_root)。"""
    td = tempfile.mkdtemp()
    root = Path(td) / "测试书"
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    (root / "_数据库" / "写作经验.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return td, root


def _read_exp(root: Path) -> dict:
    return json.loads((root / "_数据库" / "写作经验.json").read_text(encoding="utf-8"))


# ── jaccard ──────────────────────────────────────────────────────────────────

def test_jaccard_boundaries():
    """字符集合 Jaccard：空串→0、全同→1、无交集→0、部分交集精确值。"""
    assert mod.jaccard("", "abc") == 0.0
    assert mod.jaccard("abc", "") == 0.0
    assert mod.jaccard("", "") == 0.0
    assert mod.jaccard("abc", "abc") == 1.0
    # 无交集
    assert mod.jaccard("abc", "xyz") == 0.0
    # 部分交集：set("abc")&set("bcd")={b,c}=2，union={a,b,c,d}=4 → 0.5
    assert mod.jaccard("abc", "bcd") == 0.5
    # 字符集合语义：重复字符不增大集合（"aaa" 与 "a" 全同）
    assert mod.jaccard("aaa", "a") == 1.0


# ── upgrade_to_versioned ─────────────────────────────────────────────────────

def test_upgrade_to_versioned_adds_fields():
    """flat pattern → versioned：补 version=1 / evolution_history(1 条 initial) / status=active。"""
    p = {"id": "x", "recorded_at_ch": 7, "confidence": 0.5}
    up = mod.upgrade_to_versioned(p, current_ch=20)
    assert up["version"] == 1
    assert isinstance(up["evolution_history"], list) and len(up["evolution_history"]) == 1
    assert up["evolution_history"][0]["action"] == "initial_record"
    assert up["status"] == "active"
    # last_validated_at_ch 取 recorded_at_ch（有则用之，不取 current_ch）
    assert up["last_validated_at_ch"] == 7
    assert up["usage_count"] == 0


def test_upgrade_to_versioned_fallback_current_ch():
    """无 recorded_at_ch → last_validated_at_ch 回退 current_ch。"""
    up = mod.upgrade_to_versioned({"id": "y"}, current_ch=42)
    assert up["last_validated_at_ch"] == 42
    assert up["confidence"] == 0.5  # 默认


def test_upgrade_to_versioned_idempotent():
    """已含 version 的 pattern 原样返回（同一对象，不重复加 history）。"""
    already = {"id": "z", "version": 3, "evolution_history": [{"version": 1}, {"version": 2}, {"version": 3}]}
    out = mod.upgrade_to_versioned(already, current_ch=99)
    assert out is already
    assert out["version"] == 3
    assert len(out["evolution_history"]) == 3


# ── evolve ───────────────────────────────────────────────────────────────────

def test_evolve_merges_similar_and_bumps_version():
    """两条几乎同文（Jaccard>0.6）→ 合并成 1 条、version+1、confidence=+0.1、usage 累积。"""
    payload = {
        "success_patterns": [
            {"id": "a", "name": "短句提速节奏控制", "description": "短句连发提升紧张节奏控制",
             "version": 1, "confidence": 0.7, "usage_count": 3},
            {"id": "b", "name": "短句提速节奏控制", "description": "短句连发提升紧张节奏控制感",
             "version": 1, "confidence": 0.6, "usage_count": 2},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=15)
        assert len(r["merged"]) == 1, r
        assert r["merged"][0]["count"] == 2
        assert r["merged"][0]["new_version"] == 2  # max(1,1)+1
        exp = _read_exp(root)
        # 合并后 success_patterns 只剩 1 条
        assert len(exp["success_patterns"]) == 1
        merged = exp["success_patterns"][0]
        assert merged["version"] == 2
        # confidence = min(1.0, max(0.7,0.6)+0.1) = 0.8
        assert abs(merged["confidence"] - 0.8) < 1e-9
        # usage 累积 3+2=5
        assert merged["usage_count"] == 5
        assert merged["last_validated_at_ch"] == 15
        # evolution_history 追加一条 merged action
        assert merged["evolution_history"][-1]["action"] == "merged"
        assert merged["evolution_history"][-1]["merged_count"] == 2
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_evolve_keeps_distinct_patterns_separate():
    """两条文本差异大（Jaccard<=0.6）→ 不合并，仍各自升 versioned。"""
    payload = {
        "success_patterns": [
            {"id": "a", "name": "钩子", "description": "章末悬念钩子吊住读者"},
            {"id": "b", "name": "反派", "description": "塑造立体反派动机充分"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == []
        exp = _read_exp(root)
        assert len(exp["success_patterns"]) == 2
        # 都被升到 versioned
        assert all(p.get("version") == 1 for p in exp["success_patterns"])
        # upgraded_count 计了这两条初次升级
        assert r["upgraded_count"] == 2
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_evolve_logs_and_caps_at_50():
    """evolve 追加 _evolution_log；超过 50 条仅保留最近 50。"""
    payload = {
        "success_patterns": [],
        "failure_patterns": [],
        "_evolution_log": [{"ts": "old", "action": "evolve", "n": i} for i in range(55)],
    }
    td, root = _mk_project(payload)
    try:
        mod.evolve(root, current_ch=5)
        exp = _read_exp(root)
        log = exp["_evolution_log"]
        assert len(log) == 50, len(log)
        # 最新一条是本次 evolve（带 current_ch=5）
        assert log[-1]["action"] == "evolve"
        assert log[-1]["current_ch"] == 5
        # 旧的头部已被截掉（保留最近 50 = 旧 [6..54] 共 49 条 + 新 1 条）
        assert log[0]["n"] == 6
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── retire（chapter 语义）────────────────────────────────────────────────────

def test_retire_marks_long_unused():
    """current_ch - last_validated_at_ch > threshold → status=retired；近期的不动。"""
    payload = {
        "success_patterns": [
            {"id": "stale", "last_validated_at_ch": 5},   # 50-5=45 > 30 → retire
            {"id": "fresh", "last_validated_at_ch": 40},  # 50-40=10 <= 30 → keep
        ],
        "failure_patterns": [
            {"id": "stale_f", "last_validated_at_ch": 1},  # retire
        ],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.retire(root, current_ch=50, threshold_ch=30)
        assert r["retired_count"] == 2
        assert set(r["retired_ids"]) == {"stale", "stale_f"}
        exp = _read_exp(root)
        by_id = {p["id"]: p for p in exp["success_patterns"] + exp["failure_patterns"]}
        assert by_id["stale"]["status"] == "retired"
        assert by_id["stale"]["retired_at_ch"] == 50
        assert by_id["fresh"].get("status") != "retired"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_retire_skips_already_retired():
    """已 retired 的 pattern 不再被计入（不重标）。"""
    payload = {
        "success_patterns": [
            {"id": "done", "last_validated_at_ch": 1, "status": "retired"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.retire(root, current_ch=100, threshold_ch=30)
        assert r["retired_count"] == 0
        assert r["retired_ids"] == []
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── retire_by_cluster（cluster 序号阈值）─────────────────────────────────────

def test_retire_by_cluster_explicit_source_field():
    """pattern 自带 last_validated_at_cluster → 当前序号 - last > 阈值 → retire。"""
    payload = {
        "success_patterns": [
            # cur=10，last_validated_at_cluster=cluster_001 → 10-1=9 > 3 → retire
            {"id": "old", "last_validated_at_cluster": "cluster_001"},
            # last=cluster_008 → 10-8=2 <= 3 → keep
            {"id": "recent", "last_validated_at_cluster": "cluster_008"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.retire_by_cluster(root, "cluster_010", threshold_clusters=3)
        assert r["mode"] == "cluster"
        assert r["current_cluster"] == 10
        assert r["retired_count"] == 1
        assert r["retired_ids"] == ["old"]
        exp = _read_exp(root)
        by_id = {p["id"]: p for p in exp["success_patterns"]}
        assert by_id["old"]["status"] == "retired"
        assert by_id["old"]["retired_at_cluster"] == "cluster_010"
        assert by_id["recent"].get("status") != "retired"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_retire_by_cluster_conservative_when_unresolvable():
    """无 cluster 来源字段 + 无 DB 反查（last_validated_at_ch 反查不到 cluster）→ 保守不淘汰。"""
    payload = {
        "success_patterns": [
            # 既无 *_cluster 字段，又 last_validated_at_ch 在空项目里反查不到 cluster
            {"id": "ambig", "last_validated_at_ch": 3},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.retire_by_cluster(root, "cluster_099", threshold_clusters=3)
        assert r["retired_count"] == 0, "反查不到来源应保守不淘汰（不误杀）"
        assert r["retired_ids"] == []
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── _cluster_to_end_ch ───────────────────────────────────────────────────────

def test_cluster_to_end_ch_falls_back_to_seq_num():
    """无 blueprint/事件簇 → cluster_id_to_range 取不到 → 回退 cluster 序号当锚点。"""
    td, root = _mk_project({"success_patterns": [], "failure_patterns": []})
    try:
        # cluster_lookup 在场时退化用 cluster_num："cluster_007" → 7
        assert mod._cluster_to_end_ch(root, "cluster_007") == 7
        # 纯数字字符串 key
        assert mod._cluster_to_end_ch(root, "012") == 12
        # 完全无数字 → 正则兜底 0
        assert mod._cluster_to_end_ch(root, "nope") == 0
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── dashboard ────────────────────────────────────────────────────────────────

def test_dashboard_counts_status_and_versions():
    """dashboard 聚合 active/retired/promoted/versioned + by_version 分布。"""
    payload = {
        "success_patterns": [
            {"id": "a", "version": 2, "status": "active", "promoted_to_universal": True},
            {"id": "b", "version": 1, "status": "retired"},
            {"id": "c", "version": 2},  # 无 status → 计 active
        ],
        "failure_patterns": [
            {"id": "d", "version": 1, "status": "active"},
        ],
        "_evolution_log": [{"x": 1}, {"x": 2}],
    }
    td, root = _mk_project(payload)
    try:
        s = mod.dashboard(root)
        assert s["active"] == 3   # a, c, d
        assert s["retired"] == 1  # b
        assert s["promoted"] == 1  # a
        assert s["by_version"][2] == 2  # a, c
        assert s["by_version"][1] == 2  # b, d
        assert s["by_category"]["success_patterns"]["total"] == 3
        assert s["by_category"]["success_patterns"]["versioned"] == 3
        assert s["by_category"]["failure_patterns"]["active"] == 1
        assert s["evolution_log_count"] == 2
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── main CLI（subprocess 真 argparse · 退出码恒 0 · stdout 合法 JSON）─────────

def test_cli_evolve_exit_zero_and_json():
    """python skill_evolver.py <proj> evolve --cluster 001 → exit 0 + stdout 合法 JSON。

    cluster-save-state 经 `|| true`/adaptive_runner 调用，evolve 必须以 0 退出且产合法 JSON，
    否则 skill 演化静默失败。"""
    td, root = _mk_project({
        "success_patterns": [{"id": "p", "name": "n", "description": "d"}],
        "failure_patterns": [],
    })
    try:
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(root), "evolve", "--cluster", "001"],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
        out = json.loads(proc.stdout)  # 合法 JSON
        assert "merged" in out and "upgraded_count" in out
        # 真把经验库升了版
        exp = _read_exp(root)
        assert exp["success_patterns"][0].get("version") == 1
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


# ── embedding 接线（2026-07-02 · 真后端门控 + token jaccard fallback）────────────────

def test_has_real_embedding_backend_false_by_default():
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert mod._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_semantic_merge_catches_zero_token_overlap_synonym(monkeypatch):
    """真后端：两条经验 token 完全不重叠的同义改写（"对话要简短" vs "台词不宜过长"）
    仍应被 embedding 余弦判定该合并——字面 token jaccard 会判不相关（跳过合并）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    def _mock_embed(text):
        if "对话要简短" in text or "台词不宜过长" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]
    monkeypatch.setattr(embedding_store, "compute_embedding", _mock_embed)

    payload = {
        "success_patterns": [
            {"id": "syn_a", "trigger": "对话要简短", "technique": "短促应答",
             "why_works": "节奏快", "confidence": 0.7, "usage_count": 3},
            {"id": "syn_b", "trigger": "台词不宜过长", "technique": "避免长篇独白",
             "why_works": "保持张力", "confidence": 0.6, "usage_count": 2},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        # 前置断言：确认字面 token jaccard 确实判不相似（零 token 重叠）
        a_tok = mod._tokenize(mod._similarity_blob(payload["success_patterns"][0]))
        b_tok = mod._tokenize(mod._similarity_blob(payload["success_patterns"][1]))
        assert mod._token_jaccard(a_tok, b_tok) <= 0.6, "前置条件应字面不相似"

        r = mod.evolve(root, current_ch=10)
        assert len(r["merged"]) == 1, f"语义同义应合并：{r}"
        exp = _read_exp(root)
        assert len(exp["success_patterns"]) == 1
        assert exp["success_patterns"][0]["usage_count"] == 5  # 3+2 累积
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_semantic_merge_off_by_default_matches_old_behavior():
    """🔴 零回归锁：无真后端（默认）→ evolve 合并判定仍是纯字面 token jaccard（不合并两条
    token 零重叠的经验）。"""
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    payload = {
        "success_patterns": [
            {"id": "a", "trigger": "对话要简短", "technique": "短促应答", "why_works": "节奏快"},
            {"id": "b", "trigger": "台词不宜过长", "technique": "避免长篇独白", "why_works": "保持张力"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == [], "默认（无真后端）应保持字面 token jaccard 判不合并"
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_semantic_merge_falls_back_on_embedding_error(monkeypatch):
    """真后端配置但编码异常 → 回退 token jaccard（不崩·不误合并）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store

    def _boom(text):
        raise RuntimeError("模拟真后端编码失败")
    monkeypatch.setattr(embedding_store, "compute_embedding", _boom)

    payload = {
        "success_patterns": [
            {"id": "a", "trigger": "对话要简短", "technique": "短促应答", "why_works": "节奏快"},
            {"id": "b", "trigger": "台词不宜过长", "technique": "避免长篇独白", "why_works": "保持张力"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == [], "编码失败应回退字面 jaccard（本例字面也不相似·不误合并）"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_semantic_merge_empty_blob_still_never_merges(monkeypatch):
    """🔴 C12 回归锁延伸：真后端下空 blob 经验仍绝不误合并（token 空集合闸先于语义判断，
    即便 mock embedding 让"万物相似"也拦得住）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store
    monkeypatch.setattr(embedding_store, "compute_embedding", lambda t: [1.0, 0.0])

    payload = {
        "success_patterns": [
            {"id": "recur_empty1"},
            {"id": "recur_empty2"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == [], "空 blob 经验即便语义后端万物相似也不该合并"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_cli_dashboard_chapter_mode_exit_zero():
    """chapter 兼容模式 dashboard（无 --cluster，用 --ch）→ exit 0 + 合法 JSON stats。"""
    td, root = _mk_project({
        "success_patterns": [{"id": "a", "version": 1, "status": "active"}],
        "failure_patterns": [],
    })
    try:
        proc = subprocess.run(
            [sys.executable, str(_TARGET), str(root), "dashboard", "--ch", "5"],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
        stats = json.loads(proc.stdout)
        assert stats["active"] == 1
        assert "by_category" in stats
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
