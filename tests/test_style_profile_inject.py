"""style_profile_extractor + build_manifest 作者量化风格指纹注入 —— L1a 升格回归测试。

钉死（北极星①贴合作者风格 / ⑤ advisory 不黑箱不干涉模型 / ⑥ 别过度复杂）：
  · 纯函数提取（profile 路 + 原文路）产出多维数值剖面 + 显式指令文案
  · 两套蒸馏 schema 容错（惊悚乐园 dialogue_ratio / 蛊真人 dialogue_ratio_pct）
  · LLM 脏数值（"约20字"/"40%" 字符串）不崩
  · build_manifest 注入：env PROFILE_INJECT_MODE 默认 off → 字段 None（零回归）
  · shadow → 落盘 + 日志但 manifest 不注入（仍 None · 零回归）
  · active → 注入真指纹
  · 顾问层失败/无 profile/extractor 缺失 → None（绝不中断主流水线）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_profile_extractor as spe  # noqa: E402
import build_manifest as bm  # noqa: E402


# ============ 纯函数：从 profile 提取 ============

# 惊悚乐园 schema（dialogue_ratio{mean} 0-1 / punctuation_density_per_1000 / paragraph_length.mean_chars）
_PROFILE_JSL = {
    "analyzed_chapters": 250,
    "quantitative": {
        "sentence_length": {"mean": 34.18, "std": 6.84},
        "paragraph_length": {"mean_chars": 52.0, "single_sentence_para_ratio_mean": 0.51},
        "dialogue_ratio": {"mean": 0.27, "std": 0.20},
        "punctuation_density_per_1000": {
            "comma": {"mean": 62.63}, "period": {"mean": 25.06},
            "comma_period_ratio": {"mean": 2.57}, "ellipsis": {"mean": 7.92},
            "exclamation": {"mean": 1.38}, "question": {"mean": 3.98}, "dash": {"mean": 0.3},
        },
        "function_word_fingerprint_per_1000": {"的": 39.25, "了": 19.04, "着": 5.62, "也": 5.45},
    },
    "golden_passages": [
        {"text": "他握紧拳头握紧拳头，握紧拳头转身离开。握紧拳头"},
    ],
}

# 蛊真人 schema（dialogue_ratio_pct{mean} 0-100 / punctuation_per_1k / paragraph_length_chars=null）
_PROFILE_GZR = {
    "analyzed_chapters": 286,
    "quantitative": {
        "sentence_length": {"mean": 19.07, "std": 3.06, "median": 18.69},
        "dialogue_ratio_pct": {"mean": 25.41},
        "paragraph_length_chars": None,
        "punctuation_per_1k": {},
        "function_words_per_1k": {},
    },
}


def test_profile_jsl_full_fingerprint():
    """惊悚乐园 schema → 多维齐全（句长/段长/对话/标点/虚词/单句独行）。"""
    fp = spe.extract_from_author_profile(_PROFILE_JSL)
    assert fp["source"] == "author_profile"
    assert fp["n_chapters"] == 250
    m = fp["metrics"]
    assert m["sentence_length"]["mean"] == 34.18
    assert m["dialogue_ratio"] == 0.27
    assert m["single_sentence_para_ratio"] == 0.51
    assert m["paragraph_length_chars"]["p50"] == 52.0
    assert m["punctuation_per_1k"]["question"] == 3.98
    assert m["function_words_per_1k"]["的"] == 39.25
    # 指令文案存在且含数值
    joined = " ".join(fp["directives"])
    assert "句长均值目标 34 字" in joined
    assert "对话占比目标 27%" in joined
    assert "单句独行段占比目标 51%" in joined
    assert "问号" in joined


def test_profile_gzr_sparse_schema_tolerant():
    """蛊真人 schema（不同键名 + 空桶 + null 段长）→ 只下发能算的维度，不崩不编造。"""
    fp = spe.extract_from_author_profile(_PROFILE_GZR)
    m = fp["metrics"]
    assert m["sentence_length"]["mean"] == 19.07
    # dialogue_ratio_pct=25.41 → 转 0-1 = 0.2541
    assert abs(m["dialogue_ratio"] - 0.2541) < 1e-6
    # 段长 null + 空桶 → 不应出现这些维度
    assert "paragraph_length_chars" not in m
    assert "punctuation_per_1k" not in m
    assert "function_words_per_1k" not in m
    joined = " ".join(fp["directives"])
    assert "句长均值目标 19 字" in joined
    assert "对话占比目标 25%" in joined


def test_dialogue_ratio_pct_vs_fraction_distinction():
    """pct(0-100) 与 fraction(0-1) 两种对话密度表达都正确归一到 0-1。"""
    pct = spe.extract_from_author_profile(_PROFILE_GZR)["metrics"]["dialogue_ratio"]
    frac = spe.extract_from_author_profile(_PROFILE_JSL)["metrics"]["dialogue_ratio"]
    assert 0.0 <= pct <= 1.0 and 0.0 <= frac <= 1.0


def test_dirty_string_values_no_crash():
    """LLM 脏数值（mean='约20字' / '40%' / 2:1 字符串）→ 跳过该维度不崩。"""
    dirty = {
        "analyzed_chapters": "好几百",
        "quantitative": {
            "sentence_length": {"mean": "约20字", "std": None},
            "dialogue_ratio": {"mean": "40%"},
            "paragraph_length": {"mean_chars": "五十字"},
            "punctuation_density_per_1000": {"comma": {"mean": "很多"}},
        },
    }
    fp = spe.extract_from_author_profile(dirty)  # 不抛
    # 全脏 → metrics 基本为空、directives 可能为空但结构合法
    assert isinstance(fp.get("metrics"), dict)
    assert isinstance(fp.get("directives"), list)
    assert fp["n_chapters"] is None  # "好几百" 非数值


def test_quantile_triple_from_l1a_bucket():
    """作者档若已含 L1a 分位数桶（p5/p50/p95）→ 句长指令带 90% 区间。"""
    prof = {
        "quantitative": {
            "sentence_length": {"mean": 20.0, "std": 5.0, "p5": 8.0, "p50": 18.0, "p95": 40.0},
            "paragraph_length_chars": {"p5": 12.0, "p50": 30.0, "p95": 80.0, "mean": 32.0},
        }
    }
    fp = spe.extract_from_author_profile(prof)
    joined = " ".join(fp["directives"])
    assert "90% 句子落在 8-40 字" in joined
    assert "90% 段落 12-80 字" in joined


def test_empty_or_invalid_profile_returns_empty():
    assert spe.extract_from_author_profile({}) == {}
    assert spe.extract_from_author_profile(None) == {}
    assert spe.extract_from_author_profile({"quantitative": "not_a_dict"}) == {}


# ============ 纯函数：从原文文本提取 ============

_CH_A = "他推开门。\n夜色很深。\n天黑得像泼了墨，浓得化不开。\n远处传来一声犬吠。\n"
_CH_B = "她坐在窗边。\n雨点敲打着玻璃，发出细碎的声响。\n桌上的茶凉了。\n他没有说话。\n"


def test_extract_from_chapter_texts_basic():
    """原文路：聚合多维 + 段长分位数 + 单句独行比。"""
    fp = spe.extract_from_chapter_texts([_CH_A, _CH_B])
    assert fp["source"] == "chapter_texts"
    assert fp["n_chapters"] == 2
    m = fp["metrics"]
    assert m["sentence_length"]["mean"] is not None
    # 全是单句段 → 单句独行比应很高
    assert m["single_sentence_para_ratio"] >= 0.9
    assert "paragraph_length_chars" in m
    assert m["paragraph_length_chars"]["p50"] is not None
    assert len(fp["directives"]) >= 2


def test_extract_from_chapter_texts_empty():
    assert spe.extract_from_chapter_texts([]) == {}
    # 纯空白章节（无 CJK）→ 不崩，n=0
    fp = spe.extract_from_chapter_texts(["   \n  \n"])
    assert fp.get("n_chapters") == 0


def test_signature_collocations_threshold():
    """签名搭配：原文里重复 ≥3 次的 4-8 字片段才入选（噪声门槛）。"""
    text = ("他握紧了拳头。\n" * 4) + "她转身离开。\n这是偶尔出现的句子。\n"
    fp = spe.extract_from_chapter_texts([text])
    collos = fp["metrics"].get("signature_collocations", [])
    # 高频片段应被抓到（含「握紧」语感），偶发片段不入
    assert any("握紧" in c for c in collos)


def test_ai_slop_excluded_from_collocations():
    """AI 套话/工艺签名词不进签名搭配（守反 AI 腔）。"""
    text = ("与此同时他走了过来。\n" * 5)
    fp = spe.extract_from_chapter_texts([text])
    collos = fp["metrics"].get("signature_collocations", [])
    assert not any("与此同时" in c for c in collos)


# ============ 统一入口 build_style_fingerprint ============

def test_build_fingerprint_prefers_chapter_texts():
    """原文优先（ground truth），无原文退回 profile。"""
    fp_text = spe.build_style_fingerprint(profile=_PROFILE_GZR, chapter_texts=[_CH_A, _CH_B])
    assert fp_text["source"] == "chapter_texts"
    fp_prof = spe.build_style_fingerprint(profile=_PROFILE_GZR, chapter_texts=None)
    assert fp_prof["source"] == "author_profile"
    assert spe.build_style_fingerprint(profile=None, chapter_texts=None) == {}


# ============ build_manifest 注入 + env 三态 ============

def _mk_min_project(tmp: Path, with_style=True) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    prog = {
        "volumes": [{"vol": 1, "title": "第一卷", "chapter_range": [1, 8]}],
        "cluster_blueprint": {
            "cluster_001": {
                "chapter_range": [1, 4],
                "scene_storyboard": [
                    {"ch": 1, "characters": ["主角"], "key_events": ["开局"],
                     "scene_type": ["悬疑"], "summary": "主角夜里独行遇到怪事"}
                ],
            }
        },
    }
    (db / "进度.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")
    (db / "人物卡.json").write_text(
        json.dumps({"characters": [{"id": "m", "name": "主角", "role": "主角"}]},
                   ensure_ascii=False), encoding="utf-8")
    if with_style:
        (db / "作者风格.json").write_text(json.dumps(_PROFILE_JSL, ensure_ascii=False),
                                          encoding="utf-8")
    return tmp


def _run_with_mode(tmp: Path, mode):
    """以指定 PROFILE_INJECT_MODE 跑 collector + full manifest，用后还原 env。"""
    prev = os.environ.get("PROFILE_INJECT_MODE")
    if mode is None:
        os.environ.pop("PROFILE_INJECT_MODE", None)
    else:
        os.environ["PROFILE_INJECT_MODE"] = mode
    try:
        s = bm.DatabaseScanner(tmp, 1)
        collected = bm._collect_author_style_fingerprint(s)
        manifest = bm.build_manifest(tmp, 1)
        return collected, manifest
    finally:
        if prev is None:
            os.environ.pop("PROFILE_INJECT_MODE", None)
        else:
            os.environ["PROFILE_INJECT_MODE"] = prev


def test_manifest_off_is_zero_regression():
    """默认 off（env 缺省）→ collector None + manifest 字段 None（零回归 · 守纪律 2）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_min_project(Path(d))
        collected, m = _run_with_mode(tmp, None)
        assert collected is None
        assert m["preflight"]["passed"], m["preflight"]
        assert "author_style_fingerprint" in m  # 字段存在
        assert m["author_style_fingerprint"] is None  # 但为 None（不影响 writer）


