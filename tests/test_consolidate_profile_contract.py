# -*- coding: utf-8 -*-
"""C14 作者档数值契约测试（2026-06-27）——治『作者档静默退通用基线』。

背景：consolidate_author_profile 从单章 metrics 确定性聚合 consumer 数值字段。若源 metrics
schema 漂移（producer 写错字段名/结构）→ 聚合出的 quantitative 缺核心 consumer 键 → 下游
build_manifest/validate_style band 门控静默退**通用兜底基线**（弱模型被推成流水账·惊悚乐园翻车根因）。

C14 把『缺数据静默不写』升级为『缺核心契约键即响亮失败』：
  ①生产端：consolidate() 写盘前自断言 total>0 却缺核心键 → [FATAL] exit2。
  ②消费端：db_schema_validate --require-quantitative-keys 在 plan step 层再确认产出档契约键齐全。

🔴 北极星④⑤：只断言键 PRESENCE（来自真 metrics·total>0）·**绝不断言数值落某区间**
（高方差作者句长 31 也合法·防题材先验误杀）。

零依赖范式（__main__ 自跑·亦可 pytest 发现）。
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import consolidate_author_profile as cap  # noqa: E402
import db_schema_validate as dsv  # noqa: E402


# ───────── mock 风格库 ─────────
def _healthy_profile(i: int, dialogue: float = 0.25) -> dict:
    """健康单章 metrics profile（style_analyzer 标准 schema·全核心字段齐）。"""
    return {"file": f"第{i:03d}章.txt", "profile": {
        "total_chinese_chars": 2800 + i,
        "sentence_stats": {"mean": 30.0 + i, "std": 20.0, "median": 28},
        "paragraph_stats": {"mean": 50.0 + i, "std": 30.0, "median": 48},
        "dialogue_ratio": dialogue,
        "single_sentence_para_ratio": 0.50,
    }}


def _drifted_profile(i: int) -> dict:
    """漂移单章 metrics profile（字段名全改·模拟 producer schema 漂移）。"""
    return {"file": f"第{i:03d}章.txt", "profile": {
        "total_chinese_chars": 2800 + i,
        "sent_mean": 30.0,            # 漂移：应为 sentence_stats.mean
        "para_mean": 50.0,            # 漂移：应为 paragraph_stats.mean
        "dialog_pct": 25,             # 漂移：应为 dialogue_ratio
    }}


def _mk_proj(root: Path, n: int = 4, *, dialogue: float = 0.25, drift: bool = False) -> Path:
    """造最小 mock 风格库：蒸馏进度/ch{i}_metrics.json + 作者风格.json（写盘 target）。"""
    proj = root / "测试书"
    dist = proj / "蒸馏进度"
    dist.mkdir(parents=True)
    for i in range(1, n + 1):
        prof = _drifted_profile(i) if drift else _healthy_profile(i, dialogue)
        (dist / f"ch{i}_metrics.json").write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    (proj / "作者风格.json").write_text(json.dumps(
        {"source": "测试书", "core_style_signature": {"标签": "测试"}}, ensure_ascii=False), encoding="utf-8")
    return proj


def _run_capturing_exit(fn):
    """跑 fn·捕获 SystemExit + stderr（pytest fixture 无关·__main__ 亦可跑）。返回 (exit_code_or_None, stderr)。"""
    buf = io.StringIO()
    code = None
    with contextlib.redirect_stderr(buf):
        try:
            fn()
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    return code, buf.getvalue()


# ───────── C14① consolidate 生产端自断言 ─────────
def test_c14_healthy_metrics_full_keys_exit0():
    """(a) 正常 metrics → q 含全核心键 → consolidate 正常返回（无 exit）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d), 4)
        code, err = _run_capturing_exit(lambda: cap.consolidate(proj, 4))
        assert code is None, f"健康 metrics 不应 exit·得 code={code}·stderr={err}"
        q = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))["quantitative"]
        assert q["sentence_length"]["mean"] is not None
        assert q["single_sentence_para_ratio"] is not None
        assert ("dialogue_ratio_pct" in q or "dialogue_ratio" in q)


