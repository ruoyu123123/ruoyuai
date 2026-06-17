"""pov_consistency_scanner.py 专属回归测试（零依赖 / 零 LLM / 零联网）。

聚焦尚未被 `test_pov_consistency.py` 覆盖的核心确定性逻辑：
  · split_scenes —— 场景边界切割（\n---\n / 三连换行 / strip / 去空段）；
  · scan() 的两条致命退出（draft 缺失 / 无主角）；
  · scan() head-hopping ratio 阈值（>0.4）与 POV_HEAD_HOPPING 触发；
  · scan() POV_NON_PROTAGONIST_SCENE 触发（配角主导整场）；
  · _last_explicit_subject_before —— 扫全文非仅 POV 动词窗 + 宾语位排除；
  · 别名（aliases）也计入角色名集合；
  · 长名优先匹配（「林尘」不被「林」抢）；
  · load() 容错（缺文件 / 坏 JSON）；
  · main() CLI 退出码 0 / 1 / 2（subprocess 真跑）。

旧测试已覆盖 detect_pov_signal_holders 的归因纯函数（宾语位/代词承前/head-hopping
两人各拿信号/无主角不错报）+ 一个纯主角整段 scan() 无误报 —— 本文件不重复。

scanner 为 advisory，测试只锚确定性逻辑，绝不 mock 被测函数。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import pov_consistency_scanner as pov  # noqa: E402

_TARGET = _SCRIPTS / "pov_consistency_scanner.py"

PROTAG = "林尘"
SIDE = "王虎"


# ──────────────────────────────────────────────────────────────────────────
# 工具：造项目 + 人物卡 + draft
# ──────────────────────────────────────────────────────────────────────────
def _mk_project(tmp: Path, characters: list) -> Path:
    proj = tmp / "测试书"
    db = proj / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False),
        encoding="utf-8")
    return proj


def _write_draft(proj: Path, text: str) -> Path:
    draft = proj / "draft.txt"
    draft.write_text(text, encoding="utf-8")
    return draft


def _default_cards():
    return [{"name": PROTAG, "role": "主角"}, {"name": SIDE, "role": "配角"}]


# ══════════════════════════════════════════════════════════════════════════
# split_scenes —— 纯函数（旧测试未覆盖）
# ══════════════════════════════════════════════════════════════════════════
def test_split_scenes_on_dash_marker():
    """\n---\n 分隔符切场景，每段 strip。"""
    text = "第一场内容。\n---\n第二场内容。\n----\n第三场内容。"
    scenes = pov.split_scenes(text)
    assert scenes == ["第一场内容。", "第二场内容。", "第三场内容。"], scenes


def test_split_scenes_on_triple_newline_and_drops_blank():
    """三连及以上换行也切场景；空白段被丢弃。"""
    text = "场景甲。\n\n\n场景乙。\n\n\n\n   \n\n\n场景丙。"
    scenes = pov.split_scenes(text)
    assert scenes == ["场景甲。", "场景乙。", "场景丙。"], scenes


def test_split_scenes_double_newline_not_a_boundary():
    """单个空行（双换行）不构成场景边界 —— 仍是同一场景。"""
    text = "段落一。\n\n段落二。"
    scenes = pov.split_scenes(text)
    assert len(scenes) == 1, scenes
    assert "段落一" in scenes[0] and "段落二" in scenes[0]


# ══════════════════════════════════════════════════════════════════════════
# _last_explicit_subject_before —— 纯函数（旧测试未直接覆盖）
# ══════════════════════════════════════════════════════════════════════════
def test_last_explicit_subject_skips_object_position():
    """全文扫描取最近非宾语位主语；宾语位（凝视动词后）名字不算主语。"""
    names_sorted = sorted([PROTAG, SIDE], key=lambda n: (-len(n), n))
    # 「林尘盯着王虎。」—— 王虎是宾语位；pos 设在句末
    text = "林尘盯着王虎。"
    subj = pov._last_explicit_subject_before(text, len(text), names_sorted)
    assert subj == PROTAG, subj


def test_last_explicit_subject_picks_rightmost():
    """多个非宾语位主语 → 取位置最靠后的那个。"""
    names_sorted = sorted([PROTAG, SIDE], key=lambda n: (-len(n), n))
    text = "林尘走了。王虎来了。"
    subj = pov._last_explicit_subject_before(text, len(text), names_sorted)
    assert subj == SIDE, subj


def test_last_explicit_subject_none_when_no_name():
    """text[:pos] 内无任何角色名 → None。"""
    names_sorted = sorted([PROTAG, SIDE], key=lambda n: (-len(n), n))
    subj = pov._last_explicit_subject_before("屋里空无一人。", 5, names_sorted)
    assert subj is None, subj


# ══════════════════════════════════════════════════════════════════════════
# 别名 + 长名优先（角色名集合构建 + 匹配定序）
# ══════════════════════════════════════════════════════════════════════════
def test_alias_counts_as_character_name():
    """人物卡 aliases 也进角色名集合，别名作主语的 POV 信号归该角色名。"""
    text = "老王盯着别处。老王觉得不对劲。"
    counts = pov.detect_pov_signal_holders(text, {PROTAG, SIDE, "老王"},
                                           protagonist=PROTAG)
    # 「觉得」前最近非宾语主语是「老王」
    assert counts["老王"] == 1, counts
    assert counts[PROTAG] == 0 and counts[SIDE] == 0, counts


def test_long_name_not_stolen_by_substring():
    """长名优先匹配：「林尘」作主语，不被单字「林」抢走计数。"""
    names = {"林尘", "林"}  # 「林」是另一个短名角色
    text = "林尘觉得风有点凉。"
    counts = pov.detect_pov_signal_holders(text, names, protagonist="林尘")
    assert counts["林尘"] == 1, counts
    assert counts["林"] == 0, counts


# ══════════════════════════════════════════════════════════════════════════
# load() 容错
# ══════════════════════════════════════════════════════════════════════════
def test_load_missing_and_malformed_return_empty():
    """缺文件 → {}；坏 JSON → {}（容错不抛）。"""
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        assert pov.load(missing) == {}
        bad = Path(d) / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert pov.load(bad) == {}
        good = Path(d) / "good.json"
        good.write_text('{"k": 1}', encoding="utf-8")
        assert pov.load(good) == {"k": 1}


# ══════════════════════════════════════════════════════════════════════════
# scan() 致命退出（旧测试未覆盖）
# ══════════════════════════════════════════════════════════════════════════
def test_scan_fatal_missing_draft():
    """draft 不存在 → _fatal。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        report = pov.scan(proj, proj / "不存在.txt")
        assert "_fatal" in report, report
        assert "draft 不存在" in report["_fatal"], report