def test_manifest_off_explicit_string():
    """显式 PROFILE_INJECT_MODE=off → None。未知值也走 off（安全默认）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_min_project(Path(d))
        assert _run_with_mode(tmp, "off")[0] is None
        assert _run_with_mode(tmp, "garbage")[0] is None


def test_manifest_shadow_not_injected_but_logged():
    """shadow → 落盘 .style_fingerprint/ch_001.json，但 manifest 字段仍 None（零回归）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_min_project(Path(d))
        collected, m = _run_with_mode(tmp, "shadow")
        assert collected is None  # 影子不注入
        assert m["author_style_fingerprint"] is None
        # 但落盘了供离线核对
        fp_file = tmp / "_数据库" / ".style_fingerprint" / "ch_001.json"
        assert fp_file.exists()
        saved = json.loads(fp_file.read_text(encoding="utf-8"))
        assert saved["directives"]


def test_manifest_active_injects_fingerprint():
    """active → manifest.author_style_fingerprint 注入真指纹（writer 显式消费）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_min_project(Path(d))
        collected, m = _run_with_mode(tmp, "active")
        assert collected is not None
        af = m["author_style_fingerprint"]
        assert af is not None
        assert af["source"] == "author_profile"
        assert af["directives"]
        assert af["metrics"]["sentence_length"]["mean"] == 34.18


def test_no_style_profile_returns_none():
    """无 作者风格.json → collector None（即便 active）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_min_project(Path(d), with_style=False)
        # 没 style → has_style_profile False → None
        assert _run_with_mode(tmp, "active")[0] is None


def test_advisory_never_hard_gate():
    """北极星⑤：指纹是 advisory，绝不出现 hard_gate（不干涉模型判断）。"""
    fp = spe.extract_from_author_profile(_PROFILE_JSL)
    assert "hard_gate" not in json.dumps(fp, ensure_ascii=False)
    assert "advisory" in fp["_doc"]


def test_zero_new_dependencies():
    """守纪律 1：extractor 只依赖 stdlib + 同目录 style_analyzer（不引第三方）。"""
    src = (Path(__file__).resolve().parents[1] / "core" / "scripts"
           / "style_profile_extractor.py").read_text(encoding="utf-8")
    for forbidden in ("import numpy", "import pandas", "import torch", "import jieba",
                      "import sklearn", "import scipy"):
        assert forbidden not in src
