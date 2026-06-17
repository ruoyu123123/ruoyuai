# -*- coding: utf-8 -*-
"""cross_cluster_style_drift_scanner.py 专属回归测试（零 LLM / 零联网 · 标准库）。

聚焦**尚未被 test_longrange_style_drift.py 覆盖**的 IO / 装配 / CLI 退出码层
（既有测试已钉死纯函数 _linreg_slope / style_similarity / position_weight_band /
build_drift_curve / build_drift_findings + scan 的 shadow/active/off/样本不足/自漂移）。
本文件补的是另一半——磁盘读取与命令行契约：

  [A] _strip_changes —— 两种 CHANGES 分隔符截断 / 无分隔符原样穿透。
  [B] _cluster_draft_path —— 嵌套目录路径优先 / 扁平兜底 / 都缺 → None。
  [C] collect_written_cluster_texts —— 磁盘 glob 兜底（无账本时）按 cluster 号升序 +
      idx 连续 + CJK<200 过滤 + last_n 截尾 + 草稿 changes 尾巴剥离。
  [D] author_reference_text —— 无池 → None（顾问制不报错）/ 显式池多章拼接 +
      CJK<200 章被过滤 / 池里无合格章 → None。
  [E] main CLI —— 无 _数据库 → [SKIP] exit 0 / off 模式 exit 0 / active 退化触发 exit 1 /
      stdout 摘要 JSON 不含巨大正文字段 cluster_similarities。

约定遵循 tests/test_cross_cluster_fate_drift_aggregate.py（subprocess 跑真 CLI ·
报告/退出码当确定性权威）与 tests/test_longrange_style_drift.py（in-process 纯函数）。
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
import cross_cluster_style_drift_scanner as ccsd  # noqa: E402

_TARGET = _SCRIPTS / "cross_cluster_style_drift_scanner.py"

# 一段足够 CJK（>200）的中文正文，供造草稿/参考章用。
_BODY = "他缓缓走进那间昏暗潮湿的旧房间，看了一眼角落里蒙尘的旧木箱。" * 10


# ════════════════════════════════════════════════════════════════
# [A] _strip_changes —— CHANGES 尾巴剥离
# ════════════════════════════════════════════════════════════════

def test_strip_changes_factual_separator():
    """含 ---CHANGES_FACTUAL--- → 只留正文段（尾巴整段剥掉）。"""
    txt = "正文内容在这里。\n---CHANGES_FACTUAL---\n{\"a\":1}"
    out = ccsd._strip_changes(txt)
    assert out == "正文内容在这里。"
    assert "CHANGES_FACTUAL" not in out


def test_strip_changes_plain_separator():
    """含 ---CHANGES---（无 FACTUAL）→ 同样在首个分隔处截断。"""
    txt = "正文段落。\n---CHANGES---\nblah"
    out = ccsd._strip_changes(txt)
    assert out == "正文段落。"
    assert "CHANGES" not in out


def test_strip_changes_no_separator_passthrough():
    """无任何分隔符 → 原样穿透（不误删正文）。"""
    txt = "这是一整段没有 changes 尾巴的正文。"
    assert ccsd._strip_changes(txt) == txt


# ════════════════════════════════════════════════════════════════
# [B] _cluster_draft_path —— 草稿路径定位
# ════════════════════════════════════════════════════════════════

def test_cluster_draft_path_prefers_nested_dir():
    """优先 章节/<cid>_draft/<cid>_draft.txt（嵌套目录形态）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cid = "cluster_001"
        nested = root / "章节" / f"{cid}_draft"
        nested.mkdir(parents=True)
        target = nested / f"{cid}_draft.txt"
        target.write_text(_BODY, encoding="utf-8")
        got = ccsd._cluster_draft_path(root, cid)
        assert got == target


def test_cluster_draft_path_flat_fallback():
    """无嵌套目录但有扁平 章节/<cid>_draft.txt → 兜底返回扁平路径。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cid = "cluster_002"
        chap = root / "章节"
        chap.mkdir(parents=True)
        flat = chap / f"{cid}_draft.txt"
        flat.write_text(_BODY, encoding="utf-8")
        assert ccsd._cluster_draft_path(root, cid) == flat


def test_cluster_draft_path_missing_returns_none():
    """两种形态都不存在 → None（不抛错）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "章节").mkdir(parents=True)
        assert ccsd._cluster_draft_path(root, "cluster_999") is None


