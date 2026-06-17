"""style_profile_extractor.py 专属回归测试 —— 锁内部确定性原语 + _assemble 指令分支 + CLI。

与已有的 tests/test_style_profile_inject.py **不重叠**：那份钉的是「公共提取函数 + 两套蒸馏
schema 容错 + build_manifest/gen_writer 注入三态」的端到端行为。本份补它**没碰过**的更底层逻辑：

  · 数值清洗原语 _num / _stat_pair（bool 不是 int / 字符串→None）
  · _quantile_triple 的边界（p5>p95 判废、p50 缺用 median 兜底、非 dict）
  · _assemble 的指令文案分支：comma_period_ratio >1.05 才下发 / 虚词 >=1.0 噪声门槛 /
    段长 p50-only vs 全分位 两条文案 / 空维度从 metrics 剔除
  · _punct_from_profile / _fw_from_profile 的 schema 优先级（density_per_1000 优先于 per_1k）
  · _read_chapter_dir + main() CLI 退出码 0 / --output 落盘 / 坏 profile 路径不崩

北极星：①贴合作者风格 ⑤ advisory（绝不 hard_gate）⑥ 纯 stdlib·零新依赖。
真 import 真调用被测函数 —— 不 mock 任何被测逻辑。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import style_profile_extractor as spe  # noqa: E402

_TARGET = _SCRIPTS / "style_profile_extractor.py"


# ─────────────────────────────────────────────────────────────────────────────
# _num / _stat_pair —— 数值清洗原语（蒸馏 LLM 脏值不崩的第一道闸）
# ─────────────────────────────────────────────────────────────────────────────
def test_num_accepts_only_real_numbers():
    assert spe._num(19) == 19.0
    assert spe._num(34.18) == 34.18
    assert spe._num(0) == 0.0
    # 字符串 / None / dict / list 一律 None（不是数值）
    assert spe._num("约20字") is None
    assert spe._num("40%") is None
    assert spe._num(None) is None
    assert spe._num({"mean": 1}) is None
    assert spe._num([1, 2]) is None


def test_num_bool_is_not_int():
    """北极星严谨：True/False 是 int 子类但不是合法统计数值 → None（不被当 1.0/0.0）。"""
    assert spe._num(True) is None
    assert spe._num(False) is None


def test_stat_pair_extracts_mean_std():
    assert spe._stat_pair({"mean": 19.0, "std": 3.0}) == (19.0, 3.0)
    # 缺 std → std 位 None
    assert spe._stat_pair({"mean": 19.0}) == (19.0, None)
    # 全缺 / 非 dict → (None, None)
    assert spe._stat_pair({}) == (None, None)
    assert spe._stat_pair(None) == (None, None)
    assert spe._stat_pair("not_a_dict") == (None, None)
    # mean 是脏字符串 → None
    assert spe._stat_pair({"mean": "约20字", "std": 3.0}) == (None, 3.0)


# ─────────────────────────────────────────────────────────────────────────────
# _quantile_triple —— 分位数三元组的边界与兜底
# ─────────────────────────────────────────────────────────────────────────────
def test_quantile_triple_valid():
    out = spe._quantile_triple({"p5": 8.0, "p50": 18.0, "p95": 40.0})
    assert out == {"p5": 8.0, "p50": 18.0, "p95": 40.0}


def test_quantile_triple_p50_median_fallback():
    """p50 缺 → 用 median 兜底（两套蒸馏 schema 键名差异）。"""
    out = spe._quantile_triple({"p5": 8.0, "median": 17.5, "p95": 40.0})
    assert out["p50"] == 17.5
    assert out["p5"] == 8.0 and out["p95"] == 40.0


def test_quantile_triple_rejects_inverted_range():
    """p5 > p95（坏数据）→ 整体判废返回 None（不下发倒挂区间）。"""
    assert spe._quantile_triple({"p5": 50.0, "p50": 30.0, "p95": 10.0}) is None


def test_quantile_triple_requires_both_ends():
    """缺 p5 或 p95 → None（区间不完整不下发）。"""
    assert spe._quantile_triple({"p50": 18.0, "p95": 40.0}) is None  # 缺 p5
    assert spe._quantile_triple({"p5": 8.0, "p50": 18.0}) is None     # 缺 p95
    assert spe._quantile_triple(None) is None
    assert spe._quantile_triple("x") is None


def test_quantile_triple_dirty_values_dropped():
    """p5/p95 是脏字符串 → 当缺失处理 → None。"""
    assert spe._quantile_triple({"p5": "八字", "p50": 18.0, "p95": 40.0}) is None


# ─────────────────────────────────────────────────────────────────────────────
# _assemble —— 指令文案分支（comma_period_ratio / 虚词门槛 / 段长两路 / 空维度剔除）
# ─────────────────────────────────────────────────────────────────────────────
def test_assemble_comma_period_ratio_threshold():
    """逗句比 >1.05 才下发文案；<=1.05 不下发（不噪声）。"""
    metrics_hi = {"punctuation_per_1k": {"comma_period_ratio": 2.5}}
    fp_hi = spe._assemble(dict(metrics_hi), source="t", n=1)
    assert any("逗句比" in d for d in fp_hi["directives"])

    metrics_lo = {"punctuation_per_1k": {"comma_period_ratio": 1.0}}
    fp_lo = spe._assemble(dict(metrics_lo), source="t", n=1)
    assert not any("逗句比" in d for d in fp_lo["directives"])


def test_assemble_function_word_noise_threshold():
    """虚词 >=1.0 才进高频虚词指令；<1.0 视为噪声不下发。"""
    metrics = {"function_words_per_1k": {"的": 39.0, "却": 0.3, "竟": 1.0}}
    fp = spe._assemble(dict(metrics), source="t", n=1)
    fw_line = next((d for d in fp["directives"] if "高频虚词" in d), "")
    assert "「的」39" in fw_line      # >=1.0 入选
    assert "「竟」1" in fw_line        # 恰好 1.0 入选（>=）
    assert "却" not in fw_line         # 0.3 < 1.0 被滤


def test_assemble_paragraph_full_quantile_vs_p50_only():
    """段长全分位 → 90% 区间文案；只有 p50 → 段长均值约 X 字文案。"""
    full = spe._assemble(
        {"paragraph_length_chars": {"p5": 12.0, "p50": 30.0, "p95": 80.0}},
        source="t", n=1)
    assert any("90% 段落 12-80 字" in d for d in full["directives"])

    p50_only = spe._assemble(
        {"paragraph_length_chars": {"p50": 50.0}}, source="t", n=1)
    joined = " ".join(p50_only["directives"])
    assert "段长均值约 50 字" in joined
    assert "90% 段落" not in joined


def test_assemble_strips_empty_dimensions():
    """空维度（None / {} / [] / 全 None 的 dict）从 metrics 剔除，不下发噪声。"""
    fp = spe._assemble({
        "sentence_length": {"mean": 20.0, "std": None},  # std None 应被清掉
        "dialogue_ratio": None,                           # None 整维剔除
        "punctuation_per_1k": {},                          # 空 dict 剔除
        "signature_collocations": [],                      # 空 list 剔除
        "function_words_per_1k": {"的": None},            # 全 None 的 dict 剔除
    }, source="src_x", n=3)
    m = fp["metrics"]
    assert "dialogue_ratio" not in m
    assert "punctuation_per_1k" not in m
    assert "signature_collocations" not in m
    assert "function_words_per_1k" not in m
    # sentence_length 保留但内部 None 字段被清
    assert m["sentence_length"] == {"mean": 20.0}
    assert fp["source"] == "src_x"
    assert fp["n_chapters"] == 3


def test_assemble_n_chapters_coercion():
    """n 为浮点 → int；非数值 → None。"""
    assert spe._assemble({}, source="t", n=250.0)["n_chapters"] == 250
    assert spe._assemble({}, source="t", n="一堆")["n_chapters"] is None
    assert spe._assemble({}, source="t", n=None)["n_chapters"] is None


def test_assemble_doc_is_advisory():
    """北极星⑤：_doc 标 advisory，整个指纹结构里绝无 hard_gate 字样。"""
    fp = spe._assemble({"sentence_length": {"mean": 20.0}}, source="t", n=1)
    assert "advisory" in fp["_doc"]
    assert "hard_gate" not in json.dumps(fp, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# _punct_from_profile / _fw_from_profile —— schema 优先级 + 嵌套{mean} vs 裸数值
# ─────────────────────────────────────────────────────────────────────────────
def test_punct_from_profile_density_takes_priority():
    """两套键名都给时，punctuation_density_per_1000 优先于 punctuation_per_1k。"""
    q = {
        "punctuation_density_per_1000": {"question": {"mean": 4.0}},
        "punctuation_per_1k": {"question": 99.0},
    }
    assert spe._punct_from_profile(q)["question"] == 4.0


def test_punct_from_profile_bare_value_and_nested():
    """per_1k 既支持裸数值也支持嵌套 {mean}。"""
    assert spe._punct_from_profile({"punctuation_per_1k": {"comma": 62.6}})["comma"] == 62.6
    assert spe._punct_from_profile(
        {"punctuation_per_1k": {"comma": {"mean": 62.6}}})["comma"] == 62.6
    # 空 / 缺 → {}
    assert spe._punct_from_profile({}) == {}
    assert spe._punct_from_profile({"punctuation_per_1k": {}}) == {}


def test_fw_from_profile_priority_and_dirty_skip():
    """function_word_fingerprint_per_1000 优先；脏值跳过不崩。"""
    q = {
        "function_word_fingerprint_per_1000": {"的": 39.25, "了": "很多"},
        "function_words_per_1k": {"的": 1.0},
    }
    out = spe._fw_from_profile(q)
    assert out["的"] == 39.25       # 优先源
    assert "了" not in out           # 脏值被跳过


# ─────────────────────────────────────────────────────────────────────────────
# _read_chapter_dir + main() CLI —— 退出码 / --output 落盘 / 坏路径不崩
# ─────────────────────────────────────────────────────────────────────────────
def test_read_chapter_dir_sorted_and_utf8():
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        (dd / "002.txt").write_text("第二章正文", encoding="utf-8")
        (dd / "001.txt").write_text("第一章正文", encoding="utf-8")
        (dd / "note.md").write_text("不是 txt 应忽略", encoding="utf-8")
        texts = spe._read_chapter_dir(dd)
        assert texts == ["第一章正文", "第二章正文"]  # 按文件名排序 + 只读 *.txt


def test_main_profile_to_output_exit_zero():
    """main() 读 profile + 写 --output JSON → 退出码 0，输出含 directives。"""
    profile = {
        "analyzed_chapters": 250,
        "quantitative": {
            "sentence_length": {"mean": 34.0, "std": 6.0},
            "dialogue_ratio": {"mean": 0.27},
        },
    }
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        pf = dd / "作者风格.json"
        pf.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        out = dd / "fp.json"
        rc = spe.main(["--profile", str(pf), "--output", str(out)])
        assert rc == 0
        assert out.exists()
        saved = json.loads(out.read_text(encoding="utf-8"))
        assert saved["source"] == "author_profile"
        assert saved["directives"]
        assert saved["metrics"]["sentence_length"]["mean"] == 34.0


def test_main_bad_profile_path_warns_not_crash():
    """profile 路径不存在 → 打 WARN 到 stderr，仍退出 0（顾问层绝不中断）。"""
    rc = spe.main(["--profile", "D:/绝不存在的/作者风格.json"])
    assert rc == 0


def _run_cli_bytes(*args):
    """跑真 CLI · 捕字节后 errors='replace' 解码（子进程 stdout 走 Windows 控制台编码非 UTF-8·
    沿用 tests/test_cross_cluster_fate_drift_aggregate.py 约定）。"""
    p = subprocess.run([sys.executable, str(_TARGET), *args],
                       capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return p.returncode, out, err


def test_main_chapters_dir_to_output_file():
    """main() 走 --chapters-dir（原文路）→ 退出 0；指纹经 --output 显式 utf-8 落盘（确定性权威输出·
    断言锚文件而非控制台 stdout·避开 Windows 控制台编码）。subprocess 跑真 CLI。"""
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        chdir = dd / "chs"
        chdir.mkdir()
        (chdir / "001.txt").write_text(
            "他推开门。\n夜色很深。\n天黑得像泼了墨，浓得化不开。\n远处传来一声犬吠。\n",
            encoding="utf-8")
        (chdir / "002.txt").write_text(
            "她坐在窗边。\n雨点敲打着玻璃。\n桌上的茶凉了。\n他没有说话。\n",
            encoding="utf-8")
        out = dd / "fp.json"
        rc, _, err = _run_cli_bytes("--chapters-dir", str(chdir), "--output", str(out))
        assert rc == 0, err
        assert out.exists()
        fp = json.loads(out.read_text(encoding="utf-8"))
        assert fp["source"] == "chapter_texts"
        assert fp["n_chapters"] == 2
        assert fp["directives"]


def test_main_empty_args_stdout_empty_dict():
    """无 --profile 无 --chapters-dir → build_style_fingerprint 返回 {} → stdout 是 {} → 退出 0。"""
    rc, out, err = _run_cli_bytes()
    assert rc == 0, err
    assert json.loads(out.strip()) == {}
