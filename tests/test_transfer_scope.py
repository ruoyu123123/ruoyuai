"""transfer_scope filter 回归测试（2026-05-31 · 防跨项目负迁移）。

skill_evolver.promote() 旧实现「无差别升级」—— 任何 usage≥5 & confidence≥0.8 的 pattern
都升进 universal_skill_pool 跨项目复用。但作者 A 的 idiolect 特异约束（口癖/签名词/特有
遣词/角色名）升上去会污染作者 B（negative transfer）。

本组测试钉死：
  · classify_transfer_scope 分类：通用工艺→universal / 作者 idiolect 特异→local
  · 显式 transfer_scope 字段优先（advisory · 不干涉模型/作者档判断）
  · promote 过滤：universal 才跨项目升 pool；local 锁本地不升、标记一次防重复评估
  · env 默认 active；SKILL_TRANSFER_SCOPE_FILTER=0 回退旧无差别升级（向后兼容）
不碰任何 LLM、不碰 agent —— 纯确定性逻辑层。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import skill_evolver  # noqa: E402


# ── classify_transfer_scope 单元 ────────────────────────────────────────────

def test_universal_craft_classified_universal():
    """通用工艺 category（节奏/结构/钩子等）→ universal。"""
    for cat in ("rhythm", "pacing", "structure", "hook", "conflict", "节奏", "结构", "钩子"):
        scope = skill_evolver.classify_transfer_scope({"category": cat, "trigger": "x", "technique": "y"})
        assert scope == "universal", f"{cat} 应判 universal，得 {scope}"


def test_idiolect_category_classified_local():
    """作者 idiolect 语义子类（口癖/声口/遣词等）→ local。"""
    for cat in ("idiolect", "voice", "catchphrase", "diction", "口癖", "遣词", "声口", "签名词"):
        scope = skill_evolver.classify_transfer_scope({"category": cat, "trigger": "x"})
        assert scope == "local", f"{cat} 应判 local，得 {scope}"


def test_idiolect_text_marker_classified_local():
    """category 中性，但文本里含 idiolect 关键词（口癖/签名词/角色名）→ local。"""
    p1 = {"category": "dialogue", "trigger": "主角说话", "technique": "用作者特有口癖『得嘞』收尾"}
    assert skill_evolver.classify_transfer_scope(p1) == "local"
    p2 = {"category": "success", "description": "复用签名词强化辨识度"}
    assert skill_evolver.classify_transfer_scope(p2) == "local"
    p3 = {"category": "success", "name": "catchphrase reinforcement", "why_works": "verbal tic"}
    assert skill_evolver.classify_transfer_scope(p3) == "local"


def test_explicit_transfer_scope_overrides_heuristic():
    """pattern 自带显式 transfer_scope 字段 → 以其为准（advisory · 作者档/模型权威优先）。"""
    # 文本像 idiolect，但显式标 universal → 采纳 universal
    p1 = {"category": "口癖", "transfer_scope": "universal"}
    assert skill_evolver.classify_transfer_scope(p1) == "universal"
    # 文本像通用工艺，但显式标 local → 采纳 local
    p2 = {"category": "rhythm", "transfer_scope": "LOCAL"}
    assert skill_evolver.classify_transfer_scope(p2) == "local"


def test_unknown_category_defaults_universal():
    """都不命中 → 默认 universal（向后兼容：旧 promote 全升，新过滤只拦明确特异项）。"""
    assert skill_evolver.classify_transfer_scope({"category": "misc", "trigger": "通用情节推进"}) == "universal"
    assert skill_evolver.classify_transfer_scope({}) == "universal"
    assert skill_evolver.classify_transfer_scope("not_a_dict") == "universal"


# ── promote 集成 ────────────────────────────────────────────────────────────

def _make_project(td: Path, patterns: dict) -> Path:
    """造一个临时项目 + 写作经验.json。返回 project_root。"""
    root = td / "workspace" / "novels" / "测试书"
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    (root / "_数据库" / "写作经验.json").write_text(
        json.dumps(patterns, ensure_ascii=False), encoding="utf-8")
    return root


def _qualifying(category_field, **extra):
    """造一条达标（usage>=5 & conf>=0.8）的 pattern。"""
    base = {"id": extra.pop("id", "p1"), "usage_count": 5, "confidence": 0.9,
            "category": category_field}
    base.update(extra)
    return base


def _redirect_pool(monkeypatch_dir: Path):
    """把 universal_skill_pool.json 重定向到临时目录，避免污染真 pool。
    promote() 用 Path(__file__).parent.parent/claude-home/universal_skill_pool.json，
    直接 monkeypatch save_json 落点不便 —— 改为临时备份真 pool 内容、测后还原。"""
    pool_path = Path(skill_evolver.__file__).parent.parent / "claude-home" / "universal_skill_pool.json"
    original = pool_path.read_text(encoding="utf-8") if pool_path.exists() else None
    return pool_path, original


def _restore_pool(pool_path: Path, original):
    if original is None:
        if pool_path.exists():
            pool_path.unlink()
    else:
        pool_path.write_text(original, encoding="utf-8")


def test_promote_universal_crosses_pool():
    """通用工艺达标 → 升进 universal_skill_pool（filter active）。"""
    pool_path, original = _redirect_pool(Path("."))
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            root = _make_project(td, {
                "success_patterns": [_qualifying("rhythm", id="craft1", trigger="短句连发提节奏")],
                "failure_patterns": [],
            })
            os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)  # 默认 active
            before = len(json.loads(pool_path.read_text(encoding="utf-8"))["universal_patterns"]) \
                if pool_path.exists() else 0
            r = skill_evolver.promote(root)
            assert r["transfer_scope_filter"] == "active"
            assert r["promoted_count"] == 1
            assert "craft1" in r["promoted_ids"]
            after = len(json.loads(pool_path.read_text(encoding="utf-8"))["universal_patterns"])
            assert after == before + 1
            # pool 里这条标了 transfer_scope=universal
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
            assert pool["universal_patterns"][-1]["transfer_scope"] == "universal"
    finally:
        _restore_pool(pool_path, original)


def test_promote_local_idiolect_locked_not_crossed():
    """作者 idiolect 特异达标 → 锁本地不升 pool（防负迁移）。"""
    pool_path, original = _redirect_pool(Path("."))
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            root = _make_project(td, {
                "success_patterns": [_qualifying("口癖", id="tic1", trigger="主角口头禅『得嘞』")],
                "failure_patterns": [],
            })
            os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)
            before = len(json.loads(pool_path.read_text(encoding="utf-8"))["universal_patterns"]) \
                if pool_path.exists() else 0
            r = skill_evolver.promote(root)
            assert r["promoted_count"] == 0
            assert r["locked_local_count"] == 1
            assert "tic1" in r["locked_local_ids"]
            after = len(json.loads(pool_path.read_text(encoding="utf-8"))["universal_patterns"]) \
                if pool_path.exists() else 0
            assert after == before, "local idiolect 不该进 pool"
            # 写作经验.json 里该条被标 transfer_scope_locked（防重复评估）
            exp = json.loads((root / "_数据库" / "写作经验.json").read_text(encoding="utf-8"))
            tic = exp["success_patterns"][0]
            assert tic["transfer_scope"] == "local"
            assert tic["transfer_scope_locked"] is True
            assert not tic.get("promoted_to_universal")
    finally:
        _restore_pool(pool_path, original)


def test_promote_locked_local_not_reevaluated_twice():
    """已锁本地的 pattern 再跑 promote 不重复进 locked_local 列表（标记一次）。"""
    pool_path, original = _redirect_pool(Path("."))
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            root = _make_project(td, {
                "success_patterns": [_qualifying("idiolect", id="tic2", trigger="作者特有遣词")],
                "failure_patterns": [],
            })
            os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)
            r1 = skill_evolver.promote(root)
            assert r1["locked_local_count"] == 1
            r2 = skill_evolver.promote(root)
            assert r2["locked_local_count"] == 0, "第二轮不该重复上报已锁项"
    finally:
        _restore_pool(pool_path, original)


def test_promote_filter_off_reverts_indiscriminate():
    """SKILL_TRANSFER_SCOPE_FILTER=0 → 回退旧无差别升级（向后兼容）：idiolect 也升。"""
    pool_path, original = _redirect_pool(Path("."))
    try:
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            root = _make_project(td, {
                "success_patterns": [_qualifying("口癖", id="tic3", trigger="主角口头禅")],
                "failure_patterns": [],
            })
            os.environ["SKILL_TRANSFER_SCOPE_FILTER"] = "0"
            try:
                r = skill_evolver.promote(root)
                assert r["transfer_scope_filter"] == "off"
                assert r["promoted_count"] == 1, "filter 关闭时 idiolect 也应升（旧行为）"
                assert r["locked_local_count"] == 0
            finally:
                os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)
    finally:
        _restore_pool(pool_path, original)


def test_filter_active_helper_env_parsing():
    """env 默认 active；0/false/off/no/空 → 关。"""
    saved = os.environ.get("SKILL_TRANSFER_SCOPE_FILTER")
    try:
        os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)
        assert skill_evolver._transfer_scope_filter_active() is True  # 默认 active
        for off in ("0", "false", "off", "no", "", "FALSE"):
            os.environ["SKILL_TRANSFER_SCOPE_FILTER"] = off
            assert skill_evolver._transfer_scope_filter_active() is False, f"{off!r} 应判 off"
        for on in ("1", "true", "yes", "on"):
            os.environ["SKILL_TRANSFER_SCOPE_FILTER"] = on
            assert skill_evolver._transfer_scope_filter_active() is True, f"{on!r} 应判 active"
    finally:
        if saved is None:
            os.environ.pop("SKILL_TRANSFER_SCOPE_FILTER", None)
        else:
            os.environ["SKILL_TRANSFER_SCOPE_FILTER"] = saved
