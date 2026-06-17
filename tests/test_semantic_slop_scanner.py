#!/usr/bin/env python3
"""semantic_slop_scanner 专属回归测试（零依赖·标准库 only·Windows）。

【与已有间接覆盖的分工】
- tests/test_semantic_slop_dialogue_tag.py 已测 B+9 scan_dialogue_tag_density
  + ALL_CHECKS 含 dialogue_tag_density。本文件**不重复**这部分。
- tests/test_audit_hub_aggregation.py 仅把 "semantic_slop_scanner.py" 列进
  _ALL_SCANNER_NAMES（无行为断言）。

本文件聚焦尚未被覆盖的核心确定性逻辑：
  · 句/段切分 helper（split_sentences / split_paragraphs / load_chapter_body）
  · B+1 metaphor_explain（含跨对话引号边界豁免分支）
  · B+2 aphorism（短句+抽象大词+断言词·独立成段加权·hits>=2 才报）
  · B+3 neg_parallel（不是…而是 / 不仅…而且·>=2 才报）
  · B+4 copula_avoid（作为…的存在·>=2 才报）
  · B+5 fake_range（一句 >=2 个从…到…·CLUSTER_MODE 阈值 1→3）
  · B+6 over_hedge（限定词叠用 >=2）
  · B+7 forced_triple（顿号三连·>=3 才报）
  · B+8 tag_synonym_cycle（标签变体 >=6 才报）
  · scan_chapter 报告契约 + checks 子集选择 + 缺章 _fatal
  · CLI 退出码契约（0 干净 / 1 advisory / 2 缺章·非整数章号）
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import semantic_slop_scanner as S  # noqa: E402


# ---------- helper: 在临时项目里铺一章正文（走 chapter_io 标准布局） ----------

def _mk_chapter(tmp: Path, ch: int, body: str) -> None:
    d = tmp / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{ch:03d}章.txt").write_text(body, encoding="utf-8")


# ========================= 切分 helper =========================

def test_split_sentences_keeps_units_and_strips():
    """按句末标点切句·去空白·空段丢弃。"""
    s = S.split_sentences("第一句。  第二句！\n第三句？")
    assert s == ["第一句。", "第二句！", "第三句？"], s


def test_split_paragraphs_by_blank_line():
    """空行切段·strip·丢空段。"""
    p = S.split_paragraphs("段一。\n\n  段二。  \n\n\n段三。")
    assert p == ["段一。", "段二。", "段三。"], p


def test_load_chapter_body_strips_title_line():
    """load_chapter_body 读 txt 并剔除「第N章」标题行。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_chapter(tmp, 1, "第1章 风起\n这是正文第一段。\n\n第二段在这。")
        body = S.load_chapter_body(tmp, 1)
        assert "第1章" not in body, body
        assert "这是正文第一段。" in body


def test_load_chapter_body_missing_returns_none():
    """章节正文不存在 → None（绝不抛出·让上层转 _fatal）。"""
    with tempfile.TemporaryDirectory() as d:
        assert S.load_chapter_body(Path(d), 99) is None


# ========================= B+1 metaphor_explain =========================

def test_metaphor_explain_hit():
    """比喻标记后紧跟解释标记 → 命中报警。"""
    sents = ["他像困兽，这意味着他已无路可退。"]
    r = S.scan_metaphor_explain(sents)
    assert r["hits_count"] == 1, r
    assert r["warning"]
    assert r["gate_level"] == "advisory"


def test_metaphor_explain_dialogue_boundary_waiver():
    """比喻与解释之间跨对话引号边界 → 豁免不报（防跨对话误报分支）。"""
    sents = ['他像一头困兽，“这意味着什么？”她问。']
    r = S.scan_metaphor_explain(sents)
    assert r["hits_count"] == 0, r
    assert r["warning"] is None


def test_metaphor_explain_no_explain_clean():
    """只有比喻没有解释 → 干净（信任读者）。"""
    r = S.scan_metaphor_explain(["夜色像一张铺开的黑绸。"])
    assert r["hits_count"] == 0
    assert r["warning"] is None


# ========================= B+2 aphorism =========================

def test_aphorism_needs_two_hits_to_warn():
    """短句+抽象大词+断言词=金句嫌疑·hits>=2 才出 warning·独立成段加权计数。"""
    paras = ["人生从来都是孤独。", "命运终究无法选择。"]
    sents = S.split_sentences("\n".join(paras))
    r = S.scan_aphorism(paras, sents)
    assert r["hits_count"] >= 2, r
    assert r["standalone_count"] >= 1     # 两句各自独立成段
    assert r["warning"]


