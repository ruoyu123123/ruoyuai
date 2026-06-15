"""foreshadowing_handoff_scanner.py 审计修复回归测试 — 零依赖范式。

钉死 triage_worth_fixing.json 中 file==foreshadowing_handoff_scanner.py 的唯一修复
（北极星⑤克制：advisory 检测器·确定性数据投影健壮性·不干涉模型创作判断）：

  · [L84] 检测 2 promises setup_cluster 匹配：
    旧代码 `p.get("setup_cluster") == cluster_id` 只认完整串一种形态（cluster_id 在 L33
    已加 "cluster_" 前缀但未零填充）。setup_cluster 客观存在双约定——outline.md 文档示例写
    裸整数（"setup_cluster": 3），save_state.py/migrate_data_model_v2.py 写完整串
    （"cluster_004"）。故整数/裸号形态恒 False → outline 初始化路径下 Tier-1 核心伏笔被
    静默过滤，FORESHADOWING_PHYSICAL_EVIDENCE_MISSING 对核心伏笔失效（false negative）。
    修复：内联 _norm_cid（与 cluster_lookup.normalize_cluster_id 逻辑等价，零 import 依赖，
    subprocess 下不踩 sys.path），两侧归一化后比较。

守护点：
  1. 整数 setup_cluster:1 现在匹配 cluster_001（旧代码漏，promises_in_cluster=0 → 修复后计入）；
  2. 完整串 "cluster_001" 仍匹配（无回归）；裸 "1"/"001" 也匹配；
  3. 不属本 cluster 的（3 / "cluster_3" / None / True）正确不匹配（不引入假阳）；
  4. _norm_cid 单元：bool 不被当 int（True/False → None）；
  5. 缺草稿/缺 brief 安全返回 _fatal（不崩）。

跑法：PYTHONIOENCODING=utf-8 python tests/test_foreshadowing_handoff_scanner_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import foreshadowing_handoff_scanner as fhs  # noqa: E402


# ============================================================
# fixture：在临时项目落 cluster 草稿 + 事件簇 + 伏笔表，跑 scan()
# ============================================================

_DRAFT_TEXT = (
    "他握着那块温润的五色石残片，指腹蹭过裂纹。\n\n"
    "远处共工怒触不周山的传闻仍在街巷流传。\n\n"
    "七窍倒计时的刻痕，又深了一道。\n\n"
    "这一夜过得格外漫长，谁也没说一句话。"
)


def _scan_with_promises(promises, draft_text=_DRAFT_TEXT, cluster_arg="cluster_001"):
    """把 promises 写进临时项目的伏笔表.json，建好对应 cluster 草稿 + 事件簇 brief，
    返回 scan(project, cluster_arg) 的报告 dict。

    草稿目录名与 brief 的 cluster_id 都按 scanner 自身的解析逻辑（L33/L36）从 cluster_arg
    派生——scanner 用 cluster_id.replace("cluster_","") 当 draft key、用 startswith 决定
    cluster_id 串——这样无论入参是 cluster_001 / cluster_1 / 1，draft 路径与 brief 都能被找到，
    让本测试聚焦于「检测 2 的 setup_cluster 匹配」而非 draft 路径本身。
    """
    # 复刻 scanner L33：补 cluster_ 前缀（不零填充）
    cid = cluster_arg if str(cluster_arg).startswith("cluster_") else f"cluster_{cluster_arg}"
    key = cid.replace("cluster_", "")  # scanner L36 draft key
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        # 草稿：章节/cluster_<key>_draft/cluster_<key>_draft.txt（与 scanner L37 对齐）
        draft_dir = proj / "章节" / f"cluster_{key}_draft"
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / f"cluster_{key}_draft.txt").write_text(draft_text, encoding="utf-8")
        # 数据库
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        # 事件簇：brief 的 cluster_id 用归一后的 cid（scanner L50 兼容 cluster_id == cluster_id 或 == key）
        ec = {"clusters": [{"cluster_id": cid, "foreshadowing_to_plant": []}]}
        (db / "事件簇.json").write_text(json.dumps(ec, ensure_ascii=False), encoding="utf-8")
        # 伏笔表
        (db / "伏笔表.json").write_text(
            json.dumps({"promises": promises}, ensure_ascii=False), encoding="utf-8"
        )
        return fhs.scan(proj, cluster_arg)


def _p(pid, setup_cluster, evidence="五色石残片"):
    """构造一条 promise（带 physical_evidence，使其流入检测 2 的分支）。"""
    return {
        "id": pid,
        "setup_cluster": setup_cluster,
        "trigger_condition": {"physical_evidence": evidence},
    }


# ============================================================
# [L84] 核心：整数 setup_cluster 不再被静默过滤
# ============================================================

def test_integer_setup_cluster_now_matched():
    """[L84 核心] setup_cluster:1（整数）→ 旧代码 `1 == 'cluster_001'` 恒 False 被吞，
    promises_in_cluster=0；修复后归一化匹配，计入 1 条。"""
    rep = _scan_with_promises([_p("fs_yishi", 1)])
    assert "_fatal" not in rep, rep
    assert rep["promises_in_cluster"] == 1, rep


def test_full_string_setup_cluster_still_matched():
    """完整串 'cluster_001' 仍匹配（无回归）。"""
    rep = _scan_with_promises([_p("fs_t3", "cluster_001")])
    assert rep["promises_in_cluster"] == 1, rep


def test_bare_numeric_string_setup_cluster_matched():
    """裸号字符串 '1' / '001' 都匹配 cluster_001。"""
    rep1 = _scan_with_promises([_p("fs_a", "1")])
    assert rep1["promises_in_cluster"] == 1, rep1
    rep2 = _scan_with_promises([_p("fs_b", "001")])
    assert rep2["promises_in_cluster"] == 1, rep2


def test_mixed_forms_all_counted():
    """混合形态（整数 1 + 串 'cluster_001' + 裸 '001'）三条全计入；不属本 cluster 的不计。"""
    rep = _scan_with_promises([
        _p("fs_yishi", 1),            # 整数 → 旧代码漏
        _p("fs_gonggong", "cluster_001"),
        _p("fs_qiqiao", "001"),       # 裸号 → 旧代码漏
        _p("fs_other_vol", 3),        # 别的 cluster → 不计
        _p("fs_other_str", "cluster_002"),  # 不计
    ])
    assert rep["promises_in_cluster"] == 3, rep


def test_non_matching_forms_not_counted():
    """不属本 cluster：整数 3 / 'cluster_3' / None / True / 缺键 → 全部不计入（不引入假阳）。"""
    rep = _scan_with_promises([
        _p("fs_3", 3),
        _p("fs_c3", "cluster_3"),
        _p("fs_none", None),
        _p("fs_bool", True),          # bool 不被当 int → None → 不匹配
        {"id": "fs_missing", "trigger_condition": {"physical_evidence": "x"}},  # 缺 setup_cluster
    ])
    assert rep["promises_in_cluster"] == 0, rep


def test_physical_evidence_detection_reaches_for_matched_integer():
    """[L84 终点] 整数形态被匹配后，检测 2 真正执行：物理证据缺失 → emit
    FORESHADOWING_PHYSICAL_EVIDENCE_MISSING（advisory）。旧代码该 advisory 对核心伏笔永不触发。"""
    rep = _scan_with_promises([_p("fs_yishi", 1, evidence="一柄从未在正文出现的青铜古剑")])
    codes = [i["code"] for i in rep["issues"]]
    assert "FORESHADOWING_PHYSICAL_EVIDENCE_MISSING" in codes, rep
    # 整体 + 该 issue 都是 advisory（北极星⑤：顾问非门禁）
    assert rep["gate_level"] == "advisory"
    for i in rep["issues"]:
        if i["code"] == "FORESHADOWING_PHYSICAL_EVIDENCE_MISSING":
            assert i["gate_level"] == "advisory", i


def test_matched_integer_with_present_evidence_no_finding():
    """整数匹配 + 物理证据确在正文 → 计入 promises_with_evidence、不报缺证据（误报控制）。"""
    rep = _scan_with_promises([_p("fs_yishi", 1, evidence="五色石残片")])
    assert rep["promises_in_cluster"] == 1, rep
    assert rep["promises_with_evidence"] == 1, rep
    assert not any(
        i["code"] == "FORESHADOWING_PHYSICAL_EVIDENCE_MISSING" for i in rep["issues"]
    ), rep


# ============================================================
# 非完整串 cluster_id 入参（两侧归一化）
# ============================================================

def test_nonpadded_cluster_arg_both_side_normalized():
    """入参 cluster_id 为非零填充串（cluster_1）时，两侧归一化仍能匹配 setup_cluster:1 与 '001'。
    （生产路径 cluster_id 多为 cluster_001，但 L33 不零填充 → 守护两侧归一化语义。）"""
    rep = _scan_with_promises([_p("fs_a", 1), _p("fs_b", "001")], cluster_arg="cluster_1")
    assert "_fatal" not in rep, rep
    assert rep["promises_in_cluster"] == 2, rep


def test_bare_number_cluster_arg():
    """入参裸号 '1'（L33 补 cluster_ 前缀成 cluster_1）→ 仍匹配 cluster_001 族。"""
    rep = _scan_with_promises([_p("fs_a", "cluster_001")], cluster_arg="1")
    assert rep["promises_in_cluster"] == 1, rep


# ============================================================
# _norm_cid 行为（通过公开行为间接验证：bool 不当 int）
# ============================================================

def test_bool_setup_cluster_treated_as_none():
    """True/False 的 setup_cluster 不被当整数 1/0 → 归一化为 None → 不匹配任何 cluster。
    （防 Python 中 isinstance(True, int) 为 True 的坑）"""
    rep_true = _scan_with_promises([_p("fs_t", True)], cluster_arg="cluster_001")
    assert rep_true["promises_in_cluster"] == 0, rep_true
    # False 同理（即便目标是 cluster_000 也不该被 False 命中）
    rep_false = _scan_with_promises([_p("fs_f", False)], cluster_arg="cluster_000")
    assert rep_false["promises_in_cluster"] == 0, rep_false


# ============================================================
# 健壮性：缺草稿 / 缺 brief 安全返回 _fatal（不崩）
# ============================================================

def test_missing_draft_returns_fatal_not_crash():
    """cluster 草稿不存在 → 返回 _fatal（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        (proj / "_数据库").mkdir(parents=True, exist_ok=True)
        rep = fhs.scan(proj, "cluster_001")
        assert "_fatal" in rep, rep