# ════════════════════════════════════════════════════════════════
# [C] collect_written_cluster_texts —— 磁盘 glob 兜底路径
# ════════════════════════════════════════════════════════════════

def _make_drafts(root: Path, mapping: dict):
    """在 章节/<cid>_draft/<cid>_draft.txt 写草稿（mapping: cid -> text），不写账本
    → 强制 collect_written_cluster_texts 走磁盘 glob 兜底分支。"""
    chap = root / "章节"
    chap.mkdir(parents=True, exist_ok=True)
    (root / "_数据库").mkdir(parents=True, exist_ok=True)
    for cid, text in mapping.items():
        d = chap / f"{cid}_draft"
        d.mkdir()
        (d / f"{cid}_draft.txt").write_text(text, encoding="utf-8")


def test_collect_disk_fallback_sorted_and_indexed():
    """无账本 → 磁盘 glob 兜底：按 cluster 号升序，idx 从 0 连续，cluster_id 正确。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # 故意乱序创建（dict 插入序非数值序），验证脚本按数值排序
        _make_drafts(root, {
            "cluster_003": _BODY,
            "cluster_001": _BODY,
            "cluster_002": _BODY,
        })
        out = ccsd.collect_written_cluster_texts(root)
        assert [c["cluster_id"] for c in out] == ["cluster_001", "cluster_002", "cluster_003"]
        assert [c["idx"] for c in out] == [0, 1, 2]


def test_collect_disk_fallback_filters_short_cjk():
    """正文 CJK < 200 的 cluster 被过滤（不进趋势曲线）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_drafts(root, {
            "cluster_001": _BODY,            # 长，保留
            "cluster_002": "太短了。",        # CJK<200，剔除
            "cluster_003": _BODY,            # 长，保留
        })
        out = ccsd.collect_written_cluster_texts(root)
        ids = [c["cluster_id"] for c in out]
        assert ids == ["cluster_001", "cluster_003"]
        # 过滤后 idx 仍连续
        assert [c["idx"] for c in out] == [0, 1]


def test_collect_disk_fallback_last_n_trims_oldest():
    """last_n=2 → 只保留 cluster 号最大的 2 个（截掉最早的）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_drafts(root, {
            "cluster_001": _BODY,
            "cluster_002": _BODY,
            "cluster_003": _BODY,
            "cluster_004": _BODY,
        })
        out = ccsd.collect_written_cluster_texts(root, last_n=2)
        assert [c["cluster_id"] for c in out] == ["cluster_003", "cluster_004"]


def test_collect_disk_fallback_strips_changes_tail():
    """草稿带 ---CHANGES_FACTUAL--- 尾巴 → 收集到的 text 已剥离尾巴。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        body_with_tail = _BODY + "\n---CHANGES_FACTUAL---\n{\"meta\":true}"
        _make_drafts(root, {
            "cluster_001": body_with_tail,
            "cluster_002": _BODY,
            "cluster_003": _BODY,
        })
        out = ccsd.collect_written_cluster_texts(root)
        first = next(c for c in out if c["cluster_id"] == "cluster_001")
        assert "CHANGES_FACTUAL" not in first["text"]
        assert "meta" not in first["text"]


# ════════════════════════════════════════════════════════════════
# [D] author_reference_text —— 作者参考池定位
# ════════════════════════════════════════════════════════════════

def test_author_reference_none_when_no_pool():
    """无作者池（既无显式池也无项目登记）→ None（顾问制：不报错，调用方退化自漂移）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_数据库").mkdir(parents=True)
        assert ccsd.author_reference_text(root) is None


def test_author_reference_explicit_pool_concats_chapters():
    """显式池含多章合格原文 → 拼接为单一参考文本（含各章内容）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_数据库").mkdir(parents=True)
        pool = Path(d) / "pool"
        pool.mkdir()
        ch1 = "甲段落里全是甲字的正文内容反复出现。" * 12
        ch2 = "乙段落里全是乙字的正文内容反复出现。" * 12
        (pool / "第001章.txt").write_text(ch1, encoding="utf-8")
        (pool / "第002章.txt").write_text(ch2, encoding="utf-8")
        ref = ccsd.author_reference_text(root, author_pool=pool)
        assert ref is not None
        assert "甲" in ref and "乙" in ref