def test_aphorism_single_hit_no_warn():
    """只有一处金句 → hits=1 不报（阈值 >=2）。"""
    paras = ["人生从来都是孤独。", "他走进了那间空荡的屋子，把门轻轻带上。"]
    sents = S.split_sentences("\n".join(paras))
    r = S.scan_aphorism(paras, sents)
    assert r["hits_count"] == 1, r
    assert r["warning"] is None


def test_aphorism_long_sentence_skipped():
    """超长句（>28 字）即便含抽象大词+断言词也不算金句（金句靠短）。"""
    long_s = "人生从来都是一场漫长而琐碎的跋涉" + "途中遇到的人来了又走始终没有谁能真正停留下来陪你到最后一刻"
    r = S.scan_aphorism([long_s], [long_s + "。"])
    assert r["hits_count"] == 0, r


# ========================= B+3 neg_parallel =========================

def test_neg_parallel_two_hits_warn():
    """「不是…而是…」「不仅…而且…」各一处 → hits>=2 报警。"""
    sents = ["这不是结束，而是开始。", "他不仅聪明，而且勤奋。"]
    r = S.scan_neg_parallel(sents)
    assert r["hits_count"] >= 2, r
    assert r["warning"]


def test_neg_parallel_single_no_warn():
    """单处否定排比是力量不是套路 → 不报（阈值 >=2）。"""
    r = S.scan_neg_parallel(["这不是结束，而是开始。"])
    assert r["hits_count"] == 1
    assert r["warning"] is None


# ========================= B+4 copula_avoid =========================

def test_copula_avoid_two_hits_warn():
    """「作为…的存在」「充当着…的角色」绕开「是」·>=2 报警。"""
    sents = ["他作为团队核心的存在。", "她充当着调解者的角色。"]
    r = S.scan_copula_avoid(sents)
    assert r["hits_count"] >= 2, r
    assert r["warning"]


# ========================= B+5 fake_range + CLUSTER_MODE =========================

def test_fake_range_single_sentence_two_ranges_is_hit():
    """一句里 >=2 个「从X到Y」才计 hit；单个不计。"""
    one = S.scan_fake_range(["从清晨到日暮，他都在走。"])      # 单个范围
    assert one["hits_count"] == 0, one
    two = S.scan_fake_range(["从晨光到暮色，从山巅到海底，无所不至。"])
    assert two["hits_count"] == 1, two


def test_fake_range_cluster_mode_threshold():
    """CLUSTER_MODE=1 时报警阈值从 1 抬到 3（容忍 voice 必要使用）。

    构造恰好 2 个命中句：非 cluster 模式（阈值1）应报，cluster 模式（阈值3）不报。
    用 os.environ 临时切换·测完恢复（不污染其它测试）。
    """
    sents = [
        "从晨光到暮色，从山巅到海底。",
        "从过去到现在，从生到死。",
    ]
    saved = os.environ.get("CLUSTER_MODE")
    try:
        os.environ.pop("CLUSTER_MODE", None)        # 非 cluster 模式
        r_default = S.scan_fake_range(sents)
        assert r_default["hits_count"] == 2, r_default
        assert r_default["warning"], "非cluster模式·2命中应报（阈值1）"

        os.environ["CLUSTER_MODE"] = "1"            # cluster 模式
        r_cluster = S.scan_fake_range(sents)
        assert r_cluster["hits_count"] == 2
        assert r_cluster["warning"] is None, "cluster模式·2命中不报（阈值3）"
    finally:
        if saved is None:
            os.environ.pop("CLUSTER_MODE", None)
        else:
            os.environ["CLUSTER_MODE"] = saved


# ========================= B+6 over_hedge =========================

def test_over_hedge_two_hedges_warn():
    """一句叠 >=2 个限定词 → 过度限定报警。"""
    sents = ["他可能也许是想走吧。", "事情大概差不多就这样了。"]
    r = S.scan_over_hedge(sents)
    assert r["hits_count"] >= 2, r
    assert r["warning"]


def test_over_hedge_single_hedge_per_sentence_no_warn():
    """每句只有一个限定词 → 不报（管的是叠用·非单用）。"""
    r = S.scan_over_hedge(["他也许会来。", "事情大概结束了。"])
    assert r["hits_count"] == 0, r
    assert r["warning"] is None


# ========================= B+7 forced_triple =========================

