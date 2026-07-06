# -*- coding: utf-8 -*-
"""save_state 场景级 Appraisal Beat 回库测试 — 🔴 2026-06-29 chain-of-emotion。

钉死 save_state.cmd_apply_appraisal_beats：novel-summarizer 读整 cluster 正文按 Scherer CPM/OCC
评价链推理产 summary.appraisal_beats → 确定性 append 进 叙事节拍器.json.appraisal_beats
（把情绪从 prose 一句提示升维为结构化可追踪 STATE · SOTA arXiv:2309.05076 + CAPE arXiv:2410.14145）：
  · 只填 active cluster（fluid·显式标了别的 cluster 的 beat 跳过·缺/本 cluster 强制归本 cluster_id）
  · 幂等·去重：按 (cluster_id, scene_idx, focal_character)·同 scene 多 focal 各保一条
  · scene_idx 归一为 int·focal_character 必填（缺则跳）
  · 硬失败：缺 summary / summary 无 appraisal_beats / 叙事节拍器缺坏 → return 2
  · 空 appraisal_beats=[] 表示 producer 明确无情绪拍，幂等 return 0
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402
import cluster_lookup  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_project(tmp: Path, *, pacer=None, summary=None, key="001") -> Path:
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    # 事件簇供 cluster 反查（非必需·cmd 内只用 normalize_cluster_id）
    (db / "事件簇.json").write_text(json.dumps(
        {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]},
        ensure_ascii=False), encoding="utf-8")
    if pacer is not None:
        (db / "叙事节拍器.json").write_text(json.dumps(pacer, ensure_ascii=False), encoding="utf-8")
    if summary is not None:
        cid = cluster_lookup.normalize_cluster_id(key)
        (db / ".wal" / f"{cid}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return tmp


def _pacer(db_root):
    return json.loads((Path(db_root) / "_数据库" / "叙事节拍器.json").read_text(encoding="utf-8"))


_SKELETON_PACER = {"schema_version": "v27", "rhythm_profile": "混合",
                   "beat_targets": [], "appraisal_beats": []}


def _beat(scene_idx, focal, cluster_id="cluster_001", **kw):
    b = {"cluster_id": cluster_id, "scene_idx": scene_idx, "focal_character": focal,
         "trigger_event": "他发现遗嘱是未来的自己写的",
         "appraisal": {"relevance": "高", "congruence": "-", "certainty": "悬而未决",
                       "coping_potential": "无力", "accountability": "circumstance",
                       "norm_compat": "违背"},
         "prospect": {"type": "fear", "resolved_to": "fears_confirmed"},
         "derived_emotion": "被命运提前剧透时那种地面塌陷的眩晕",
         "behavior_externalization": "把信纸边角一遍遍抠出毛边",
         "vad_bin": {"valence": "L", "arousal": "H", "dominance": "L"}}
    b.update(kw)
    return b


# ═══════════════════════ 回填 ═══════════════════════

def test_append_appraisal_beats():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"summary": "x", "appraisal_beats": [_beat(2, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1
        assert ab[0]["focal_character"] == "C_PROT"
        assert ab[0]["scene_idx"] == 2
        assert ab[0]["cluster_id"] == "cluster_001"
        # STATE 字段完整透传
        assert ab[0]["derived_emotion"].startswith("被命运")
        assert ab[0]["vad_bin"]["arousal"] == "H"


def test_idempotent_no_dup():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [_beat(2, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0  # re-apply
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1, "re-apply 不得重复 append"


def test_same_scene_multiple_focal_both_kept():
    """同 scene 多个 focal 角色各一拍 → 去重键含 focal·两条都留。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [
                               _beat(2, "C_PROT"), _beat(2, "C_AMY")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 2
        assert {b["focal_character"] for b in ab} == {"C_PROT", "C_AMY"}


def test_only_active_cluster_other_skipped():
    """显式标别的 cluster 的 beat → 跳过（fluid·不回填非本 cluster）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [
                               _beat(0, "C_PROT", cluster_id="cluster_001"),
                               _beat(1, "C_PROT", cluster_id="cluster_005")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1
        assert ab[0]["cluster_id"] == "cluster_001"


def test_cluster_id_forced_when_absent():
    """beat 缺 cluster_id → 强制归本 active cluster。"""
    with tempfile.TemporaryDirectory() as d:
        b = _beat(0, "C_PROT")
        b.pop("cluster_id")
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [b]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1 and ab[0]["cluster_id"] == "cluster_001"


def test_scene_idx_string_normalized_to_int():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [_beat("3", "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert ab[0]["scene_idx"] == 3 and isinstance(ab[0]["scene_idx"], int)


def test_missing_focal_character_skipped():
    with tempfile.TemporaryDirectory() as d:
        b = _beat(0, "")
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [b, _beat(1, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1 and ab[0]["focal_character"] == "C_PROT"


def test_appends_to_existing_beats_no_overwrite():
    """已有 appraisal_beats（前 cluster 留的）→ append 不覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        prior = _beat(0, "C_OLD", cluster_id="cluster_001")
        pacer = dict(_SKELETON_PACER); pacer["appraisal_beats"] = [prior]
        root = _mk_project(Path(d), pacer=pacer,
                           summary={"appraisal_beats": [_beat(2, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 2
        assert {b["focal_character"] for b in ab} == {"C_OLD", "C_PROT"}


# ═══════════════════════ 默认安全 / 向后兼容 ═══════════════════════

def test_no_appraisal_beats_in_summary_hard_fails():
    """summary 无 appraisal_beats → required 字段缺失，return 2。"""
    with tempfile.TemporaryDirectory() as d:
        pacer = dict(_SKELETON_PACER)
        root = _mk_project(Path(d), pacer=pacer, summary={"summary": "只有摘要没情绪拍"})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 2
        assert _pacer(root)["appraisal_beats"] == []


def test_empty_appraisal_beats_list_noop():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": []})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        assert _pacer(root)["appraisal_beats"] == []


def test_missing_summary_file_hard_fails():
    """summary.json 不存在（summarizer 未跑）→ return 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER), summary=None)
        assert ss.cmd_apply_appraisal_beats(root, "001") == 2
        assert _pacer(root)["appraisal_beats"] == []


def test_missing_pacer_file_hard_fails():
    """叙事节拍器.json 缺 → required 状态库不可用，return 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=None,
                           summary={"appraisal_beats": [_beat(0, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 2
        assert not (Path(root) / "_数据库" / "叙事节拍器.json").exists()


def test_broken_pacer_file_hard_fails():
    """叙事节拍器.json 损坏 → return 2。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [_beat(0, "C_PROT")]})
        (Path(root) / "_数据库" / "叙事节拍器.json").write_text("{ broken", encoding="utf-8")
        assert ss.cmd_apply_appraisal_beats(root, "001") == 2


def test_pacer_without_appraisal_beats_key_initializes():
    """旧 pacer 无 appraisal_beats key（建库早于本特性）→ setdefault 初始化后 append。"""
    with tempfile.TemporaryDirectory() as d:
        pacer = {"schema_version": "v27", "rhythm_profile": "混合", "beat_targets": []}
        root = _mk_project(Path(d), pacer=pacer,
                           summary={"appraisal_beats": [_beat(0, "C_PROT")]})
        assert ss.cmd_apply_appraisal_beats(root, "001") == 0
        ab = _pacer(root)["appraisal_beats"]
        assert len(ab) == 1


def test_cluster_prefixed_key_accepted():
    """传 cluster_001（带前缀）也能定位 summary + 归一。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pacer=dict(_SKELETON_PACER),
                           summary={"appraisal_beats": [_beat(0, "C_PROT")]}, key="001")
        assert ss.cmd_apply_appraisal_beats(root, "cluster_001") == 0
        assert len(_pacer(root)["appraisal_beats"]) == 1


# ═══════════════════════ 单一真理源：skeleton 含 appraisal_beats ═══════════════════════

def test_skeleton_emits_appraisal_beats():
    """scaffold emit 的 叙事节拍器.json 必含 appraisal_beats[]（单一真理源·subsystem_skeletons）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scaffold_subsystems", _SCRIPTS / "scaffold_subsystems.py")
    scaf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scaf)
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "_数据库"
        scaf.cmd_emit(["--db-dir", str(db)])
        pacer = json.loads((db / "叙事节拍器.json").read_text(encoding="utf-8"))
        assert "appraisal_beats" in pacer and pacer["appraisal_beats"] == []
        assert "_appraisal_beats_schema" in pacer  # schema hint 在册


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
