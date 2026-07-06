"""validate_chapter.py 专属确定性回归测试（零 LLM / 零联网）。

被测脚本 = core/scripts/validate_chapter.py（cluster 内物理章硬性校验器·机械扫描 7 类硬约束）。

【已有间接覆盖】（不重复）：
  tests/test_audit_hub_aggregation.py 与 tests/test_cross_cluster_will_learn_aggregate_audit.py
  仅把 validate_chapter **当成假 scanner 名字 / 假脚本 stub** 引用（写一个 emit
  LOCKED_FACT_CONFLICT 或零 issue 的假 validate_chapter.py 喂 audit_hub）——它们测的是
  audit_hub 的聚合/路由/--json 解析契约，**从未 import 也从未调用本脚本任何真实函数**。
  → 本测试聚焦本脚本自身的确定性算法 / 边界 / 退出码，全部首次覆盖。

钉死的核心不变量：
  · check_word_count   —— 下限/上限/CLUSTER_MODE 整块语义切换/per-chapter 回退
  · check_banned_words —— count>=3 升 error、首现行号定位、零命中无误报
  · check_changes_factual —— factual 为空 → CHANGES_MISSING(fatal)
  · _flatten_locked_facts —— list / dict / 嵌套 dict / 非法输入 四种 schema 摊平
  · check_secret_reveal —— manifest 要求揭露但 changes 缺 → SECRET_NOT_REVEALED
  · check_locked_facts —— 左右手对抗 / 年龄数字冲突 → LOCKED_FACT_CONFLICT
  · format_json       —— v18 --json 输出契约（summary 计数 / errors 字段透传）
  · CLI 退出码        —— 旧单章位置参硬拒 / cluster FILE_NOT_FOUND→exit2（真 subprocess）

约定：零依赖（只用标准库）· test_* 无参数 · Windows · tempfile.mkdtemp + utf-8。
含 sys.exit 的 main() 走真 subprocess（参照 test_cross_cluster_fate_drift_aggregate）。
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
import validate_chapter as mod  # noqa: E402

_TARGET = _SCRIPTS / "validate_chapter.py"


# ══════════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════════
def _clear_cluster_env():
    """确保单测互不串环境变量（check_word_count / check_dialogue_craft 读 CLUSTER_MODE）。"""
    for k in ("CLUSTER_MODE", "CLUSTER_PER_CHAPTER"):
        os.environ.pop(k, None)


def _run_cli(*args):
    """跑真 CLI；返回 CompletedProcess（stdout/stderr 容错解码，断言只锚 returncode）。"""
    p = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, cwd=str(_ROOT))
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


# ══════════════════════════════════════════════════════════════════════════
# check_word_count —— 字数下限/上限/cluster 语义
# ══════════════════════════════════════════════════════════════════════════
def test_word_count_too_short_and_too_long():
    """低于 min → WC_TOO_SHORT(error)；高于 max → WC_TOO_LONG(warning)；区间内 → 无 issue。"""
    _clear_cluster_env()
    short = mod.check_word_count("字" * 100, target=3500, min_=3000, max_=5000)
    assert len(short) == 1 and short[0]["code"] == "WC_TOO_SHORT"
    assert short[0]["severity"] == "error"
    # 缺口字数算对：3000 - 100 = 2900
    assert "2900" in short[0]["msg"]

    long = mod.check_word_count("字" * 6000, target=3500, min_=3000, max_=5000)
    assert len(long) == 1 and long[0]["code"] == "WC_TOO_LONG"
    assert long[0]["severity"] == "warning"

    ok = mod.check_word_count("字" * 4000, target=3500, min_=3000, max_=5000)
    assert ok == []


def test_word_count_cluster_mode_overrides_thresholds():
    """CLUSTER_MODE=1 且非 per-chapter → 整块语义强制 min=8000/max=30000，无视传入区间。"""
    _clear_cluster_env()
    os.environ["CLUSTER_MODE"] = "1"
    try:
        # 4000 字本来落在传入 [3000,5000] 区间，但整块语义下限是 8000 → 仍 TOO_SHORT
        errs = mod.check_word_count("字" * 4000, target=3500, min_=3000, max_=5000)
        assert len(errs) == 1 and errs[0]["code"] == "WC_TOO_SHORT"
        assert "8000" in errs[0]["msg"]
    finally:
        _clear_cluster_env()


def test_word_count_cluster_per_chapter_falls_back_to_chapter_semantics():
    """CLUSTER_MODE=1 + CLUSTER_PER_CHAPTER=1 → 回退章级语义（保留传入 min/max，不套 8000）。"""
    _clear_cluster_env()
    os.environ["CLUSTER_MODE"] = "1"
    os.environ["CLUSTER_PER_CHAPTER"] = "1"
    try:
        # 4000 字在传入 [3000,5000] 内 → per-chapter 回退后应放行（不套 8000 下限）
        errs = mod.check_word_count("字" * 4000, target=3500, min_=3000, max_=5000)
        assert errs == [], f"per-chapter 应回退章级语义放行，却报: {errs}"
    finally:
        _clear_cluster_env()


# ══════════════════════════════════════════════════════════════════════════
# check_banned_words —— 计数升级 / 行号定位 / 零误报
# ══════════════════════════════════════════════════════════════════════════
def test_banned_word_count_drives_severity():
    """同一禁用词出现 >=3 次 → error；1-2 次 → warning。"""
    # "顿时" 是 BANNED_WORDS 成员
    three = "他顿时停下。\n她顿时回头。\n风顿时停了。"
    errs = [e for e in mod.check_banned_words(three) if "顿时" in e["msg"]]
    assert len(errs) == 1 and errs[0]["code"] == "BANNED_WORD"
    assert errs[0]["severity"] == "error"
    assert "3 次" in errs[0]["msg"]

    once = "他顿时停下来。"
    errs2 = [e for e in mod.check_banned_words(once) if "顿时" in e["msg"]]
    assert len(errs2) == 1 and errs2[0]["severity"] == "warning"


def test_banned_word_first_occurrence_line_number():
    """首现行号必须指向具体行（子代理直接 Edit 的契约）。"""
    body = "第一行干净。\n第二行也干净。\n这里出现似乎一词。"
    errs = [e for e in mod.check_banned_words(body) if "似乎" in e["msg"]]
    assert len(errs) == 1
    # "似乎" 在第 3 行
    assert "第 3 行" in errs[0]["msg"]


def test_banned_word_clean_body_no_false_positive():
    """无禁用词正文 → 零 issue。"""
    assert mod.check_banned_words("他握紧拳头，把杯子摔在地上。") == []


# ══════════════════════════════════════════════════════════════════════════
# check_changes_factual —— factual 空 → CHANGES_MISSING(fatal)
# ══════════════════════════════════════════════════════════════════════════
def test_changes_factual_missing_is_fatal():
    errs, changes = mod.check_changes_factual(None)
    assert changes is None
    assert len(errs) == 1 and errs[0]["code"] == "CHANGES_MISSING"
    assert errs[0]["severity"] == "fatal"

    # 空 dict 也算缺失（not {} 为真）
    errs2, ch2 = mod.check_changes_factual({})
    assert ch2 is None and errs2[0]["code"] == "CHANGES_MISSING"


def test_changes_factual_present_passes_through():
    factual = {"locked_facts": ["主角左手有疤"]}
    errs, changes = mod.check_changes_factual(factual)
    assert errs == []
    assert changes is factual


# ══════════════════════════════════════════════════════════════════════════
# _flatten_locked_facts —— 三种 schema + 非法输入
# ══════════════════════════════════════════════════════════════════════════
def test_flatten_locked_facts_all_schemas():
    # list（旧 schema）原样转字符串
    assert mod._flatten_locked_facts(["a", "b"]) == ["a", "b"]
    # dict：标量 → "k=v"
    out = mod._flatten_locked_facts({"年龄": 21})
    assert "年龄=21" in out
    # dict：list 值 → 摊平元素
    out2 = mod._flatten_locked_facts({"特征": ["左手疤", "灰色瞳"]})
    assert "左手疤" in out2 and "灰色瞳" in out2
    # 嵌套 dict → "k.sk=sv"
    out3 = mod._flatten_locked_facts({"family": {"father": "克拉伦斯"}})
    assert "family.father=克拉伦斯" in out3
    # 非法输入（None / str / int）→ 空 list，绝不崩
    assert mod._flatten_locked_facts(None) == []
    assert mod._flatten_locked_facts("garbage") == []
    assert mod._flatten_locked_facts(123) == []


# ══════════════════════════════════════════════════════════════════════════
# check_secret_reveal —— manifest 要求揭露 vs changes 实际
# ══════════════════════════════════════════════════════════════════════════
def test_secret_reveal_missing_triggers_error():
    """manifest 要求揭露 1 条 secret，但 changes 无 secret.reveal → SECRET_NOT_REVEALED(error)。"""
    manifest = {"foreshadowing_summary": {"must_reveal_this_ch": 1}}
    errs = mod.check_secret_reveal("正文", {"foreshadowing_actions": []}, manifest)
    assert len(errs) == 1 and errs[0]["code"] == "SECRET_NOT_REVEALED"
    assert errs[0]["severity"] == "error"


def test_secret_reveal_satisfied_no_error():
    """changes 含足量 secret.reveal → 无 issue。manifest 不要求时也无 issue。"""
    manifest = {"foreshadowing_summary": {"must_reveal_this_ch": 1}}
    changes = {"foreshadowing_actions": [{"category": "secret", "type": "reveal"}]}
    assert mod.check_secret_reveal("正文", changes, manifest) == []
    # must_reveal_this_ch=0 → 直接短路
    assert mod.check_secret_reveal("正文", {}, {"foreshadowing_summary": {}}) == []


# ══════════════════════════════════════════════════════════════════════════
# check_locked_facts —— 左右手对抗 + 年龄冲突
# ══════════════════════════════════════════════════════════════════════════
def _mk_project_with_cards(tmp: Path, characters: list) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    (db / "人物卡.json").write_text(
        json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    return tmp


def test_locked_fact_left_right_hand_conflict():
    """locked_fact 含「左手」，正文只出现「右手」且无「左手」→ LOCKED_FACT_CONFLICT。"""
    tmp = Path(tempfile.mkdtemp())
    proj = _mk_project_with_cards(tmp, [
        {"id": "c1", "name": "陆参", "locked_facts": ["陆参的左手有旧疤"]},
    ])
    manifest = {"active_characters": ["陆参"]}
    body = "陆参抬起右手，按在墙上。右手指节发白。"
    errs = mod.check_locked_facts(body, proj, manifest)
    codes = [e["code"] for e in errs]
    assert "LOCKED_FACT_CONFLICT" in codes, f"应检出左右手冲突，得到 {errs}"


def test_locked_fact_age_conflict():
    """locked_fact 年龄 21，正文说角色 25 岁 → LOCKED_FACT_CONFLICT。"""
    tmp = Path(tempfile.mkdtemp())
    proj = _mk_project_with_cards(tmp, [
        {"id": "c1", "name": "江条款", "locked_facts": ["江条款今年21岁"]},
    ])
    manifest = {"active_characters": ["江条款"]}
    body = "江条款已经25岁了，却还像个孩子。"
    errs = mod.check_locked_facts(body, proj, manifest)
    msgs = " ".join(e["msg"] for e in errs)
    assert any(e["code"] == "LOCKED_FACT_CONFLICT" for e in errs), f"应检出年龄冲突: {errs}"
    assert "21" in msgs and "25" in msgs


def test_locked_fact_no_conflict_when_consistent():
    """正文与 locked_fact 一致（年龄相同 / 左手存在）→ 无 LOCKED_FACT_CONFLICT。"""
    tmp = Path(tempfile.mkdtemp())
    proj = _mk_project_with_cards(tmp, [
        {"id": "c1", "name": "陆参", "locked_facts": ["陆参的左手有疤", "陆参21岁"]},
    ])
    manifest = {"active_characters": ["陆参"]}
    body = "陆参的左手按在桌上，他今年21岁。"
    errs = mod.check_locked_facts(body, proj, manifest)
    assert [e for e in errs if e["code"] == "LOCKED_FACT_CONFLICT"] == []
    # 角色不在 active_characters → 完全短路
    assert mod.check_locked_facts(body, proj, {"active_characters": []}) == []


# ══════════════════════════════════════════════════════════════════════════
# format_json —— v18 --json 输出契约
# ══════════════════════════════════════════════════════════════════════════
def test_format_json_contract_shape_and_counts():
    """summary 四级计数 + total + errors 字段透传，schema_version/scanner 固定。"""
    result = {
        "passed": False,
        "fatal_count": 1, "error_count": 2, "warning_count": 1,
        "chapter_file": "章节/第001章/第001章.txt",
        "errors": [
            {"code": "CHANGES_MISSING", "severity": "fatal", "msg": "x", "fix_hint": "y"},
            {"code": "WC_TOO_SHORT", "severity": "error", "msg": "短", "fix_hint": "扩"},
            {"code": "BANNED_WORD", "severity": "error", "msg": "词", "fix_hint": "换"},
            {"code": "POV_HEAD_HOPPING", "severity": "warning", "msg": "跳", "fix_hint": "改"},
            {"code": "UNKNOWN_CHARACTER_DETECTED", "severity": "info", "msg": "未登记",
             "fix_hint": "声明"},
        ],
    }
    out = json.loads(mod.format_json(result, 1))
    assert out["schema_version"] == "1.0"
    assert out["scanner"] == "validate_chapter"
    assert out["chapter"] == 1
    assert out["chapter_file"] == "章节/第001章/第001章.txt"
    assert out["passed"] is False
    s = out["summary"]
    assert s["fatal"] == 1 and s["error"] == 2 and s["warning"] == 1
    # info 由 format_json 现场数 errors 里 severity==info 的条目
    assert s["info"] == 1
    assert s["total"] == 5
    # errors 字段裁剪到 code/severity/msg/fix_hint 四键
    assert set(out["errors"][0].keys()) == {"code", "severity", "msg", "fix_hint"}
    assert out["errors"][0]["code"] == "CHANGES_MISSING"


# ══════════════════════════════════════════════════════════════════════════
# CLI 退出码 —— 真 subprocess（含 sys.exit）
# ══════════════════════════════════════════════════════════════════════════
def test_cli_single_chapter_entry_removed_exits_2():
    """旧单章位置参入口不再公开：必须走 --cluster。"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
    p = _run_cli(str(tmp), "1", "--json")
    assert p.returncode == 2, f"期望 exit 2，实得 {p.returncode}; stderr={p.stderr}"
    assert "--cluster" in p.stdout
    assert "不再接受单章位置参" in p.stdout


