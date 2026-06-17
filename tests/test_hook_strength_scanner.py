"""hook_strength_scanner.py 确定性单元测试（零 LLM / 零联网 / 仅标准库）。

补「专属回归测试」缺口：此前 tests/ 里仅 test_audit_hub_aggregation.py 把
hook_strength_scanner.py **当 scanner stub 文件名** 列入调度集合（用空报告 stub
替身、从不跑真逻辑），test_narrative_scanner_audit.py 仅在注释里把它当「姊妹
找末句写法」引用——核心评分/位置/过渡/拟切点算法从未被真调用过。

本测试钉死这些纯确定性逻辑（北极星⑤：本检测器全 advisory · 只测算法不测 LLM）：

  · detect_hooks_in_text  —— 5 类钩子关键词计数（命中/未命中）
  · split_paragraphs      —— 空行切段
  · score_ending_hook     —— 章末评分（类型分封顶 6 / 形式分封顶 3 / 反模式 -3 / 下限 0）
  · scan_hook_positions   —— 开头/中段/章末窗口切分
  · detect_transition_chapter —— 两条过渡判定分支
  · scan_cluster_hook_pacing  —— 拟切点节奏 + 段数不足守卫
  · scan                  —— 报告组装 + CLUSTER_MODE 改判 + 过渡章→info 抑制
  · main CLI              —— 退出码（0 达标 / 1 偏弱 / 2 fatal&usage）· 走 subprocess 跑真 CLI

退出码语义是与 audit_hub 调度的契约（0/1/2 各有别），故走 subprocess 真跑、断言 returncode。
其余纯函数 in-process 真 import 真调用，绝不 mock 被测逻辑。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import hook_strength_scanner as mod  # noqa: E402

_TARGET = _SCRIPTS / "hook_strength_scanner.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造章节正文文件（标准 v18 布局 章节/第NNN章/第NNN章.txt）
# ──────────────────────────────────────────────────────────────────────────
def _write_chapter(proj: Path, ch: int, body: str) -> Path:
    d = proj / "章节" / f"第{ch:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"第{ch:03d}章.txt"
    p.write_text(body, encoding="utf-8")
    return p


def _run_cli(proj: Path, ch, env_extra=None):
    """跑真 CLI（真 argparse + 真 scan + 真退出码）。

    子进程 stdout 走 Windows 控制台编码（非 UTF-8），用 errors='replace' 容错解码；
    断言只锚 returncode（与 audit_hub 的契约）+ 报告 JSON 字段（显式 ensure_ascii 输出）。
    """
    env = dict(os.environ)
    # 被测脚本用 print(json.dumps(..., ensure_ascii=False)) 出报告——Windows 下 stdout 是
    # 管道时默认 gbk 编码，遇 ⚠(U+26A0) 等非 gbk 字符会 UnicodeEncodeError 崩、stdout 全空。
    # 真流水线（audit_hub）也是以 UTF-8 stdout 跑 scanner，这里对齐：强制子进程 UTF-8 输出。
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(proj), str(ch)],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


# ══════════════════════════════════════════════════════════════════════════
# detect_hooks_in_text —— 5 类钩子关键词计数
# ══════════════════════════════════════════════════════════════════════════
def test_detect_hooks_classifies_each_type_and_counts():
    """5 类钩子各自命中：返回 {type: 命中次数}，未命中类不出现在 dict。"""
    # 悬念×2（为什么 / 是谁）· 冲突×1（拔刀）· 反差×1（竟然）· 信息缺口×1（欲言又止）
    text = "他不明白为什么。门后是谁？那人拔刀就要动手。他竟然认得她。对方欲言又止。"
    hits = mod.detect_hooks_in_text(text)
    # 悬念命中至少 2（为什么 + 是谁 + 不明白 都在 SUSPENSE_KW）
    assert hits.get("suspense", 0) >= 2, hits
    assert hits.get("conflict", 0) >= 1, hits  # 拔 + 动手
    assert hits.get("contrast", 0) >= 1, hits  # 竟然
    assert hits.get("infogap", 0) >= 1, hits   # 欲言又止
    # 计数是真累加（findall 总数），不是布尔
    assert all(isinstance(v, int) and v >= 1 for v in hits.values()), hits


def test_detect_hooks_empty_when_neutral_prose():
    """纯平铺中性叙述：零钩子命中 → 返回空 dict（不能凭空冒类型）。"""
    text = "早上他起床洗脸吃了饭走到院子里晒了会儿太阳就回屋坐下喝茶看书。"
    assert mod.detect_hooks_in_text(text) == {}


# ══════════════════════════════════════════════════════════════════════════
# split_paragraphs —— 空行切段
# ══════════════════════════════════════════════════════════════════════════
def test_split_paragraphs_by_blank_lines():
    """按空行切段、strip 两端、丢弃空段。"""
    body = "第一段。\n\n第二段。\n  \n第三段。\n\n\n   "
    paras = mod.split_paragraphs(body)
    assert paras == ["第一段。", "第二段。", "第三段。"], paras


# ══════════════════════════════════════════════════════════════════════════
# score_ending_hook —— 核心评分算法
# ══════════════════════════════════════════════════════════════════════════
def test_score_ending_empty_paragraphs():
    """空段列表 → score 0 + 明确 detail，不崩。"""
    r = mod.score_ending_hook([])
    assert r["score"] == 0
    assert r["hook_types"] == [] and r["form_signals"] == []


def test_score_ending_type_score_capped_at_six():
    """命中 ≥4 类钩子，类型分仍封顶 6（每类 2 分 · 贵精不贵多）。"""
    # 末段同时塞悬念/冲突/反差/信息缺口/诡异 5 类，验证 min(6, n*2) 封顶
    tail = "他想不通是谁。对方拔刀逼近。竟然不是他写的。话没说完就停住。门自己开了。"
    r = mod.score_ending_hook(["前文。", "过渡。", tail])
    assert len(r["hook_type_hits"]) >= 4, r["hook_type_hits"]
    assert r["type_score"] == 6, r  # 封顶
    assert r["score"] >= 6


def test_score_ending_form_signals_short_and_ellipsis_and_question():
    """形式信号三件套（独立短句 ≤15字 / 省略号收尾 / 问句收尾），form_score 封顶 3。

    注：ELLIPSIS_END / QUESTION_END 都锚定段末（\\s*$），同段末尾互斥；但「省略号收尾」
    可来自章末窗口任一段（tail），故让倒数第二段以 …… 结尾、末段为 ？ 短问句 → 三信号齐。"""
    r = mod.score_ending_hook(["很长的铺垫段落写了很多内容……", "他还活着吗？"])
    assert "独立短句收尾" in r["form_signals"], r["form_signals"]
    assert "省略号/破折号收尾" in r["form_signals"], r["form_signals"]
    assert "问句收尾" in r["form_signals"], r["form_signals"]
    assert r["form_score"] == 3, r  # 三信号全中、封顶 3


def test_score_ending_summary_penalty_and_floor_zero():
    """总结式收尾反模式 -3 分；纯总结无钩子时 score 被 max(0,...) 钳到 0（不为负）。"""
    # 末段命中 SUMMARY_ENDING（从此/风平浪静）、无任何钩子类型 → type 0 form 0 penalty 3
    r = mod.score_ending_hook(["铺垫。", "从此一切都风平浪静尘埃落定告一段落。"])
    assert r["penalty"] == 3, r
    assert r["penalty_reason"] == "命中总结式收尾反模式"
    assert r["score"] == 0, r  # max(0, 0+0-3) == 0，不为负


def test_score_ending_net_score_subtracts_penalty():
    """有钩子又有总结反模式：净分 = 类型分 + 形式分 - 3，验证扣分真生效。"""
    # 悬念（是谁）+ 总结（从此）同段：type=2, form=0, penalty=3 → score = max(0, 2-3) = 0
    r = mod.score_ending_hook(["铺垫段落。", "门后是谁不知道，但从此再没人提起。"])
    assert r["type_score"] >= 2 and r["penalty"] == 3, r
    assert r["score"] == max(0, r["type_score"] + r["form_score"] - 3), r


# ══════════════════════════════════════════════════════════════════════════
# scan_hook_positions —— 开头/中段/章末窗口
# ══════════════════════════════════════════════════════════════════════════
def test_scan_hook_positions_partitions_head_middle_tail():
    """20 段 → head/tail 各 max(1,20//10)=2 段、middle = 16 段；钩子按位置归类。"""
    paras = [f"中性叙述第{i}段没有任何钩子词汇就是平铺。" for i in range(20)]
    # 在开头第 0 段塞悬念、章末最后一段塞冲突
    paras[0] = "他想不通这到底是为什么又是谁干的。"
    paras[-1] = "那人拔刀逼近就要动手了。"
    pos = mod.scan_hook_positions(paras)
    assert pos["opening"]["paragraphs"] == 2
    assert pos["ending"]["paragraphs"] == 2
    assert pos["middle"]["paragraphs"] == 16
    assert "suspense" in pos["opening"]["hooks"], pos["opening"]
    assert "conflict" in pos["ending"]["hooks"], pos["ending"]
    # 中段是纯中性叙述 → 无钩子
    assert pos["middle"]["hooks"] == {}, pos["middle"]


def test_scan_hook_positions_empty():
    """空段列表 → 三窗口全空 dict，不崩。"""
    assert mod.scan_hook_positions([]) == {"opening": {}, "middle": {}, "ending": {}}


# ══════════════════════════════════════════════════════════════════════════
# detect_transition_chapter —— 两条过渡判定分支
# ══════════════════════════════════════════════════════════════════════════
def test_transition_by_signal_density():
    """过渡信号 ≥2 处 且 全章钩子命中 <3 → 判过渡（分支一）。"""
    body = ("几天后日子一天天平静地过去。\n\n"
            "和往常一样照例例行公事没什么特别。\n\n"
            "他照例起床照例吃饭照例睡下。")
    paras = mod.split_paragraphs(body)
    is_tr, reason = mod.detect_transition_chapter(body, paras)
    assert is_tr is True, (is_tr, reason)
    assert "过渡信号" in reason


def test_transition_by_short_chapter_no_tail_hook():
    """短章（<1500 字）且章末无任何钩子命中 → 判过渡（分支二）。"""
    body = "他走进房间坐下喝了口水然后翻开桌上的书静静看了起来直到天黑。"
    paras = mod.split_paragraphs(body)
    is_tr, reason = mod.detect_transition_chapter(body, paras)
    assert is_tr is True, (is_tr, reason)
    assert "短章" in reason


def test_not_transition_when_strong_hooks_present():
    """有强钩子的正常章不被误判为过渡（防过度抑制）。"""
    # 拼到 1500 字以上 + 章末强冲突钩，避开两条过渡分支
    filler = "他在街上走着看着来往的行人想着心事盘算着接下来该怎么办才好。" * 30
    body = filler + "\n\n那人忽然拔刀逼近，竟然不是他，是谁？话没说完……"
    paras = mod.split_paragraphs(body)
    is_tr, _ = mod.detect_transition_chapter(body, paras)
    assert is_tr is False, paras[-1]


# ══════════════════════════════════════════════════════════════════════════
# scan_cluster_hook_pacing —— 拟切点节奏 + 段数守卫
# ══════════════════════════════════════════════════════════════════════════
def test_cluster_pacing_guard_too_few_paragraphs():
    """段数 <5 → 不取拟切点，返回零节奏（避免短 cluster 抖动）。"""
    r = mod.scan_cluster_hook_pacing(["一", "二", "三", "四"], n_pseudo_cuts=4)
    assert r["pseudo_cuts"] == 0
    assert r["scores"] == [] and r["mean_score"] == 0 and r["min_score"] == 0


def test_cluster_pacing_scores_each_cut_and_aggregates():
    """≥5 段：每个拟切点前 3 段算钩子强度 → 返回 mean + min；mean=scores 均值。"""
    # 造 24 段，在第 ~12 段位置（拟切点之一前窗）塞强钩子、其余中性
    paras = [f"中性第{i}段平铺叙述没有钩子词。" for i in range(24)]
    # n_pseudo_cuts=4 → 切点 idx ≈ 4,9,14,19；给 idx≈14 前窗（11,12,13）塞强钩
    paras[12] = "那人拔刀逼近竟然不是他是谁话没说完就停住了门自己开了。"
    r = mod.scan_cluster_hook_pacing(paras, n_pseudo_cuts=4)
    assert r["pseudo_cuts"] == 4, r
    assert len(r["scores"]) == 4
    assert max(r["scores"]) > 0, r  # 至少有钩子的切点得分 >0
    expect_mean = round(sum(r["scores"]) / len(r["scores"]), 1)
    assert r["mean_score"] == expect_mean, r
    assert r["min_score"] == min(r["scores"]), r


# ══════════════════════════════════════════════════════════════════════════
# scan —— 报告组装 / CLUSTER_MODE 改判 / 过渡→info 抑制 / fatal
# ══════════════════════════════════════════════════════════════════════════
def test_scan_fatal_when_chapter_missing():
    """正文不存在 → 报告带 _fatal（main 会据此 exit 2）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空书"
        proj.mkdir(parents=True)
        r = mod.scan(proj, 1)
        assert "_fatal" in r, r


def test_scan_strong_chapter_no_warning():
    """强钩子正常章：ending.score ≥ 阈值 → 无 warning + gate_level=advisory。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        filler = "他在长街上慢慢走着回想这些天发生的事盘算下一步的打算。" * 40
        body = (f"第001章 风起\n{filler}\n\n"
                "那人忽然拔刀逼近，竟然不是他写的，门后是谁？话没说完……")
        _write_chapter(proj, 1, body)
        r = mod.scan(proj, 1)
        assert r["gate_level"] == "advisory"
        assert r["cluster_mode"] is False
        assert r["ending_hook"]["score"] >= r["pass_threshold"], r["ending_hook"]
        assert r["warning"] is None, r["warning"]


def test_scan_transition_chapter_suppressed_to_info():
    """过渡章偏弱：weak 触发 warning 但 severity 降级为 info + 带 suppressed_reason。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        body = ("第001章 平常日子\n几天后日子一天天平静地过去。\n\n"
                "和往常一样照例例行公事没什么特别没什么波澜。\n\n"
                "他照例起床照例吃饭照例睡下一切如旧。")
        _write_chapter(proj, 1, body)
        r = mod.scan(proj, 1)
        assert r["is_transition_chapter"] is True, r
        assert r["warning"] is not None  # 偏弱仍出 warning 文案
        assert r["severity"] == "info", r
        assert "suppressed_reason" in r, r


def test_scan_cluster_mode_uses_pseudo_cut_mean():
    """CLUSTER_MODE=1：报告带 cluster_pacing、weak 用拟切点均值改判（不看末段单点）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        # 弱钩子长 cluster：多段中性 → 拟切点均值偏弱
        body = "第001章 起\n" + "\n\n".join(
            f"中性第{i}段平铺叙述没有钩子词就是日常描写。" for i in range(20))
        _write_chapter(proj, 1, body)
        os.environ["CLUSTER_MODE"] = "1"
        try:
            r = mod.scan(proj, 1)
        finally:
            os.environ.pop("CLUSTER_MODE", None)
        assert r["cluster_mode"] is True, r
        assert r["cluster_pacing"] is not None, r
        assert "mean_score" in r["cluster_pacing"], r
        # 弱钩子 → 均值 < 阈值 → 出 cluster 视野 warning
        assert r["cluster_pacing"]["mean_score"] < r["pass_threshold"], r
        assert "拟切点" in (r["warning"] or ""), r["warning"]


# ══════════════════════════════════════════════════════════════════════════
# main CLI —— 退出码契约（走 subprocess 真跑）
# ══════════════════════════════════════════════════════════════════════════
def test_cli_exit2_usage_when_missing_args():
    """缺参数 → 打印用法 + exit 2（与 fatal 同码，audit_hub 据此区分）。"""
    p = subprocess.run([sys.executable, str(_TARGET)],
                       capture_output=True, cwd=str(_ROOT))
    assert p.returncode == 2, p.returncode


def test_cli_exit2_when_chapter_int_invalid():
    """章节号非整数 → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        proj.mkdir(parents=True)
        r = _run_cli(proj, "abc")
        assert r.returncode == 2, (r.returncode, r.stderr)


def test_cli_exit2_when_chapter_missing():
    """正文文件不存在 → fatal → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        proj.mkdir(parents=True)
        r = _run_cli(proj, 1)
        assert r.returncode == 2, (r.returncode, r.stderr)


def test_cli_exit1_weak_hook_warning():
    """偏弱非过渡章 → advisory warning → exit 1（报告 JSON 落 stdout）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        # 长正文(wc≥1500)避开短章过渡分支(wc<1500且末无钩子→过渡)、末段平淡无钩子无
        # 过渡词(TRANSITION_KW) → ending.score<4 且 is_transition=False → severity=warning
        filler = "他坐在窗边端着茶杯一口一口慢慢地喝着看着窗外的街景出神。" * 80
        body = f"第001章 静日\n{filler}\n\n他又往杯里续了些热水，捧在手心里，还是先前那个味道。"
        _write_chapter(proj, 1, body)
        r = _run_cli(proj, 1)
        assert r.returncode == 1, (r.returncode, r.stdout[:200], r.stderr[:200])
        rep = json.loads(r.stdout)
        assert rep["gate_level"] == "advisory"
        assert rep["is_transition_chapter"] is False, rep
        assert rep["warning"] is not None


def test_cli_exit0_strong_hook_pass():
    """强钩子达标章 → 无 warning → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        filler = "他在长街上慢慢走着回想这些天发生的事盘算下一步该怎么走。" * 40
        body = (f"第001章 风起\n{filler}\n\n"
                "那人忽然拔刀逼近，竟然不是他写的，门后到底是谁？话没说完……")
        _write_chapter(proj, 1, body)
        r = _run_cli(proj, 1)
        assert r.returncode == 0, (r.returncode, r.stdout[:300], r.stderr[:200])
        rep = json.loads(r.stdout)
        assert rep["warning"] is None, rep
        assert rep["ending_hook"]["score"] >= rep["pass_threshold"], rep
