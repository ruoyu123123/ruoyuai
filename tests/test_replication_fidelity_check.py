# -*- coding: utf-8 -*-
"""replication_fidelity_check.py 专属回归（聚焦 · 锁尚未覆盖的确定性核心逻辑）。

已有间接覆盖（不重复）：
  · test_replication_fidelity.py        —— _metrics 基本 / _author_baseline 读取+缺失 / comedy band
  · test_mstyle_cosine_subscore.py      —— _mstyle_cosine_subscore hash-invalid / never-raises
  · test_av_judge_swap.py               —— _flatten_principles / intent_recovery_* 全套护栏
  · test_genre_baseline.py              —— _BANDS 键集 superset

本文件补的盲区（直接 import 真调用 · 零 LLM / 零联网 / stdlib only）：
  1. _cjk           —— 只数 CJK 表意字（标点/拉丁/空白不计）
  2. _metrics       —— 每千字标点归一化精确值 / 空文本除零守卫 / single_para_ratio 阈值分支
  3. _author_baseline —— 段长+单句独行的 *回退键* 路径 / 标点 mean 裸标量 / 非法 JSON / quantitative 非 dict
  4. main()（CLI 全链路 · 此前完全无覆盖）—— exit 0 / 报告落盘 / verdict pass·advisory /
     comedy_engine 仅 ratio<lo 触发 / 无正文放行 / 无作者档放行 / --chapters 入口
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import replication_fidelity_check as rf  # noqa: E402

_TARGET = _SCRIPTS / "replication_fidelity_check.py"


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def _mk_project(tmp: Path) -> Path:
    proj = tmp / "测试书"
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    return proj


def _write_baseline(proj: Path, quantitative: dict) -> None:
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"quantitative": quantitative}, ensure_ascii=False),
        encoding="utf-8")


def _write_cluster_draft(proj: Path, cluster: int, text: str) -> None:
    d = proj / "章节" / f"cluster_{cluster:03d}_draft"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"cluster_{cluster:03d}_draft.txt").write_text(text, encoding="utf-8")


def _write_chapter(proj: Path, n: int, text: str) -> None:
    d = proj / "章节" / f"第{n:03d}章"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"第{n:03d}章.txt").write_text(text, encoding="utf-8")


def _run_cli(proj: Path, *args):
    """跑真 CLI（真 argparse + 真 _metrics/_author_baseline + 真报告落盘）。

    🔴 Windows 控制台输出按系统代码页（GBK）编码 · 解码用 errors='replace' 防崩 ·
    断言只看退出码 + 落盘的 UTF-8 报告（确定性）· 不断言 stdout/stderr 中文子串（代码页相关）。
    """
    return subprocess.run(
        [sys.executable, str(_TARGET), "--project", str(proj), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(_ROOT))


def _report_for(proj: Path, tag: str) -> dict:
    p = proj / "_数据库" / ".audit" / f"replication_fidelity_{tag}.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# 1. _cjk —— 只数 CJK 表意字
# ══════════════════════════════════════════════════════════════════════════
def test_cjk_counts_only_ideographs():
    # 4 个汉字 + 标点 + 拉丁 + 空白 + 数字 —— 仅汉字计数
    assert rf._cjk("他说：hello, 世界 123！") == 4
    assert rf._cjk("") == 0
    assert rf._cjk("abc123  ，。！") == 0   # 全是非表意字
    # 边界字符 一(U+4E00) 与上界附近字都算
    assert rf._cjk("一龙") == 2


# ══════════════════════════════════════════════════════════════════════════
# 2. _metrics —— 每千字归一化 / 空文本除零守卫 / single_para_ratio
# ══════════════════════════════════════════════════════════════════════════
def test_metrics_empty_text_no_divzero():
    """空文本：total=0 时 k 守卫成 1，全部维度回 0 不抛 ZeroDivisionError。"""
    m = rf._metrics("")
    assert m["cjk"] == 0
    assert m["sentence_mean"] == 0
    assert m["para_mean"] == 0
    assert m["single_para_ratio"] == 0
    assert m["comma_k"] == 0 and m["period_k"] == 0


def test_metrics_per_1000_normalization_exact():
    """标点密度严格 = count / (cjk/1000)。构造 1000 个汉字 + 已知标点数验证精确值。"""
    # 998 汉字 + 1 逗号 + 1 句号，再加非表意标点不影响 cjk 计数。
    body = "字" * 998
    text = body + "，。！？"   # cjk=998；逗号1/句号1/感叹1/问号1
    m = rf._metrics(text)
    assert m["cjk"] == 998
    k = 998 / 1000
    assert abs(m["comma_k"] - round(1 / k, 1)) < 1e-9
    assert abs(m["period_k"] - round(1 / k, 1)) < 1e-9
    assert abs(m["excl_k"] - round(1 / k, 1)) < 1e-9
    assert abs(m["ques_k"] - round(1 / k, 1)) < 1e-9


def test_metrics_single_para_ratio_threshold():
    """单句独行率：段内句末符 <=1 算「单句独行」。两段一单一多 → 0.5。"""
    # 段1：一个句号（=1，算单句）；段2：两个句号（=2，不算）
    text = "他走了。\n\n他来了。又走了。"
    m = rf._metrics(text)
    assert m["single_para_ratio"] == 0.5


def test_metrics_ellipsis_counts_both_forms():
    """省略号维度同时计 …… 与单个 …（脚本对两种都累加）。"""
    text = "字" * 1000 + "……" + "…"
    m = rf._metrics(text)
    # …… 含两个 … → text.count("…")=3；text.count("……")=1 → 累加 4
    k = m["cjk"] / 1000
    assert abs(m["ellipsis_k"] - round((1 + 3) / k, 1)) < 1e-9


# ══════════════════════════════════════════════════════════════════════════
# 3. _author_baseline —— 回退键路径 / 裸标量标点 / 非法输入
# ══════════════════════════════════════════════════════════════════════════
def test_author_baseline_fallback_keys():
    """段长走 paragraph_length.mean_chars 回退 · 单句独行走 paragraph_length.single_..._mean 回退。"""
    proj = _mk_project(_tmp())
    _write_baseline(proj, {
        "sentence_length": {"mean": 30.0},
        # 注意：没有 paragraph_length_chars，只有 paragraph_length.mean_chars
        "paragraph_length": {"mean_chars": 41.0, "single_sentence_para_ratio_mean": 0.66},
        "punctuation_density_per_1000": {"comma": 55.0},   # 裸标量（非 {mean:...}）
    })
    b = rf._author_baseline(proj)
    assert b is not None
    assert abs(b["para_mean"] - 41.0) < 1e-9
    assert abs(b["single_para_ratio"] - 0.66) < 1e-9
    # 裸标量标点 mean 也能取到
    assert abs(b["comma_k"] - 55.0) < 1e-9


def test_author_baseline_malformed_json_returns_none():
    proj = _mk_project(_tmp())
    (proj / "_数据库" / "作者风格.json").write_text("{ not valid json", encoding="utf-8")
    assert rf._author_baseline(proj) is None


def test_author_baseline_quantitative_not_dict_returns_none():
    proj = _mk_project(_tmp())
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps({"quantitative": ["不是 dict"]}, ensure_ascii=False), encoding="utf-8")
    assert rf._author_baseline(proj) is None


def test_author_baseline_missing_dims_become_none_not_crash():
    """quantitative 是 dict 但缺各维度键 → 各项 None（不崩 · 下游 main 会 continue 跳过）。"""
    proj = _mk_project(_tmp())
    _write_baseline(proj, {})   # 空 quantitative
    b = rf._author_baseline(proj)
    assert b is not None
    assert b["sentence_mean"] is None
    assert b["comma_k"] is None


# ══════════════════════════════════════════════════════════════════════════
# 4. main() CLI 全链路（此前完全无覆盖）
# ══════════════════════════════════════════════════════════════════════════
def test_main_no_text_exits_0():
    """无生成正文 → exit 0（放行 · 不落报告）。"""
    proj = _mk_project(_tmp())
    r = _run_cli(proj, "--cluster", "1")
    assert r.returncode == 0, r.stderr
    # 放行路径不落报告
    assert not (proj / "_数据库" / ".audit").exists()


def test_main_no_baseline_exits_0():
    """有正文但无作者风格档 → 跳过放行 exit 0（不落报告）。"""
    proj = _mk_project(_tmp())
    _write_cluster_draft(proj, 1, "他睁开眼。\n\n卧槽这也行？！\n\n" + "字" * 200)
    r = _run_cli(proj, "--cluster", "1")
    assert r.returncode == 0, r.stderr
    assert not (proj / "_数据库" / ".audit").exists() or not list(
        (proj / "_数据库" / ".audit").glob("replication_fidelity_*.json"))


def test_main_pass_verdict_when_in_band():
    """生成指纹贴合作者基线 → verdict=pass · 0 偏离 · exit 0 · 报告落盘。"""
    proj = _mk_project(_tmp())
    # 造一段段长/句长贴近作者基线的正文，标点稀疏；作者基线设宽到 band 内。
    para = "他缓步走进那扇半掩的木门后头停下来打量四周的陈设。"  # 单段 ~24 CJK
    text = "\n\n".join([para] * 8)
    _write_cluster_draft(proj, 1, text)
    gen = rf._metrics(text)
    # 作者基线 = 生成值本身 → ratio==1.0 全部落 band 内 → pass。
    # 🔴 标点维度照搬生成值（含 0）：作者值=0 时 main 走 `a == 0` 跳过该维度（不会误判），
    #    作者值>0 时 ratio==1.0 落 band 内 —— 两种都不产 issue。
    _write_baseline(proj, {
        "sentence_length": {"mean": gen["sentence_mean"]},
        "paragraph_length_chars": {"mean": gen["para_mean"]},
        "single_sentence_para_ratio": gen["single_para_ratio"],
        "punctuation_density_per_1000": {
            "comma": {"mean": gen["comma_k"]},
            "period": {"mean": gen["period_k"]},
            "dash": {"mean": gen["dash_k"]},
            "ellipsis": {"mean": gen["ellipsis_k"]},
            "exclamation": {"mean": gen["excl_k"]},
            "question": {"mean": gen["ques_k"]},
        },
    })
    r = _run_cli(proj, "--cluster", "1")
    assert r.returncode == 0, r.stderr
    rep = _report_for(proj, "cluster_001")
    assert rep["verdict"] == "pass", rep
    assert rep["issues"] == []


def test_main_comedy_engine_flag_only_when_below_lo():
    """情绪标点严重偏低(生成≈0 vs 作者高) → comedy_engine 标记 · verdict=advisory · exit 仍 0。"""
    proj = _mk_project(_tmp())
    # 正文几乎无感叹/问号/省略
    text = "\n\n".join(["他走进房间看了看然后坐下"] * 10)
    _write_cluster_draft(proj, 1, text)
    gen = rf._metrics(text)
    # 其余维度照搬生成值（落 band 内不产 issue），只让情绪标点 author 远高于生成 → 唯一偏离。
    _write_baseline(proj, {
        "sentence_length": {"mean": gen["sentence_mean"]},
        "paragraph_length_chars": {"mean": gen["para_mean"]},
        "single_sentence_para_ratio": gen["single_para_ratio"],
        "punctuation_density_per_1000": {
            "comma": {"mean": gen["comma_k"]},
            "period": {"mean": gen["period_k"]},
            "exclamation": {"mean": 8.0},   # 作者高感叹 → 生成 0 → ratio≈0 < lo
            "question": {"mean": 6.0},
        },
    })
    r = _run_cli(proj, "--cluster", "1")
    assert r.returncode == 0, r.stderr
    rep = _report_for(proj, "cluster_001")
    assert rep["verdict"] == "advisory", rep
    tags = {i["key"]: i["tag"] for i in rep["issues"]}
    # 感叹/问号偏低 → comedy_engine（不是普通 advisory）
    assert tags.get("excl_k") == "comedy_engine", rep["issues"]
    assert tags.get("ques_k") == "comedy_engine", rep["issues"]
    # 只有这两个情绪标点维度偏离（其余照搬生成值落 band 内）
    assert set(tags.keys()) == {"excl_k", "ques_k"}, rep["issues"]


def test_main_chapters_entry_and_report_tag():
    """--chapters 入口拼接逐章正文 · 报告以 chapters 区间名落盘。"""
    proj = _mk_project(_tmp())
    body = "\n\n".join(["他穿过长长的走廊推开尽头那道门走了进去看着里面"] * 4)
    _write_chapter(proj, 1, body)
    _write_chapter(proj, 2, body)
    _write_baseline(proj, {
        "sentence_length": {"mean": 20.0},
        "paragraph_length_chars": {"mean": 22.0},
        "single_sentence_para_ratio": 0.9,
        "punctuation_density_per_1000": {"comma": {"mean": 10.0}, "period": {"mean": 40.0}},
    })
    r = _run_cli(proj, "--chapters", "1-2")
    assert r.returncode == 0, r.stderr
    # 报告 tag = "1-2"（args.chapters）· 两章正文都拼进去 → cjk = 单章 *2
    rep = _report_for(proj, "1-2")
    assert "gen" in rep and rep["gen"]["cjk"] > 0
    one = rf._metrics(body)["cjk"]
    assert rep["gen"]["cjk"] == one * 2, rep["gen"]


# ══════════════════════════════════════════════════════════════════════════
# 5. 近零基线护栏（2026-07-08）—— per-1000 密度维基线 < _NEARZERO_K 时走绝对口径
#    真机 catch-22 实证：主神大道 dash_k=0.048 → 生成 0 次 = 0.0x 假偏离；
#    3k 短稿 1 次命中 = 6.3x 假偏离 → strict 下数学上不可能通过。
# ══════════════════════════════════════════════════════════════════════════
def _nearzero_baseline(gen: dict, dash_mean: float) -> dict:
    """其余维度照搬生成值（ratio=1 落 band 内），只造近零 dash 基线。"""
    return {
        "sentence_length": {"mean": gen["sentence_mean"]},
        "paragraph_length_chars": {"mean": gen["para_mean"]},
        "single_sentence_para_ratio": gen["single_para_ratio"],
        "punctuation_density_per_1000": {
            "comma": {"mean": gen["comma_k"]},
            "period": {"mean": gen["period_k"]},
            "dash": {"mean": dash_mean},
            "ellipsis": {"mean": gen["ellipsis_k"]},
            "exclamation": {"mean": gen["excl_k"]},
            "question": {"mean": gen["ques_k"]},
        },
    }


def test_nearzero_baseline_zero_gen_passes_strict():
    """作者 dash≈0.05/千 · 生成 0 破折号 → 不再报 0.0x 假偏离 · strict exit 0。"""
    proj = _mk_project(_tmp())
    text = "\n\n".join(["他缓步走进那扇半掩的木门后头停下来打量四周的陈设。"] * 8)
    _write_cluster_draft(proj, 1, text)
    gen = rf._metrics(text)
    assert gen["dash_k"] == 0.0
    _write_baseline(proj, _nearzero_baseline(gen, dash_mean=0.0479))
    r = _run_cli(proj, "--cluster", "1", "--strict")
    assert r.returncode == 0, (r.stdout, r.stderr)
    rep = _report_for(proj, "cluster_001")
    assert rep["verdict"] == "pass", rep
    assert rep["issues"] == []


def test_nearzero_baseline_real_usage_still_flagged():
    """作者 dash≈0.05/千 · 生成真在用（> _NEARZERO_K/千）→ 仍按偏离报（绝对口径）。"""
    proj = _mk_project(_tmp())
    para = "他缓步走进那扇半掩的木门——后头停下来打量四周的陈设。"
    text = "\n\n".join([para] * 8)  # 每段 1 处破折号 · 密度远超 0.5/千
    _write_cluster_draft(proj, 1, text)
    gen = rf._metrics(text)
    assert gen["dash_k"] > rf._NEARZERO_K
    _write_baseline(proj, _nearzero_baseline(gen, dash_mean=0.0479))
    r = _run_cli(proj, "--cluster", "1", "--strict")
    assert r.returncode == 1, (r.stdout, r.stderr)
    rep = _report_for(proj, "cluster_001")
    keys = {i["key"] for i in rep["issues"]}
    assert keys == {"dash_k"}, rep["issues"]
    assert rep["issues"][0].get("note", "").startswith("近零基线绝对口径")
