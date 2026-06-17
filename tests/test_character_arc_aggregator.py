"""character_arc_aggregator.py 确定性单元测试（零 LLM / 零联网）。

被测脚本：core/scripts/character_arc_aggregator.py —— MARCUS 范式角色情感弧聚合。
此前无专属 test 文件（test_audit_batch2_remaining.py 只测同目录另一个脚本
arc_aggregator，仅在 docstring 提及本脚本 bug 但未真 import/调用它）。

本套钉死纯确定性逻辑（全程不 mock 被测函数）：
- estimate_intensity_from_label：label 子串映射强度 + 空/未知兜底。
- estimate_from_arc_progress：actor/experiencer 关键词独立估算 + chapter 抖动 + clamp。
- simple_smoothing：滑窗均值（短于 window 原样返回）。
- pearson_correlation：相关系数（n<2 / 常量列 → 0.0；正负相关）。
- detect_rhythm_pattern：上升 / 下降 / 平稳 / 样本不足分支。
- collect_character_deltas：continuity JSON 聚合（new schema delta / chapter str→int
  守卫 / 同章去重取 max / fallback arc_progress 路径）。
- compute_stanford_6component：6 维 + importance + tier 边界。
- build_character_arc：组装 + 空 entries error + Stanford 注入开关。
- main CLI（含 sys.exit）：走 subprocess 跑真 argparse + 真聚合 + 真落盘，
  断言退出码 / 输出文件内容。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import character_arc_aggregator as mod  # noqa: E402

_TARGET = _SCRIPTS / "character_arc_aggregator.py"


# ──────────────────────────────────────────────────────────────────────────
# estimate_intensity_from_label
# ──────────────────────────────────────────────────────────────────────────
def test_estimate_intensity_from_label():
    # 精确表项命中
    assert mod.estimate_intensity_from_label("崩溃") == 1.0, "崩溃应映射 1.0"
    assert mod.estimate_intensity_from_label("平静") == 0.2, "平静应映射 0.2"
    # 子串命中（label 含已知 key）
    assert mod.estimate_intensity_from_label("极度震惊") == 0.9, "含『震惊』子串应命中 0.9"
    # 空 label 兜底 0.3
    assert mod.estimate_intensity_from_label("") == 0.3, "空 label 兜底 0.3"
    assert mod.estimate_intensity_from_label(None) == 0.3, "None 兜底 0.3"
    # 未知 label 兜底 0.4
    assert mod.estimate_intensity_from_label("说不上来的情绪") == 0.4, "未知 label 兜底 0.4"


# ──────────────────────────────────────────────────────────────────────────
# estimate_from_arc_progress
# ──────────────────────────────────────────────────────────────────────────
def test_estimate_from_arc_progress():
    # 空字符串 → (0.3, 0.3)
    assert mod.estimate_from_arc_progress("") == (0.3, 0.3), "空 arc_progress 应 (0.3,0.3)"

    # 无 chapter 抖动时：actor high 关键词命中 → 0.75；experiencer high → 0.8
    a, e = mod.estimate_from_arc_progress("他完成了突破，目睹了致命一击", chapter=0)
    assert a == 0.75, f"含『完成/突破』actor high → 0.75, got {a}"
    assert e == 0.8, f"含『目睹/致命』experiencer high → 0.8, got {e}"

    # actor mid 关键词（推进）单独命中 → 0.55；无 experiencer 关键词 → 默认 0.25
    a2, e2 = mod.estimate_from_arc_progress("推进调查", chapter=0)
    assert a2 == 0.55, f"actor mid → 0.55, got {a2}"
    assert e2 == 0.25, f"无 experiencer 关键词 → 默认 0.25, got {e2}"

    # chapter > 0 注入抖动：actor/experiencer 各自不同方向，结果落在 [0.05, 1.0]
    a3, e3 = mod.estimate_from_arc_progress("完成", chapter=5)
    assert 0.05 <= a3 <= 1.0, f"actor clamp 区间, got {a3}"
    assert 0.05 <= e3 <= 1.0
    # 抖动确定性可复现（同输入同输出）
    assert mod.estimate_from_arc_progress("完成", chapter=5) == (a3, e3), "确定性可复现"
    # actor/experiencer 抖动公式不同 → 同基线但不同章号应能区分（避免 corr=1.0 假相关）
    a_c1, _ = mod.estimate_from_arc_progress("推进", chapter=1)
    a_c2, _ = mod.estimate_from_arc_progress("推进", chapter=2)
    assert a_c1 != a_c2, "不同章号 actor 抖动不同（防恒定相关）"


# ──────────────────────────────────────────────────────────────────────────
# simple_smoothing
# ──────────────────────────────────────────────────────────────────────────
def test_simple_smoothing():
    # 短于 window → 原样返回（list 拷贝）
    short = [0.5, 0.6]
    out = mod.simple_smoothing(short, window=3)
    assert out == [0.5, 0.6], "短于 window 原样返回"

    # window=3 滑窗均值：端点用半窗，中点用全窗
    curve = [0.0, 0.3, 0.9, 0.3, 0.0]
    sm = mod.simple_smoothing(curve, window=3)
    assert len(sm) == len(curve), "长度不变"
    # i=0: mean(0.0,0.3)=0.15 ; i=2: mean(0.3,0.9,0.3)=0.5 ; i=4: mean(0.3,0.0)=0.15
    assert sm[0] == 0.15, f"端点半窗均值, got {sm[0]}"
    assert sm[2] == 0.5, f"中点全窗均值, got {sm[2]}"
    assert sm[4] == 0.15, f"末端半窗均值, got {sm[4]}"


# ──────────────────────────────────────────────────────────────────────────
# pearson_correlation
# ──────────────────────────────────────────────────────────────────────────
def test_pearson_correlation():
    # n < 2 → 0.0
    assert mod.pearson_correlation([0.5], [0.5]) == 0.0, "单点 → 0.0"
    assert mod.pearson_correlation([], []) == 0.0, "空 → 0.0"
    # 完美正相关 → 1.0
    assert mod.pearson_correlation([1, 2, 3], [2, 4, 6]) == 1.0, "正相关 → 1.0"
    # 完美负相关 → -1.0
    assert mod.pearson_correlation([1, 2, 3], [3, 2, 1]) == -1.0, "负相关 → -1.0"
    # 常量列（方差 0）→ 0.0（避免除零）
    assert mod.pearson_correlation([0.5, 0.5, 0.5], [1, 2, 3]) == 0.0, "常量列 → 0.0"
    # 不等长 → 取较短长度
    assert mod.pearson_correlation([1, 2, 3, 99], [2, 4, 6]) == 1.0, "截到较短长度仍正相关"


# ──────────────────────────────────────────────────────────────────────────
# detect_rhythm_pattern
# ──────────────────────────────────────────────────────────────────────────
def test_detect_rhythm_pattern():
    # < 4 点 → 样本不足
    assert mod.detect_rhythm_pattern([0.1, 0.2, 0.3]) == "样本不足无法判断"
    # 持续上升（>70% 上升）
    assert "持续上升" in mod.detect_rhythm_pattern([0.1, 0.2, 0.3, 0.4, 0.5])
    # 持续下降（>70% 下降）
    assert "持续下降" in mod.detect_rhythm_pattern([0.5, 0.4, 0.3, 0.2, 0.1])
    # 平稳（波动 < 0.2，非单调）
    flat = mod.detect_rhythm_pattern([0.50, 0.55, 0.50, 0.55, 0.50, 0.55])
    assert "平稳" in flat, f"低波动非单调应平稳, got {flat}"


# ──────────────────────────────────────────────────────────────────────────
# collect_character_deltas
# ──────────────────────────────────────────────────────────────────────────
def _write_continuity(continuity_dir: Path, fname: str, payload: dict) -> None:
    continuity_dir.mkdir(parents=True, exist_ok=True)
    (continuity_dir / fname).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_character_deltas_new_schema():
    """新 schema character_emotion_delta：直接取 delta 值 + abs + round。"""
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "衔接分析"
        _write_continuity(cdir, "ch1_continuity.json", {
            "character_emotion_delta": [{
                "character": "赵子龙",
                "chapter_changes": [
                    {"chapter": 1, "actor_emotion": {"delta": 0.6, "label": "决断"},
                     "experiencer_emotion": {"delta": -0.4, "label": "悲伤"},
                     "trigger_event": "出战", "stage_progress": "初登场"},
                    {"chapter": 2, "actor_emotion": {"delta": 0.8, "label": "极怒"},
                     "experiencer_emotion": {"delta": 0.5, "label": "焦虑"},
                     "trigger_event": "中伏", "stage_progress": "受挫"},
                ],
            }],
        })
        by = mod.collect_character_deltas(cdir)
        assert "赵子龙" in by, "应聚合出赵子龙"
        entries = by["赵子龙"]
        assert [e["chapter"] for e in entries] == [1, 2], "按章排序"
        assert entries[0]["actor_value"] == 0.6, "actor delta 取值"
        assert entries[0]["experiencer_value"] == 0.4, "experiencer delta 取 abs"


def test_collect_character_deltas_chapter_str_int_guard():
    """🔴 审计修：chapter 写成 str（'2'/'ch3'）必须强制 int，非数字键跳过——
    防 str/int 混入后 sorted/减法 TypeError 整角色崩。"""
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "衔接分析"
        _write_continuity(cdir, "ch1_continuity.json", {
            "character_emotion_delta": [{
                "character": "X",
                "chapter_changes": [
                    {"chapter": "2", "actor_emotion": {"delta": 0.5},
                     "experiencer_emotion": {"delta": 0.5}},
                    {"chapter": 1, "actor_emotion": {"delta": 0.3},
                     "experiencer_emotion": {"delta": 0.3}},
                    {"chapter": "ch_bad", "actor_emotion": {"delta": 0.9},
                     "experiencer_emotion": {"delta": 0.9}},  # 非数字 → 跳过
                ],
            }],
        })
        by = mod.collect_character_deltas(cdir)
        chs = [e["chapter"] for e in by["X"]]
        assert all(isinstance(c, int) for c in chs), f"chapter 全 int, got {chs}"
        assert chs == [1, 2], f"'2'→int 并排序，'ch_bad' 跳过, got {chs}"


def test_collect_character_deltas_dedup_takes_max_actor():
    """同章重复 → 合并取 actor_value 更大的那条（更显著事件优先）。"""
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "衔接分析"
        # 两个 continuity 文件，同角色同章不同强度
        _write_continuity(cdir, "a_continuity.json", {
            "character_emotion_delta": [{"character": "Y", "chapter_changes": [
                {"chapter": 5, "actor_emotion": {"delta": 0.2},
                 "experiencer_emotion": {"delta": 0.2}}]}]})
        _write_continuity(cdir, "b_continuity.json", {
            "character_emotion_delta": [{"character": "Y", "chapter_changes": [
                {"chapter": 5, "actor_emotion": {"delta": 0.9},
                 "experiencer_emotion": {"delta": 0.1}}]}]})
        by = mod.collect_character_deltas(cdir)
        assert len(by["Y"]) == 1, "同章去重 → 单条"
        assert by["Y"][0]["actor_value"] == 0.9, "取 actor_value 更大者"


def test_collect_character_deltas_fallback_arc_progress():
    """无 character_emotion_delta → fallback 用 character_continuity.arc_progress
    + chapter_range 展开成逐章数据点。"""
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "衔接分析"
        _write_continuity(cdir, "ch1_continuity.json", {
            "chapter_range": "ch1-ch3",
            "character_continuity": [
                {"character": "老王", "arc_progress": "完成突破后陷入迷茫"},
            ],
        })
        by = mod.collect_character_deltas(cdir)
        assert "老王" in by, "fallback 应聚合出角色"
        chs = [e["chapter"] for e in by["老王"]]
        assert chs == [1, 2, 3], f"chapter_range 展开 ch1-3, got {chs}"
        # fallback label 打标记
        assert by["老王"][0]["actor_label"] == "（fallback估算）"


def test_collect_character_deltas_tolerant_garbage():
    """consumer tolerant：character_emotion_delta 写成 str / 元素非 dict / 缺目录
    → 不崩，返回空或跳过。"""
    # 缺目录
    with tempfile.TemporaryDirectory() as d:
        assert mod.collect_character_deltas(Path(d) / "不存在") == {}
    # delta 是 str（弱模型写 "TODO"）+ 元素非 dict
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "衔接分析"
        _write_continuity(cdir, "ch1_continuity.json",
                          {"character_emotion_delta": "TODO"})
        _write_continuity(cdir, "ch2_continuity.json",
                          {"character_emotion_delta": ["不是dict", 123]})
        by = mod.collect_character_deltas(cdir)
        assert by == {}, "脏数据全跳过 → 空 dict"


# ──────────────────────────────────────────────────────────────────────────
# compute_stanford_6component
# ──────────────────────────────────────────────────────────────────────────
def test_compute_stanford_6component():
    # 空 entries / total 0 → 空 dict
    assert mod.compute_stanford_6component("X", [], 10) == {}
    assert mod.compute_stanford_6component("X", [{"chapter": 1}], 0) == {}

    entries = [
        {"chapter": 1, "actor_value": 0.8, "experiencer_value": 0.6,
         "trigger_event": "他说道", "stage_progress": "起始阶段角色登场建立"},
        {"chapter": 2, "actor_value": 0.6, "experiencer_value": 0.4,
         "trigger_event": "回答提问", "stage_progress": "推进阶段冲突升级展开"},
        {"chapter": 3, "actor_value": 0.4, "experiencer_value": 0.2,
         "trigger_event": "决战", "stage_progress": "高潮阶段命运抉择对决"},
    ]
    out = mod.compute_stanford_6component("主角", entries, total_book_chapters=3)
    s6 = out["stanford_6_component"]
    # N = 3/3 = 1.0
    assert s6["N_naming"] == 1.0, f"N=出场章/全书章, got {s6['N_naming']}"
    # C：3 章里有对话信号（说/答/问）的占比 = 2/3 ≈ 0.667
    assert s6["C_communication"] == round(2 / 3, 3), f"C 对话占比, got {s6['C_communication']}"
    # A = actor 均值 (0.8+0.6+0.4)/3 = 0.6
    assert s6["A_agency"] == 0.6, f"A=actor均值, got {s6['A_agency']}"
    # I = experiencer 均值 (0.6+0.4+0.2)/3 = 0.4
    assert s6["I_interiority"] == 0.4, f"I=experiencer均值, got {s6['I_interiority']}"
    # tier 由 importance 决定，三档之一
    assert s6["tier"] in ("protagonist", "supporting", "minor")


def test_compute_stanford_tier_boundaries():
    """tier 边界：importance > 0.5 protagonist / 0.3-0.5 supporting / < 0.3 minor。"""
    # 全高值 → importance 大 → protagonist
    hi = [{"chapter": i, "actor_value": 1.0, "experiencer_value": 1.0,
           "trigger_event": "他说", "stage_progress": "x" * 40} for i in range(1, 6)]
    out_hi = mod.compute_stanford_6component("A", hi, 5)
    assert out_hi["stanford_6_component"]["tier"] == "protagonist"

    # 极低值 + 大全书章数稀释 N → minor
    lo = [{"chapter": 1, "actor_value": 0.05, "experiencer_value": 0.05,
           "trigger_event": "", "stage_progress": ""}]
    out_lo = mod.compute_stanford_6component("B", lo, 1000)
    s6 = out_lo["stanford_6_component"]
    assert s6["overall_importance"] < 0.3, f"低值大稀释 → importance<0.3, got {s6['overall_importance']}"
    assert s6["tier"] == "minor"


# ──────────────────────────────────────────────────────────────────────────
# build_character_arc
# ──────────────────────────────────────────────────────────────────────────
def test_build_character_arc_empty():
    out = mod.build_character_arc("空角色", [])
    assert out == {"character": "空角色", "error": "no data"}, "空 entries → error"


def test_build_character_arc_full():
    entries = [
        {"chapter": 1, "actor_value": 0.2, "experiencer_value": 0.3,
         "actor_label": "冷静", "experiencer_label": "好奇",
         "trigger_event": "登场", "stage_progress": "新手期"},
        {"chapter": 2, "actor_value": 0.5, "experiencer_value": 0.5,
         "actor_label": "决断", "experiencer_label": "紧张",
         "trigger_event": "立功", "stage_progress": "成长期"},
        {"chapter": 3, "actor_value": 0.9, "experiencer_value": 0.8,
         "actor_label": "极怒", "experiencer_label": "震惊",
         "trigger_event": "复仇", "stage_progress": "巅峰期"},
    ]
    arc = mod.build_character_arc("主角", entries, total_book_chapters=3)
    assert arc["character"] == "主角"
    assert arc["total_chapters_appeared"] == 3
    assert arc["chapter_range"] == "ch1-3"
    assert arc["chapters_with_data"] == [1, 2, 3]
    assert arc["emotion_actor_curve_raw"] == [0.2, 0.5, 0.9]
    assert arc["average_actor_intensity"] == round((0.2 + 0.5 + 0.9) / 3, 3)
    # 三段 stage 全不同 → 3 个 transition（首段 from='（起始）'）
    assert len(arc["stage_transitions"]) == 3
    assert arc["stage_transitions"][0]["from"] == "（起始）"
    # Stanford 6-component 注入（total_book_chapters > 0）
    assert "stanford_6_component" in arc, "total>0 应注入 Stanford"
    # metadata 范式标记
    assert arc["_metadata"]["marcus_paradigm"] is True

    # total_book_chapters=0 → 不注入 Stanford
    arc0 = mod.build_character_arc("主角", entries, total_book_chapters=0)
    assert "stanford_6_component" not in arc0, "total=0 不注入 Stanford"


# ──────────────────────────────────────────────────────────────────────────
# main CLI（subprocess · 含 sys.exit）
# ──────────────────────────────────────────────────────────────────────────
def _run_cli(project: Path, *args):
    # 强制子进程 UTF-8 输出 + 容错解码：脚本打印中文，Windows 默认 GBK 控制台
    # 会让 subprocess 的 reader 线程 UTF-8 解码报错（不影响退出码/落盘，但噪声大）。
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(_TARGET), "--project", str(project), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


def test_main_cli_project_not_found_exit2():
    """--project 不存在 → exit 2。"""
    with tempfile.TemporaryDirectory() as d:
        r = _run_cli(Path(d) / "不存在的项目")
        assert r.returncode == 2, f"项目缺失应 exit 2, got {r.returncode}\n{r.stderr}"


def test_main_cli_no_data_exit1():
    """项目存在但无 continuity 数据 → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "空项目"
        proj.mkdir()
        r = _run_cli(proj)
        assert r.returncode == 1, f"无数据应 exit 1, got {r.returncode}\n{r.stderr}"