def test_c14_drifted_metrics_empty_q_exit2_fatal():
    """(b) metrics 键漂移 → q 缺核心键 → [FATAL] exit2 + stderr。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d), 3, drift=True)
        code, err = _run_capturing_exit(lambda: cap.consolidate(proj, 3))
        assert code == 2, f"漂移 metrics 应 exit2·得 code={code}"
        assert "[FATAL]" in err
        # 应列出缺失的核心契约键（疑似源 schema 漂移）
        assert "sentence_length" in err and "single_sentence_para_ratio" in err


def test_c14_dialogue_ratio_zero_key_present_exit0():
    """(c) 对话比真为 0 → dialogue_ratio 键仍在（断言键非值）→ 不误杀 exit0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d), 4, dialogue=0.0)
        code, err = _run_capturing_exit(lambda: cap.consolidate(proj, 4))
        assert code is None, f"对话比 0 不应被误杀·得 code={code}·stderr={err}"
        q = json.loads((proj / "作者风格.json").read_text(encoding="utf-8"))["quantitative"]
        assert q["dialogue_ratio"]["mean"] == 0.0  # 值为 0 但键在
        assert q["dialogue_ratio_pct"]["mean"] == 0.0


def test_c14_short_sample_keys_present_exit0():
    """(d) 短样本 2 章但核心键齐 → exit0（断言键存在·不因样本短而误杀）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d), 2)
        code, err = _run_capturing_exit(lambda: cap.consolidate(proj, 2))
        assert code is None, f"短样本但键齐不应 exit·得 code={code}·stderr={err}"


def test_c14_assert_never_checks_value_range():
    """北极星④⑤守护：高方差作者句长 31（远超通用碎句基线）→ 键在即过·绝不因数值越界 exit。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_proj(Path(d), 4)  # _healthy 句长 mean ~31-34（惊悚乐园金标准 31）
        code, _ = _run_capturing_exit(lambda: cap.consolidate(proj, 4))
        assert code is None
        # _missing_consumer_keys 纯查键·句长值任意都不应进 missing
        assert cap._missing_consumer_keys({"sentence_length": {"mean": 999.0},
                                           "paragraph_length_chars": {"mean": 1},
                                           "dialogue_ratio": {"mean": 0.0},
                                           "single_sentence_para_ratio": 0.0}) == []


# ───────── C14② db_schema_validate --require-quantitative-keys 消费端二道闸 ─────────
def _write_profile(root: Path, quantitative: dict) -> Path:
    p = root / "作者风格.json"
    p.write_text(json.dumps({"quantitative": quantitative}, ensure_ascii=False), encoding="utf-8")
    return p


def test_c14_require_keys_pass():
    """齐全 quantitative → _require_quantitative_keys 返回 0。"""
    with tempfile.TemporaryDirectory() as d:
        p = _write_profile(Path(d), {
            "sentence_length": {"mean": 30.0},
            "paragraph_length_chars": {"mean": 50.0},
            "dialogue_ratio_pct": {"mean": 25.0},
            "single_sentence_para_ratio": 0.5,
        })
        assert dsv._require_quantitative_keys(p) == 0


def test_c14_require_keys_alias_paragraph_mean_chars_pass():
    """paragraph_length.mean_chars 别名（无 paragraph_length_chars）也算契约满足。"""
    with tempfile.TemporaryDirectory() as d:
        p = _write_profile(Path(d), {
            "sentence_length": {"mean": 30.0},
            "paragraph_length": {"mean_chars": 50.0, "single_sentence_para_ratio_mean": 0.5},
            "dialogue_ratio": {"mean": 0.25},
            "single_sentence_para_ratio": 0.5,
        })
        assert dsv._require_quantitative_keys(p) == 0


def test_c14_require_keys_missing_returns_2():
    """缺 single_sentence_para_ratio → 返回 2。"""
    with tempfile.TemporaryDirectory() as d:
        p = _write_profile(Path(d), {
            "sentence_length": {"mean": 30.0},
            "paragraph_length_chars": {"mean": 50.0},
            "dialogue_ratio_pct": {"mean": 25.0},
        })
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = dsv._require_quantitative_keys(p)
        assert rc == 2
        assert "[FATAL]" in buf.getvalue() and "single_sentence_para_ratio" in buf.getvalue()


def test_c14_require_keys_missing_file_returns_2():
    """作者档不存在 → 2（缺产出即失败）。"""
    with tempfile.TemporaryDirectory() as d:
        assert dsv._require_quantitative_keys(Path(d) / "不存在.json") == 2


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
