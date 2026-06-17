"""skill_contract_table.py 聚焦回归测试 — 锁尚未被覆盖的核心确定性逻辑。

已有间接覆盖（不重复）：
  · tests/test_l3c_skill_dual_layer.py 已钉死 extract_contract_table 的 schema 容忍、
    render_contract_table_md / render_dual_layer_scaffold 的内容、顾问非法官措辞、
    inject_header_into_skill 的幂等/front-matter 注入、两书真档金标准。
  · tests/test_consolidate_author_profile.py 间接调 extract_contract_table 验聚合可读。

本文件聚焦上述测试**没有触及**的核心逻辑：
  · main() CLI 退出码契约（缺文件→2 / render→0 / inject 缺 --skill→2 / inject 缺文件→2 /
    inject 真写盘→0 且文件含块）—— 含 sys.exit，走 subprocess 跑真 CLI。
  · _fmt() 数值格式化（None→未蒸出 / float 整数折叠成 int / 后缀）。
  · _punct_dist() p50 优先 mean、裸标量 cell、缺字段跳过、别名 key。
  · _top_dist() 降序 Top-N + 过滤非数值。
  · has_contract_block 判定边界。
  · inject_header_into_skill 空文件（head=="" 分支）。

零依赖（纯 stdlib）· test_* 无参 · 断言失败 raise AssertionError。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import skill_contract_table as sct  # noqa: E402

_TARGET = _SCRIPTS / "skill_contract_table.py"

# 最小可渲染合成档（quantitative 顶层包裹）
_MIN_STYLE = {
    "quantitative": {
        "sentence_length": {"mean": 20.0, "std": 3.0},
        "chapter_chars": {"mean": 2700.0, "std": 500.0},
        "dialogue_ratio_pct": {"mean": 25.0},
        "paragraph_length_chars": {"p5": 24.0, "p50": 30.0, "p95": 38.0},
        "function_words_per_1k": {"的": {"p50": 34.4}, "了": {"p50": 13.7}},
        "punctuation_per_1k": {"comma_period_ratio": {"p50": 1.7}},
    }
}


def _write_json(path: Path, obj) -> None:
    import json
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _run_cli(*args):
    """跑真 CLI（真 argparse + 真文件 IO）· 返回 CompletedProcess。

    Windows 上子进程默认继承控制台 GBK 编解码器，print emoji/CJK 到管道会
    UnicodeEncodeError 崩溃，且 stderr 以 GBK 字节回传又被 utf-8 解码失败 → None。
    强制子进程走 UTF-8 I/O（PYTHONIOENCODING/PYTHONUTF8），这是环境无关化而非改脚本。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


# ════════════════════════════════════════════════════════════════
# [1] _fmt 数值格式化（None→未蒸出 · float 整数折叠 · 后缀）
# ════════════════════════════════════════════════════════════════

def test_fmt_none_is_unshipped():
    """None → 「未蒸出」（绝不编造）。"""
    assert sct._fmt(None) == "未蒸出"
    assert sct._fmt(None, " 字") == "未蒸出"  # None 时后缀也不附加


def test_fmt_float_integer_collapsed_to_int():
    """float 恰为整数 → 折叠成 int 显示（30.0 → 30 而非 30.0）。"""
    assert sct._fmt(30.0) == "30"
    assert sct._fmt(30.0, " 字") == "30 字"
    # 非整数 float 保留小数
    assert sct._fmt(30.5) == "30.5"
    # int 原样
    assert sct._fmt(7, "%") == "7%"


# ════════════════════════════════════════════════════════════════
# [2] _punct_dist（p50 优先 mean · 裸标量 · 缺字段跳过 · 别名 key）
# ════════════════════════════════════════════════════════════════

def test_punct_dist_p50_preferred_over_mean():
    """cell 是 dict 时 p50 优先于 mean。"""
    out = sct._punct_dist({"punctuation_per_1k": {
        "comma_period_ratio": {"p50": 1.7, "mean": 9.9}}})
    assert out["逗句比"] == 1.7  # 取 p50 不取 mean


def test_punct_dist_mean_fallback_and_scalar_cell():
    """无 p50 → 退 mean；cell 是裸标量 → 直接取。"""
    out = sct._punct_dist({"punctuation_per_1k": {
        "ellipsis": {"mean": 2.5},   # 无 p50 退 mean
        "exclamation": 4.0,          # 裸标量
    }})
    assert out["省略号"] == 2.5
    assert out["感叹号"] == 4.0


def test_punct_dist_missing_keys_skipped_and_alias():
    """缺的标点项不出现在输出（不编造 0）；吃 punctuation_density_per_1000 别名 key。"""
    out = sct._punct_dist({"punctuation_density_per_1000": {
        "question": {"p50": 3.0}}})
    assert out == {"问号": 3.0}  # 只有 question·其余项缺失被跳过


# ════════════════════════════════════════════════════════════════
# [3] _top_dist（降序 Top-N + 过滤非数值）
# ════════════════════════════════════════════════════════════════

