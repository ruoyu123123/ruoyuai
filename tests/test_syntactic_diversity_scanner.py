# -*- coding: utf-8 -*-
"""syntactic_diversity_scanner.py 专属回归测试（聚焦底层确定性 helper + CLI · 零依赖）。

与既有 tests/test_perplexity_obsolete_blindspot.py 互补：那份测的是 scan() 端到端
+ 金标准防矫枉过正 + advisory/hard_gate 制度锁；本份**专攻它未直接覆盖的低层纯逻辑单元**——

  · cjk()                确定性 CJK 计数（非汉字 / 空串 / 边界码点）
  · _load_json()         读 JSON 容错（合法 / 损坏 / 缺失 → None）
  · _author_baseline()   作者档第一权威的优先级合约（--style > 项目双档·作者风格.json
                         先于 _FINAL·部分字段·缺维 from_author_profile=False·类型过滤）
  · split_scenes()       场景切分（硬分隔 \\n---\\n / 三连换行 / 不足 target 合并 / 空白）
  · _strip_for_pos()     剥对话引号内容 + 章节标题行 + 【标记行（只留叙述骨架）
  · _template_coverage() 核心 POS n-gram 覆盖率算法（τ=3 阈值边界 / 短序列 / 唯一 token / 全重复）
  · main() CLI           退出码合约（PASS→0 / FAIL→1 / 文件缺失→2）

铁律：真 import 真调用被测函数，锁真实行为；不重复 perplexity_obsolete 已测的 scan() 路径。
零依赖（stdlib + jieba）·test_* 无参数·失败 raise AssertionError·Windows/UTF-8。
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
import syntactic_diversity_scanner as S  # noqa: E402

_TARGET = _SCRIPTS / "syntactic_diversity_scanner.py"


# ══════════════════════════════════════════════════════════════════════════
# cjk() —— 纯确定性 CJK 计数
# ══════════════════════════════════════════════════════════════════════════
def test_cjk_counts_only_han():
    """只数汉字：拉丁字母 / 数字 / 标点 / 空白都不计。"""
    assert S.cjk("他abc走123了。 \n") == 3, "应只计『他走了』3 个汉字"
    assert S.cjk("") == 0
    assert S.cjk("ABC 123 ！？，。") == 0, "纯非汉字应为 0"


def test_cjk_boundary_codepoints():
    """边界码点：CJK Unified (U+4E00–U+9FFF) + 扩展A区 (U+3400–U+4DBF) 都计入
    （字数口径已收敛到 text_metrics.count_cjk 单一真理源·含扩展A区·北极星⑥）。"""
    assert S.cjk("一") == 1
    assert S.cjk("鿿") == 1
    # '㐀'(U+3400 扩展A区) 计入——canonical count_cjk 覆盖扩展A区（比旧 BMP-only 更正确）
    assert S.cjk("㐀") == 1
    assert S.cjk("一鿿一") == 3


# ══════════════════════════════════════════════════════════════════════════
# _load_json() —— 读 JSON 容错
# ══════════════════════════════════════════════════════════════════════════
def test_load_json_valid_corrupt_missing():
    """合法 JSON 返回对象；损坏内容 / 不存在的路径都吞异常返回 None（不崩）。"""
    d = Path(tempfile.mkdtemp())
    ok = d / "ok.json"
    ok.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
    assert S._load_json(ok) == {"a": 1, "b": [2, 3]}

    bad = d / "bad.json"
    bad.write_text("not json {{{", encoding="utf-8")
    assert S._load_json(bad) is None, "损坏 JSON 应返回 None 而非抛"

    assert S._load_json(d / "nope.json") is None, "缺失文件应返回 None"


# ══════════════════════════════════════════════════════════════════════════
# _author_baseline() —— 作者档第一权威优先级合约
# ══════════════════════════════════════════════════════════════════════════
def _write_profile(path: Path, synt):
    """往作者风格档塞 quantitative.syntactic_diversity；synt=None 则建空 quantitative。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    q = {} if synt is None else {"syntactic_diversity": synt}
    path.write_text(json.dumps({"quantitative": q}, ensure_ascii=False), encoding="utf-8")


def test_author_baseline_style_path_full_fields():
    """--style 档含全四字段 → 全读出 + from_author_profile=True。"""
    d = Path(tempfile.mkdtemp())
    sp = d / "style.json"
    _write_profile(sp, {"template_coverage_mean": 0.4, "template_coverage_std": 0.05,
                        "theme_explain_per_kcjk_mean": 0.2, "theme_explain_per_kcjk_std": 0.1})
    b = S._author_baseline(None, sp)
    assert b["template_cov_mean"] == 0.4 and b["template_cov_std"] == 0.05
    assert b["theme_per_k_mean"] == 0.2 and b["theme_per_k_std"] == 0.1
    assert b["from_author_profile"] is True


def test_author_baseline_style_overrides_project():
    """--style 优先于项目档：style 给 0.4，项目档给 0.30 → 取 0.4。"""
    d = Path(tempfile.mkdtemp())
    sp = d / "style.json"
    _write_profile(sp, {"template_coverage_mean": 0.4})
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格.json", {"template_coverage_mean": 0.30})
    b = S._author_baseline(proj, sp)
    assert b["template_cov_mean"] == 0.4, "style_path 应压过项目档"


