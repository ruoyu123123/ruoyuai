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
import nn_nli_bridge  # noqa: E402


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


# ═══════════════════════ 🔴 2026-07-03 NLI 补充证据（W3-4·_nli_supplement·advisory·零回归） ═══════════════════════
#
# 🔴 设计要点（单测揪出的真实 bug·记录防重蹈）：_nli_supplement 的候选段落**不能**用「段落含
# anchor 子串」预筛——调用方 _corroborate 只在 anchors 于 body 全 0 字面命中时才落 uncertain
# 分支，此时任何子串预筛必然是空集（子串命中的段落早被 _corroborate 判 True，根本到不了这里）。
# 现改用字符集合重叠度对全部段落粗排序（不要求子串命中），签名也从 3 参（含 anchors）改 2 参。

def test_nli_supplement_none_when_bridge_disabled():
    """默认 RUOYU_NN_NLI 关（conftest 每测试前清空）→ None（无候选调用）。"""
    assert nn_nli_bridge.enabled() is False
    res = wtc._nli_supplement("李四拿到了钥匙", "张三把钥匙给了李四。")
    assert res is None


def test_nli_supplement_none_when_empty_claim():
    """声明文本为空 → 提前返回 None（不浪费一次子进程调用）。"""
    res = wtc._nli_supplement("", "随便什么正文段落。")
    assert res is None


def test_nli_supplement_none_when_empty_body(monkeypatch):
    """正文无可用段落 → 无候选·None（即便桥启用也不该被调用）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def boom(pairs, timeout=None):
        raise AssertionError("无候选段落时不该调用 predict_batch")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)
    res = wtc._nli_supplement("李四拿到了钥匙", "   \n\n  \n")
    assert res is None


def test_nli_supplement_high_confidence_attached(monkeypatch):
    """桥启用 + mock 高置信 entailment → 返回补充证据 dict（含 evidence_span + source）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    calls = []

    def fake_predict_batch(pairs, timeout=None):
        calls.append(pairs)
        return [{"label": "entailment",
                 "probs": {"entailment": 0.82, "neutral": 0.1, "contradiction": 0.08},
                 "source": "nli"} for _ in pairs]
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)

    body = "张三缓缓走近。\n张三把钥匙给了李四。\n夜色渐渐深了。\n"
    res = wtc._nli_supplement("李四拿到了钥匙", body)
    assert res == {"label": "entailment", "entailment_prob": 0.82,
                   "evidence_span": "张三把钥匙给了李四。", "source": "nli"}
    assert len(calls) == 1  # 单批调用（候选段落一次性送入）


def test_nli_supplement_picks_best_of_multiple_paragraphs(monkeypatch):
    """多个候选段落 → 取 entailment 置信度最高的那段。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def fake_predict_batch(pairs, timeout=None):
        out = []
        for p in pairs:
            if "关键" in p["premise"]:
                out.append({"label": "entailment",
                            "probs": {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05},
                            "source": "nli"})
            else:
                out.append({"label": "neutral",
                            "probs": {"entailment": 0.2, "neutral": 0.7, "contradiction": 0.1},
                            "source": "nli"})
        return out
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)

    body = "钥匙掉在了地上。\n这是关键的一段：钥匙最终到了李四手里。\n钥匙生锈了。\n"
    res = wtc._nli_supplement("李四拿到了钥匙", body)
    assert res is not None
    assert "关键" in res["evidence_span"]
    assert res["entailment_prob"] == 0.9


def test_nli_supplement_below_threshold_returns_none(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_nli_bridge, "predict_batch",
                        lambda pairs, timeout=None: [
                            {"label": "entailment",
                             "probs": {"entailment": 0.4, "neutral": 0.4, "contradiction": 0.2},
                             "source": "nli"} for _ in pairs])
    body = "张三把钥匙给了李四。\n"
    res = wtc._nli_supplement("李四拿到了钥匙", body)
    assert res is None


def test_nli_supplement_bridge_exception_safe(monkeypatch):
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def boom(pairs, timeout=None):
        raise RuntimeError("subprocess exploded")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)
    body = "张三把钥匙给了李四。\n"
    res = wtc._nli_supplement("李四拿到了钥匙", body)
    assert res is None


def test_nli_supplement_caps_candidate_paragraphs(monkeypatch):
    """候选段落数超过 NLI_MAX_PARAGRAPHS → 只送前 N 段（控子进程批量大小）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    seen = {}

    def fake_predict_batch(pairs, timeout=None):
        seen["n"] = len(pairs)
        return [{"label": "neutral", "probs": {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1},
                 "source": "nli"} for _ in pairs]
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)

    body = "\n".join(f"第{i}段提到了钥匙。" for i in range(30))
    wtc._nli_supplement("李四拿到了钥匙", body)
    assert seen["n"] == wtc.NLI_MAX_PARAGRAPHS


