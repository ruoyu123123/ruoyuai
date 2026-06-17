"""maybe_judge_consensus.py 确定性单元测试（零 LLM / 零联网）。

钉死该脚本的纯确定性核心逻辑（绝不打 LLM、绝不联网）：
- `load_json`：缺文件给 default / 坏 JSON 给 default / 正常解析。
- `_is_cluster_mode`：CLUSTER_MODE=1 / CLUSTER_ID env 命中即 cluster 模式（无 env 默认 False）。
- `_emotion_value`：emotion.value 取数 / 非 dict / value 非数值 → 0。
- `_cluster_trigger_chapters`：核心算法 —— 每 cluster 取「末章 + climax 章」触发；
  climax 靠 turning_point 关键词（强度 100+emo）/ |emotion|≥7 取最强一章；事件簇兜底末章。
- `_resolve_cluster_chapters`：cluster key（'001'/'cluster_001'）→ 章号列表；range 缺失 → []。
- `is_key_chapter`：cluster 模式触发章命中 / 卷起始卷末 / ending_type 关键收尾 → 返回 reasons。
- `trigger_consensus`：.judge_reports 不存在 / report<2 的 [SKIP] 早退分支（返回 0 不崩）。
- `run_cluster`：range 缺失 → 优雅 [SKIP] 返回 0（plan 主路径绝不能崩）。

被测函数全部真 import 真调用，不 mock 被测逻辑。涉及子进程派发 judge_consensus.py
的分支只测「不满足触发条件时的早退」，不实际起子进程（也就不打 LLM）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import maybe_judge_consensus as mod  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _mk_project() -> Path:
    tmp = Path(tempfile.mkdtemp())
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_progress(proj: Path, data: dict) -> None:
    (proj / "_数据库" / "进度.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_clusters(proj: Path, clusters: list) -> None:
    (proj / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": clusters}, ensure_ascii=False), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# load_json
# ──────────────────────────────────────────────────────────────────────────
def test_load_json_missing_returns_default():
    proj = _mk_project()
    sentinel = {"x": 1}
    got = mod.load_json(proj / "_数据库" / "不存在.json", sentinel)
    if got is not sentinel:
        raise AssertionError(f"缺文件应返回 default，得 {got!r}")


def test_load_json_bad_json_returns_default():
    proj = _mk_project()
    p = proj / "_数据库" / "坏.json"
    p.write_text("{这不是合法json", encoding="utf-8")
    got = mod.load_json(p, "FALLBACK")
    if got != "FALLBACK":
        raise AssertionError(f"坏 JSON 应返回 default，得 {got!r}")


def test_load_json_valid_parses():
    proj = _mk_project()
    p = proj / "_数据库" / "好.json"
    p.write_text(json.dumps({"k": [1, 2, 3]}, ensure_ascii=False), encoding="utf-8")
    got = mod.load_json(p, None)
    if got != {"k": [1, 2, 3]}:
        raise AssertionError(f"合法 JSON 解析错，得 {got!r}")


# ──────────────────────────────────────────────────────────────────────────
# _is_cluster_mode（env 控制）
# ──────────────────────────────────────────────────────────────────────────
def test_is_cluster_mode_env_flags():
    proj = _mk_project()
    saved = {k: os.environ.get(k) for k in ("CLUSTER_MODE", "CLUSTER_ID")}
    try:
        # 清空两个 env → 默认 False（reader 未必判 cluster）
        os.environ.pop("CLUSTER_MODE", None)
        os.environ.pop("CLUSTER_ID", None)
        base = mod._is_cluster_mode(proj)
        if base is not False:
            raise AssertionError(f"无 env 时 _is_cluster_mode 应 False，得 {base!r}")

        # CLUSTER_MODE=1 → True
        os.environ["CLUSTER_MODE"] = "1"
        if mod._is_cluster_mode(proj) is not True:
            raise AssertionError("CLUSTER_MODE=1 应判 cluster 模式")
        os.environ.pop("CLUSTER_MODE", None)

        # CLUSTER_ID 非空 → True
        os.environ["CLUSTER_ID"] = "cluster_001"
        if mod._is_cluster_mode(proj) is not True:
            raise AssertionError("CLUSTER_ID 非空应判 cluster 模式")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ──────────────────────────────────────────────────────────────────────────
# _emotion_value
# ──────────────────────────────────────────────────────────────────────────
def test_emotion_value_branches():
    if mod._emotion_value({"emotion": {"value": 8}}) != 8:
        raise AssertionError("正常 emotion.value 取数错")
    if mod._emotion_value({"emotion": {"value": -9}}) != -9:
        raise AssertionError("负值 emotion.value 取数错")
    # emotion 非 dict → 0
    if mod._emotion_value({"emotion": "high"}) != 0:
        raise AssertionError("emotion 非 dict 应 0")
    # value 非数值 → 0
    if mod._emotion_value({"emotion": {"value": "强"}}) != 0:
        raise AssertionError("value 非数值应 0")
    # 缺 emotion → 0
    if mod._emotion_value({}) != 0:
        raise AssertionError("缺 emotion 应 0")


# ──────────────────────────────────────────────────────────────────────────
# _cluster_trigger_chapters（核心算法）
# ──────────────────────────────────────────────────────────────────────────
def test_cluster_trigger_endchapter_and_climax_keyword():
    """blueprint 一个 cluster：末章 = chapter_range[1]；climax = turning_point 含关键词的章。"""
    proj = _mk_project()
    _write_progress(proj, {
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [1, 5],
                "scene_storyboard": [
                    {"ch": 2, "turning_point": "日常铺垫", "emotion": {"value": 1}},
                    {"ch": 3, "turning_point": "主角觉醒了能力", "emotion": {"value": 2}},
                ],
            }
        }
    })
    triggers = mod._cluster_trigger_chapters(proj)
    # 末章 5
    if 5 not in triggers or not any("末章" in r for r in triggers[5]):
        raise AssertionError(f"末章 5 未触发，triggers={triggers!r}")
    # climax = ch3（turning_point 含 '觉醒'）
    if 3 not in triggers:
        raise AssertionError(f"climax ch3（含'觉醒'）未触发，triggers={triggers!r}")
    if not any("climax" in r and "觉醒" in r for r in triggers[3]):
        raise AssertionError(f"climax 原因未含'觉醒'：{triggers.get(3)!r}")


def test_cluster_trigger_climax_by_strong_emotion():
    """无关键词但 |emotion|>=7 的最强一章也触发 climax。"""
    proj = _mk_project()
    _write_progress(proj, {
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [10, 12],
                "scene_storyboard": [
                    {"ch": 10, "turning_point": "走路", "emotion": {"value": 3}},
                    {"ch": 11, "turning_point": "吃饭", "emotion": {"value": -8}},
                ],
            }
        }
    })
    triggers = mod._cluster_trigger_chapters(proj)
    if 11 not in triggers:
        raise AssertionError(f"强情绪 ch11(|emo|=8) 应触发 climax，triggers={triggers!r}")
    if not any("emotion" in r for r in triggers[11]):
        raise AssertionError(f"climax 原因应标 emotion：{triggers.get(11)!r}")
    # ch10 emotion=3 < 7 且无关键词 → 不应被当 climax 触发
    if 10 in triggers:
        raise AssertionError(f"ch10(emo=3) 不该触发，triggers={triggers!r}")


def test_cluster_trigger_eventcluster_fallback_endchapter():
    """blueprint 缺该末章时，事件簇.json.chapter_range 兜底补末章。"""
    proj = _mk_project()
    _write_progress(proj, {})  # blueprint 空
    _write_clusters(proj, [
        {"cluster_id": "cluster_002", "chapter_range": [6, 9]},
    ])
    triggers = mod._cluster_trigger_chapters(proj)
    if 9 not in triggers or not any("末章" in r for r in triggers[9]):
        raise AssertionError(f"事件簇兜底末章 9 未触发，triggers={triggers!r}")


# ──────────────────────────────────────────────────────────────────────────
# _resolve_cluster_chapters
# ──────────────────────────────────────────────────────────────────────────
def test_resolve_cluster_chapters_expands_range():
    proj = _mk_project()
    _write_clusters(proj, [
        {"cluster_id": "cluster_003", "chapter_range": [7, 10]},
    ])
    # 既支持纯数字 key 也支持 cluster_ 前缀
    if mod._resolve_cluster_chapters(proj, "003") != [7, 8, 9, 10]:
        raise AssertionError("纯数字 key 展开章号错")
    if mod._resolve_cluster_chapters(proj, "cluster_003") != [7, 8, 9, 10]:
        raise AssertionError("cluster_ 前缀 key 展开章号错")


def test_resolve_cluster_chapters_missing_range_returns_empty():
    proj = _mk_project()
    _write_clusters(proj, [
        {"cluster_id": "cluster_004"},  # 无 chapter_range（fluid 未切）
    ])
    if mod._resolve_cluster_chapters(proj, "004") != []:
        raise AssertionError("range 缺失应返回 []")
    # 不存在的 cluster
    if mod._resolve_cluster_chapters(proj, "999") != []:
        raise AssertionError("不存在 cluster 应返回 []")


# ──────────────────────────────────────────────────────────────────────────
# is_key_chapter
# ──────────────────────────────────────────────────────────────────────────
def test_is_key_chapter_volume_and_ending_type():
    """非 cluster 模式：卷起始/卷末 + ending_type 关键收尾命中。"""
    proj = _mk_project()
    saved = {k: os.environ.get(k) for k in ("CLUSTER_MODE", "CLUSTER_ID")}
    try:
        os.environ.pop("CLUSTER_MODE", None)
        os.environ.pop("CLUSTER_ID", None)
        _write_progress(proj, {
            "volumes": [{"vol": 1, "chapter_range": [1, 20]}],
        })
        # 写 ch20 的 changes，ending_type 命中 KEY_ENDING_TYPES（悬念断章）
        ch_dir = proj / "章节" / "第020章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "第020章_changes.json").write_text(
            json.dumps({"self_eval": {"applied_style": {"ending_type": "悬念断章"}}},
                       ensure_ascii=False), encoding="utf-8")

        is_key, reasons = mod.is_key_chapter(proj, 20)
        if not is_key:
            raise AssertionError(f"ch20 应判关键章，reasons={reasons!r}")
        if not any("卷末" in r for r in reasons):
            raise AssertionError(f"应含'卷末'原因：{reasons!r}")
        if not any("ending_type=悬念断章" in r for r in reasons):
            raise AssertionError(f"应含 ending_type 原因：{reasons!r}")

        # 卷起始 ch1
        is_key1, reasons1 = mod.is_key_chapter(proj, 1)
        if not is_key1 or not any("卷起始" in r for r in reasons1):
            raise AssertionError(f"ch1 应判卷起始，reasons={reasons1!r}")

        # 普通章 ch7：无任何触发 → 非关键
        is_key7, reasons7 = mod.is_key_chapter(proj, 7)
        if is_key7:
            raise AssertionError(f"ch7 不该判关键，reasons={reasons7!r}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ──────────────────────────────────────────────────────────────────────────
# trigger_consensus 早退分支（不起子进程 → 不打 LLM）
# ──────────────────────────────────────────────────────────────────────────
def test_trigger_consensus_skip_no_judge_dir():
    proj = _mk_project()  # 无 .judge_reports/
    rc = mod.trigger_consensus(proj, 5)
    if rc != 0:
        raise AssertionError(f".judge_reports 不存在应返回 0，得 {rc!r}")


def test_trigger_consensus_skip_less_than_two_reports():
    proj = _mk_project()
    jd = proj / "_数据库" / ".judge_reports"
    jd.mkdir(parents=True, exist_ok=True)
    # 只放 1 份 report（且不是 consensus）→ <2 早退
    (jd / "ch_005_summarizer.json").write_text("{}", encoding="utf-8")
    rc = mod.trigger_consensus(proj, 5)
    if rc != 0:
        raise AssertionError(f"<2 份 report 应返回 0，得 {rc!r}")
    # 不该生成 consensus 文件（没起合并）
    if (jd / "ch_005_consensus.json").exists():
        raise AssertionError("未达阈值不该生成 consensus 文件")


# ──────────────────────────────────────────────────────────────────────────
# run_cluster 优雅早退（range 缺失 → [SKIP] 返回 0）
# ──────────────────────────────────────────────────────────────────────────
def test_run_cluster_skip_when_no_chapter_range():
    proj = _mk_project()
    _write_clusters(proj, [
        {"cluster_id": "cluster_005"},  # 无 chapter_range（fluid 未切）
    ])
    rc = mod.run_cluster(proj, "005")
    if rc != 0:
        raise AssertionError(f"range 缺失 run_cluster 应优雅返回 0，得 {rc!r}")
