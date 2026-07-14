"""changes_io 回归锁 —— cluster changes.json 字数遥测的唯一回写入口。

真机 e2e 暴露的 P0：gen_fixer 改稿覆写 cluster 草稿后**不回写** changes.json 的
`self_eval.ecas_metadata.cjk_actual`，声明字数停在改稿前旧值（实测 12055 vs 真实 12004）。
下游 writer_truth_check 把 declared != actual 计为 writer 说谎（lie），cluster-save-state
step 3 见 lie_count != 0 直接阻断流水线（真机只能人工手改 changes.json 才走得下去）。

本文件锁死：
  ① 改稿后 sync_cjk_actual 把 cjk_actual / word_count_cjk / length_telemetry 对齐磁盘草稿真值
  ② 别名字段（word_count_cjk / final_cjk）不留第二口径
  ③ self_eval 其余内容（waivers / applied_style / polish 留痕）不被回写抹掉
  ④ 契约破损（非 cluster 草稿路径 / 草稿缺失 / changes 缺失）响亮失败，不静默跳过
  ⑤ end-to-end：sync 之后 writer_truth_check 的 cjk lie 归零
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import changes_io  # noqa: E402
import writer_truth_check as wtc  # noqa: E402
from text_metrics import count_cjk  # noqa: E402


_BODY_BEFORE = "他把杯子摔在地上。\n\n瓷片溅到墙角，划出一道白痕。\n"
_BODY_AFTER = "他把杯子摔在地上。\n\n瓷片溅到墙角。\n"  # 改稿后变短（gen_fixer 精修）


def _mk_cluster(root: Path, key: str = "001", body: str = _BODY_BEFORE,
                changes: dict = None) -> Path:
    """造一个 cluster 草稿目录（草稿 + changes.json），返回草稿路径。"""
    d = root / "章节" / f"cluster_{key}_draft"
    d.mkdir(parents=True, exist_ok=True)
    draft = d / f"cluster_{key}_draft.txt"
    draft.write_text(body, encoding="utf-8")
    if changes is not None:
        (d / f"cluster_{key}_changes.json").write_text(
            json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    return draft


def _changes_with_cjk(cjk: int, key: str = "001", **meta_extra) -> dict:
    meta = {"cluster_id": f"cluster_{key}", "cjk_actual": cjk,
            "writer_mode": "claude_draft_gemini_polish_v29"}
    meta.update(meta_extra)
    return {"self_eval": {"ecas_metadata": meta, "waivers": [], "uncertainty_flags": []}}


# ============ ① 改稿后回写对齐磁盘真值 ============
def test_sync_corrects_stale_cjk_after_rewrite():
    """gen_fixer 场景：草稿被改短，changes 里的 cjk_actual 必须跟着改，不留过期值。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stale = count_cjk(_BODY_BEFORE)
        draft = _mk_cluster(root, "001", _BODY_BEFORE, _changes_with_cjk(stale))

        # gen_fixer 原地覆写草稿（更短的修复稿）
        draft.write_text(_BODY_AFTER, encoding="utf-8")
        real = count_cjk(_BODY_AFTER)
        assert real != stale, "fixture 必须真的改变字数，否则测不出 desync"

        out = changes_io.sync_cjk_actual(draft)

        assert out["cjk"] == real
        meta = json.loads(out["changes_path"].read_text(encoding="utf-8"))[
            "self_eval"]["ecas_metadata"]
        assert meta["cjk_actual"] == real, "cjk_actual 必须对齐改稿后的草稿真值"


def test_sync_counts_from_disk_not_from_caller_memory():
    """CJK 一律从磁盘草稿重算——下游 writer_truth_check / splitter 读的就是这份正文。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft = _mk_cluster(root, "003", _BODY_AFTER, _changes_with_cjk(99999, key="003"))
        out = changes_io.sync_cjk_actual(draft)
        assert out["cjk"] == count_cjk(draft.read_text(encoding="utf-8"))


# ============ ② 别名字段不留第二口径 ============
def test_sync_writes_word_count_cjk_alias_same_value():
    """writer_truth_check 认 cjk_actual → word_count_cjk → final_cjk；别名必须同真值。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft = _mk_cluster(root, "002", _BODY_AFTER, _changes_with_cjk(1, key="002"))
        out = changes_io.sync_cjk_actual(draft)
        meta = out["changes"]["self_eval"]["ecas_metadata"]
        assert meta["cjk_actual"] == out["cjk"]
        assert meta["word_count_cjk"] == out["cjk"]


