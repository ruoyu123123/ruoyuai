"""repeat_noun_density_scanner.py 专属回归测试 —— 聚焦未被覆盖的核心确定性逻辑。

已有 tests/test_repeat_noun_density.py 覆盖了：extract_tokens 返回名词 token /
白名单外+白名单时代名词检测 / 低于阈值 PASS / gate_level=advisory /
count_token_in_window 计数。

本文件**不重复**上述，聚焦其遗漏的核心算法分支与退出码：
  · severity 分级边界（cnt>=6 → major，4-5 → minor）；
  · verdict 三态（PASS / FAIL_MINOR / FAIL_MAJOR，任一 major → FAIL_MAJOR）；
  · 重叠窗口去重（neighbor_key：同 token 在 i-k<=2 的相邻窗口只报一次）；
  · _count_noun_phrase 直接计数（数词/量词槽 + 名词匹配）；
  · 数词/量词正则槽（那一道光 / 那两把剑）正确剥到名词；
  · evidence_paras 截断契约（每段 ≤60 字 + 仅取窗口前 3 段）；
  · main() CLI 退出码（缺参/文件不存在=2，PASS=0，FAIL=1）+ JSON 输出含 file 字段；
  · 空行段落过滤（split('\n') 后 strip 空段不进 paras）。

零依赖：仅标准库，test_* 无参数，断言失败 raise AssertionError。
main() 含 sys.exit → 走 subprocess 跑真 CLI 锚退出码（参照
tests/test_cross_cluster_fate_drift_aggregate.py 范式）。绝不 mock 被测逻辑。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import repeat_noun_density_scanner as r  # noqa: E402

_TARGET = _SCRIPTS / "repeat_noun_density_scanner.py"


def _run_cli(*args):
    """跑真 CLI；返回 (returncode, stdout_text)。

    子进程 stdout 走 Windows 控制台编码，用 errors='replace' 容错解码，
    断言只锚 returncode + JSON 结构关键字（确定性）。"""
    p = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    return p.returncode, out


# ══════════════════════════════════════════════════════════════════════════
# severity 分级边界 —— cnt>=6 → major，4/5 → minor（line 82）
# ══════════════════════════════════════════════════════════════════════════
def test_severity_minor_at_threshold():
    """窗口内同名词恰好 4 次（4 <= cnt < 6）→ severity=minor。"""
    text = "\n".join([
        "那把剑、那把剑。",   # 2
        "这把剑。",          # 1
        "那把剑。",          # 1  -> 窗口 0:5 共 4
        "无关一句。",
        "无关一句。",
    ])
    res = r.scan_chapter(text)
    v = next(v for v in res["violations"] if v["token"] == "剑")
    assert v["count_in_window"] == 4, v
    assert v["severity"] == "minor", v


def test_severity_major_at_six():
    """窗口内同名词达 6 次（cnt>=6）→ severity=major。"""
    text = "\n".join([
        "那把剑、那把剑、那把剑。",   # 3
        "这把剑、那把剑。",          # 2
        "还有那把剑。",             # 1  -> 窗口 0:5 共 6
        "无关。",
        "无关。",
    ])
    res = r.scan_chapter(text)
    v = next(v for v in res["violations"] if v["token"] == "剑")
    assert v["count_in_window"] == 6, v
    assert v["severity"] == "major", v


# ══════════════════════════════════════════════════════════════════════════
# verdict 三态聚合
# ══════════════════════════════════════════════════════════════════════════
def test_verdict_pass_when_no_violation():
    """无任何 violation → verdict=PASS。"""
    text = "\n".join(["风停了。", "屋里很暗。", "他走出门。", "雨开始下。", "灯亮着。"])
    res = r.scan_chapter(text)
    assert res["violations_count"] == 0, res
    assert res["verdict"] == "PASS", res


def test_verdict_fail_minor_when_only_minor():
    """仅 minor violation（cnt 4-5）→ verdict=FAIL_MINOR。"""
    text = "\n".join([
        "那把剑、那把剑。",
        "这把剑。",
        "那把剑。",   # 共 4，minor
        "无关。",
        "无关。",
    ])
    res = r.scan_chapter(text)
    assert all(v["severity"] == "minor" for v in res["violations"]), res
    assert res["verdict"] == "FAIL_MINOR", res


def test_verdict_fail_major_when_any_major():
    """存在 major violation（cnt>=6）→ verdict=FAIL_MAJOR（major 优先聚合）。"""
    text = "\n".join([
        "那把剑、那把剑、那把剑。",
        "这把剑、那把剑。",
        "还有那把剑。",   # 共 6，major
        "无关。",
        "无关。",
    ])
    res = r.scan_chapter(text)
    assert any(v["severity"] == "major" for v in res["violations"]), res
    assert res["verdict"] == "FAIL_MAJOR", res


# ══════════════════════════════════════════════════════════════════════════
# 重叠窗口去重 —— neighbor_key：同 token 在 i-k<=2 的相邻窗口只报一次（line 75）
# ══════════════════════════════════════════════════════════════════════════
def test_overlapping_windows_deduplicated():
    """同名词在每段都出现，多个重叠窗口都命中 → 只报 1 条（去重）。

    6 段每段 1 次「那把剑」：窗口 i=0(p0-4) 和 i=1(p1-5) 都 >=4，
    但 i=1 与已记录的 (0,'剑') 相邻（i-k=1<=2）→ 不重复 append。"""
    text = "\n".join(["那把剑。"] * 6)
    res = r.scan_chapter(text)
    jian = [v for v in res["violations"] if v["token"] == "剑"]
    assert len(jian) == 1, res
    # 报告的是首个命中窗口（i=0 → window_start_para=1）
    assert jian[0]["window_start_para"] == 1, jian


# ══════════════════════════════════════════════════════════════════════════
# _count_noun_phrase 直接计数 —— 数词/量词槽 + 名词匹配
# ══════════════════════════════════════════════════════════════════════════
def test_count_noun_phrase_direct():
    """_count_noun_phrase 只数「指示词(+数词?+量词?)+<noun>」组合，裸名词不计。

    注：名词槽 [一-鿿]{1,3} 是贪婪捕获，名词后接 CJK 会被吃进 token；
    故用名词后接标点（确定性切断）的形态——这正是 scanner 可靠命中的场景。"""
    # 那把剑 / 这把剑 / 那剑（均名词后接标点）→ keyed on「剑」共 3；裸「剑客」的剑不匹配
    para = "那把剑，这把剑，唯独那剑！剑客冷笑。"
    assert r._count_noun_phrase(para, "剑") == 3, r._count_noun_phrase(para, "剑")
    # 不存在的名词 → 0
    assert r._count_noun_phrase(para, "刀") == 0


def test_numeral_quantifier_slot_strips_to_noun():
    """数词槽([一两二三])+量词槽 都吃掉，extract_tokens 只留名词。"""
    assert r.extract_tokens("那一道光，刺眼。") == {"光"}
    assert r.extract_tokens("那两把剑，交错。") == {"剑"}
    assert r.extract_tokens("这三块石头。") == {"石头"}
    # 无量词裸接名词也命中（量词槽可选）
    assert r.extract_tokens("那剑，出鞘。") == {"剑"}


# ══════════════════════════════════════════════════════════════════════════
# evidence_paras 截断契约 —— 每段 ≤60 字 + 仅窗口前 3 段（line 83）
# ══════════════════════════════════════════════════════════════════════════
def test_evidence_paras_truncation_contract():
    """evidence_paras 每段截断到 60 字，且只取窗口前 3 段。"""
    # 名词后接标点（，）确定性切断贪婪槽 → token=「剑」；后接超长填充验证 60 字截断
    long_para = "那把剑，" + "啊" * 200   # 远超 60
    text = "\n".join([
        long_para,        # p0（超长，那把剑，…）
        "这把剑。",        # p1
        "那把剑。",        # p2
        "那把剑。",        # p3  -> 窗口 0:5 命中「剑」
        "尾段无关一句。",
    ])
    res = r.scan_chapter(text)
    v = next(v for v in res["violations"] if v["token"] == "剑")
    assert len(v["evidence_paras"]) == 3, v          # 仅前 3 段
    assert all(len(p) <= 60 for p in v["evidence_paras"]), v  # 每段 ≤60 字
    assert len(v["evidence_paras"][0]) == 60, v      # 超长段确被截到 60


# ══════════════════════════════════════════════════════════════════════════
# 空行段落过滤 —— split('\n') 后 strip 空段不计入 paras（line 59）
# ══════════════════════════════════════════════════════════════════════════
def test_blank_paragraphs_filtered():
    """空行/纯空白行不进 paras：total_paras 只数非空段。"""
    text = "第一段。\n\n   \n第二段。\n\n第三段。\n"
    res = r.scan_chapter(text)
    assert res["total_paras"] == 3, res


def test_short_text_below_window_no_crash():
    """段数 < window_size（5）→ 循环 range 为空，安全返回 PASS 不报错。"""
    text = "那把剑。\n那把剑。\n那把剑。\n那把剑。"   # 4 段 < 5
    res = r.scan_chapter(text)
    assert res["total_paras"] == 4, res
    assert res["violations_count"] == 0, res
    assert res["verdict"] == "PASS", res


# ══════════════════════════════════════════════════════════════════════════
# main() CLI 退出码契约 —— subprocess 跑真 CLI
# ══════════════════════════════════════════════════════════════════════════
def test_cli_exit_2_when_no_arg():
    """缺章节路径参数 → sys.exit(2)。"""
    rc, _ = _run_cli()
    assert rc == 2, rc


def test_cli_exit_2_when_file_missing():
    """传入不存在的路径 → sys.exit(2)。"""
    rc, _ = _run_cli(str(_ROOT / "__no_such_chapter_file__.txt"))
    assert rc == 2, rc


def test_cli_exit_0_and_json_on_pass():
    """PASS 章节 → 退出码 0，stdout 是含 verdict/file 字段的 JSON。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "clean.txt"
        f.write_text("风停了。\n屋里很暗。\n他走出门。\n雨开始下。\n灯亮着。\n",
                     encoding="utf-8")
        rc, out = _run_cli(str(f))
        assert rc == 0, (rc, out)
        data = json.loads(out)
        assert data["verdict"] == "PASS", data
        assert data["scanner"] == "repeat_noun_density", data
        assert data["gate_level"] == "advisory", data
        assert data["file"] == str(f), data


def test_cli_exit_1_on_fail():
    """FAIL 章节（窗口内重复 ≥4）→ 退出码 1。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "bad.txt"
        f.write_text("\n".join(["那把剑。", "那把剑。", "那把剑。", "那把剑。", "尾段。"]),
                     encoding="utf-8")
        rc, out = _run_cli(str(f))
        assert rc == 1, (rc, out)
        data = json.loads(out)
        assert data["verdict"] != "PASS", data
        assert data["violations_count"] >= 1, data