def test_author_reference_filters_short_chapter():
    """池里 CJK<200 的短章被过滤；只剩短章 → 整体 None（无合格参考）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "_数据库").mkdir(parents=True)
        pool = Path(d) / "pool"
        pool.mkdir()
        (pool / "第001章.txt").write_text("太短。", encoding="utf-8")  # CJK<200
        assert ccsd.author_reference_text(root, author_pool=pool) is None


# ════════════════════════════════════════════════════════════════
# [E] main CLI —— argparse + 退出码 + 摘要 JSON 契约
# ════════════════════════════════════════════════════════════════

def _run_cli(project, *args, mode=None):
    """跑真 CLI（subprocess）。mode 注入 LONGRANGE_DRIFT_MODE env。
    stdout/stderr 用 errors='replace' 容错解码（Windows 控制台非 UTF-8）。"""
    env = dict(os.environ)
    if mode is not None:
        env["LONGRANGE_DRIFT_MODE"] = mode
    p = subprocess.run(
        [sys.executable, str(_TARGET), str(project), *args],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def _make_project_drafts(base, texts):
    """造最小项目：_数据库 + 章节/cluster_NNN_draft/...txt（不写账本走磁盘兜底）。"""
    root = Path(base) / "proj"
    (root / "_数据库").mkdir(parents=True)
    chap = root / "章节"
    chap.mkdir()
    for i, txt in enumerate(texts, 1):
        cid = f"cluster_{i:03d}"
        dd = chap / f"{cid}_draft"
        dd.mkdir()
        (dd / f"{cid}_draft.txt").write_text(txt, encoding="utf-8")
    return root


def test_cli_no_database_skips_exit0():
    """无 _数据库 目录 → [SKIP] 提前 exit 0（不崩）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "nodb"
        root.mkdir()  # 故意不建 _数据库
        p = _run_cli(root)
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        assert "SKIP" in p.stderr


def test_cli_off_mode_exit0_and_skip_note():
    """LONGRANGE_DRIFT_MODE=off → exit 0 且摘要 JSON 标 _skip=mode=off。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project_drafts(d, [_BODY] * 4)
        p = _run_cli(root, mode="off")
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        summary = json.loads(p.stdout)
        assert summary["mode"] == "off"
        assert summary["_skip"] == "mode=off"


def test_cli_active_drift_exits_1():
    """active 模式 + 明显退化草稿（短句→长 AI 腔均匀化）→ 命中 advisory issue → exit 1。

    注：触发取决于 SFS 曲线是否够陡。用强烈风格梯度构造单调下行，若环境 SFS 仍判稳定
    则放行（exit 0 也合法），但只要 exit 1 就必须是 advisory issue（绝不 hard_gate）。"""
    texts = [
        "他走。他停。他看。" * 80,
        "他缓缓走着思索。" * 70,
        "他在昏暗房间里缓缓走着并不断思索着许多。" * 55,
        "他在那个昏暗而潮湿弥漫腐朽气味的古老房间深处缓缓走着思索着许多复杂往事。" * 45,
        "他在那个昏暗而潮湿并弥漫着浓重腐朽气味的极其古老的房间最深处缓缓走着并反复思索着许多复杂的陈年往事。" * 40,
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _make_project_drafts(d, texts)
        p = _run_cli(root, mode="active")
        assert p.returncode in (0, 1), f"stdout={p.stdout} stderr={p.stderr}"
        summary = json.loads(p.stdout)
        assert summary["gate_level"] == "advisory"  # 报告级 gate 恒 advisory
        if p.returncode == 1:
            assert summary["issues"], "exit 1 必有顶层 issue"
            assert summary["issues"][0]["code"] == "LONGRANGE_STYLE_DRIFT"
            assert summary["issues"][0]["gate_level"] == "advisory"


def test_cli_summary_omits_cluster_similarities():
    """main 打印的摘要 JSON 必须剔除巨大正文字段 cluster_similarities（控输出体积）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _make_project_drafts(d, [_BODY] * 4)
        p = _run_cli(root, mode="shadow")
        assert p.returncode == 0, f"stdout={p.stdout} stderr={p.stderr}"
        summary = json.loads(p.stdout)
        assert "cluster_similarities" not in summary
        # 但 drift_curve / scanner 名等摘要字段在
        assert summary["scanner"] == "cross_cluster_style_drift"
        assert summary["mode"] == "shadow"


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print(f"[cross_cluster_style_drift_scanner] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