def test_missing_brief_returns_fatal_not_crash():
    """有草稿但事件簇.json 无对应 cluster brief → 返回 _fatal（不抛异常）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        draft_dir = proj / "章节" / "cluster_001_draft"
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / "cluster_001_draft.txt").write_text(_DRAFT_TEXT, encoding="utf-8")
        db = proj / "_数据库"
        db.mkdir(parents=True, exist_ok=True)
        (db / "事件簇.json").write_text(json.dumps({"clusters": []}, ensure_ascii=False), encoding="utf-8")
        (db / "伏笔表.json").write_text(json.dumps({"promises": []}, ensure_ascii=False), encoding="utf-8")
        rep = fhs.scan(proj, "cluster_001")
        assert "_fatal" in rep, rep


# ============================================================
# 与 cluster_lookup.normalize_cluster_id 一致性（若可 import）
# ============================================================

def test_norm_equivalent_to_cluster_lookup():
    """内联归一化与权威 cluster_lookup.normalize_cluster_id 在关键形态上结论一致：
    1/'1'/'001'/'cluster_001' → cluster_001（匹配）；3/None/True → 不匹配。
    用公开行为（promises_in_cluster）对账，避免触碰私有内联函数。"""
    try:
        import cluster_lookup  # noqa: F401
    except Exception:
        return  # cluster_lookup 不可用则跳过该一致性对账（不影响主修复验证）
    for form in (1, "1", "001", "cluster_001"):
        norm = cluster_lookup.normalize_cluster_id(form)
        assert norm == "cluster_001"
        rep = _scan_with_promises([_p("fs", form)], cluster_arg="cluster_001")
        assert rep["promises_in_cluster"] == 1, (form, rep)
    for form in (3, None, True):
        rep = _scan_with_promises([_p("fs", form)], cluster_arg="cluster_001")
        assert rep["promises_in_cluster"] == 0, (form, rep)


# ============================================================
# 零依赖 __main__ runner
# ============================================================

if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = 0
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"[OK] {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else ""))
    sys.exit(1 if failed else 0)