def test_forced_triple_needs_three_warn():
    """顿号三连 >=3 处才报（高频=AI凑全面感）。"""
    sents = [
        "创新、灵感、洞察。",
        "山川、河流、湖泊。",
        "勇气、智慧、力量。",
    ]
    r = S.scan_forced_triple(sents)
    assert r["hits_count"] >= 3, r
    assert r["warning"]


def test_forced_triple_under_three_no_warn():
    """两处顿号三连 → 不报（阈值 >=3）。"""
    sents = ["创新、灵感、洞察。", "山川、河流、湖泊。"]
    r = S.scan_forced_triple(sents)
    assert r["hits_count"] == 2
    assert r["warning"] is None


# ========================= B+8 tag_synonym_cycle =========================

def test_tag_synonym_cycle_six_variants_warn():
    """对话标签变体 >=6 种 → 同义词循环报警·distinct 计数准确。"""
    body = "说道问道答道回道笑道怒道"
    r = S.scan_tag_synonym_cycle(body)
    assert r["distinct_variants"] == 6, r
    assert r["warning"]


def test_tag_synonym_cycle_few_variants_no_warn():
    """变体少（即便总数多）→ 不报（管变体数不管总数）。"""
    body = "说道" * 10
    r = S.scan_tag_synonym_cycle(body)
    assert r["speech_tags_total"] == 10
    assert r["distinct_variants"] == 1
    assert r["warning"] is None


# ========================= scan_chapter 编排契约 =========================

def test_scan_chapter_report_contract_and_subset():
    """scan_chapter：顶层契约字段齐全 + 只跑指定 checks 子集。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_chapter(tmp, 1, "第1章\n这不是结束，而是开始。\n\n他不仅来了，而且带了人。")
        rep = S.scan_chapter(tmp, 1, ["neg_parallel"])
        assert rep["scanner"] == "semantic_slop_scanner"
        assert rep["chapter"] == 1
        assert rep["checks_run"] == ["neg_parallel"]
        assert "neg_parallel" in rep
        # 未请求的检测器不应出现在报告里
        assert "aphorism" not in rep
        assert "metaphor_explain" not in rep
        assert rep["neg_parallel"]["warning"]   # 两处否定排比应报


def test_scan_chapter_missing_chapter_fatal():
    """缺章 → 返回带 _fatal 的 dict（CLI 据此 exit 2）。"""
    with tempfile.TemporaryDirectory() as d:
        rep = S.scan_chapter(Path(d), 42, list(S.ALL_CHECKS.keys()))
        assert "_fatal" in rep
        assert "42" in rep["_fatal"]


# ========================= CLI 退出码契约 =========================

def _run_cli(args):
    """跑真 CLI·返回 (returncode, stdout, stderr)。参照 test_cross_cluster_fate_drift 范式。

    Windows 默认控制台编码是 GBK，子进程会按系统码页输出含中文的 JSON，导致
    text=True 解码崩。这里强制子进程 stdout/stderr 走 UTF-8（PYTHONIOENCODING），
    并对残留乱码字节 errors="replace" 容错（断言只看退出码 + JSON 结构）。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "semantic_slop_scanner.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_exit_0_when_clean():
    """无 advisory 警告 → exit 0 + stdout 是合法 JSON。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _mk_chapter(tmp, 1, "第1章\n他推开门，雨已经停了。\n\n院子里只剩湿漉漉的青石板。")
        rc, out, _ = _run_cli([str(tmp), "1", "--all"])
        assert rc == 0, (rc, out)
        rep = json.loads(out)
        assert rep["scanner"] == "semantic_slop_scanner"


def test_cli_exit_1_when_advisory():
    """有 advisory 警告 → exit 1 + JSON 仍打到 stdout。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        body = ("第1章\n这不是结束，而是开始。\n\n他不仅来了，而且带了人。\n\n"
                "事情不只是巧合，而是安排。")
        _mk_chapter(tmp, 1, body)
        rc, out, err = _run_cli([str(tmp), "1", "--checks", "neg_parallel"])
        assert rc == 1, (rc, out, err)
        rep = json.loads(out)               # exit 1 时 stdout 仍是合法 JSON
        assert rep["neg_parallel"]["warning"]


def test_cli_exit_2_missing_chapter():
    """章节不存在 → exit 2（致命）。"""
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run_cli([d, "7"])
        assert rc == 2, (rc, err)


def test_cli_exit_2_non_integer_chapter():
    """章节号非整数 → exit 2（参数校验）。"""
    with tempfile.TemporaryDirectory() as d:
        rc, _, err = _run_cli([d, "abc"])
        assert rc == 2, (rc, err)


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