def test_main_cli_writes_arc_json():
    """端到端：真 continuity → 真聚合 → 落盘 <角色>_emotion_arc.json，
    min-appearances 过滤生效。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "测试书"
        cdir = proj / "衔接分析"
        # 主角 5 章（过阈），龙套 1 章（被 min-appearances 过滤）
        changes_hero = [
            {"chapter": i, "actor_emotion": {"delta": 0.5 + i * 0.05, "label": "决断"},
             "experiencer_emotion": {"delta": 0.3, "label": "焦虑"},
             "trigger_event": f"事件{i}", "stage_progress": f"阶段{i}"}
            for i in range(1, 6)
        ]
        _write_continuity(cdir, "ch1_continuity.json", {
            "character_emotion_delta": [
                {"character": "主角C", "chapter_changes": changes_hero},
                {"character": "龙套D", "chapter_changes": [
                    {"chapter": 1, "actor_emotion": {"delta": 0.2},
                     "experiencer_emotion": {"delta": 0.2}}]},
            ],
        })
        r = _run_cli(proj, "--min-appearances", "3")
        assert r.returncode == 0, f"成功应 exit 0, got {r.returncode}\n{r.stderr}"

        out_dir = proj / "character_arcs"
        hero_file = out_dir / "主角C_emotion_arc.json"
        assert hero_file.exists(), "主角弧文件应落盘"
        arc = json.loads(hero_file.read_text(encoding="utf-8"))
        assert arc["character"] == "主角C"
        assert arc["total_chapters_appeared"] == 5
        assert "stanford_6_component" in arc, "main 估算 total_chs>0 应注入 Stanford"

        # 龙套被过滤，无文件
        assert not (out_dir / "龙套D_emotion_arc.json").exists(), "龙套 < min 应被跳过"


def test_main_cli_character_filter_and_safe_name():
    """--character 只聚合指定角色；含非法文件名字符的角色名落盘时被替换。"""
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "书"
        cdir = proj / "衔接分析"
        changes = [
            {"chapter": i, "actor_emotion": {"delta": 0.5}, "experiencer_emotion": {"delta": 0.5},
             "trigger_event": "x", "stage_progress": "y"}
            for i in range(1, 5)
        ]
        _write_continuity(cdir, "ch1_continuity.json", {
            "character_emotion_delta": [
                {"character": "甲/乙:丙", "chapter_changes": changes},
                {"character": "丁", "chapter_changes": changes},
            ],
        })
        r = _run_cli(proj, "--character", "甲/乙:丙", "--min-appearances", "3")
        assert r.returncode == 0, f"got {r.returncode}\n{r.stderr}"
        out_dir = proj / "character_arcs"
        # 非法字符 / 与 : 被替换为 _
        assert (out_dir / "甲_乙_丙_emotion_arc.json").exists(), "非法字符替换为 _ 后落盘"
        # 未指定的角色不应落盘
        assert not (out_dir / "丁_emotion_arc.json").exists(), "--character 过滤其它角色"


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