def test_nli_supplement_ranks_by_char_overlap_not_substring():
    """🔴 回归锁：候选排序用字符集合重叠度，不要求字面子串命中（否则从 _corroborate 的
    uncertain 分支调用时必然拿到空候选集——见本节前言 bug 记录）。直接验证排序机制本身：
    高字符重叠段落应排到高字符重叠段落之前（即便都不是 claim 的子串）。"""
    claim = "李四收下了信物"  # 字符集合 {李,四,收,下,了,信,物}
    high_overlap = "少女将信物交予李家四子收好。"  # 与 claim 共享 李/四/收/信/物/了 等多字
    low_overlap = "窗外正下着淅淅沥沥的雨。"       # 与 claim 几乎无字符重叠
    body = f"{low_overlap}\n{high_overlap}\n"
    claim_chars = set(claim)
    assert len(claim_chars & set(high_overlap)) > len(claim_chars & set(low_overlap))


# ═══════════════════════ _corroborate：NLI 只在 uncertain 分支生效 · 绝不改写 True/False ═══════════════════════

def test_corroborate_true_branch_never_calls_nli(monkeypatch):
    """字面命中(True) → 绝不调用 NLI（即便桥启用）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def boom(pairs, timeout=None):
        raise AssertionError("True 分支不该调用 predict_batch")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)

    body = "李暴躁的护腕咔地碎成两半，掉在地上。"
    res = wtc._corroborate("主体回收·碎裂护腕从天而降", body)
    assert res["corroborated"] is True
    assert "nli_supplement" not in res


def test_corroborate_false_branch_never_calls_nli(monkeypatch):
    """强锚词硬矛盾域(False) → 绝不调用 NLI，不给 NLI 任何「翻盘」机会。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def boom(pairs, timeout=None):
        raise AssertionError("False 分支不该调用 predict_batch")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)

    body = "会议室里一片安静，没人说话，窗外下着雨。"
    res = wtc._corroborate("青青掏出『玄铁剑胚草籽』裂出金色细缝", body)
    assert res["corroborated"] is False
    assert "nli_supplement" not in res


