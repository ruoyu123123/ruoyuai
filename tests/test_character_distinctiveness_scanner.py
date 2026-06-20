"""character_distinctiveness_scanner 专属测试 — 跨角色 Gini（advisory · 2026-06-20）"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import character_distinctiveness_scanner as cds  # noqa: E402


def _filler(n=15):
    return "\n".join(["夜色压在山脊石阶上他独自向上踏步影子拉得很长。"] * n)


def _write(text):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def _mk_project(baseline=None):
    proj = Path(tempfile.mkdtemp())
    (proj / "_数据库").mkdir(parents=True, exist_ok=True)
    obj = {}
    if baseline is not None:
        obj["character_voice_gini_baseline"] = baseline
    (proj / "_数据库" / "作者风格.json").write_text(
        json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return proj


# 4 角色：A 文绉绉 / B 粗鄙短句 / C 古风四字 / D 现代俚语
# 每角色 dialogue 拼足 MIN_CHAR_TOKENS=80 CJK
_LONG_DIALOGUES_DISTINCT = [
    '张三说：「鄙人乃江南举子敢问尊驾何方仙乡此地荒山野岭莫非有别事相托还望相告。」',
    '张三道：「在下读书十年颇通经史子集若是文章相试自当应承愿听高见。」',
    '李四喊：「滚蛋老子才不管什么经史子集你他妈再废话信不信我抽你大耳光啊。」',
    '李四怒道：「老子今天就要打你说什么道理屁都没用闪开别挡老子的路废话连篇。」',
    '王五低声道：「天行有常不为尧存不为桀亡君子之交淡如水小人之交甘若醴此乃古训也。」',
    '王五说道：「青山隐隐水迢迢秋尽江南草未凋君当作磐石妾当作蒲苇此句最是动人也。」',
    '赵六笑道：「哥们这事儿吧搞不好就拉胯了我先撤了哈兄弟你自己看着办再见拜拜了您嘞。」',
    '赵六道：「我这人吧最讨厌墨迹的事儿直接给个痛快话呗别整那些虚头巴脑的没意思啊。」',
]

# 4 角色但 dialogue 几乎一样（voice collapse）
_LONG_DIALOGUES_COLLAPSED = [
    '张三说：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '张三道：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '李四说：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '李四道：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '王五说：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '王五道：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '赵六说：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
    '赵六道：「他立刻拔出了剑挡在他的面前坚定地说我会保护你不会让你受到任何伤害。」',
]


# ---------- extraction ----------

def test_extract_dialogue_by_character():
    text = '\n'.join(_LONG_DIALOGUES_DISTINCT)
    bag = cds.extract_dialogues_by_character(text)
    assert set(bag.keys()) >= {"张三", "李四", "王五", "赵六"}
    assert all(len("".join(v)) > 0 for v in bag.values())


def test_extract_no_dialogue():
    bag = cds.extract_dialogues_by_character("他走进山门炉火映得脸通红。")
    assert bag == {}


# ---------- distinctiveness ----------

def test_compute_distinct_voices():
    """4 个区分度高的角色 → mean pair distance 大。"""
    text = "\n".join(_LONG_DIALOGUES_DISTINCT) + "\n" + _filler()
    r = cds.compute_distinctiveness(text)
    assert r["char_count"] >= 4
    assert r["mean_pair_distance"] > 0.4


def test_compute_collapsed_voices():
    """所有角色 dialogue 一致 → mean pair distance 极小。"""
    text = "\n".join(_LONG_DIALOGUES_COLLAPSED) + "\n" + _filler()
    r = cds.compute_distinctiveness(text)
    assert r["char_count"] >= 2
    assert r["mean_pair_distance"] is not None
    assert r["mean_pair_distance"] < 0.15


def test_compute_too_few_chars():
    """单一角色或无 dialogue → 不算。"""
    r = cds.compute_distinctiveness('张三说：「就这一句。」')
    assert r["mean_pair_distance"] is None
    assert "note" in r


# ---------- scan() ----------

def test_scan_active_collapsed_reports():
    text = "\n".join(_LONG_DIALOGUES_COLLAPSED) + "\n" + _filler()
    p = _write(text)
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p))
        assert r["verdict"] == "FAIL_MINOR"
        assert r["violations"][0]["kind"] == "inter_character_voice_collapse"
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_active_distinct_pass():
    text = "\n".join(_LONG_DIALOGUES_DISTINCT) + "\n" + _filler()
    p = _write(text)
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p))
        assert r["verdict"] == "PASS"
        assert r["mean_pair_distance"] > 0.2
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_shadow_no_report():
    text = "\n".join(_LONG_DIALOGUES_COLLAPSED) + "\n" + _filler()
    p = _write(text)
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "shadow"
        r = cds.scan(str(p))
        assert r["mode"] == "shadow"
        assert r["violations"] == []
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_off_skeleton():
    p = _write("\n".join(_LONG_DIALOGUES_DISTINCT))
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "off"
        r = cds.scan(str(p))
        assert r["mode"] == "off"
        assert r["violations"] == []
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_default_shadow():
    p = _write(_filler())
    try:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        r = cds.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        p.unlink(missing_ok=True)


def test_scan_short_skip():
    p = _write("张三说：「短。」")
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p))
        assert "太短" in r.get("note", "")
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_scan_single_character_skip():
    """只 1 个角色 → note 不算。"""
    p = _write('张三说：「' + '我去山上看那棵树这是一段很长的对话。' * 5 + '」' + _filler())
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p))
        # 单角色不算 / 无violations
        assert r["violations"] == []
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


# ---------- baseline ----------

def test_baseline_author_profile_first():
    text = "\n".join(_LONG_DIALOGUES_DISTINCT) + "\n" + _filler()
    p = _write(text)
    # 极高 floor·distinct 也得报
    proj = _mk_project(baseline={"mean_distance_min": 0.99})
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p), project_root=proj)
        assert r["baseline_source"] == "author_profile"
        assert r["thresholds"]["mean_distance_floor"] == 0.99
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


# ---------- advisory ----------

def test_always_advisory():
    text = "\n".join(_LONG_DIALOGUES_COLLAPSED) + "\n" + _filler()
    p = _write(text)
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "active"
        r = cds.scan(str(p))
        assert r["gate_level"] == "advisory"
        assert "hard_gate" not in json.dumps(r, ensure_ascii=False)
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)


def test_issue_code_not_hard_gate():
    assert cds.ISSUE_CODE == "INTER_CHARACTER_VOICE_COLLAPSE"
    try:
        import audit_hub
        assert "INTER_CHARACTER_VOICE_COLLAPSE" not in audit_hub.HARD_GATE_CODES
    except ImportError:
        pass


# ---------- helpers ----------

def test_char_3gram():
    c = cds._char_3gram_counter("你好世界")
    assert "你好世" in c
    assert "好世界" in c


def test_gini_uniform():
    """均匀分布 Gini 应为 0。"""
    assert cds._gini([0.5, 0.5, 0.5, 0.5]) == 0.0


def test_gini_concentrated():
    """集中分布 Gini 应较大。"""
    assert cds._gini([0, 0, 0, 1]) > 0.4


def test_cosine_empty():
    from collections import Counter
    assert cds._cosine_sim(Counter(), Counter()) == 0.0


def test_strip_changes():
    assert cds._strip_changes("正文。\n---CHANGES---\nx") == "正文。"


def test_invalid_mode_fallback():
    p = _write(_filler())
    try:
        os.environ["CHARACTER_DISTINCTIVENESS_MODE"] = "bogus"
        r = cds.scan(str(p))
        assert r["mode"] == "shadow"
    finally:
        os.environ.pop("CHARACTER_DISTINCTIVENESS_MODE", None)
        p.unlink(missing_ok=True)