def test_scan_fatal_no_protagonist():
    """人物卡无 role='主角' → _fatal。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [
            {"name": SIDE, "role": "配角"},
            {"name": "张姐", "role": "配角"},
        ])
        draft = _write_draft(proj, "王虎觉得很累。")
        report = pov.scan(proj, draft)
        assert "_fatal" in report, report
        assert "主角" in report["_fatal"], report


# ══════════════════════════════════════════════════════════════════════════
# scan() head-hopping ratio 阈值（>0.4）
# ══════════════════════════════════════════════════════════════════════════
def test_scan_head_hopping_triggers_above_ratio():
    """单场景内两人各显式主语 + 心理 → runner-up/dominant 比例 > 0.4 → POV_HEAD_HOPPING。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        # 林尘 2 个 POV 信号，王虎 1 个 → ratio = 0.5 > 0.4
        draft = _write_draft(
            proj,
            "林尘觉得局势失控。林尘想到退路。王虎心想机会来了。")
        report = pov.scan(proj, draft)
        assert "_fatal" not in report, report
        codes = {i["code"] for i in report["issues"]}
        assert "POV_HEAD_HOPPING" in codes, report
        assert report["head_hopping_count"] >= 1, report


def test_scan_dominant_only_no_head_hopping():
    """单场景仅主角持 POV（无 runner-up）→ 不触发 head-hopping。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        draft = _write_draft(
            proj,
            "林尘觉得气氛不对。他想到昨夜的事。林尘盯着王虎，看出对方心虚。")
        report = pov.scan(proj, draft)
        assert "_fatal" not in report, report
        codes = {i["code"] for i in report["issues"]}
        assert "POV_HEAD_HOPPING" not in codes, report
        assert report["head_hopping_count"] == 0, report


# ══════════════════════════════════════════════════════════════════════════
# scan() 非主角场景（POV_NON_PROTAGONIST_SCENE）
# ══════════════════════════════════════════════════════════════════════════
def test_scan_non_protagonist_scene_flagged():
    """整场配角主导 POV（主角不在场）→ POV_NON_PROTAGONIST_SCENE。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        # 仅王虎的心理活动，主角林尘整场不出现
        draft = _write_draft(
            proj,
            "王虎觉得这局棋越来越复杂。王虎想到那笔账还没算清。")
        report = pov.scan(proj, draft)
        assert "_fatal" not in report, report
        codes = {i["code"] for i in report["issues"]}
        assert "POV_NON_PROTAGONIST_SCENE" in codes, report
        assert report["non_protag_scenes_count"] >= 1, report
        # warning 字段应被填充（有 issue 时）
        assert report["warning"] is not None, report


