# -*- coding: utf-8 -*-
"""test_statistical_threshold_baseline.py — Wave-6 统计族阈值基线 harness 回归测试。

确定性·纯 stdlib（除 6 个真 scanner 模块本身也是纯 stdlib/regex）·不接触 embedding/daemon。

覆盖：
  1. 章号正则兼容不同位数补零（诡秘之主 0001 vs 主神大道/轮回乐园无补零）
  2. list_chapter_files：按章号升序 + 忽略非「第N章.txt」文件 + 目录不存在诚实报错
  3. sample_chapters：同 seed 确定性 / 不同 seed 通常不同 / n≥可用章数直接全收 /
     书目录不存在 + 目录存在但无匹配文件 两种「诚实报错」路径
  4. percentile / distribution_stats / ecdf_at 已知值对拍
  5. aggregate_rows：ineligible 章不进分位数分布，但 flagged 计数与 eligible 无关独立累计
  6. build_advice 三态判据（过严 >30% / 过松 <1% / 适中）含精确边界（=30%、=1% 不触发严格分支）
  7. run_baseline 注入 mock 指标抽取器端到端产报告（结构 + 落盘 json/md + 可 json 序列化）
  8. run_baseline 防呆：未注册 scanner 名 → KeyError；书目录不存在 → 诚实 FileNotFoundError
     （不吞、不跳过、不产生部分报告）
  9. render_markdown 含各 scanner 小节 + 判定文案
  10. 真 scanner 集成：make_real_extract_fn 复现 scanner 自身 scan() 计算结果（零逻辑复制的
      验证）+ group_dialogue_balance 的 eligible_guard 正确排除非群戏规模章
  11. run_baseline 真 scanner 路径下 6 个 scanner 的 registry_runtime_consistent 全真
      （registry current_value 与运行时常量同步·防文档漂移）
  12. main() CLI 全流程（真 scanner·真 argparse·落盘 json/md）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_CALIB_DIR = _ROOT / "core" / "ml" / "calibration"
if str(_CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(_CALIB_DIR))

import statistical_threshold_baseline as mod  # noqa: E402


# ════════════════════════════ 通用 fixture 素材 ════════════════════════════

def _write_chapters(styles_root: Path, book: str, chapters: dict) -> Path:
    """chapters: {章号: 正文}。写到 <styles_root>/<book>/原文/第N章.txt。"""
    d = styles_root / book / "原文"
    d.mkdir(parents=True, exist_ok=True)
    for num, text in chapters.items():
        (d / f"第{num}章.txt").write_text(text, encoding="utf-8")
    return d


def _write_registry(path: Path, entries: list) -> None:
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")


# 干净填充：不含任何受测 6 scanner 的触发词（借用 test_group_dialogue_balance_scanner.py 同款）
_FILLER = "群山连绵云雾缭绕溪水潺潺向远方静静流淌。\n" * 30

# dramatic irony 高密度触发稿（借用 test_dramatic_irony_scanner.py 同款搭配模式）
_IRONY_DRAFT = ("他推开门朝里面看了一眼众人谈笑风生毫无察觉。殊不知这背后另有隐情。"
                "夜色渐深无人知晓一切静悄悄的。殊不知真相远比想象中复杂。"
                "众人各自散去谁都没有多想。殊不知一切早已注定。") + "\n" + _FILLER

_CLEAN_DRAFT = "他推开门，桌上的信封压着一枚铜钥匙。他没回头，影子在门后停住。" + "\n" + _FILLER

# 12 行显式点名归属（专名+说类动词·群戏规模）—— 借用 test_group_dialogue_balance_scanner.py 原文
_NAME_LINES = [
    "张三笑道：“今天天气真好我们一起去爬山看风景吧。”",
    "李四说：“好啊那就这么定下来一起出发。”",
    "老钟道：“我去准备一些干粮和饮用清水。”",
    "王五问：“我们到底要从哪一条山路上去呢。”",
    "陈六喊：“快一点天色不早路上还得赶时间。”",
    "赵七叫道：“前面那条小溪边的景色最好看。”",
    "孙八开口：“要不要带上帐篷今晚就在山上过夜。”",
    "周九说道：“我看天上的云有点厚怕是要变天。”",
    "吴十冷笑道：“你们这些人就是胆子太小怕什么。”",
    "郑大问道：“万一真下雨了我们该往哪里躲避呢。”",
    "王二喊道：“别磨蹭了再不走太阳就要落下去了。”",
    "钱三道：“放心吧这条路我从小走到大闭眼都行。”",
]
_GROUP_DIALOGUE_DRAFT = "\n".join(_NAME_LINES) + "\n" + _FILLER
_FEW_DIALOGUE_DRAFT = "\n".join(_NAME_LINES[:3]) + "\n" + _FILLER   # 3 行 < MIN_DIALOGUE_LINES=8


# ════════════════════════════ 1. 章号正则 + 文件枚举 ════════════════════════════

@pytest.mark.parametrize("stem,expected", [
    ("第0001章", 1), ("第1000章", 1000), ("第100章", 100), ("第0042章", 42),
])
def test_chapter_num_regex_handles_padding_variants(stem, expected):
    m = mod.CHAPTER_NUM_RE.search(stem)
    assert m is not None
    assert int(m.group(1)) == expected


def test_list_chapter_files_sorts_ascending_and_ignores_non_matching(tmp_path):
    d = _write_chapters(tmp_path / "styles", "书甲", {3: "c", 1: "a", 2: "b"})
    (d / "说明.txt").write_text("不是章节文件", encoding="utf-8")   # 无「第N章」pattern·应被忽略
    files = mod.list_chapter_files(d)
    assert [num for num, _ in files] == [1, 2, 3]
    assert all(fp.name != "说明.txt" for _, fp in files)


def test_list_chapter_files_missing_dir_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod.list_chapter_files(tmp_path / "不存在的书" / "原文")


# ════════════════════════════ 2. 采样确定性 + 诚实报错 ════════════════════════════

def test_sample_chapters_same_seed_deterministic(tmp_path):
    d = _write_chapters(tmp_path / "styles", "书甲", {i: f"第{i}章正文" for i in range(1, 41)})
    p1 = mod.sample_chapters(d, 10, 20260704, "书甲")
    p2 = mod.sample_chapters(d, 10, 20260704, "书甲")
    assert p1 == p2
    assert [num for num, _ in p1] == sorted(num for num, _ in p1)   # 章号升序返回


def test_sample_chapters_different_seed_usually_differs(tmp_path):
    d = _write_chapters(tmp_path / "styles", "书甲", {i: f"第{i}章正文" for i in range(1, 41)})
    p1 = mod.sample_chapters(d, 10, 1, "书甲")
    p2 = mod.sample_chapters(d, 10, 2, "书甲")
    assert p1 != p2


def test_sample_chapters_returns_all_sorted_when_n_gte_available(tmp_path):
    d = _write_chapters(tmp_path / "styles", "书甲", {i: f"第{i}章正文" for i in range(1, 6)})
    picked = mod.sample_chapters(d, 100, 42, "书甲")
    assert [num for num, _ in picked] == [1, 2, 3, 4, 5]


def test_sample_chapters_missing_book_dir_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        mod.sample_chapters(tmp_path / "styles" / "不存在的书" / "原文", 10, 1, "不存在的书")


def test_sample_chapters_no_matching_files_raises_filenotfound(tmp_path):
    d = tmp_path / "styles" / "空书" / "原文"
    d.mkdir(parents=True)
    (d / "笔记.txt").write_text("无章号文件", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        mod.sample_chapters(d, 10, 1, "空书")


# ════════════════════════════ 3. 统计函数已知值对拍 ════════════════════════════

def test_percentile_known_values():
    assert mod.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert mod.percentile([], 0.5) == 0.0
    assert mod.percentile([42.0], 0.9) == 42.0
    assert mod.percentile(list(range(0, 101, 10)), 0.9) == 90   # 精确落在下标 9


def test_distribution_stats_keys_and_known_values():
    empty = mod.distribution_stats([])
    assert empty == {"n": 0, "mean": 0.0, "p5": 0.0, "p25": 0.0, "p50": 0.0,
                      "p75": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0}
    s = mod.distribution_stats([1, 2, 3, 4, 5])
    assert s["n"] == 5 and s["mean"] == 3.0 and s["p50"] == 3.0 and s["max"] == 5.0


def test_ecdf_at_known_values_and_empty():
    xs = [1, 2, 3, 4, 5]
    assert mod.ecdf_at(xs, 3) == 0.6
    assert mod.ecdf_at(xs, 0) == 0.0
    assert mod.ecdf_at(xs, 5) == 1.0
    assert mod.ecdf_at([], 1.0) == 0.0


# ════════════════════════════ 4. aggregate_rows ════════════════════════════

def test_aggregate_rows_filters_ineligible_but_counts_flagged_independently():
    """ineligible 章不进 metrics/dist·但 chapters_flagged 按全部 rows 累计(与 eligible 无关·
    这是 flag_rate_scanned vs flag_rate_eligible 两套分母并存的关键行为)。"""
    rows = [
        {"metric": 1.0, "flagged": False, "eligible": True},
        {"metric": 2.0, "flagged": True, "eligible": True},
        {"metric": 5.0, "flagged": True, "eligible": False},     # 边界情形：ineligible 仍 flagged
        {"metric": None, "flagged": False, "eligible": False},
        {"metric": None, "flagged": False, "eligible": False, "error": "UnicodeDecodeError: boom"},
    ]
    agg = mod.aggregate_rows(rows)
    assert agg["chapters_scanned"] == 5
    assert agg["chapters_eligible"] == 2
    assert agg["chapters_flagged"] == 2
    assert agg["errors"] == 1
    assert agg["_metrics"] == [1.0, 2.0]
    assert agg["dist"]["n"] == 2


# ════════════════════════════ 5. build_advice 三态判据 ════════════════════════════

def _merged(metrics, flagged, scanned=None):
    scanned = len(metrics) if scanned is None else scanned
    return {"chapters_scanned": scanned, "chapters_flagged": flagged,
            "chapters_eligible": len(metrics), "dist": mod.distribution_stats(metrics),
            "_metrics": metrics}


def test_build_advice_overstrict_verdict():
    merged = _merged(list(range(10)), flagged=4)   # 40% > 30%
    advice = mod.build_advice({"constant": "FAKE_FLOOR"}, floor=3, merged=merged)
    assert advice["verdict"] == "过严"
    # verdict 标签本身只在 render_markdown 拼接时才嵌入文案·text 字段自身只含常量名+数据
    assert "FAKE_FLOOR" in advice["text"] and "40.0%" in advice["text"]
    assert advice["flag_rate_scanned"] == pytest.approx(0.4)


def test_build_advice_uninformative_verdict():
    merged = _merged(list(range(200)), flagged=0)   # 0% < 1%
    advice = mod.build_advice({"constant": "FAKE_FLOOR"}, floor=3, merged=merged)
    assert advice["verdict"] == "过松/无信息量"


def test_build_advice_moderate_verdict_and_exact_boundaries():
    merged = _merged(list(range(20)), flagged=2)   # 10%：1%~30% 之间
    advice = mod.build_advice({"constant": "FAKE_FLOOR"}, floor=3, merged=merged)
    assert advice["verdict"] == "适中"

    # 精确 30%（=OVERSTRICT_FLAG_RATE）严格大于判据·不触发"过严"
    at_30 = mod.build_advice({"constant": "F"}, floor=3, merged=_merged(list(range(10)), flagged=3))
    assert at_30["verdict"] != "过严"

    # 精确 1%（=UNINFORMATIVE_FLAG_RATE）严格小于判据·不触发"过松"
    at_1 = mod.build_advice({"constant": "F"}, floor=3, merged=_merged(list(range(100)), flagged=1))
    assert at_1["verdict"] != "过松/无信息量"


# ════════════════════════════ 6. run_baseline 注入 mock 端到端 ════════════════════════════

@pytest.fixture
def mock_corpus(tmp_path):
    """书甲 5 章 metric=[0.1..0.5]·书乙 3 章 metric=[0.05,0.15,0.9]（全部人工可手算分位数）。
    flag_floor=0.5：8 章里只有 0.9 那章 flagged=True。"""
    styles_root = tmp_path / "styles"
    metrics = {}
    plan = {"书甲": [0.1, 0.2, 0.3, 0.4, 0.5], "书乙": [0.05, 0.15, 0.9]}
    for book, values in plan.items():
        d = styles_root / book / "原文"
        d.mkdir(parents=True)
        for i, v in enumerate(values, start=1):
            fp = d / f"第{i}章.txt"
            fp.write_text(f"占位第{i}章正文", encoding="utf-8")
            metrics[str(fp)] = v
    return styles_root, metrics


def _mock_extract_factory(metrics: dict, flag_floor: float = 0.5):
    def _extract(cfg, chapter_path):
        v = metrics[str(chapter_path)]
        return {"metric": v, "flagged": v > flag_floor, "eligible": True}
    return _extract


def test_run_baseline_mock_end_to_end_report_structure_and_files(mock_corpus, tmp_path):
    styles_root, metrics = mock_corpus
    reg_path = tmp_path / "fake_registry.json"
    _write_registry(reg_path, [
        {"id": "dramatic_irony_scanner.py::IRONY_TELL_FLOOR", "current_value": 0.5,
         "note": "fake registry note"},
    ])
    out_dir = tmp_path / "reports"
    report = mod.run_baseline(
        books=["书甲", "书乙"], chapters_per_book=10, seed=1, styles_root=styles_root,
        out_dir=out_dir, scanner_names=["dramatic_irony"],
        extract_fn=_mock_extract_factory(metrics), registry_path=reg_path)

    assert report["meta"]["books"] == ["书甲", "书乙"]
    assert report["meta"]["extractor"] == "injected_mock"
    assert set(report["meta"]["chapters_sampled"]["书甲"]) == {1, 2, 3, 4, 5}
    assert set(report["meta"]["chapters_sampled"]["书乙"]) == {1, 2, 3}

    node = report["scanners"]["dramatic_irony"]
    assert node["registry_current_value"] == 0.5
    assert node["current_floor_runtime"] == 0.5
    assert node["registry_runtime_consistent"] is True
    assert node["registry_note"] == "fake registry note"

    merged = node["merged"]
    all_metrics = sorted(metrics.values())
    expected_dist = mod.distribution_stats(all_metrics)
    assert merged["dist"] == expected_dist
    assert merged["chapters_scanned"] == 8
    assert merged["chapters_flagged"] == 1          # 仅 0.9 那章 > 0.5
    advice = node["advice"]
    assert advice["verdict"] == "适中"              # 1/8=12.5%，1%~30% 之间

    json_files = list(out_dir.glob("statistical_baseline_*.json"))
    md_files = list(out_dir.glob("statistical_baseline_*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    on_disk = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert on_disk["scanners"]["dramatic_irony"]["merged"]["chapters_scanned"] == 8
    json.dumps(report, ensure_ascii=False)   # 整份 report 自身也须可序列化


def test_run_baseline_unknown_scanner_raises_keyerror(mock_corpus, tmp_path):
    styles_root, metrics = mock_corpus
    with pytest.raises(KeyError):
        mod.run_baseline(
            books=["书甲"], chapters_per_book=10, seed=1, styles_root=styles_root,
            out_dir=tmp_path / "reports", scanner_names=["不存在的scanner"],
            extract_fn=_mock_extract_factory(metrics))


def test_run_baseline_missing_book_directory_raises_honest_error(mock_corpus, tmp_path):
    """一书存在一书不存在 → 诚实抛错·不产生部分报告(北极星⑤：不吞错不静默降级)。"""
    styles_root, metrics = mock_corpus
    out_dir = tmp_path / "reports"
    with pytest.raises(FileNotFoundError):
        mod.run_baseline(
            books=["书甲", "不存在的书"], chapters_per_book=10, seed=1, styles_root=styles_root,
            out_dir=out_dir, scanner_names=["dramatic_irony"],
            extract_fn=_mock_extract_factory(metrics))
    assert not list(out_dir.glob("*.json"))   # 没有半成品报告落盘


def test_render_markdown_contains_scanner_sections_and_verdict(mock_corpus, tmp_path):
    styles_root, metrics = mock_corpus
    report = mod.run_baseline(
        books=["书甲", "书乙"], chapters_per_book=10, seed=1, styles_root=styles_root,
        out_dir=tmp_path / "reports", scanner_names=["dramatic_irony"],
        extract_fn=_mock_extract_factory(metrics))
    text = mod.render_markdown(report)
    assert "## dramatic_irony" in text
    assert report["scanners"]["dramatic_irony"]["advice"]["verdict"] in text


# ════════════════════════════ 7. 真 scanner 集成（零逻辑复制的验证） ════════════════════════════

def test_real_extract_dramatic_irony_matches_direct_scanner_call(tmp_path):
    """harness 的 make_real_extract_fn 必须原样复现 scanner 自己 scan() 的输出——
    不是重新实现一遍 per_1k 算法。直接调 dramatic_irony_scanner.scan() 做基准对拍。"""
    import dramatic_irony_scanner as dis
    p = tmp_path / "第1章.txt"
    p.write_text(_IRONY_DRAFT, encoding="utf-8")

    direct = dis.scan(str(p))   # 基准：直接调 scanner
    extract = mod.make_real_extract_fn({"dramatic_irony": dis})
    cfg = dict(mod.SCANNERS["dramatic_irony"], name="dramatic_irony")
    with mod._force_active_modes([cfg]):
        got = extract(cfg, p)

    assert direct["per_1k"] > dis.IRONY_TELL_FLOOR   # 确认稿子本身真触发(不是空对空)
    assert got["metric"] == direct["per_1k"]
    assert got["flagged"] == bool(direct["warning"])
    assert got["eligible"] is True


def test_real_extract_group_dialogue_eligible_guard_excludes_low_dialogue_chapter(tmp_path):
    """对话行 3 < MIN_DIALOGUE_LINES=8 → harness 判 ineligible(不进分位数分布)，
    即便 scanner 自身仍算出了一个 ratio 数值（scan() 早退前已赋值该字段）。"""
    import group_dialogue_balance_scanner as gdb
    few = tmp_path / "第1章.txt"
    few.write_text(_FEW_DIALOGUE_DRAFT, encoding="utf-8")
    many = tmp_path / "第2章.txt"
    many.write_text(_GROUP_DIALOGUE_DRAFT, encoding="utf-8")

    cfg = dict(mod.SCANNERS["group_dialogue_balance"], name="group_dialogue_balance")
    extract = mod.make_real_extract_fn({"group_dialogue_balance": gdb})
    with mod._force_active_modes([cfg]):
        got_few = extract(cfg, few)
        got_many = extract(cfg, many)

    assert got_few["eligible"] is False    # 非群戏规模·守卫生效
    assert got_many["eligible"] is True
    assert got_many["metric"] == pytest.approx(1.0)   # 12 行全点名 → ratio=1.0
    assert got_many["flagged"] is True                 # 1.0 > RATIO_FLOOR(0.85)


def test_run_baseline_real_scanner_registry_consistency_all_six(tmp_path):
    """真 scanner 路径(extract_fn=None)下·6 个受测 scanner 的运行时常量应与
    threshold_registry.json 记录的 current_value 一致(防文档漂移·CLAUDE.md 流程一致性规则)。"""
    styles_root = tmp_path / "styles"
    _write_chapters(styles_root, "测试书", {1: _CLEAN_DRAFT, 2: _IRONY_DRAFT})
    report = mod.run_baseline(
        books=["测试书"], chapters_per_book=10, seed=1, styles_root=styles_root,
        out_dir=tmp_path / "reports")   # scanner_names=None → 全部 6 个·extract_fn=None → 真 scanner
    assert report["meta"]["extractor"] == "real_scanner_scan"
    assert set(report["scanners"]) == set(mod.SCANNERS)
    for name, node in report["scanners"].items():
        assert node["registry_runtime_consistent"] is True, (
            f"{name}: registry current_value={node['registry_current_value']} != "
            f"runtime={node['current_floor_runtime']}（registry 已过时需同步）")


def test_main_cli_writes_json_and_md_with_real_scanners(tmp_path, monkeypatch):
    styles_root = tmp_path / "styles"
    _write_chapters(styles_root, "测试书", {1: _CLEAN_DRAFT, 2: _IRONY_DRAFT, 3: _GROUP_DIALOGUE_DRAFT})
    out_dir = tmp_path / "reports"
    argv = ["statistical_threshold_baseline.py",
            "--books", "测试书", "--chapters", "10", "--seed", "7",
            "--styles-root", str(styles_root), "--output-dir", str(out_dir),
            "--scanners", "dramatic_irony,group_dialogue_balance"]
    monkeypatch.setattr(sys, "argv", argv)

    rc = mod.main()
    assert rc == 0

    json_files = list(out_dir.glob("statistical_baseline_*.json"))
    md_files = list(out_dir.glob("statistical_baseline_*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert set(payload["scanners"]) == {"dramatic_irony", "group_dialogue_balance"}
    assert payload["meta"]["extractor"] == "real_scanner_scan"
