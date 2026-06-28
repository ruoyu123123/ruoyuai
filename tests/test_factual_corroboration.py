"""C10 声明-vs-正文校验 + SYS-3 伏笔字面 trace 回归测试（🔴 2026-06-27）。

被测：core/scripts/writer_truth_check.py 新增的宽松证据匹配器与 factual 段——
  · _extract_anchors —— 『』「」“”【】 强锚词 + 2-4 字 CJK 弱锚词抽取
  · _corroborate     —— True（有痕迹）/ False（强锚词全 0 命中=硬矛盾）/ "uncertain"（弱信号）
  · corroborate_factual —— 遍历 factual 四类（伏笔兑现/角色死亡/道具转移/秘密揭示）+ SYS-3 no_trace
  · truth_check_cluster / write_back —— 端到端把 factual_corroboration 并入 report + 回写

北极星护栏验证：匹配器宽松（措辞不同字面不判违 · 只有完全无任何痕迹才算硬矛盾）·
全 advisory（不进 lies/lie_count·不改退出码）·uncertain → FACTUAL_CLAIM_UNCORROBORATED。

零依赖：标准库 + tempfile 临时项目，绝不写真项目。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import chapter_io as cio  # noqa: E402
import writer_truth_check as wtc  # noqa: E402


# ═══════════════════════ _extract_anchors ═══════════════════════

def test_extract_anchors_strong_and_weak():
    strong, weak = wtc._extract_anchors("他掏出『青青剑胚』，又看了眼【国运调度】面板")
    assert "青青剑胚" in strong
    assert "国运调度" in strong
    # 弱锚词为 2-4 字 CJK 片段
    assert all(2 <= len(w) <= 4 for w in weak)


def test_extract_anchors_empty():
    assert wtc._extract_anchors("") == ([], [])
    # 纯标点/英文数字 → 无 CJK 弱锚词、无引号强锚词
    s, w = wtc._extract_anchors("×5 → ×7 !!!")
    assert s == [] and w == []


# ═══════════════════════ _corroborate 三态 ═══════════════════════

def test_corroborate_true_when_anchor_in_body():
    """任一锚词命中正文 → True（措辞不同字面不判违·宽松）。"""
    body = "李暴躁的护腕咔地碎成两半，掉在地上。"
    res = wtc._corroborate("主体回收·碎裂护腕从天而降", body)
    assert res["corroborated"] is True
    assert res["evidence_span"]  # 有证据片段


def test_corroborate_false_when_strong_anchor_absent():
    """有强锚词（具体专名/信物）但正文完全无痕迹 → False（硬矛盾域）。"""
    body = "会议室里一片安静，没人说话，窗外下着雨。"
    res = wtc._corroborate("青青掏出『玄铁剑胚草籽』裂出金色细缝", body)
    assert res["corroborated"] is False
    assert res["evidence_span"] == ""


def test_corroborate_uncertain_when_only_weak_miss():
    """只有弱锚词且全 0 命中（无强锚词）→ uncertain（弱信号·不硬判违）。"""
    body = "天空很蓝，街道很长。"
    res = wtc._corroborate("林若昭被派来监督", body)  # 无引号强锚词
    assert res["corroborated"] == "uncertain"


def test_corroborate_uncertain_when_no_extractable_anchor():
    res = wtc._corroborate("×5 → ×7", "随便什么正文")
    assert res["corroborated"] == "uncertain"
    assert res["anchors"] == []


# ═══════════════════════ corroborate_factual 四类 ═══════════════════════

def _mk_project(tmp: Path, fs: dict) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "伏笔表.json").write_text(json.dumps(fs, ensure_ascii=False), encoding="utf-8")
    return tmp


def _empty_fs():
    return {"promises": [], "deadlines": [], "pledges": [], "secrets": []}


def test_factual_foreshadowing_no_trace_sys3():
    """SYS-3：声明 paid 但锚词集非空且全 0 命中 → foreshadowing_paid_no_trace（advisory）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_004", "description": "青青玄铁剑胚里冒出青色草汁"}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        changes = {"factual": {"foreshadowing_paid": [
            {"id": "fs_004", "desc": "『青青剑胚』裂出金色细缝·本人金色光环异化"}]}}
        body = "整章都在写党为国跟老赵的合同金融博弈，没有半点与那把武器有关的内容。"
        fc = wtc.corroborate_factual(tmp, changes, body)
        trace_ids = [t["fs_id"] for t in fc["foreshadowing_trace"]]
        assert "fs_004" in trace_ids
        # 同时进 claims（corroborated False/uncertain）
        assert any(c.get("fs_id") == "fs_004" for c in fc["claims"])


