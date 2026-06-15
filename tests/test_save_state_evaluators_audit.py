"""save_state_evaluators.py 审计修复回归测试 — 守护「第二个 ch⇄cluster 真理源」根除。

审计 bug（triage_worth_fixing.json · file=save_state_evaluators.py · line 35 · medium）：
  get_cluster_chapter_range() 旧实现是本地手写解析 _数据库/事件簇.json clusters[].chapter_range，
  与 cluster_lookup（北极星①唯一权威反查）各自重实现。本地副本**缺 blueprint 兜底**——
  fluid v27 下 chapter_range 由 splitter step 6 才回填，未回填时本地副本返回 [] →
  调用方 main() L111-113 打印 [FATAL] 并 return 2 → 中断 save-state step 9 的 evaluator 段；
  而权威 cluster_lookup.cluster_id_to_range() 此时能从 进度.json.cluster_blueprint 恢复章范围。

修复（2026-06-16 审计修复批次）：
  - 顶部 import cluster_lookup。
  - get_cluster_chapter_range 函数体改为委托 cluster_lookup.cluster_id_to_range()
    （事件簇.json 权威 + 进度.json blueprint 兜底 + 归一化），保留函数名/签名/调用方不动。

这组测试钉死「委托权威反查、blueprint 兜底生效、happy-path 等价、查不到仍返回 []」，
**不碰任何 evaluator 业务调度逻辑**（北极星⑤：确定性 ch⇄cluster 投影硬化，不干涉模型创作）。
零依赖范式：文件尾 __main__ 循环跑 test_* 打 [OK]/[FAIL]，失败 exit 非 0。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_lookup  # noqa: E402
import save_state_evaluators as sse  # noqa: E402


def _mk_db(tmp: Path) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    return db


def _write(db: Path, name: str, obj) -> None:
    (db / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 委托权威反查（防回归）

def test_module_imports_cluster_lookup():
    """save_state_evaluators 必须 import 同一个权威 cluster_lookup（模块身份钉死）。"""
    assert sse.cluster_lookup is cluster_lookup


def test_no_local_eventcluster_reparse_regression():
    """源码层防回归：get_cluster_chapter_range 不得再本地手解析 事件簇.json clusters[]。

    旧反模式：函数内自己 json.loads(事件簇.json) + 遍历 c.get("chapter_range")。
    修复后该函数体只委托 cluster_lookup.cluster_id_to_range，不得再出现本地重解析。
    """
    src = Path(sse.__file__).read_text(encoding="utf-8")
    import re
    # 定位 get_cluster_chapter_range 函数体（到下一个顶层 def 为止）
    m = re.search(r"def get_cluster_chapter_range\(.*?\n(.*?)\n\S", src, re.DOTALL)
    assert m, "找不到 get_cluster_chapter_range 函数定义"
    body = m.group(1)
    # 剥掉注释行：合法说明文案里允许出现「事件簇.json」字样，只看真实代码。
    code = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert "cluster_id_to_range" in code, "函数未委托权威反查 cluster_id_to_range"
    # 旧本地重解析痕迹（真实代码层）必须消失
    assert "read_text" not in code, "函数体回归成本地读 事件簇.json（read_text）"
    assert "json.loads" not in code, "函数体回归成本地 json.loads 重解析"
    assert 'get("clusters"' not in code, "函数体回归成本地遍历 clusters[]"


# ---------------------------------------------------------------- happy-path 等价

def test_eventcluster_range_inclusive_equivalence():
    """事件簇.json 已回填 chapter_range → 返回闭区间章列表（与旧本地实现等价）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write(db, "事件簇.json", {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002", "chapter_range": [4, 7]},
        ]})
        assert sse.get_cluster_chapter_range(tmp, "cluster_001") == [1, 2, 3]
        assert sse.get_cluster_chapter_range(tmp, "cluster_002") == [4, 5, 6, 7]
        # cluster_key 归一化：裸数字 / 短 id 也应命中（权威 normalize 行为）
        assert sse.get_cluster_chapter_range(tmp, "cluster_2") == [4, 5, 6, 7]
        assert sse.get_cluster_chapter_range(tmp, 1) == [1, 2, 3]


# ---------------------------------------------------------------- blueprint 兜底（核心修复点）

def test_blueprint_fallback_when_eventcluster_range_missing():
    """核心：事件簇.json 有该 cluster 但 chapter_range 未回填（fluid v27 splitter 前）→
    旧本地实现返回 [] 触发 [FATAL]，修复后从 进度.json.cluster_blueprint 恢复章范围。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        # 事件簇.json 列了 cluster_002 但故意没 chapter_range（涌现后、切章前）
        _write(db, "事件簇.json", {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
            {"cluster_id": "cluster_002"},
        ]})
        # 进度.json blueprint 提供兜底范围
        _write(db, "进度.json", {"cluster_blueprint": {
            "cluster_002": {"chapter_range": [4, 6]},
        }})
        assert sse.get_cluster_chapter_range(tmp, "cluster_002") == [4, 5, 6], \
            "blueprint 兜底未生效（旧本地副本会返回 [] → [FATAL] 中断 step 9）"


def test_blueprint_list_form_storyboard_fallback():
    """城南实测 blueprint 为 list 形态、靠 scene_storyboard[].ch 推章范围 → 也要兜底成功。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write(db, "事件簇.json", {"clusters": [{"cluster_id": "cluster_001"}]})
        _write(db, "进度.json", {"cluster_blueprint": [
            {"cluster": "cluster_001", "ch": 1},
            {"cluster": "cluster_001", "ch": 2},
        ]})
        assert sse.get_cluster_chapter_range(tmp, "cluster_001") == [1, 2]


# ---------------------------------------------------------------- 查不到仍返回 []（保留 FATAL 守卫语义）

def test_not_found_returns_empty_preserves_fatal_guard():
    """真正查不到的 cluster → 返回 []（调用方据此打 [FATAL]·禁止伪造章范围）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = _mk_db(tmp)
        _write(db, "事件簇.json", {"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        ]})
        assert sse.get_cluster_chapter_range(tmp, "cluster_099") == []


def test_missing_db_files_returns_empty_no_crash():
    """事件簇.json / 进度.json 全缺失 → 返回 [] 不崩（权威反查内部已兜底）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_db(tmp)  # 只建空 _数据库 目录，不放任何 JSON
        assert sse.get_cluster_chapter_range(tmp, "cluster_001") == []


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