def test_author_baseline_project_prefers_plain_over_final():
    """项目内『作者风格.json』先于『作者风格_FINAL.json』（脚本 cand 顺序）。"""
    d = Path(tempfile.mkdtemp())
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格.json", {"template_coverage_mean": 0.30})
    _write_profile(proj / "_数据库" / "作者风格_FINAL.json", {"template_coverage_mean": 0.99})
    b = S._author_baseline(proj, None)
    assert b["template_cov_mean"] == 0.30, "应优先取作者风格.json 而非 _FINAL"


def test_author_baseline_final_fallback_when_plain_absent():
    """无『作者风格.json』时回退到『作者风格_FINAL.json』。"""
    d = Path(tempfile.mkdtemp())
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格_FINAL.json", {"template_coverage_mean": 0.77})
    b = S._author_baseline(proj, None)
    assert b["template_cov_mean"] == 0.77


def test_author_baseline_no_dim_marks_not_from_profile():
    """档存在但无 syntactic_diversity 维 → from_author_profile=False·mean 全 None（退绝对地板路径）。"""
    d = Path(tempfile.mkdtemp())
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格.json", None)  # 空 quantitative
    b = S._author_baseline(proj, None)
    assert b["from_author_profile"] is False
    assert b["template_cov_mean"] is None and b["theme_per_k_mean"] is None


def test_author_baseline_theme_only_field():
    """只给 theme 维 → from_author_profile=True，theme 读出但 template 仍 None（字段独立判定）。"""
    d = Path(tempfile.mkdtemp())
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格.json", {"theme_explain_per_kcjk_mean": 0.15})
    b = S._author_baseline(proj, None)
    assert b["from_author_profile"] is True
    assert b["theme_per_k_mean"] == 0.15
    assert b["template_cov_mean"] is None, "未给 template 维则保持 None"


def test_author_baseline_non_numeric_mean_ignored():
    """mean 是字符串等非数值 → 类型过滤丢弃·from_author_profile 不被点亮（防脏档污染）。"""
    d = Path(tempfile.mkdtemp())
    proj = d / "proj"
    _write_profile(proj / "_数据库" / "作者风格.json",
                   {"template_coverage_mean": "0.4", "template_coverage_std": 0.05})
    b = S._author_baseline(proj, None)
    assert b["template_cov_mean"] is None, "字符串 mean 应被类型过滤"
    assert b["from_author_profile"] is False


def test_author_baseline_no_source_returns_empty():
    """既无 style 也无 project → 全 None·from_author_profile=False。"""
    b = S._author_baseline(None, None)
    assert b["from_author_profile"] is False
    assert b["template_cov_mean"] is None and b["theme_per_k_mean"] is None


# ══════════════════════════════════════════════════════════════════════════
# split_scenes() —— 场景切分
# ══════════════════════════════════════════════════════════════════════════
def test_split_scenes_hard_separators():
    """\\n---\\n 与三连换行都当硬场景边界（A|B|C 三场景）。"""
    sc = S.split_scenes("A\n---\nB\n\n\nC")
    assert len(sc) == 3, f"硬分隔应切 3 段，实际 {len(sc)}"


def test_split_scenes_merges_until_target():
    """不足 target(=600) 的相邻短段累积合并：10×100cjk 段 → 每攒满 600 收一刀。"""
    text = ("段" * 100 + "\n\n") * 10  # 10 段、每段 100 CJK
    sc = S.split_scenes(text)
    # 1000 CJK 攒到 600 收一刀 → 第一场景 ≥600，余量收尾 → 共 2 段
    assert len(sc) == 2, f"应合并为 2 场景，实际 {len(sc)}"
    assert S.cjk(sc[0]) >= 600, "首场景应攒到 target 才收"


def test_split_scenes_empty_and_whitespace():
    """空串 / 纯空白 → 空 list（无场景·防下游除零/噪声）。"""
    assert S.split_scenes("") == []
    assert S.split_scenes("   \n  \n ") == []


def test_split_scenes_single_block_below_target():
    """单段且不足 target → 整段作为唯一场景返回（不丢内容）。"""
    sc = S.split_scenes("就这么一小段话。")
    assert len(sc) == 1 and "就这么一小段话" in sc[0]


# ══════════════════════════════════════════════════════════════════════════
# _strip_for_pos() —— 剥对话/标题/标记，只留叙述骨架
# ══════════════════════════════════════════════════════════════════════════
def test_strip_for_pos_removes_title_marker_dialogue():
    """章节标题行『第N章』+ 【标记行 + 对话引号内内容全剥；叙述骨架保留。"""
    src = '第1章 标题\n【系统】这是标记行\n他说“你好世界”然后走了。'
    out = S._strip_for_pos(src)
    assert "第1章" not in out, "章节标题行应剥"
    assert "系统" not in out, "【标记行应剥"
    assert "你好世界" not in out, "对话引号内内容应剥"
    assert "然后走了" in out, "叙述骨架应保留"


