"""cross_scene_voice_drift_scanner.py 确定性回归测试（零 LLM / 零联网 / 零依赖）。

聚焦**尚未被姊妹测试覆盖**的核心确定性逻辑：
  - test_l4_voice_d4_d8.py 覆盖 _d4d8_mode / _voice_fingerprint / _fingerprint_distance /
    compute_d4_distinctiveness / compute_d8_tic_consistency / scan 的 D4D8 mode 分支。
  - test_mmr_voice.py 覆盖 compute_cross_char_voice_collapse + issues[] + audit_hub gate。

本文件钉死的是 scanner 的「主业」管线（这些之前无任何直测）：
  1. load()                         —— JSON 加载容错（缺失/损坏/正常）
  2. split_scenes()                 —— 场景切分启发式（分隔符/min_len 过滤/单场景回退）
  3. extract_dialogues_by_speaker() —— speaker tag 追踪 + alias 归一 + 弯引号抽取 + last_speaker 续接
  4. compute_voice_metrics()        —— 单角色 voice 指标（含空集 None）
  5. scan() 的 VOICE_DRIFT 主逻辑   —— 句长 >50% 偏差 + count>=3 才报 / <2 场景不报 / _fatal
  6. main() CLI 退出码              —— 缺参 exit2 / _fatal exit2 / 漂移 warning exit1

约定：标准库 only · test_* 无参数 · 失败 raise AssertionError · 文件 IO utf-8。
scan 内含 env 门控 D4D8，drift 主逻辑测试一律先设 VOICE_D4D8_MODE=off 隔离（只测 drift）。
main() 含 sys.exit → 走 subprocess 跑真 CLI（参照 test_cross_cluster_fate_drift_aggregate.py）。
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
import cross_scene_voice_drift_scanner as vd  # noqa: E402

_TARGET = _SCRIPTS / "cross_scene_voice_drift_scanner.py"


# ──────────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────────
def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def _draft_with_chars(tmp: Path, text: str, characters=None) -> Path:
    """写 draft.txt（+可选人物卡.json 提供 aliases）。"""
    db = tmp / "_数据库"
    db.mkdir(exist_ok=True)
    if characters is not None:
        (db / "人物卡.json").write_text(
            json.dumps({"characters": characters}, ensure_ascii=False),
            encoding="utf-8")
    d = tmp / "draft.txt"
    d.write_text(text, encoding="utf-8")
    return d


def _run_cli(*args, env_extra=None):
    """跑真 CLI；stdout/err 容错解码（Windows 控制台非 UTF-8），断言只锚 returncode。"""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(_TARGET), *[str(a) for a in args]],
        capture_output=True, cwd=str(_ROOT), env=env)
    out = p.stdout.decode("utf-8", errors="replace") if p.stdout else ""
    err = p.stderr.decode("utf-8", errors="replace") if p.stderr else ""
    return p.returncode, out, err


# ══════════════════════════════════════════════════════════════════════════
# [A] load() —— JSON 加载容错
# ══════════════════════════════════════════════════════════════════════════
def test_A_load_missing_returns_empty_dict():
    tmp = _tmp()
    assert vd.load(tmp / "nope.json") == {}


def test_A_load_corrupt_returns_empty_dict():
    tmp = _tmp()
    bad = tmp / "bad.json"
    bad.write_text("{not valid json,,,", encoding="utf-8")
    assert vd.load(bad) == {}   # 异常吞掉 → {} 兜底


def test_A_load_valid_returns_parsed():
    tmp = _tmp()
    ok = tmp / "ok.json"
    ok.write_text(json.dumps({"k": [1, 2]}, ensure_ascii=False), encoding="utf-8")
    assert vd.load(ok) == {"k": [1, 2]}


# ══════════════════════════════════════════════════════════════════════════
# [B] split_scenes() —— 场景切分启发式
# ══════════════════════════════════════════════════════════════════════════
def test_B_split_on_dash_separator():
    # 两段各 >= min_scene_len(500) → 切成 2 场景
    text = "甲" * 600 + "\n---\n" + "乙" * 600
    assert len(vd.split_scenes(text)) == 2


def test_B_split_on_triple_newline():
    text = "甲" * 600 + "\n\n\n" + "乙" * 600
    assert len(vd.split_scenes(text)) == 2


def test_B_short_parts_filtered_then_fallback_to_whole():
    # 整体太短（< 500）→ 过滤后为空 → 回退整段单场景
    scenes = vd.split_scenes("很短的一段文字")
    assert scenes == ["很短的一段文字"]


def test_B_below_min_len_part_dropped():
    # 第一段 >= 500 保留，第二段 < 500 被 min_scene_len 过滤掉 → 只剩 1 场景
    text = "甲" * 600 + "\n---\n" + "乙" * 100
    assert len(vd.split_scenes(text)) == 1


def test_B_custom_min_scene_len():
    # 显式放低门槛 → 两段都保留
    text = "甲" * 50 + "\n---\n" + "乙" * 50
    assert len(vd.split_scenes(text, min_scene_len=10)) == 2


# ══════════════════════════════════════════════════════════════════════════
# [C] extract_dialogues_by_speaker() —— speaker 追踪 + alias + 弯引号 + 续接
# ══════════════════════════════════════════════════════════════════════════
def test_C_extract_wide_and_ascii_and_corner_quotes():
    # 弯引号 “” / ASCII "" / 方引号「」 三种都要抽到。
    # speaker 名需满足 SPEAKER_PATTERN 的 2-4 汉字（"阿强" 合法，单字 "他" 不匹配）。
    line = '阿强开口：“弯引号对白”然后"ascii对白"还有「方引号对白」'
    out = vd.extract_dialogues_by_speaker(line, {})
    # 3 条对白全归在同一 speaker key("阿强") 下
    assert out == {"阿强": ["弯引号对白", "ascii对白", "方引号对白"]}


def test_C_alias_canonicalization():
    # 开口 形态可干净捕获 "阿强" → alias 映射到 canonical "陈强"
    out = vd.extract_dialogues_by_speaker('阿强开口：“你好。”', {"阿强": "陈强"})
    assert "陈强" in out
    assert out["陈强"] == ["你好。"]


def test_C_last_speaker_continuation():
    # 首行带 speaker tag，后续裸对白行续接到 last_speaker。
    # 中间叙述行必须不含 说/道/问/答 等 SPEAKER_PATTERN 触发词，否则会误抢 last_speaker。
    txt = '阿强开口：“第一句。”\n他低头看了看地面。\n“第二句继续。”'
    out = vd.extract_dialogues_by_speaker(txt, {})
    assert out == {"阿强": ["第一句。", "第二句继续。"]}


def test_C_no_speaker_no_attribution():
    # 全程没有 speaker tag → last_speaker 为 None → 对白被丢弃（不归任何角色）
    out = vd.extract_dialogues_by_speaker('“无主对白一。”\n“无主对白二。”', {})
    assert out == {}


# ══════════════════════════════════════════════════════════════════════════
# [D] compute_voice_metrics() —— 单角色 voice 指标
# ══════════════════════════════════════════════════════════════════════════
def test_D_metrics_basic_fields():
    m = vd.compute_voice_metrics(["你好？", "走…", "嗯"])
    assert m["count"] == 3
    assert m["avg_len"] == (3 + 2 + 1) / 3          # 平均字符数
    assert m["max_len"] == 3
    assert abs(m["has_ellipsis_ratio"] - 1 / 3) < 1e-9   # "走…" 含省略号
    assert abs(m["has_question_ratio"] - 1 / 3) < 1e-9   # "你好？" 含问号


def test_D_metrics_ascii_ellipsis_and_question():
    # ASCII "..." 和 ASCII "?" 也要识别
    m = vd.compute_voice_metrics(["wait...", "really?"])
    assert m["has_ellipsis_ratio"] == 0.5
    assert m["has_question_ratio"] == 0.5


def test_D_metrics_empty_returns_none():
    assert vd.compute_voice_metrics([]) is None


# ══════════════════════════════════════════════════════════════════════════
# [E] scan() —— VOICE_DRIFT 主逻辑（D4D8 关掉以隔离 drift）
# ══════════════════════════════════════════════════════════════════════════
def _drift_draft():
    """同一角色（陈强，alias 阿强）句长在两场景剧烈漂移（极短 vs 极长）。"""
    pad = "旁白叙述内容补足场景长度。" * 80   # 每场景 >= 500 字
    short = "\n".join('阿强开口：“嗯。”' for _ in range(4))
    longl = "\n".join(
        '阿强开口：“这是一段非常非常非常非常非常非常非常长长长长长的对白内容哈哈哈哈。”'
        for _ in range(4))
    sc0 = short + "\n" + pad
    sc1 = longl + "\n" + pad
    return sc0 + "\n---\n" + sc1


def test_E_drift_detected_with_alias_canonicalization():
    os.environ["VOICE_D4D8_MODE"] = "off"   # 隔离：只测 drift 主逻辑
    try:
        tmp = _tmp()
        d = _draft_with_chars(tmp, _drift_draft(),
                              characters=[{"name": "陈强", "aliases": ["阿强"]}])
        rep = vd.scan(tmp, d)
        assert rep["scenes_scanned"] == 2
        # alias 阿强 → canonical 陈强，且两场景都标漂移
        assert rep["drift_issues_count"] >= 1
        chars = {it["character"] for it in rep["drift_issues"]}
        assert "陈强" in chars   # alias 已归一
        it = rep["drift_issues"][0]
        assert it["type"] == "avg_dialogue_length_drift"
        assert it["deviation_pct"] > 50.0       # >50% 才报
        assert it["sample_count"] >= 3          # count>=3 阈值
        assert rep["severity"] == "warning"
        assert rep["warning"] and "漂移" in rep["warning"]
    finally:
        os.environ.pop("VOICE_D4D8_MODE", None)


def test_E_no_drift_when_consistent_length():
    os.environ["VOICE_D4D8_MODE"] = "off"
    try:
        tmp = _tmp()
        pad = "旁白叙述内容补足场景长度。" * 80
        # 两场景句长一致 → 无漂移
        same = "\n".join('阿强开口：“走吧朋友。”' for _ in range(4))
        text = (same + "\n" + pad) + "\n---\n" + (same + "\n" + pad)
        d = _draft_with_chars(tmp, text, characters=[])
        rep = vd.scan(tmp, d)
        assert rep["scenes_scanned"] == 2
        assert rep["drift_issues_count"] == 0
        assert rep["warning"] is None
        assert rep["severity"] == "info"
    finally:
        os.environ.pop("VOICE_D4D8_MODE", None)


def test_E_single_scene_no_cross_scene_comparison():
    os.environ["VOICE_D4D8_MODE"] = "off"
    try:
        tmp = _tmp()
        # 整段无分隔符 → 单场景 → 角色出场 <2 场景 → 跳过对比
        text = "\n".join('阿强开口：“嗯。”' for _ in range(5))
        d = _draft_with_chars(tmp, text, characters=[])
        rep = vd.scan(tmp, d)
        assert rep["scenes_scanned"] == 1
        assert rep["drift_issues_count"] == 0
    finally:
        os.environ.pop("VOICE_D4D8_MODE", None)


def test_E_sample_count_below_3_not_flagged():
    os.environ["VOICE_D4D8_MODE"] = "off"
    try:
        tmp = _tmp()
        pad = "旁白叙述内容补足场景长度。" * 80
        # 每场景该角色只有 2 句（< 3）→ 即使句长漂移巨大也不报
        sc0 = "阿强开口：“嗯。”\n阿强开口：“哦。”\n" + pad
        sc1 = ("阿强开口：“这是一段非常非常非常非常非常非常长长长长长的话哈哈哈哈。”\n"
               "阿强开口：“又一段同样很长很长很长很长很长很长很长的话内容。”\n") + pad
        d = _draft_with_chars(tmp, sc0 + "\n---\n" + sc1, characters=[])
        rep = vd.scan(tmp, d)
        assert rep["scenes_scanned"] == 2
        # sample_count==2 < 3 → drift 逻辑跳过该场景项
        assert rep["drift_issues_count"] == 0
    finally:
        os.environ.pop("VOICE_D4D8_MODE", None)


def test_E_fatal_on_missing_draft():
    tmp = _tmp()
    rep = vd.scan(tmp, tmp / "does_not_exist.txt")
    assert "_fatal" in rep
    assert "不存在" in rep["_fatal"]


# ══════════════════════════════════════════════════════════════════════════
# [F] main() CLI 退出码 —— subprocess 跑真 CLI
# ══════════════════════════════════════════════════════════════════════════
def test_F_cli_missing_args_exit_2():
    rc, out, err = _run_cli()   # 缺参（少于 2 个）
    assert rc == 2


def test_F_cli_fatal_missing_draft_exit_2():
    tmp = _tmp()
    rc, out, err = _run_cli(tmp, tmp / "nope.txt")
    assert rc == 2   # _fatal → exit 2（数据缺失≠干净通过）


def test_F_cli_clean_draft_exit_0():
    tmp = _tmp()
    # 无对白草稿 → 无漂移无 warning → exit 0
    d = _draft_with_chars(tmp, "纯叙述没有任何对白内容。" * 20, characters=[])
    rc, out, err = _run_cli(tmp, d, env_extra={"VOICE_D4D8_MODE": "off"})
    assert rc == 0


def test_F_cli_drift_warning_exit_1():
    tmp = _tmp()
    d = _draft_with_chars(tmp, _drift_draft(),
                          characters=[{"name": "陈强", "aliases": ["阿强"]}])
    rc, out, err = _run_cli(tmp, d, env_extra={"VOICE_D4D8_MODE": "off"})
    assert rc == 1   # warning → exit 1


# ──────────────────────────────────────────────────────────────────────────
# 自跑入口（零依赖 · 直跑亦可）
# ──────────────────────────────────────────────────────────────────────────
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
    print(f"[cross_scene_voice_drift_scanner] "
          f"{passed} passed / {failed} failed / {passed + failed} total")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if _run() else 1)