def test_sync_updates_final_cjk_alias_only_when_present():
    """已存在的 final_cjk 别名同刻刷新（不留过期第二口径）；不存在则不凭空造字段。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d1 = _mk_cluster(root, "004", _BODY_AFTER,
                         _changes_with_cjk(1, key="004", final_cjk=1))
        m1 = changes_io.sync_cjk_actual(d1)["changes"]["self_eval"]["ecas_metadata"]
        assert m1["final_cjk"] == count_cjk(_BODY_AFTER)

        d2 = _mk_cluster(root, "005", _BODY_AFTER, _changes_with_cjk(1, key="005"))
        m2 = changes_io.sync_cjk_actual(d2)["changes"]["self_eval"]["ecas_metadata"]
        assert "final_cjk" not in m2


def test_sync_refreshes_length_telemetry_with_cjk():
    """length_telemetry 是 cjk 的派生量 —— 与 cjk 同刻回写，不留旧分。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft = _mk_cluster(root, "006", _BODY_AFTER, _changes_with_cjk(
            1, key="006", length_telemetry={"score": 0.0, "band": [1, 2], "formula": "stale"}))
        out = changes_io.sync_cjk_actual(draft)
        lt = out["changes"]["self_eval"]["ecas_metadata"]["length_telemetry"]
        band = changes_io.length_telemetry_band()
        assert lt["band"] == list(band)
        assert lt["score"] == changes_io.length_telemetry_score(out["cjk"], band)


# ============ ③ 不抹掉 self_eval 其余内容 ============
def test_sync_preserves_other_self_eval_content():
    """回写只动字数遥测：waivers / applied_style / polish 留痕原样保留。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        changes = _changes_with_cjk(1, key="007", polish={"mode": "per_scene_polish_v29"})
        changes["self_eval"]["waivers"] = [{"code": "STYLE_拟声格式", "reason": "纯独白块"}]
        changes["self_eval"]["applied_style"] = {"ending_type": "钩子"}
        draft = _mk_cluster(root, "007", _BODY_AFTER, changes)

        se = changes_io.sync_cjk_actual(draft)["changes"]["self_eval"]
        assert se["waivers"] == [{"code": "STYLE_拟声格式", "reason": "纯独白块"}]
        assert se["applied_style"] == {"ending_type": "钩子"}
        assert se["ecas_metadata"]["polish"] == {"mode": "per_scene_polish_v29"}
        assert se["ecas_metadata"]["writer_mode"] == "claude_draft_gemini_polish_v29"


# ============ ④ 契约破损响亮失败（不静默跳过·不降级） ============
def test_non_cluster_draft_path_rejected():
    """gen_writer / gen_fixer 只在 cluster 草稿层工作（北极星④）——物理章路径必须被拒。"""
    with pytest.raises(changes_io.ChangesIOError):
        changes_io.changes_path_for_draft("章节/第006章/第006章.txt")
    # Claude 亲笔基线稿不是终稿，不该被当草稿回写
    with pytest.raises(changes_io.ChangesIOError):
        changes_io.changes_path_for_draft(
            "章节/cluster_006_draft/cluster_006_draft_claude.txt")


def test_missing_changes_json_raises():
    """cluster 草稿必须有同名 changes.json（CHANGES_MISSING 是 hard_gate 文件契约）。"""
    with tempfile.TemporaryDirectory() as td:
        draft = _mk_cluster(Path(td), "008", _BODY_AFTER, changes=None)
        with pytest.raises(changes_io.ChangesIOError):
            changes_io.sync_cjk_actual(draft)


def test_missing_draft_raises():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "章节" / "cluster_009_draft" / "cluster_009_draft.txt"
        with pytest.raises(changes_io.ChangesIOError):
            changes_io.sync_cjk_actual(missing)


def test_corrupt_changes_json_raises_not_silently_reset():
    """半截/损坏 changes.json 不能被当空对象覆盖（会静默丢掉 writer 的 waivers）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        draft = _mk_cluster(root, "010", _BODY_AFTER, changes=None)
        (draft.parent / "cluster_010_changes.json").write_text("{半截", encoding="utf-8")
        with pytest.raises(changes_io.ChangesIOError):
            changes_io.sync_cjk_actual(draft)


# ============ ⑤ end-to-end：writer_truth_check 的 cjk lie 归零 ============
def test_sync_clears_writer_truth_check_cjk_lie():
    """真机根因回归锁：改稿不回写 → writer_truth_check 判 lie → cluster-save-state step 3 阻断。

    sync_cjk_actual 之后 lie_count 必须归零（管线放行）。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        stale = count_cjk(_BODY_BEFORE)
        draft = _mk_cluster(root, "001", _BODY_BEFORE, _changes_with_cjk(stale))

        draft.write_text(_BODY_AFTER, encoding="utf-8")  # gen_fixer 改稿

        # 不回写 → writer_truth_check 报 cjk 说谎
        before = wtc.truth_check_cluster(root, "cluster_001")
        assert before["lie_count"] >= 1
        assert any(x["field"] == "ecas_metadata.cjk_actual" for x in before["lies_detected"])

        changes_io.sync_cjk_actual(draft)

        after = wtc.truth_check_cluster(root, "cluster_001")
        assert not any(x["field"] == "ecas_metadata.cjk_actual" for x in after["lies_detected"])
        assert after["lie_count"] == 0
        assert after["verdict"] == "pass"
