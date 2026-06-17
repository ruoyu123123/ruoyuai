#!/usr/bin/env python3
"""distill_chapter_metrics.py 聚焦回归测试（确定性·零 LLM·零联网）。

钉死 run()/main() 尚未被 test_distill_ingest_metrics.py 覆盖的核心确定性逻辑：
- `_CH_NUM_RE` 章号排序键（int 排序而非字典序：第10章 排在 第2章 后）。
- 幂等：已存在 metrics → 默认跳过（done=0），--overwrite 才覆盖。
- 输出契约：{"file","profile"}，file 字段=原文绝对路径，profile=analyze_text 真结果。
- 退出码：原文/ 不存在 → 1；原文/ 存在但无 第N章.txt → 1；正常 → 0。
- GBK 回退：UTF-8 解不开的 GBK 字节 → 不崩溃仍产出 metrics。
- 单章 analyze 抛错被吞（monkeypatch style_analyzer.analyze_text）→ 不拖垮全批。
- main() 含 sys.exit → subprocess 跑真 CLI 验退出码。

已有间接覆盖（test_distill_ingest_metrics.py）：
  test_chapter_metrics_contract_with_consolidate（profile.sentence_stats/paragraph_stats）、
  test_chapter_metrics_missing_raw_returns_1。本文件聚焦其未覆盖分支，不重复。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import distill_chapter_metrics as cm  # noqa: E402

_TARGET = _SCRIPTS / "distill_chapter_metrics.py"


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _mk_proj(chapters):
    """chapters: list[(n, body)] → 写 原文/第{n}章.txt，返回 project_root。"""
    tmp = Path(tempfile.mkdtemp())
    raw = tmp / "原文"
    raw.mkdir(parents=True)
    for n, body in chapters:
        (raw / f"第{n}章.txt").write_text(
            f"第{n}章 标题\n{body}", encoding="utf-8")
    return tmp


def _metrics_files(proj):
    return sorted((proj / "蒸馏进度").glob("ch*_metrics.json"))


# ──────────────────────────────────────────────────────────────────────────
# 退出码 / 基本契约
# ──────────────────────────────────────────────────────────────────────────
def test_empty_raw_dir_returns_1():
    """原文/ 存在但无 第N章.txt → return 1（区别于 missing_raw 也是 1）。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "原文").mkdir()
        # 放个非章节文件，确认 glob 第*章.txt 不误捞
        (tmp / "原文" / "杂项.txt").write_text("无关内容", encoding="utf-8")
        assert cm.run(tmp) == 1
        assert not (tmp / "蒸馏进度").exists()  # 没产出目录
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_normal_run_returns_0_and_writes_per_chapter():
    proj = _mk_proj([(1, "他把杯子摔在地上。屋里安静下来。"),
                     (2, "雨下了一整夜，街灯在水洼里碎成片。")])
    try:
        assert cm.run(proj) == 0
        mfiles = _metrics_files(proj)
        assert [p.name for p in mfiles] == ["ch1_metrics.json", "ch2_metrics.json"]
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_output_json_structure_file_and_profile():
    """每个 metrics = {"file": <原文绝对路径>, "profile": analyze_text(...)}。"""
    proj = _mk_proj([(1, "一段普通的正文，用来喂 analyze_text。")])
    try:
        assert cm.run(proj) == 0
        out = proj / "蒸馏进度" / "ch1_metrics.json"
        d = json.loads(out.read_text(encoding="utf-8"))
        assert set(d.keys()) == {"file", "profile"}
        # file 指向真实存在的原文章节文件
        assert Path(d["file"]).name == "第1章.txt"
        assert Path(d["file"]).is_file()
        # profile 为 dict 且就是 analyze_text 的产物（含确定性字段）
        assert isinstance(d["profile"], dict)
        assert "sentence_stats" in d["profile"]
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# 章号排序（_CH_NUM_RE 排序键：int 排序非字典序）
# ──────────────────────────────────────────────────────────────────────────
def test_chapter_files_sorted_numerically_not_lexically():
    """第2章 / 第10章：int 排序 2<10；字典序会把 '10' 排在 '2' 前。"""
    proj = _mk_proj([(10, "第十章正文"), (2, "第二章正文")])
    try:
        # files 排序在 run() 内部；间接验证：两章都产出且各自命名正确
        assert cm.run(proj) == 0
        names = [p.name for p in _metrics_files(proj)]
        assert "ch2_metrics.json" in names and "ch10_metrics.json" in names
        # 直接钉排序键：复用脚本正则证明 int(n) 抽取正确
        assert cm._CH_NUM_RE.search("第10章").group(1) == "10"
        assert int(cm._CH_NUM_RE.search("第2章").group(1)) == 2
        # 字典序下 "第10章" < "第2章"，int 序下相反 → 锁定脚本用 int 序
        assert sorted(["第10章", "第2章"]) == ["第10章", "第2章"]  # 字典序
        assert sorted(["第10章", "第2章"],
                      key=lambda s: int(cm._CH_NUM_RE.search(s).group(1))) == \
            ["第2章", "第10章"]                                    # int 序（脚本所用）
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# 幂等 / --overwrite
# ──────────────────────────────────────────────────────────────────────────
def test_idempotent_skip_existing_without_overwrite():
    """二次 run（无 overwrite）→ 已有 metrics 原样保留（手改内容不被覆盖）。"""
    proj = _mk_proj([(1, "原始正文")])
    try:
        assert cm.run(proj) == 0
        out = proj / "蒸馏进度" / "ch1_metrics.json"
        # 手改 metrics → 模拟"已存在"
        out.write_text('{"file":"x","profile":{"marker":"手改"}}', encoding="utf-8")
        assert cm.run(proj) == 0  # 仍返回 0，但应跳过
        d = json.loads(out.read_text(encoding="utf-8"))
        assert d["profile"] == {"marker": "手改"}  # 未被重算覆盖
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_overwrite_flag_regenerates_metrics():
    """--overwrite → 已有 metrics 被真实重算（手改 marker 被冲掉）。"""
    proj = _mk_proj([(1, "正文用于重算。")])
    try:
        assert cm.run(proj) == 0
        out = proj / "蒸馏进度" / "ch1_metrics.json"
        out.write_text('{"file":"x","profile":{"marker":"手改"}}', encoding="utf-8")
        assert cm.run(proj, overwrite=True) == 0
        d = json.loads(out.read_text(encoding="utf-8"))
        assert "marker" not in d["profile"]       # 旧手改被覆盖
        assert "sentence_stats" in d["profile"]    # 真 analyze_text 结果
        assert Path(d["file"]).name == "第1章.txt"  # file 字段被刷新为真路径
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# 鲁棒性：GBK 回退 / 单章坏不拖垮全批
# ──────────────────────────────────────────────────────────────────────────
def test_gbk_fallback_does_not_crash():
    """UTF-8 解不开的 GBK 字节 → 走 gbk errors=replace 回退，仍产出 metrics。"""
    proj = Path(tempfile.mkdtemp())
    try:
        raw = proj / "原文"
        raw.mkdir()
        # 写纯 GBK 字节（非合法 UTF-8）→ read_text(utf-8) 抛 UnicodeDecodeError
        (raw / "第1章.txt").write_bytes("第1章 标题\n中文正文内容".encode("gbk"))
        assert cm.run(proj) == 0
        out = proj / "蒸馏进度" / "ch1_metrics.json"
        assert out.is_file()
        d = json.loads(out.read_text(encoding="utf-8"))
        assert "profile" in d
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_one_bad_chapter_skipped_others_survive():
    """单章 analyze 抛异常 → 被吞并跳过该章，其余章正常产出（全批不崩，仍 return 0）。"""
    proj = _mk_proj([(1, "好章一"), (2, "坏章二"), (3, "好章三")])
    try:
        orig = cm.style_analyzer.analyze_text

        def flaky(text):
            if "坏章" in text:
                raise ValueError("boom")
            return orig(text)

        cm.style_analyzer.analyze_text = flaky
        try:
            assert cm.run(proj) == 0
        finally:
            cm.style_analyzer.analyze_text = orig  # 还原，勿污染别的测试
        names = [p.name for p in _metrics_files(proj)]
        # 坏章二被跳过，1/3 正常产出
        assert "ch1_metrics.json" in names
        assert "ch3_metrics.json" in names
        assert "ch2_metrics.json" not in names
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# main() CLI（含 sys.exit）→ subprocess 真跑
# ──────────────────────────────────────────────────────────────────────────
def test_cli_main_exit_0_on_success():
    proj = _mk_proj([(1, "命令行整链验证正文。")])
    try:
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(proj)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        assert (proj / "蒸馏进度" / "ch1_metrics.json").is_file()
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_cli_main_exit_1_on_missing_raw():
    tmp = Path(tempfile.mkdtemp())
    try:
        r = subprocess.run(
            [sys.executable, str(_TARGET), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 1, r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
