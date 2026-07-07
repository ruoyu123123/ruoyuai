# -*- coding: utf-8 -*-
"""S11 scene 级大势对齐三问 + 自评 回归测试（2026-07-07 · 零 LLM / 零联网 / 仅标准库）。

出处 research/open_source_writing_systems_round2.md S11 · 借鉴 moyin
shot-calibration-stages.ts:188-202 叙事一致性三问。

锁三件事：
  1. schema：event_cluster_schema.json scene_storyboard.items 的 conflict_stage /
     scene_purpose / alignment 三字段——枚举值精确、**optional 不进 required**（fluid
     纪律·北极星⑤ 不硬锁）+ 枚举正反例（手写 mini 校验·环境无 jsonschema 库）。
  2. 消费端：volume_arc_drift_scanner.scan() 汇总本卷 alignment=needs-review 的
     计数/清单进报告 alignment_review 段——**只报告不裁决**（不生成 issue·不改退出码）。
  3. 文档同步锁：novel-outline-planner.md 三问在场（两个详化场合）+ outline.md 对应段。

零依赖范式（__main__ 自跑 · 与 tests/test_volume_arc_drift_scanner.py 同款）。
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import volume_arc_drift_scanner as mod  # noqa: E402

_SCHEMA_PATH = _ROOT / "core" / "claude-home" / "schemas" / "event_cluster_schema.json"
_PLANNER_MD = _ROOT / ".claude" / "agents" / "novel-outline-planner.md"
_OUTLINE_MD = _ROOT / ".claude" / "commands" / "outline.md"


def _scene_items_schema() -> dict:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    # scene_storyboard 定义在 clusters.items.properties 下
    clusters_items = schema["properties"]["clusters"]["items"]
    return clusters_items["properties"]["scene_storyboard"]["items"]


# ══════════════════════════════════════════════════════════════════════════
# 1) schema：字段存在 + 枚举精确 + optional（fluid）
# ══════════════════════════════════════════════════════════════════════════
def test_schema_conflict_stage_enum_exact():
    """conflict_stage 枚举 = 铺垫|升级|高潮|转折|尾声（S11 三问① 冲突弧位置）。"""
    props = _scene_items_schema()["properties"]
    assert "conflict_stage" in props, "schema 缺 conflict_stage 字段"
    assert props["conflict_stage"]["enum"] == ["铺垫", "升级", "高潮", "转折", "尾声"], \
        props["conflict_stage"]["enum"]
    assert props["conflict_stage"]["type"] == "string"


def test_schema_alignment_enum_exact_and_scene_purpose_string():
    """alignment 枚举 = aligned|minor-deviation|needs-review；scene_purpose 为 string。"""
    props = _scene_items_schema()["properties"]
    assert "alignment" in props and "scene_purpose" in props
    assert props["alignment"]["enum"] == ["aligned", "minor-deviation", "needs-review"], \
        props["alignment"]["enum"]
    assert props["scene_purpose"]["type"] == "string"


def test_schema_s11_fields_optional_fluid():
    """🔴 fluid 纪律：三字段不进任何 required——缺字段的历史产物合法（北极星⑤ 不硬锁）。"""
    items = _scene_items_schema()
    required = items.get("required") or []
    for f in ("conflict_stage", "scene_purpose", "alignment"):
        assert f not in required, f"{f} 不许设 required（advisory·fluid）"


def _enum_check(scene: dict) -> bool:
    """mini 枚举校验（环境无 jsonschema 库·按权威 schema 的 enum 手校）：
    字段缺失 = 合法（optional）；字段存在但值不在 enum = 非法。"""
    props = _scene_items_schema()["properties"]
    for f in ("conflict_stage", "alignment"):
        if f in scene and scene[f] not in props[f]["enum"]:
            return False
    if "scene_purpose" in scene and not isinstance(scene["scene_purpose"], str):
        return False
    return True


def test_schema_enum_positive_and_negative_examples():
    """枚举正反例：合法值/缺字段通过；非法枚举值（如 alignment='perfect'）拒绝。"""
    # 正例：三字段齐全且合法
    assert _enum_check({"conflict_stage": "高潮",
                        "scene_purpose": "让主角首次直面卷核心冲突的代价",
                        "alignment": "needs-review"})
    # 正例：全缺（optional·历史产物）
    assert _enum_check({"scene_idx": 0, "scene": "开场"})
    # 反例：非法枚举值
    assert not _enum_check({"alignment": "perfect"})
    assert not _enum_check({"conflict_stage": "序幕"})
    assert not _enum_check({"scene_purpose": 123})


# ══════════════════════════════════════════════════════════════════════════
# 2) 消费端：volume_arc_drift_scanner 汇总 needs-review（只报告不裁决）
# ══════════════════════════════════════════════════════════════════════════
def _mk_project() -> Path:
    # 与 test_volume_arc_drift_scanner._mk_project 同款：重置内容后端为不可用，
    # 防本机备好 bge 模型时跨测试非确定性污染（__main__ 自跑器不过 pytest fixture）。
    import embedding_store
    embedding_store.content_backend_available = lambda: False
    proj = Path(tempfile.mkdtemp()) / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write(proj: Path, name: str, obj) -> None:
    (proj / "_数据库" / name).write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _mk_aligned_project(storyboards: dict) -> Path:
    """无漂移基线项目（milestone 被触及）+ 按 cluster_id 挂 scene_storyboard。"""
    proj = _mk_project()
    _write(proj, "大势卡.json", {
        "volumes": [{"vol": 1, "key_milestones": ["夺取王座"]}],
        "major_events": [{"volume": 1, "status": "completed"}],
    })
    clusters = []
    for cid, sb in storyboards.items():
        clusters.append({"cluster_id": cid, "volume": 1,
                         "chapter_range": [1, 3], "status": "已完成",
                         "scope_summary": "主角夺取王座", "scene_storyboard": sb})
    _write(proj, "事件簇.json", {"clusters": clusters})
    return proj


def test_scanner_aggregates_needs_review_scenes():
    """本卷 scene 里 alignment=needs-review 的计数/清单进报告 alignment_review 段。"""
    proj = _mk_aligned_project({
        "cluster_001": [
            {"scene_idx": 0, "scene": "开场", "alignment": "aligned"},
            {"scene_idx": 1, "scene": "支线闲笔", "alignment": "needs-review",
             "scene_purpose": "给配角铺情感债", "conflict_stage": "铺垫"},
        ],
        "cluster_002": [
            {"scene_idx": 0, "scene": "又一处拿不准", "alignment": "needs-review"},
        ],
    })
    r = mod.scan(proj)
    ar = r["alignment_review"]
    assert ar["needs_review_count"] == 2, ar
    scenes = ar["needs_review_scenes"]
    assert {(s["cluster_id"], s["scene_idx"]) for s in scenes} == {
        ("cluster_001", 1), ("cluster_002", 0)}, scenes
    hit = [s for s in scenes if s["cluster_id"] == "cluster_001"][0]
    assert hit["scene_purpose"] == "给配角铺情感债" and hit["conflict_stage"] == "铺垫", hit


def test_scanner_needs_review_report_only_never_issue():
    """🔴 只报告不裁决：有 needs-review 但无漂移 → issues 仍空、退出码仍 0
    （needs-review 是诚实标记不是失败·北极星⑤）。"""
    proj = _mk_aligned_project({
        "cluster_001": [{"scene_idx": 0, "alignment": "needs-review"}],
    })
    r = mod.scan(proj)
    assert r["alignment_review"]["needs_review_count"] == 1, r
    assert r["issues"] == [], "needs-review 绝不生成 issue（只报告不裁决）"
    # main() 退出码不受 needs-review 影响
    old_argv = sys.argv
    sys.argv = ["volume_arc_drift_scanner.py", str(proj)]
    code = None
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            mod.main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        sys.argv = old_argv
    assert code == 0, "needs-review 只报告·不改退出码"


def test_scanner_zero_needs_review_and_missing_fields_legal():
    """反例 + fluid：无 alignment 字段（历史产物）/ aligned / 无 storyboard → 计数 0 不崩。"""
    proj = _mk_aligned_project({
        "cluster_001": [
            {"scene_idx": 0, "scene": "老产物无自评字段"},
            {"scene_idx": 1, "alignment": "aligned"},
            {"scene_idx": 2, "alignment": "minor-deviation"},
        ],
        "cluster_002": None,  # 无 storyboard（非 list）→ 防御性跳过
    })
    r = mod.scan(proj)
    ar = r["alignment_review"]
    assert ar["needs_review_count"] == 0 and ar["needs_review_scenes"] == [], ar
    assert r["issues"] == [], r


# ══════════════════════════════════════════════════════════════════════════
# 3) 文档同步锁（流程一致性·防新会话被旧口径误导）
# ══════════════════════════════════════════════════════════════════════════
def test_planner_contract_three_questions_present():
    """planner 合约 grep 锁：三问 + 三字段 + 北极星⑤ 自评透明化措辞在场。"""
    text = _PLANNER_MD.read_text(encoding="utf-8")
    for anchor in (
        "大势对齐三问",              # 三问总纲（⑧ 节）
        "如何推动卷核心冲突",        # 三问①
        "locked_facts",              # 三问②（世界观/locked_facts）
        "scene_purpose",             # 三问③ 落字段
        "conflict_stage",
        '"alignment"',               # brief 示例里的自评字段
        "needs-review",
        "诚实标记不是失败",          # 北极星⑤：自评透明化非外部裁决
        "不能只描述画面",            # scene_purpose 的核心要求
    ):
        assert anchor in text, f"novel-outline-planner.md 缺三问合约锚点: {anchor}"
    # 两个详化场合都被覆盖（cluster_001 首块 + 涌现 brief）
    assert "cluster_001 与 `cluster_emergence` 的涌现 brief" in text or \
           ("ecas_cluster_brief" in text and "cluster_emergence" in text), \
        "三问须覆盖 cluster_001 详化与涌现 brief 两个场合"


def test_outline_doc_sync_s11_fields():
    """outline.md 对应段同步锁：三字段 + alignment_review 消费口径 + 只报告不裁决。"""
    text = _OUTLINE_MD.read_text(encoding="utf-8")
    for anchor in ("conflict_stage", "scene_purpose", '"alignment"',
                   "needs-review", "alignment_review", "只报告不裁决",
                   "volume_arc_drift_scanner"):
        assert anchor in text, f"outline.md 缺 S11 同步锚点: {anchor}"


def test_schema_doc_marks_advisory_not_hard_gate():
    """schema _doc 锁：三字段自述 advisory / 不裁决——绝不被后人改成 hard_gate 口径。"""
    props = _scene_items_schema()["properties"]
    assert "advisory" in props["conflict_stage"]["_doc"]
    assert "advisory" in props["alignment"]["_doc"]
    assert "只报告不裁决" in props["alignment"]["_doc"]
    assert "诚实标记" in props["alignment"]["_doc"]


if __name__ == "__main__":
    fails = 0
    for nm in sorted(globals()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    print(f"[volume_arc_scene_alignment] {'ALL OK' if not fails else str(fails) + ' FAIL'}")
    sys.exit(1 if fails else 0)