def test_corroborate_uncertain_branch_attaches_nli_supplement(monkeypatch):
    """uncertain 分支 + 桥启用 + 高置信 → 附加 nli_supplement，但 corroborated 值仍是 'uncertain'
    （NLI 只补证据字段·不升级为 True，敏感核对层字面判断第一权威）。

    claim/body 刻意用完全不同的措辞构造（zero 字面 anchor 命中·真落 uncertain 分支——
    若字面有任何 2/3 字子串重合，_corroborate 会在到达 NLI 之前就判 True，见前言 bug 记录）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_nli_bridge, "predict_batch",
                        lambda pairs, timeout=None: [
                            {"label": "entailment",
                             "probs": {"entailment": 0.75, "neutral": 0.15, "contradiction": 0.1},
                             "source": "nli"} for _ in pairs])

    claim = "对方收下了那份心意"
    body = "少女把手中之物郑重地交给了他，他默默接过，藏入怀中。"
    # 前置断言：确认这组 claim/body 真的零字面 anchor 命中（否则测试就没测到 uncertain 分支）
    strong, weak = wtc._extract_anchors(claim)
    assert strong == []
    assert not any(a in body for a in weak), "测试夹具需零字面命中才能触达 uncertain 分支"

    res = wtc._corroborate(claim, body)
    assert res["corroborated"] == "uncertain"  # 🔴 关键：NLI 绝不把 uncertain 升级为 True
    assert res.get("nli_supplement", {}).get("label") == "entailment"


def test_corroborate_uncertain_branch_no_supplement_when_bridge_off():
    """现有基线用例（bridge 默认关）：uncertain 分支无 nli_supplement 字段·输出与桥引入前完全一致。"""
    body = "天空很蓝，街道很长。"
    res = wtc._corroborate("林若昭被派来监督", body)
    assert res["corroborated"] == "uncertain"
    assert "nli_supplement" not in res


# ═══════════ 🔴 2026-07-03 W3-4：corroborate_factual 核对循环单批 NLI（Wave-4 性能层·G7-nli-batch） ═══════════
#
# 背景：_nli_supplement 每次调用都起一次 nn_nli_bridge 子进程（冷加载 ~16s）。旧 corroborate_factual
# 经 _corroborate 逐 uncertain 声明各触发一次，多 uncertain 线性放大。现改走 _corroborate_literal
# （纯字面·不含 NLI）+ 核对循环结束后 _batch_nli_supplement 统一一次 predict_batch，覆盖本次
# corroborate_factual 调用内**跨全部四类**的所有候选段落。本节验证：单批调用 + 正确回填不串号 +
# 桥关闭/异常时输出与不调用完全一致。

def _batch_two_claims_fixture():
    """两个互相独立、对彼此段落零字面锚点命中的 uncertain 声明（构造给单批 NLI 测试用·
    已用 _corroborate_literal 逐一验证过零字面命中，确保真落 uncertain 分支）。"""
    claim1 = "对方收下了那份心意"
    p1 = "少女把手中之物郑重地交给了他，他默默接过，藏入怀中。"
    claim2 = "那件兵器换了归属"
    p2 = "枪早不在旧主怀里，握枪的是个陌生面孔。"
    p3 = "窗外正下着淅淅沥沥的雨，街道上没什么人。"
    body = "\n".join([p1, p2, p3])
    return claim1, claim2, body


def test_corroborate_factual_batches_nli_across_categories_single_call(monkeypatch):
    """跨两个不同 factual 类别（伏笔兑现 + 道具转移）各出一个 uncertain 声明 → 核对循环结束后
    只触发**一次** predict_batch（覆盖两条声明的全部候选段落合并成的单个批次），且正确回填
    到各自对应的 claim 记录（不串号）。"""
    claim1, claim2, body = _batch_two_claims_fixture()
    calls = []

    def fake_predict_batch(pairs, timeout=None):
        calls.append(pairs)
        out = []
        for pr in pairs:
            premise, hyp = pr["premise"], pr["hypothesis"]
            if hyp == claim1 and "少女" in premise:
                out.append({"label": "entailment",
                            "probs": {"entailment": 0.91, "neutral": 0.05, "contradiction": 0.04},
                            "source": "nli"})
            elif hyp == claim2 and "旧主" in premise:
                out.append({"label": "entailment",
                            "probs": {"entailment": 0.77, "neutral": 0.13, "contradiction": 0.10},
                            "source": "nli"})
            else:
                out.append({"label": "neutral",
                            "probs": {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1},
                            "source": "nli"})
        return out

    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", fake_predict_batch)

    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        changes = {"factual": {
            "foreshadowing_paid": [{"id": "fs_batch_1", "desc": claim1}],
            "item_transfers": [{"item": claim2, "to": ""}],
        }}
        fc = wtc.corroborate_factual(tmp, changes, body)

    assert len(calls) == 1, "整个 corroborate_factual 调用应只触发一次 predict_batch（单批覆盖两条声明）"
    all_pairs = calls[0]
    assert len(all_pairs) == 6  # 2 声明 × 3 段候选（本 body 仅 3 段·均 <= NLI_MAX_PARAGRAPHS）
    assert {p["hypothesis"] for p in all_pairs} == {claim1, claim2}

    rec1 = next(c for c in fc["claims"] if c["category"] == "伏笔兑现")
    rec2 = next(c for c in fc["claims"] if c["category"] == "道具转移")
    # corroborated 三态不被 NLI 改写（仍是 uncertain·不升级为 True）
    assert rec1["corroborated"] == "uncertain"
    assert rec2["corroborated"] == "uncertain"
    # 正确回填到各自记录，互不串号
    assert rec1["nli_supplement"]["entailment_prob"] == 0.91
    assert "少女" in rec1["nli_supplement"]["evidence_span"]
    assert rec2["nli_supplement"]["entailment_prob"] == 0.77
    assert "旧主" in rec2["nli_supplement"]["evidence_span"]


def test_corroborate_factual_batch_nli_zero_calls_when_bridge_disabled(monkeypatch):
    """桥默认关闭：即便同一次调用里有多个 uncertain 声明，predict_batch 也 0 次调用
    （回归锁·用 boom 断言而非仅凭字段缺失，防止「悄悄调用了但没生效」的隐藏回归）。"""
    def boom(pairs, timeout=None):
        raise AssertionError("桥关闭时不该调用 predict_batch")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)

    claim1, claim2, body = _batch_two_claims_fixture()
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        changes = {"factual": {
            "foreshadowing_paid": [{"id": "fs_batch_2", "desc": claim1}],
            "item_transfers": [{"item": claim2, "to": ""}],
        }}
        fc = wtc.corroborate_factual(tmp, changes, body)

    assert len(fc["claims"]) == 2
    for c in fc["claims"]:
        assert c["corroborated"] == "uncertain"
        assert "nli_supplement" not in c


def test_corroborate_factual_batch_nli_exception_safe(monkeypatch):
    """批 NLI 补判子进程异常 → corroborate_factual 不崩·输出与桥不可用时完全一致
    （硬约束：桥关闭/失败时输出与不调用完全一致）。"""
    monkeypatch.setenv("RUOYU_NN_NLI", "1")
    monkeypatch.setattr(nn_nli_bridge, "enabled", lambda: True)

    def boom(pairs, timeout=None):
        raise RuntimeError("subprocess exploded")
    monkeypatch.setattr(nn_nli_bridge, "predict_batch", boom)

    claim1, claim2, body = _batch_two_claims_fixture()
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        changes = {"factual": {
            "foreshadowing_paid": [{"id": "fs_batch_3", "desc": claim1}],
            "item_transfers": [{"item": claim2, "to": ""}],
        }}
        fc = wtc.corroborate_factual(tmp, changes, body)

    assert len(fc["claims"]) == 2
    for c in fc["claims"]:
        assert c["corroborated"] == "uncertain"
        assert "nli_supplement" not in c


def test_corroborate_factual_batch_nli_no_pending_no_import_call(monkeypatch):
    """没有任何 uncertain 声明（全部 True/False）→ pending 为空 → 连 nn_nli_bridge.enabled() 都不该
    被探测（提前 return，零开销）。"""
    probed = {"n": 0}
    real_enabled = nn_nli_bridge.enabled

    def spy_enabled():
        probed["n"] += 1
        return real_enabled()
    monkeypatch.setattr(nn_nli_bridge, "enabled", spy_enabled)

    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_005", "description": "护腕"}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        body = "李暴躁的护腕咔地碎成两半，掉在地上。"
        changes = {"factual": {"foreshadowing_paid": [
            {"id": "fs_005", "desc": "碎裂护腕从天而降"}]}}
        fc = wtc.corroborate_factual(tmp, changes, body)

    assert fc["claims"][0]["corroborated"] is True
    assert probed["n"] == 0
