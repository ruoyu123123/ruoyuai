"""阶段1：叙事节奏组 A1-A5 聚合测试（consolidate.aggregate_rhythm·纯函数·零 LLM）。

钉死 cluster 级序列节奏聚合：节拍转移矩阵/场景翻转率/张力曲线统计/钩子分布/推进密度。
读 cluster_*_surface.json（cluster 级·不逐章重复）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402


def _mk_surfaces(root: Path, clusters: list[dict]) -> Path:
    """造 蒸馏进度/cluster_NNN_surface.json（每份带 dim49-53）。"""
    proj = root / "测试书"
    dist = proj / "蒸馏进度"
    dist.mkdir(parents=True)
    for i, dims in enumerate(clusters, 1):
        (dist / f"cluster_{i:03d}_surface.json").write_text(
            json.dumps({"cluster_id": f"cluster_{i:03d}",
                        "qualitative_dims": dims}, ensure_ascii=False),
            encoding="utf-8")
    return proj


def test_beat_transition_matrix():
    """A1：节拍序列 → 相邻转移概率矩阵（归 4 大类）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [
            {"dim49_节拍序列": ["推进-灾难", "缓冲-反应", "缓冲-决定", "揭示"]},
            {"dim49_节拍序列": ["推进-冲突", "推进-灾难", "缓冲-反应"]},
        ])
        r = cap.aggregate_rhythm(proj)
        m = r["beat_transition_matrix"]
        # 推进→缓冲 出现 2 次（cluster1 灾难→反应 + cluster2 灾难→反应）
        assert "推进→缓冲" in m
        assert m["推进→缓冲"]["count"] == 2
        # 缓冲→缓冲（反应→决定）+ 缓冲→揭示 + 推进→推进
        assert "缓冲→揭示" in m
        assert "推进→推进" in m
        assert 0 < m["推进→缓冲"]["prob"] <= 1


def test_scene_turn_ratio():
    """A2：场景翻转率 = 翻转场景 / 总场景。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [
            {"dim50_场景翻转": [{"翻转": True}, {"翻转": False}, {"翻转": "是"}]},
            {"dim50_场景翻转": [{"翻转": True}]},
        ])
        r = cap.aggregate_rhythm(proj)
        assert r["scene_turn_ratio"] == round(3 / 4, 3)  # 3 翻转 / 4 总


def test_tension_trajectory_no_premature_resolution():
    """A3：悲剧型张力撑到结尾（后段保持度高）vs 过早收束（后段塌）。"""
    with tempfile.TemporaryDirectory() as d:
        # 张力一路高到结尾（专业·后段保持度高）
        proj = _mk_surfaces(Path(d), [
            {"dim51_张力曲线": [{"pct": 10, "tension": 3, "valence": "-"},
                              {"pct": 50, "tension": 7, "valence": "-"},
                              {"pct": 90, "tension": 9, "valence": "-"}]},
        ])
        r = cap.aggregate_rhythm(proj)
        tt = r["tension_trajectory"]
        assert tt["post_climax_retention"] >= 0.9  # 后段(90%)=9/峰值9=1.0
        assert tt["dominant_emotion_shape"] == "升升"  # 一路上升(悲剧张力累积)


def test_tension_premature_resolution_low_retention():
    """A3 反面：张力中段就塌（LLM 过早收束）→ 后段保持度低。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [
            {"dim51_张力曲线": [{"pct": 10, "tension": 4},
                              {"pct": 50, "tension": 9},
                              {"pct": 90, "tension": 2}]},  # 后段塌
        ])
        r = cap.aggregate_rhythm(proj)
        assert r["tension_trajectory"]["post_climax_retention"] < 0.3  # 2/9


def test_hook_distribution_and_payoff():
    """A4：钩子类型分布 + 兑现章距中位数。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [
            {"dim52_钩子兑现": [{"类型": "对话断句钩", "兑现章距": 2},
                              {"类型": "情绪高潮钩", "兑现章距": 5}]},
            {"dim52_钩子兑现": [{"类型": "对话断句钩", "兑现章距": 3}]},
        ])
        r = cap.aggregate_rhythm(proj)
        assert r["hook_type_distribution"]["对话断句钩"] == 2
        assert r["hook_payoff_gap_median"] == 3  # [2,3,5] 中位


def test_propulsion_density_bucket():
    """A5：推进密度自由文本 → 枚举桶。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [
            {"dim53_推进密度": "三章一爆·信息密集释放"},
            {"dim53_推进密度": "前松后紧·后期加速"},
        ])
        r = cap.aggregate_rhythm(proj)
        assert "三章一爆" in r["propulsion_density"]
        assert "前松后紧" in r["propulsion_density"]


def test_empty_surfaces_safe():
    """无 dim49-53（旧 surface）→ 返回空·不崩。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_surfaces(Path(d), [{"dim1_句式节奏": "短句"}])
        assert cap.aggregate_rhythm(proj) == {}


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[narrative_rhythm_aggregate] {passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