def test_top_dist_sorted_desc_and_capped():
    """按值降序 + 钳到 N。"""
    d = {f"k{i}": float(i) for i in range(8)}
    top = sct._top_dist(d, 3)
    assert [k for k, _ in top] == ["k7", "k6", "k5"]
    assert top[0][1] == 7.0


def test_top_dist_filters_non_numeric():
    """非数值 value 被过滤（不抛错·不进结果）。"""
    d = {"a": 0.5, "b": "not_a_number", "c": 0.9, "d": None}
    top = sct._top_dist(d, 5)
    keys = [k for k, _ in top]
    assert keys == ["c", "a"]  # b/d 被滤掉·c>a 降序


def test_top_dist_non_dict_returns_empty():
    """传入非 dict → 空列表（防御）。"""
    assert sct._top_dist(None) == []
    assert sct._top_dist([1, 2, 3]) == []


# ════════════════════════════════════════════════════════════════
# [4] has_contract_block / inject 空文件边界
# ════════════════════════════════════════════════════════════════

def test_has_contract_block_requires_both_sentinels():
    """需 BEGIN 与 END 哨兵同时存在才算有块。"""
    assert not sct.has_contract_block("无哨兵正文")
    assert not sct.has_contract_block(sct.SENTINEL_BEGIN + " 只半截")
    full = sct.SENTINEL_BEGIN + "\nx\n" + sct.SENTINEL_END
    assert sct.has_contract_block(full)


def test_inject_into_empty_skill_head_empty_branch():
    """空 skill（无 front-matter·无正文）→ 块插到顶端不抛错（head=='' 分支）。"""
    header = sct.build_skill_header(_MIN_STYLE)
    out = sct.inject_header_into_skill("", header)
    assert sct.has_contract_block(out)
    assert out.startswith(sct.SENTINEL_BEGIN)


# ════════════════════════════════════════════════════════════════
# [5] main() CLI 退出码契约（subprocess · 含 sys.exit）
# ════════════════════════════════════════════════════════════════

def test_cli_missing_style_exits_2():
    """--style 指向不存在文件 → exit 2 + [FATAL] 到 stderr。"""
    r = _run_cli("--style", str(_ROOT / "_不存在的档.json"))
    assert r.returncode == 2, r.stderr
    assert "[FATAL]" in r.stderr


def test_cli_render_prints_header_exit_0():
    """--render → 打印 header（含哨兵 + 契约表）· exit 0 · 不写任何文件。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        _write_json(sp, _MIN_STYLE)
        r = _run_cli("--style", str(sp), "--render")
        assert r.returncode == 0, r.stderr
        assert sct.SENTINEL_BEGIN in r.stdout
        assert "数值契约表" in r.stdout
        assert "句级层" in r.stdout and "段级层" in r.stdout


def test_cli_default_no_inject_acts_as_render():
    """既无 --render 也无 --inject → 默认打印 header（render 同效）· exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        _write_json(sp, _MIN_STYLE)
        r = _run_cli("--style", str(sp))
        assert r.returncode == 0, r.stderr
        assert sct.SENTINEL_BEGIN in r.stdout


def test_cli_inject_without_skill_exits_2():
    """--inject 缺 --skill → exit 2 + [FATAL]。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        _write_json(sp, _MIN_STYLE)
        r = _run_cli("--style", str(sp), "--inject")
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "[FATAL]" in r.stderr


def test_cli_inject_missing_skill_file_exits_2():
    """--inject 指向不存在的 skill 文件 → exit 2 + [FATAL]。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        _write_json(sp, _MIN_STYLE)
        r = _run_cli("--style", str(sp), "--inject",
                     "--skill", str(Path(d) / "no_skill.md"))
        assert r.returncode == 2, (r.returncode, r.stderr)
        assert "[FATAL]" in r.stderr


def test_cli_inject_writes_block_into_skill_file_exit_0():
    """--inject 真写盘：exit 0 · skill 文件被改入 L3c 块 · 重跑幂等不堆叠。"""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "style.json"
        _write_json(sp, _MIN_STYLE)
        skill = Path(d) / "skill_v1.md"
        skill.write_text("---\nname: t\n---\n\n# 正文\n内容。\n", encoding="utf-8")

        r1 = _run_cli("--style", str(sp), "--inject", "--skill", str(skill))
        assert r1.returncode == 0, r1.stderr
        body1 = skill.read_text(encoding="utf-8")
        assert sct.has_contract_block(body1)
        assert "[OK]" in r1.stderr and "注入" in r1.stderr
        # front-matter 与正文保留
        assert body1.startswith("---\nname: t")
        assert "# 正文" in body1

        # 重跑：替换块内·不堆叠（哨兵恒为 1）
        r2 = _run_cli("--style", str(sp), "--inject", "--skill", str(skill))
        assert r2.returncode == 0, r2.stderr
        body2 = skill.read_text(encoding="utf-8")
        assert body2.count(sct.SENTINEL_BEGIN) == 1
        assert body2.count(sct.SENTINEL_END) == 1
        assert "替换" in r2.stderr  # 第二次是「替换」非「注入」