def test_scan_clean_report_shape_and_no_warning():
    """干净主角视角 → 无 issue + warning=None + 关键字段齐全。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        draft = _write_draft(
            proj,
            "林尘走进房间。他觉得很安静。林尘想到该收手了。")
        report = pov.scan(proj, draft)
        assert "_fatal" not in report, report
        assert report["issues"] == [], report
        assert report["warning"] is None, report
        assert report["scanner"] == "pov_consistency_scanner"
        assert report["gate_level"] == "advisory"
        assert report["protagonist"] == PROTAG
        assert report["scenes_scanned"] >= 1


# ══════════════════════════════════════════════════════════════════════════
# main() CLI —— subprocess 真跑，锚退出码 0 / 1 / 2
# ══════════════════════════════════════════════════════════════════════════
def _run_cli(*args):
    p = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def test_cli_missing_args_exits_2():
    """少于 2 个参数 → 打印 doc → exit 2。"""
    p = _run_cli()  # 0 个参数
    assert p.returncode == 2, f"rc={p.returncode} stderr={p.stderr}"


def test_cli_warning_exits_1():
    """非主角场景 → warning → exit 1。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        draft = _write_draft(
            proj,
            "王虎觉得这局棋越来越复杂。王虎想到那笔账还没算清。")
        p = _run_cli(str(proj), str(draft))
        assert p.returncode == 1, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"


def test_cli_clean_exits_0():
    """干净主角视角 → 无 warning → exit 0。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), _default_cards())
        draft = _write_draft(
            proj,
            "林尘走进房间。他觉得很安静。林尘想到该收手了。")
        p = _run_cli(str(proj), str(draft))
        assert p.returncode == 0, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"


def test_cli_fatal_no_protagonist_exits_2():
    """无主角 → _fatal → exit 2（CLI 层）。"""
    with tempfile.TemporaryDirectory() as d:
        proj = _mk_project(Path(d), [{"name": SIDE, "role": "配角"}])
        draft = _write_draft(proj, "王虎觉得很累。")
        p = _run_cli(str(proj), str(draft))
        assert p.returncode == 2, f"rc={p.returncode} stdout={p.stdout} stderr={p.stderr}"


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
    print(f"[pov_consistency_scanner] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
