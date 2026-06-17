"""cluster_summary_store 回归测试 — 钉死账本（故事块摘要.json）唯一原子写入器的核心确定性逻辑。

被测核心（绝不打 LLM / 不联网，纯本地原子文件合并）：
- _db_dir：project_root 末名是 _数据库 → 原样，否则追加 _数据库
- _deep_merge：dict 递归合，list/标量直接覆盖
- _apply_upsert：按归一化 cluster_id 反查/新建记录 + 深合并 + cluster_id 恒归一化 + 坏 summary/clusters 兜底
- upsert_cluster：归一化等价（int 6 / "6" / "cluster_006" 同一 cluster）+ 二次合并不丢字段 + 原子落盘
- patch_chapter：便捷嵌进 chapters[str(ch)]

零依赖约定：只用标准库，test_* 无参数，断言失败 raise AssertionError。
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))
import cluster_summary_store as mod  # noqa: E402
from cluster_summary_reader import SUMMARY_FILENAME, load_summary  # noqa: E402


def _summary_path(root: Path) -> Path:
    return root / "_数据库" / SUMMARY_FILENAME


# ── _db_dir ─────────────────────────────────────────────────────────────────

def test_db_dir_appends_db_folder():
    """普通项目根 → 追加 _数据库 子目录。"""
    root = Path(tempfile.mkdtemp())
    got = mod._db_dir(root)
    assert got == root / "_数据库", got


def test_db_dir_no_double_append_when_already_db():
    """末名已是 _数据库 → 原样返回，不重复追加。"""
    root = Path(tempfile.mkdtemp()) / "_数据库"
    got = mod._db_dir(root)
    assert got == root, got
    assert got.name == "_数据库"


# ── _deep_merge ─────────────────────────────────────────────────────────────

def test_deep_merge_recurses_dict_overwrites_list_and_scalar():
    base = {"a": {"x": 1, "y": 2}, "lst": [1, 2], "s": "old", "keep": 9}
    patch = {"a": {"y": 20, "z": 30}, "lst": [9], "s": "new"}
    out = mod._deep_merge(base, patch)
    # dict 递归：y 被覆盖、x 保留、z 新增
    assert out["a"] == {"x": 1, "y": 20, "z": 30}, out["a"]
    # list 整体覆盖（非合并）
    assert out["lst"] == [9], out["lst"]
    # 标量覆盖
    assert out["s"] == "new"
    # patch 没碰的键保留
    assert out["keep"] == 9
    # 原地返回同一对象
    assert out is base


def test_deep_merge_dict_replaces_when_base_value_not_dict():
    """base 该键不是 dict 时，patch 的 dict 直接覆盖（不递归）。"""
    base = {"k": "scalar"}
    out = mod._deep_merge(base, {"k": {"nested": 1}})
    assert out["k"] == {"nested": 1}, out["k"]


# ── _apply_upsert：纯内存合并语义 ───────────────────────────────────────────

def test_apply_upsert_creates_record_and_normalizes_id():
    """空账本 → 新建记录，cluster_id 恒归一化形态。"""
    summary = {"schema_version": "v2.cluster", "clusters": []}
    out = mod._apply_upsert(summary, "cluster_002", {"title": "T"})
    assert len(out["clusters"]) == 1
    rec = out["clusters"][0]
    assert rec["cluster_id"] == "cluster_002"
    assert rec["title"] == "T"


def test_apply_upsert_matches_existing_by_normalized_id_no_dup():
    """已有 cluster_002 记录 + 同 cluster 不同写法 → 合并到同一条，不新建。"""
    summary = {"clusters": [{"cluster_id": "cluster_002", "title": "A", "word_count": 100}]}
    # 传 int 形式的同一 cluster
    out = mod._apply_upsert(summary, mod.cluster_lookup.normalize_cluster_id(2), {"judge_grade": "B"})
    assert len(out["clusters"]) == 1, out["clusters"]
    rec = out["clusters"][0]
    assert rec["word_count"] == 100 and rec["judge_grade"] == "B"
    assert rec["title"] == "A"


def test_apply_upsert_repairs_non_dict_summary_and_clusters():
    """坏输入兜底：summary 非 dict / clusters 非 list 都要被修成空骨架后正常写入。"""
    # summary 非 dict
    out1 = mod._apply_upsert("garbage", "cluster_001", {"title": "x"})
    assert isinstance(out1, dict) and out1["clusters"][0]["cluster_id"] == "cluster_001"
    # clusters 非 list
    out2 = mod._apply_upsert({"clusters": "not-a-list"}, "cluster_003", {"title": "y"})
    assert isinstance(out2["clusters"], list)
    assert out2["clusters"][0]["cluster_id"] == "cluster_003"


def test_apply_upsert_skips_malformed_cluster_entries():
    """clusters 里混入非 dict 项时跳过、不崩，且匹配到合法目标记录。"""
    summary = {"clusters": ["bad", 42, {"cluster_id": "cluster_005", "title": "ok"}]}
    out = mod._apply_upsert(summary, "cluster_005", {"word_count": 7})
    rec = next(c for c in out["clusters"] if isinstance(c, dict) and c.get("cluster_id") == "cluster_005")
    assert rec["word_count"] == 7 and rec["title"] == "ok"


# ── upsert_cluster：原子落盘 + 归一化等价 + 不丢字段 ────────────────────────

def test_upsert_cluster_persists_and_normalizes_across_id_forms():
    """int 6 / "6" / "cluster_006" 视为同一 cluster：三次写汇聚到一条记录、字段累积。"""
    root = Path(tempfile.mkdtemp())
    mod.upsert_cluster(root, "cluster_006", {"title": "六", "word_count": 5000})
    mod.upsert_cluster(root, 6, {"judge_grade": "A"})
    mod.upsert_cluster(root, "6", {"word_count": 5500})  # 同字段覆盖

    s = load_summary(root)
    assert len(s["clusters"]) == 1, s["clusters"]
    rec = s["clusters"][0]
    assert rec["cluster_id"] == "cluster_006"
    assert rec["title"] == "六"
    assert rec["judge_grade"] == "A"
    assert rec["word_count"] == 5500  # 后写覆盖
    # 文件真落盘且是合法 JSON
    assert _summary_path(root).exists()
    on_disk = json.loads(_summary_path(root).read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == "v2.cluster"


def test_upsert_cluster_second_merge_does_not_drop_existing_fields():
    """二次 upsert 不同字段 → 深合并不丢先前字段（M1 丢更新防护点）。"""
    root = Path(tempfile.mkdtemp())
    mod.upsert_cluster(root, "cluster_002", {"chapter_range": [5, 8], "word_count": 12000})
    mod.upsert_cluster(root, "cluster_002", {"judge_grade": "B"})
    rec = load_summary(root)["clusters"][0]
    assert rec["word_count"] == 12000
    assert rec["chapter_range"] == [5, 8]
    assert rec["judge_grade"] == "B"


# ── patch_chapter：嵌入 chapters[str(ch)] ───────────────────────────────────

def test_patch_chapter_nests_under_chapters_string_key():
    """patch_chapter(ch=5) → chapters["5"]，且多章互不覆盖、同章字段累积。"""
    root = Path(tempfile.mkdtemp())
    mod.upsert_cluster(root, "cluster_002", {"title": "块"})
    mod.patch_chapter(root, 2, 5, {"cjk_count": 3200, "scene_type": "悬疑"})
    mod.patch_chapter(root, "cluster_002", 6, {"cjk_count": 3400})
    mod.patch_chapter(root, 2, 5, {"pov": "第三人称"})  # 同章追加字段

    rec = load_summary(root)["clusters"][0]
    chapters = rec["chapters"]
    # key 是字符串章号
    assert "5" in chapters and "6" in chapters
    assert chapters["5"]["cjk_count"] == 3200
    assert chapters["5"]["scene_type"] == "悬疑"
    assert chapters["5"]["pov"] == "第三人称"  # 同章深合并不丢
    assert chapters["6"]["cjk_count"] == 3400
    # title 不被章补丁影响
    assert rec["title"] == "块"


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    for n in sorted(g):
        if n.startswith("test_"):
            try:
                g[n]()
                print("OK", n)
            except Exception as e:
                print("FAIL", n, e)
                traceback.print_exc()
