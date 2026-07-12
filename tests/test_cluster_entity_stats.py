# -*- coding: utf-8 -*-
"""A15 确定性实体统计测试 — 🔴 2026-07-07（moyin collectCharacterStats 范式）。

钉死 cluster_entity_stats.py（archivist 前置证据基线·零 LLM）：
  · 已知实体出场计数（人物卡 + 角色池·变体长度降序遮罩·别名不双算）
  · 对白条数估计（引号邻域归属：前缀最近实体 / 后缀「名+说话动词」）
  · 新专名候选启发式（speaker_pattern 同源 validate_chapter·频次≥3 防噪·
    已知名子串/超串排除）
  · 输出 schema / 幂等（无时间戳·同输入同字节）/ 草稿缺失 exit 2
  · plan step5 接线契约（scripts 前置行 + ENTITY_STATS_PATH + expected_outputs）
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_entity_stats as ces  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

# 手算对照草稿（每行都进了下方断言的手算账·改动必须重新手算）
_DRAFT = (
    "伊莱推开钟楼的门。\n"
    "“今晚风大。”伊莱说。\n"
    "玛莎修女站在楼梯口。\n"
    "“孩子，回来吃饭。”玛莎修女说。\n"
    "“我不饿。”\n"
    "老汤姆在楼下咳嗽。玛莎叫了一声小伊。\n"
    "塞拉斯举起灯。\n"
    "“钟停了。”塞拉斯说。\n"
    "塞拉斯又看了一眼钟摆。塞拉斯离开了。\n"
    "玛莎修女低声道：“晚安。”\n"
    "“走吧。”阿福说。\n"
    "阿福离开。\n"
)


def _mk_project(tmp: Path, *, draft: str = _DRAFT, cards=None, pool=None) -> Path:
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    if cards is None:
        cards = {"schema_version": 1, "characters": [
            {"id": "C_PROT", "name": "伊莱", "aliases": ["小伊"]},
            {"id": "C_MARTHA", "name": "玛莎修女", "aliases": ["玛莎"]},
        ]}
    (db / "人物卡.json").write_text(
        json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    if pool is None:
        pool = {"schema_version": 1, "core": [],
                "emerged": [{"id": "C_TOM", "name": "老汤姆"}], "extras": []}
    (db / "角色池.json").write_text(
        json.dumps(pool, ensure_ascii=False), encoding="utf-8")
    d = tmp / "章节" / "cluster_001_draft"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cluster_001_draft.txt").write_text(draft, encoding="utf-8")
    return tmp


def _run(tmp: Path, extra_argv=None) -> dict:
    rc = ces.main([str(tmp), "--cluster", "001"] + (extra_argv or []))
    assert rc == 0, f"main 应成功 exit 0，实得 {rc}"
    out = tmp / "_数据库" / ".wal" / "cluster_001_entity_stats.json"
    assert out.exists(), "统计产物未落盘"
    return json.loads(out.read_text(encoding="utf-8"))


def _by_name(report: dict) -> dict:
    return {e["name"]: e for e in report["known_entities"]}


# ═══════════════ 1. 统计正确性（已知草稿手算对照） ═══════════════

def test_known_entity_mention_counts(tmp_path):
    """手算账：伊莱=2(本名)+1(小伊)=3 · 玛莎修女=3(全名)+1(玛莎裸称)=4 ·
    老汤姆(角色池 emerged)=1。首现位置/出场标记同步核对。"""
    report = _run(_mk_project(tmp_path))
    ents = _by_name(report)
    assert ents["伊莱"]["mention_count"] == 3, ents["伊莱"]
    assert ents["玛莎修女"]["mention_count"] == 4, ents["玛莎修女"]
    assert ents["老汤姆"]["mention_count"] == 1, ents["老汤姆"]
    assert ents["老汤姆"]["source"] == "角色池"
    assert ents["伊莱"]["first_offset"] == 0
    assert ents["伊莱"]["first_context"].startswith("伊莱推开钟楼")
    assert all(e["appears"] for e in report["known_entities"])


def test_alias_masking_no_double_count(tmp_path):
    """别名是全名子串（玛莎 ⊂ 玛莎修女）时绝不双算：
    naive 口径 count(玛莎修女)=3 + count(玛莎)=4 会虚报 7；遮罩后必须是 4。"""
    report = _run(_mk_project(tmp_path))
    assert _by_name(report)["玛莎修女"]["mention_count"] == 4


# ═══════════════ 2. 引号邻域对白归属估计 ═══════════════

def test_dialogue_attribution_estimate(tmp_path):
    """手算账（6 条引文）：伊莱=1（后缀「伊莱说」）· 玛莎修女=2（后缀 1 +
    前缀「玛莎修女低声道：」1）· 老汤姆=0；孤行引文/未知说话人（塞拉斯、阿福）
    共 3 条未归属。"""
    report = _run(_mk_project(tmp_path))
    ents = _by_name(report)
    assert ents["伊莱"]["dialogue_count_estimate"] == 1, ents["伊莱"]
    assert ents["玛莎修女"]["dialogue_count_estimate"] == 2, ents["玛莎修女"]
    assert ents["老汤姆"]["dialogue_count_estimate"] == 0
    assert report["totals"]["quote_count"] == 6
    assert report["totals"]["unattributed_quotes"] == 3


# ═══════════════ 3. 新专名候选阈值 ═══════════════

def test_new_name_candidate_threshold(tmp_path):
    """塞拉斯出现 4 次（≥3）→ 列为候选；阿福只出现 2 次（<3）→ 防噪不列。"""
    report = _run(_mk_project(tmp_path))
    names = {c["name"] for c in report["new_name_candidates"]}
    assert "塞拉斯" in names, report["new_name_candidates"]
    assert "阿福" not in names, report["new_name_candidates"]
    cand = next(c for c in report["new_name_candidates"] if c["name"] == "塞拉斯")
    assert cand["mention_count"] == 4
    assert cand["evidence"] == "speaker_pattern"
    assert cand["first_offset"] == _DRAFT.find("塞拉斯")


def test_new_name_candidate_min_freq_flag(tmp_path):
    """--min-new-name-freq 2 时阿福（2 次·有 speaker 证据）也入候选。"""
    report = _run(_mk_project(tmp_path), ["--min-new-name-freq", "2"])
    names = {c["name"] for c in report["new_name_candidates"]}
    assert {"塞拉斯", "阿福"} <= names, report["new_name_candidates"]


def test_quoted_term_fragment_not_candidate(tmp_path):
    """引文内术语「“无限主神大道”」的「X道”」形态不是说话归属 → 不产候选
    （回归锁·实测《主神验尸官》cluster_001 误报「限主神大」）。"""
    draft = ("“无限主神大道”是这里的名字。\n"
             "他念叨着“无限主神大道”不肯睡。\n"
             "石碑上刻着“无限主神大道”五个字。\n")
    tmp = _mk_project(tmp_path, draft=draft,
                      cards={"characters": [{"id": "C_PROT", "name": "秦烬", "aliases": []}]},
                      pool={"core": [], "emerged": [], "extras": []})
    report = _run(tmp)
    assert report["new_name_candidates"] == [], report["new_name_candidates"]


def test_candidate_containing_known_name_excluded(tmp_path):
    """含已知名的超串（伊莱恩 ⊃ 伊莱）不当新专名（按同人歧义处理·防碎片立卡）。"""
    draft = ("伊莱恩走进大厅。\n"
             "“到了。”伊莱恩说。\n"
             "“坐吧。”伊莱恩说。\n")
    tmp = _mk_project(tmp_path, draft=draft,
                      cards={"characters": [{"id": "C_PROT", "name": "伊莱", "aliases": []}]},
                      pool={"core": [], "emerged": [], "extras": []})
    report = _run(tmp)
    assert report["new_name_candidates"] == [], report["new_name_candidates"]


# ═══════════════ 4. 输出 schema / 幂等 / 失败硬停 ═══════════════

def test_output_schema(tmp_path):
    report = _run(_mk_project(tmp_path))
    for key in ("schema_version", "generator", "cluster_id", "draft_path",
                "draft_chars", "draft_cjk", "params", "known_entities",
                "new_name_candidates", "totals"):
        assert key in report, f"schema 缺键 {key}"
    assert report["generator"] == "cluster_entity_stats"
    assert report["cluster_id"] == "cluster_001"
    assert report["params"]["min_new_name_freq"] == 3
    assert report["draft_cjk"] > 0
    t = report["totals"]
    for key in ("known_registered", "known_appearing", "new_candidates",
                "quote_count", "unattributed_quotes"):
        assert isinstance(t[key], int), f"totals.{key} 非 int"
    for e in report["known_entities"]:
        for key in ("id", "name", "source", "mention_count",
                    "dialogue_count_estimate", "appears", "first_offset"):
            assert key in e, f"known_entities 行缺键 {key}"


def test_idempotent_same_bytes(tmp_path):
    """确定性纪律：无时间戳·同输入两次运行产物字节级相同。"""
    tmp = _mk_project(tmp_path)
    out = tmp / "_数据库" / ".wal" / "cluster_001_entity_stats.json"
    assert ces.main([str(tmp), "--cluster", "001"]) == 0
    first = out.read_bytes()
    assert ces.main([str(tmp), "--cluster", "001"]) == 0
    assert out.read_bytes() == first, "两次运行产物不一致（引入了非确定性）"


def test_missing_draft_exit_2(tmp_path):
    """草稿缺失=契约破损 → exit 2 硬停（不产半截统计）。"""
    tmp = _mk_project(tmp_path)
    (tmp / "章节" / "cluster_001_draft" / "cluster_001_draft.txt").unlink()
    assert ces.main([str(tmp), "--cluster", "001"]) == 2
    assert not (tmp / "_数据库" / ".wal" / "cluster_001_entity_stats.json").exists()


# ═══════════════ 5. plan step5 接线契约 ═══════════════

def _plan_step5() -> dict:
    plan = json.loads(
        (_ROOT / "core" / "claude-home" / "plans" / "cluster-save-state.plan.json")
        .read_text(encoding="utf-8"))
    return next(s for s in plan["steps"] if s["n"] == 5)


def test_plan_step5_runs_entity_stats_before_spawn():
    """step5 scripts 必须含 cluster_entity_stats.py 前置行（spawn archivist 之前跑）。"""
    step = _plan_step5()
    lines = step.get("scripts") or []
    assert any("core/scripts/cluster_entity_stats.py" in l for l in lines), lines
    assert step.get("must_spawn_agent") == ["novel-archivist", "novel-state-tracker"]


def test_plan_step5_agent_input_and_outputs_wired():
    """agent_input 带 ENTITY_STATS_PATH，expected_outputs 含统计产物（假完成防线）。"""
    step = _plan_step5()
    assert step["agent_input"].get("ENTITY_STATS_PATH") == \
        "_数据库/.wal/cluster_{key}_entity_stats.json", step["agent_input"]
    assert "_数据库/.wal/cluster_{key}_entity_stats.json" in step["expected_outputs"]


def test_archivist_doc_declares_entity_stats_input():
    """novel-archivist.md 必须声明确定性实体统计输入。"""
    text = (_ROOT / ".claude" / "agents" / "novel-archivist.md").read_text(encoding="utf-8")
    assert "ENTITY_STATS_PATH" in text
    assert "确定性实体统计" in text


if __name__ == "__main__":
    import tempfile
    import traceback
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            fn = globals()[nm]
            try:
                if fn.__code__.co_argcount:
                    with tempfile.TemporaryDirectory() as td:
                        fn(Path(td))
                else:
                    fn()
                print(f"  [OK] {nm}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
