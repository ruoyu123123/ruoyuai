"""C12-SKILL-EVOLVER-WIRE-UP 契约回归（2026-06-27 · 零 LLM / 零联网）。

锁死「经验沉淀数据流形状」的两处结构性空转修复（北极星⑤：只校验确定性事实，
不规定哪两条该合并 / 哪条该升 universal —— 那是阈值 + 模型判断的事）：

① skill_evolver.evolve 相似 blob：
   旧实现用 description+name 的 char-set jaccard。producer（learning_loop/self_heal）
   把经验塞进 trigger/technique/why_works，description/name 多为空 → 旧 p_desc=" "
   → jaccard(" "," ")=1.0 → 不相干经验被结构性误并。
   修复后：producer 真填字段并集 + token 级分词 + 空 blob 显式不并。
   本组钉死：同 key（同内容）→ 必合并；异 key（异内容）→ 必不合并；
   空 blob 一对 → 必不合并（锁 1.0 char-set 误并回归）。

② usage_count producer：
   旧实现全系统无 producer 写 usage_count（build_manifest 只读不写）→ promote 门槛
   usage_count>=5 永远到不了 → 升 universal_skill_pool 结构性空转。
   修复后：build_manifest 检索命中即对原对象 +1 写回 写作经验.json（原子写）。
   本组钉死：① 至少一个 producer 路径（_collect_relevant_heuristics）会写 usage_count；
   ② usage_count>=5 & conf>=0.8 → promote 必写 universal_pool 且非空。

全程真 import 真调用，断言真实行为，绝不 mock 被测逻辑。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import skill_evolver as mod  # noqa: E402
import build_manifest as bm  # noqa: E402


# ── 工具 ──────────────────────────────────────────────────────────────────────

def _mk_project(payload: dict) -> tuple:
    """临时项目 + _数据库/写作经验.json。返回 (tmp_dir, project_root)。"""
    td = tempfile.mkdtemp()
    root = Path(td) / "测试书"
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    (root / "_数据库" / "写作经验.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    # 进度.json 给个空骨架，build_manifest._collect_relevant_heuristics 读它取 context（空也不崩）
    (root / "_数据库" / "进度.json").write_text("{}", encoding="utf-8")
    return td, root


def _read_exp(root: Path) -> dict:
    return json.loads((root / "_数据库" / "写作经验.json").read_text(encoding="utf-8"))


def _rm(td):
    import shutil
    shutil.rmtree(td, ignore_errors=True)


# ── ① evolve：同 key 合并 / 异 key 不合并 / 空 blob 不合并 ─────────────────────

def test_evolve_merges_same_content_recur_entries():
    """一对 recur_* 同内容（trigger/technique/why_works 全同·desc/name 空）→ 必合并。

    旧逻辑只看 description+name（这里空），会判不相似而漏并；新逻辑用 producer 真填字段
    → token jaccard=1.0 → 合并。"""
    payload = {
        "success_patterns": [
            {"id": "recur_a1", "trigger": "对话冲突场景提速",
             "technique": "短句连发推进对话张力", "why_works": "节奏紧凑读者代入",
             "version": 1, "confidence": 0.7, "usage_count": 3},
            {"id": "recur_a2", "trigger": "对话冲突场景提速",
             "technique": "短句连发推进对话张力", "why_works": "节奏紧凑读者代入",
             "version": 1, "confidence": 0.6, "usage_count": 2},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert len(r["merged"]) == 1, f"同内容应合并：{r}"
        assert r["merged"][0]["count"] == 2
        exp = _read_exp(root)
        assert len(exp["success_patterns"]) == 1, "合并后只剩 1 条"
        assert exp["success_patterns"][0]["usage_count"] == 5  # 3+2 累积
    finally:
        _rm(td)


def test_evolve_keeps_different_content_recur_entries_separate():
    """一对 recur_* 异内容（trigger/technique/why_works 全不同）→ 必不合并。"""
    payload = {
        "success_patterns": [
            {"id": "recur_b1", "trigger": "对话冲突提速",
             "technique": "短句连发推进张力", "why_works": "节奏紧凑代入"},
            {"id": "recur_b2", "trigger": "风景过渡舒缓铺陈",
             "technique": "长句渲染环境氛围", "why_works": "拉开呼吸"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == [], f"异内容不该合并：{r}"
        exp = _read_exp(root)
        assert len(exp["success_patterns"]) == 2
    finally:
        _rm(td)


def test_evolve_empty_blob_entries_never_merge_regression():
    """🔴 锁回归：一对 producer 字段全空的经验 → 绝不能被误并。

    旧 char-set jaccard 对双空 description+name 返回 jaccard(" "," ")=1.0 → 必并（bug）。
    新逻辑：空 blob → 空 token 集 → 显式不发起 / 不并入合并。"""
    # 先证旧根因仍在 char-set jaccard 函数里（说明为什么必须换掉它）
    assert mod.jaccard(" ", " ") == 1.0, "char-set jaccard 对双空白返 1.0 —— 这正是旧误并根因"

    payload = {
        "success_patterns": [
            {"id": "recur_empty1"},  # 无 trigger/technique/why_works/description/name
            {"id": "recur_empty2"},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        r = mod.evolve(root, current_ch=10)
        assert r["merged"] == [], f"空 blob 经验绝不能被误并（锁 1.0 回归）：{r}"
        exp = _read_exp(root)
        assert len(exp["success_patterns"]) == 2, "两条空经验应原样保留各自独立"
    finally:
        _rm(td)


def test_tokenize_empty_yields_empty_set():
    """空 / 纯空白文本 → 空 token 集（空 blob 永不相似的底层保证）。"""
    assert mod._tokenize("") == set()
    assert mod._tokenize("   ") == set()
    assert mod._tokenize(None) == set()
    # 空 blob 的 token jaccard 恒 0（任一空集合）
    assert mod._token_jaccard(set(), {"a"}) == 0.0
    assert mod._token_jaccard({"a"}, set()) == 0.0
    # 同内容 → 1.0；无交集 → 0.0
    t = mod._tokenize("短句连发推进对话张力")
    assert t and mod._token_jaccard(t, t) == 1.0


# ── ② usage_count producer：build_manifest 检索命中 → 写回 ─────────────────────

def test_build_manifest_retrieval_writes_usage_count():
    """🔴 至少一个 producer 路径写 usage_count：build_manifest 检索命中后读回 >0。

    旧实现 build_manifest 只读不写 → usage_count 恒 0 → promote 门槛永不达 → 空转。"""
    payload = {
        "success_patterns": [
            {"id": "h1", "name": "钩子收束节奏", "description": "章末悬念钩子吊住读者",
             "confidence": 0.6, "version": 1, "usage_count": 0},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        scanner = bm.DatabaseScanner(root, 1)
        res = bm._collect_relevant_heuristics(scanner, 1)
        assert res.get("mode") == "on"
        assert res.get("retrieved_count", 0) >= 1
        # 返回结构不泄漏内部 _orig / _retrieved_at_ch
        for item in res.get("retrieved", []):
            assert "_orig" not in item
        # 写回生效：磁盘上的 usage_count 被 +1
        exp = _read_exp(root)
        h1 = exp["success_patterns"][0]
        assert h1["usage_count"] >= 1, f"检索命中应 +1 写回 usage_count，得 {h1.get('usage_count')}"
        assert h1.get("last_retrieved_at_ch") == 1
    finally:
        _rm(td)


def test_build_manifest_retrieval_accumulates_usage_count():
    """连续两次检索 → usage_count 单调累加（producer 持续沉淀）。"""
    payload = {
        "success_patterns": [
            {"id": "h2", "name": "反派动机", "description": "塑造立体反派动机充分",
             "confidence": 0.6, "version": 1, "usage_count": 0},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        scanner1 = bm.DatabaseScanner(root, 1)
        bm._collect_relevant_heuristics(scanner1, 1)
        first = _read_exp(root)["success_patterns"][0]["usage_count"]
        # 新 scanner（重读磁盘，含上轮 +1）再检索一次
        scanner2 = bm.DatabaseScanner(root, 2)
        bm._collect_relevant_heuristics(scanner2, 2)
        second = _read_exp(root)["success_patterns"][0]["usage_count"]
        assert second == first + 1 >= 2, f"应单调累加：{first} -> {second}"
    finally:
        _rm(td)


# ── ② promote：达标 → 写 universal_pool 非空 ──────────────────────────────────

def _pool_path() -> Path:
    """与 promote() 同口径解析 pool 落点（user_data_dir 优先·dev=仓库根）。"""
    try:
        from frozen_util import user_data_dir as _udd
        return _udd() / "core" / "claude-home" / "universal_skill_pool.json"
    except Exception:
        return Path(mod.__file__).parent.parent / "claude-home" / "universal_skill_pool.json"


def test_promote_qualifying_writes_universal_pool_nonempty():
    """usage_count>=5 & conf>=0.8 & 通用工艺 → promote 必写 universal_pool 且非空。

    依赖 ① 修好的 producer（usage_count 能涨到 5），此处直接喂达标条做下游 promote 验证。
    备份/还原真 pool 文件，测后不污染。"""
    pool_path = _pool_path()
    original = pool_path.read_text(encoding="utf-8") if pool_path.exists() else None
    payload = {
        "success_patterns": [
            {"id": "promo1", "category": "rhythm", "trigger": "短句连发提节奏",
             "technique": "紧张段落短句推进", "usage_count": 5, "confidence": 0.9},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        before = (len(json.loads(pool_path.read_text(encoding="utf-8")).get("universal_patterns", []))
                  if pool_path.exists() else 0)
        r = mod.promote(root)
        assert r["promoted_count"] == 1, f"达标通用工艺应升 universal：{r}"
        assert "promo1" in r["promoted_ids"]
        assert pool_path.exists(), "promote 应写出 universal_skill_pool.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        assert len(pool["universal_patterns"]) == before + 1
        assert pool["universal_patterns"][-1]["transfer_scope"] == "universal"
        # 源经验被标 promoted_to_universal（防重复升）
        exp = _read_exp(root)
        assert exp["success_patterns"][0]["promoted_to_universal"] is True
    finally:
        _rm(td)
        # 还原真 pool
        if original is None:
            if pool_path.exists():
                pool_path.unlink()
        else:
            pool_path.write_text(original, encoding="utf-8")


def test_promote_below_threshold_not_promoted():
    """usage_count<5 → 不升（门槛真生效·证 producer 必须先把 usage 顶到 5）。"""
    pool_path = _pool_path()
    original = pool_path.read_text(encoding="utf-8") if pool_path.exists() else None
    payload = {
        "success_patterns": [
            {"id": "low1", "category": "rhythm", "trigger": "x", "usage_count": 4, "confidence": 0.9},
        ],
        "failure_patterns": [],
    }
    td, root = _mk_project(payload)
    try:
        before = (len(json.loads(pool_path.read_text(encoding="utf-8")).get("universal_patterns", []))
                  if pool_path.exists() else 0)
        r = mod.promote(root)
        assert r["promoted_count"] == 0, "usage<5 不该升"
        after = (len(json.loads(pool_path.read_text(encoding="utf-8")).get("universal_patterns", []))
                 if pool_path.exists() else 0)
        assert after == before
    finally:
        _rm(td)
        if original is None:
            if pool_path.exists():
                pool_path.unlink()
        else:
            pool_path.write_text(original, encoding="utf-8")


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