def test_factual_foreshadowing_corroborated_no_advisory():
    """paid 锚词在正文留痕 → corroborated True·不进 no_trace·不发 advisory。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_003", "description": "李暴躁砸培罗神坛·碎裂护腕"}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        changes = {"factual": {"foreshadowing_paid": [
            {"id": "fs_003", "desc": "主体回收·碎裂护腕从天而降"}]}}
        body = "李暴躁一拳砸在培罗神坛上，护腕咔地碎成两半。"
        fc = wtc.corroborate_factual(tmp, changes, body)
        assert fc["foreshadowing_trace"] == []
        c = next(c for c in fc["claims"] if c.get("fs_id") == "fs_003")
        assert c["corroborated"] is True


def test_factual_item_transfer_and_secret_reveal():
    """道具转移 + 秘密揭示两类纳入 corroboration。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [], "deadlines": [], "pledges": [],
            "secrets": [{"id": "sc_003", "secret": "1985 年特勤五处内部绝密条例第十三条"}],
        })
        changes = {"factual": {
            "item_transfers": [{"item": "碎裂护腕", "to": "魏无咎"}],
            "foreshadowing_actions": [
                {"category": "secret", "type": "reveal", "id": "sc_003",
                 "how": "1985 照片揭示绝密条例"}],
        }}
        body = ("半截碎裂护腕从天而降，砸在魏无咎脚边。"
                "照片背面写着 1985 年特勤五处内部绝密条例第十三条。")
        fc = wtc.corroborate_factual(tmp, changes, body)
        cats = {c["category"] for c in fc["claims"]}
        assert "道具转移" in cats
        assert "秘密揭示" in cats
        # 两条都该有正文留痕 → True
        assert all(c["corroborated"] is True for c in fc["claims"])


def test_factual_uncorroborated_advisory_emitted():
    """uncertain 类 → 发 FACTUAL_CLAIM_UNCORROBORATED advisory（放本 report·可豁免）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        changes = {"factual": {"item_transfers": [{"item": "某物", "to": "某人"}]}}
        body = "完全不相关的正文内容。"
        fc = wtc.corroborate_factual(tmp, changes, body)
        codes = [a["code"] for a in fc["advisories"]]
        assert "FACTUAL_CLAIM_UNCORROBORATED" in codes


def test_corroborate_factual_empty_no_crash():
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        fc = wtc.corroborate_factual(tmp, {"factual": {}}, "随便正文")
        assert fc["claims"] == []
        assert fc["advisories"] == []
        assert fc["foreshadowing_trace"] == []


# ═══════════════════════ 端到端：truth_check_cluster 并入 factual_corroboration ═══════════════════════

def _write_chapter(tmp: Path, ch: int, body: str, changes: dict):
    cio.write_body(tmp, ch, body)
    cio.write_changes(tmp, ch, changes)


def test_truth_check_cluster_includes_factual_corroboration():
    """truth_check_cluster 报告含 factual_corroboration 段（cluster 整草稿视野）·
    且不污染 lies/lie_count（advisory 正交）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_003", "description": "碎裂护腕"}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        body1 = "第014章 起\n李暴躁砸神坛，护腕咔地碎成两半。"
        body2 = "第015章\n半截碎裂护腕从天而降。"
        changes = {
            "factual": {"foreshadowing_paid": [{"id": "fs_003", "desc": "主体回收·碎裂护腕"}]},
            "self_eval": {"applied_style": {}},
        }
        _write_chapter(tmp, 14, body1, changes)
        _write_chapter(tmp, 15, body2, changes)
        rep = wtc.truth_check_cluster(tmp, [14, 15])
        assert "factual_corroboration" in rep
        fc = rep["factual_corroboration"]
        # 护腕在全 cluster 拼接 body 留痕 → corroborated True·no_trace 空
        assert fc["foreshadowing_trace"] == []
        c = next(c for c in fc["claims"] if c.get("fs_id") == "fs_003")
        assert c["corroborated"] is True
        # advisory 不计撒谎
        assert rep["lie_count"] == 0


def test_truth_check_cluster_no_trace_is_advisory_not_lie():
    """声明兑现但 cluster 整草稿 0 痕迹 → 记 no_trace（advisory）·lie_count 仍为 0。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_004", "description": "青青玄铁剑胚草汁"}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        body = "第014章 起\n整章只写合同与金融博弈，没有半点与那把兵器有关的内容。"
        changes = {
            "factual": {"foreshadowing_paid": [
                {"id": "fs_004", "desc": "『青青剑胚』裂出金色细缝兑现"}]},
            "self_eval": {"applied_style": {}},
        }
        _write_chapter(tmp, 14, body, changes)
        rep = wtc.truth_check_cluster(tmp, [14])
        # body 与声明锚词零字面重叠（见上）→ 必触发 no_trace
        fc = rep["factual_corroboration"]
        assert any(t["fs_id"] == "fs_004" for t in fc["foreshadowing_trace"])
        assert rep["lie_count"] == 0  # advisory 不升级为撒谎