def test_cli_insufficient_args_exits_2():
    """位置参不足 → 用法提示 → exit 2。"""
    tmp = Path(tempfile.mkdtemp())
    p = _run_cli(str(tmp))
    assert p.returncode == 2, f"期望 exit 2，实得 {p.returncode}"


def test_cli_non_integer_chapter_exits_2():
    """旧单章章节号位置参无论是否整数都硬拒。"""
    tmp = Path(tempfile.mkdtemp())
    p = _run_cli(str(tmp), "abc")
    assert p.returncode == 2, f"期望 exit 2，实得 {p.returncode}"
    assert "不再接受单章位置参" in p.stdout


def test_cli_cluster_range_not_found_exits_2():
    """--cluster 指向不存在的 cluster（章范围未回填）→ FILE_NOT_FOUND(fatal) → exit 2。"""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
    # 空事件簇.json → cluster_id_to_range 返回 None
    (tmp / "_数据库" / "事件簇.json").write_text(
        json.dumps({"clusters": []}, ensure_ascii=False), encoding="utf-8")
    p = _run_cli(str(tmp), "--cluster", "cluster_999", "--json")
    assert p.returncode == 2, f"期望 exit 2，实得 {p.returncode}; stderr={p.stderr}"
    out = json.loads(p.stdout)
    assert out["chapter"] == -1  # cluster 模式 chapter 占位 -1
    assert any(e["code"] == "FILE_NOT_FOUND" for e in out["errors"])