def test_strip_for_pos_multiple_quote_styles():
    """三种引号（直角『』/方角「」/弯引号“”）内内容均被 _DIALOGUE_SPAN 剥除。"""
    src = '他喊“快跑”，又低声『别回头』，末了「我走了」。'
    out = S._strip_for_pos(src)
    for inner in ("快跑", "别回头", "我走了"):
        assert inner not in out, f"对话内容 {inner} 应剥除"
    assert "他喊" in out and "又低声" in out, "叙述连接词应保留"


# ══════════════════════════════════════════════════════════════════════════
# _template_coverage() —— 核心 POS n-gram 覆盖率算法（不需 jieba·直喂 token）
# ══════════════════════════════════════════════════════════════════════════
def test_template_coverage_short_and_empty():
    """长度 < min(ns)=4 或空 → 0.0（短序列不参与·防噪）。"""
    assert S._template_coverage([]) == 0.0
    assert S._template_coverage(["a", "b", "c"]) == 0.0  # < 4


def test_template_coverage_unique_tokens_zero():
    """全唯一 token → 任何 n-gram 都不达 τ=3 → 覆盖率 0.0（多样性满分不误报）。"""
    uniq = [str(i) for i in range(50)]
    assert S._template_coverage(uniq) == 0.0


def test_template_coverage_full_repeat_high():
    """同一 4-gram 反复出现（远超 τ）→ 覆盖率拉满到 1.0。"""
    toks = ["a", "b", "c", "d"] * 5
    assert S._template_coverage(toks) == 1.0


def test_template_coverage_tau_threshold_boundary():
    """τ=3 阈值边界：同 4-gram 出现 2 次 → 未达阈不覆盖(0.0)；出现 3 次 → 被覆盖(>0)。"""
    # 'm n o p' 出现 2 次（用唯一隔断符防别的 n-gram 偶然达阈）
    two = ["m", "n", "o", "p", "X", "m", "n", "o", "p"]
    assert S._template_coverage(two) == 0.0, "出现 2 次 (<τ) 不应覆盖"
    # 同一 4-gram 出现 3 次 → 达 τ → 这些位置被标覆盖
    three = ["m", "n", "o", "p", "X", "m", "n", "o", "p", "Y", "m", "n", "o", "p"]
    cov3 = S._template_coverage(three)
    assert cov3 > 0.0, f"出现 3 次 (==τ) 应覆盖，实际 {cov3}"


# ══════════════════════════════════════════════════════════════════════════
# main() CLI —— 退出码合约（真 argparse + 真落盘 + 真退出码）
# ══════════════════════════════════════════════════════════════════════════
def _run_cli(*args):
    """跑真 CLI；stdout/stderr 用 errors='replace' 容错解码（Windows 控制台编码）。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(_TARGET), *args],
                       capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return p.returncode, out, err


def test_cli_missing_file_exits_2():
    """draft 路径不存在 → stderr 提示 + exit 2（区别于内容判定的 0/1）。"""
    rc, out, err = _run_cli(str(_ROOT / "definitely_missing_xyz.txt"))
    assert rc == 2, f"缺文件应 exit 2，实际 {rc}（err={err}）"
    assert "路径不存在" in err


def test_cli_pass_text_exits_0():
    """PASS 草稿（短叙述·无模板/无过度点题）→ verdict PASS → exit 0 + 输出含 scanner 名。"""
    d = Path(tempfile.mkdtemp())
    f = d / "ok.txt"
    f.write_text("他走进巷子环顾四周然后在铁门前停下敲了三下退后半步等里面的人开门。",
                 encoding="utf-8")
    rc, out, err = _run_cli(str(f))
    assert rc == 0, f"PASS 文本应 exit 0，实际 {rc}（err={err}）"
    assert '"verdict": "PASS"' in out and "syntactic_diversity" in out


def test_cli_fail_templated_exits_1():
    """高度模板化草稿（无作者档·破地板）→ FAIL → exit 1 + 报出 syntactic_template_overuse。"""
    d = Path(tempfile.mkdtemp())
    f = d / "tmpl.txt"
    f.write_text("\n".join("他摸出手机看了一眼屏幕然后皱起了眉头。" for _ in range(40)),
                 encoding="utf-8")
    rc, out, err = _run_cli(str(f))
    assert rc == 1, f"模板文本应 exit 1，实际 {rc}（err={err}）"
    assert "syntactic_template_overuse" in out
    assert '"gate_level": "advisory"' in out, "无论 FAIL 都恒 advisory"


# ── __main__ 自跑（PYTHONIOENCODING=utf-8 python tests/test_syntactic_diversity_scanner.py）──
def _run_all():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    fns = [(n, g) for n, g in sorted(globals().items())
           if n.startswith("test_") and callable(g)]
    passed = failed = 0
    fails = []
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  [OK] {name}")
        except Exception as e:
            failed += 1
            fails.append((name, repr(e)))
            print(f"  [FAIL] {name}: {e!r}")
    print(f"\n{passed}/{passed + failed} OK"
          + (f"  ·  {failed} FAILED" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
