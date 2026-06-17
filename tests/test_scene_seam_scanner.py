"""scene_seam_scanner.py 专属回归测试（确定性 · 零 LLM / 零联网）。

⚠️ 已有 tests/test_seam_scanner.py 直接覆盖了 classify_seam_head / scan_seam_distribution /
reconcile_distribution / detect_cliche_connectors(主路) / load_author_seam_distribution /
模式 / gate_level / 真作者金标准。本文件**只补它没钉的底层原语与边界分支**，不重复：

  1. iter_seam_candidates —— 候选衔接点切分原语（CJK>=4 过滤 / 分隔符虚拟点 /
     is_after_break 只标分隔符后第一段）。
  2. split_seam_points —— 段首窗口列表 wrapper。
  3. _seam_head_window —— 段首窗口截断长度。
  4. _map_author_label —— 蒸馏标签→canonical 映射（子串先到先得 / 「其他」/ 映射不到 None）。
  5. resolve_style_json —— 风格 JSON 定位降级链（显式 / _数据库 优先序 / 用户偏好路径 / None）。
  6. detect_cliche_connectors —— HARD 最长匹配优先 + never 黑名单把 DUAL 升格为可判。
  7. scan_seam_distribution —— 分隔符虚拟点归「分隔符过渡」+ _MIN_SEAMS 样本不足闸。
  8. scan / main —— fatal 缺文件路径 + CLI 真退出码（2 fatal / 1 active warning / 0 clean）。

只用标准库 · test_* 无参数 · IO 走 tempfile.mkdtemp() · Windows utf-8。
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
import scene_seam_scanner as s  # noqa: E402

_TARGET = _SCRIPTS / "scene_seam_scanner.py"


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="seam_scene_"))


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ════════════════════════════════════════════════════════════════
# 1. iter_seam_candidates —— 候选衔接点切分原语
# ════════════════════════════════════════════════════════════════

def test_iter_candidates_filters_short_paragraphs():
    """CJK < 4 的段不进候选（短段噪声不污染分布）· >=4 才进。"""
    text = "abc\n好的\n方源走进山门看了一眼。"   # 前两段 CJK<4，末段 CJK>=4
    cands = s.iter_seam_candidates(text)
    heads = [h for h, _ in cands]
    assert any("方源走进山门" in h for h in heads), cands
    assert not any(h.strip() in ("abc", "好的") for h in heads), cands


def test_iter_candidates_divider_creates_virtual_point():
    """显式场景分隔符（单独成行 ---）本身记一个虚拟分隔符点 head='……'，且只对其后
    第一段标 is_after_break=True。"""
    text = "前面有一整段足够长的内容在这里铺陈。\n\n———\n后面新场景的第一段也足够长。\n这是同场景的第二段也足够长。"
    cands = s.iter_seam_candidates(text)
    # 必有一个虚拟分隔符点
    virtual = [c for c in cands if c[0] == "……"]
    assert len(virtual) == 1, cands
    assert virtual[0][1] is False, virtual   # 虚拟点自身 after_break=False
    # 分隔符后第一个实段 after_break=True，第二段 after_break=False
    real_after = [c for c in cands if c[1] is True]
    assert len(real_after) == 1, cands
    assert "后面新场景" in real_after[0][0], real_after


def test_iter_candidates_no_divider_all_false():
    """无分隔符正文：所有候选 after_break 都为 False（首块 bi==0）。"""
    text = "第一段内容足够长可以参与。\n\n第二段内容也足够长可以参与。"
    cands = s.iter_seam_candidates(text)
    assert cands, cands
    assert all(after is False for _, after in cands), cands


# ════════════════════════════════════════════════════════════════
# 2. split_seam_points / 3. _seam_head_window
# ════════════════════════════════════════════════════════════════

def test_split_seam_points_returns_only_heads():
    """split_seam_points 返回纯 head 列表（丢掉 after_break 标志）· 与 iter 同源。"""
    text = "第一段足够长用于切分测试。\n\n———\n第二段也足够长用于切分。"
    heads = s.split_seam_points(text)
    cand_heads = [h for h, _ in s.iter_seam_candidates(text)]
    assert heads == cand_heads, (heads, cand_heads)
    assert "……" in heads, heads   # 分隔符虚拟点在内


def test_seam_head_window_truncates():
    """段首窗口默认取前 14 字 · strip 前后空白。"""
    para = "   零一二三四五六七八九十甲乙丙丁戊己庚辛   "
    w = s._seam_head_window(para)
    assert len(w) == 14, w
    assert w == "零一二三四五六七八九十甲乙丙", w
    assert s._seam_head_window("短", n=14) == "短"


# ════════════════════════════════════════════════════════════════
# 4. _map_author_label —— 蒸馏标签→canonical 映射
# ════════════════════════════════════════════════════════════════

def test_map_author_label_substring_first_match():
    """子串匹配 · 先到先得：含『硬切』→ 硬切无过渡；含『心理』→ 心理过渡。"""
    assert s._map_author_label("空间硬切") == "硬切无过渡"
    assert s._map_author_label("内心独白承接") == "心理过渡"
    assert s._map_author_label("行动承接") == "动作承接"
    assert s._map_author_label("分隔符过场") == "分隔符过渡"


def test_map_author_label_other_and_unmappable_none():
    """『其他』/ 空 / 映射不到的标签 → None（不强行归类 · 不污染对账）。"""
    assert s._map_author_label("其他") is None
    assert s._map_author_label("") is None
    assert s._map_author_label(None) is None
    assert s._map_author_label("完全不存在的标签xyz") is None


# ════════════════════════════════════════════════════════════════
# 5. resolve_style_json —— 风格 JSON 定位降级链
# ════════════════════════════════════════════════════════════════

def test_resolve_explicit_path_wins():
    """① 显式 --style 存在 → 直接用；不存在 → None。"""
    d = _tmpdir()
    sp = _write(d / "my_style.json", "{}")
    assert s.resolve_style_json(None, str(sp)) == sp
    assert s.resolve_style_json(None, str(d / "missing.json")) is None


def test_resolve_db_final_priority_over_plain():
    """② 项目 _数据库：作者风格_FINAL.json 优先于 作者风格.json。"""
    d = _tmpdir()
    db = d / "_数据库"
    _write(db / "作者风格_FINAL.json", "{}")
    _write(db / "作者风格.json", "{}")
    got = s.resolve_style_json(d, None)
    assert got == db / "作者风格_FINAL.json", got


def test_resolve_db_plain_fallback():
    """② 只有 作者风格.json（无 FINAL）→ 回退到它。"""
    d = _tmpdir()
    db = d / "_数据库"
    plain = _write(db / "作者风格.json", "{}")
    assert s.resolve_style_json(d, None) == plain


def test_resolve_from_user_pref_path():
    """③ _数据库 无风格档 → 读 用户偏好.json.style_baseline_data_path 指向的文件。"""
    d = _tmpdir()
    db = d / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    ext = _write(d / "外部风格.json", "{}")
    _write(db / "用户偏好.json",
           json.dumps({"style_baseline_data_path": str(ext)}, ensure_ascii=False))
    assert s.resolve_style_json(d, None) == ext


def test_resolve_none_when_nothing():
    """无 project / 无任何风格档 → None（顾问制降级单轨 · 不报错）。"""
    assert s.resolve_style_json(None, None) is None
    d = _tmpdir()
    (d / "_数据库").mkdir(parents=True, exist_ok=True)
    assert s.resolve_style_json(d, None) is None


# ════════════════════════════════════════════════════════════════
# 6. detect_cliche_connectors —— 最长匹配 + never 升格 DUAL
# ════════════════════════════════════════════════════════════════

def test_cliche_longest_match_precedence():
    """HARD 集按长度降序匹配：『话说回来』优先于其子词『话说』（命中更具体的连接词）。"""
    text = "前面有一整段足够长的铺陈内容在此。\n\n———\n话说回来，那族长终究还是来了。"
    res = s.detect_cliche_connectors(text, never_connectors=[])
    hits = res["cliche_hits"]
    assert hits, res
    assert hits[0]["connector"] == "话说回来", hits   # 不是被截成「话说」
    assert hits[0]["tier"] == "hard", hits


def test_cliche_dual_needs_never_declaration():
    """DUAL 双用词只有在作者 never 黑名单声明后才升格为可判（分隔符后过场）。"""
    text = "前面一整段足够长内容铺陈在此处。\n\n———\n随后，他走进了密室深处。"
    # never 未声明『随后』→ 不判（避免对真作者合法时间副词矫枉过正）
    assert s.detect_cliche_connectors(text, never_connectors=[])["cliche_hit_count"] == 0
    # never 声明后 → 判
    res = s.detect_cliche_connectors(text, never_connectors=["随后"])
    assert res["cliche_hit_count"] >= 1, res
    assert any(h["connector"] == "随后" and h["tier"] == "dual" for h in res["cliche_hits"]), res


# ════════════════════════════════════════════════════════════════
# 7. scan_seam_distribution —— 分隔符虚拟点 + _MIN_SEAMS 闸
# ════════════════════════════════════════════════════════════════

def test_divider_virtual_point_counts_as_separator_category():
    """分隔符虚拟点 head='……' 且 after_break=False → 归『分隔符过渡』类目。"""
    text = "前面一整段足够长内容在此铺陈描写。\n\n———\n后面也是一整段足够长的内容。"
    res = s.scan_seam_distribution(text)
    assert res["counts"].get("分隔符过渡", 0) >= 1, res


def test_min_seams_gate_blocks_advisory():
    """衔接点 < _MIN_SEAMS → enough=False → 即使有套话也不上浮 advisory（样本太少不判）。"""
    # 只造 2 个套话衔接点（< _MIN_SEAMS=6）
    text = "\n\n".join(["———\n话分两头，且说一段足够长的内容。",
                        "———\n镜头一转，再来一段足够长的内容。"])
    draft = _write(_tmpdir() / "draft.txt", text)
    rep = s.scan(draft, None)
    assert rep["seam_points"] < s._MIN_SEAMS, rep
    assert rep["enough_samples"] is False, rep
    assert rep["issues"] == [], rep   # 样本不足 → 不上浮
    assert rep["warning"] is None, rep


# ════════════════════════════════════════════════════════════════
# 8. scan / main —— fatal 缺文件 + CLI 真退出码
# ════════════════════════════════════════════════════════════════

def test_scan_fatal_when_draft_missing():
    """draft 文件不存在 → 返回 {_fatal: ...}（不抛异常）。"""
    rep = s.scan(_tmpdir() / "does_not_exist.txt", None)
    assert "_fatal" in rep, rep


def test_corrupt_author_json_degrades_not_crash():
    """作者档损坏 → 降级（author._error 记录 · distribution 空）· 不抛异常·不 fatal。"""
    d = _tmpdir()
    draft = _write(d / "draft.txt", "一整段足够长用于扫描的正文内容铺陈。")
    bad_style = _write(d / "bad.json", "{ not valid json ]")
    rep = s.scan(draft, bad_style)
    assert "_fatal" not in rep, rep
    assert rep["author_loaded"] is False, rep


def _run_cli(args, env):
    """跑真 CLI · 强制子进程 UTF-8 stdio（Windows 控制台默认 cp936 会把含中文的 JSON
    输出解码崩溃）· 二进制取回手动 utf-8(replace) 解码，绝不让 reader thread 崩。"""
    proc = subprocess.run(
        [sys.executable, str(_TARGET), *args],
        capture_output=True, env=env)   # 不传 text/encoding → bytes
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    return proc.returncode, out


def test_cli_exit_codes():
    """真 CLI 退出码三态：缺文件=2(fatal) · active 命中套话=1 · 干净=0。"""
    d = _tmpdir()
    env = dict(os.environ)
    env.pop("SEAM_SCANNER_MODE", None)   # 确保 active 默认
    env["PYTHONIOENCODING"] = "utf-8"    # 强制子进程以 UTF-8 写 stdout

    # exit 2：draft 不存在
    rc2, _ = _run_cli([str(d / "nope.txt")], env)
    assert rc2 == 2, rc2

    # exit 1：active 模式 6+ 个 HARD 套话衔接点（rate 高 → advisory 上浮）
    cliche_text = "\n\n".join(
        [f"———\n话分两头，且说第{i}段足够长的内容铺陈在此。" for i in range(7)])
    cliche_draft = _write(d / "cliche.txt", cliche_text)
    rc1, out1 = _run_cli([str(cliche_draft)], env)
    assert rc1 == 1, (rc1, out1)
    rep1 = json.loads(out1)
    assert rep1["warning"], rep1

    # exit 0：干净正文（无套话 · 普通叙述）
    clean_text = "\n\n".join(
        [f"他握紧手中的剑，掌心微微冒汗，第{i}段足够长的内容。" for i in range(7)])
    clean_draft = _write(d / "clean.txt", clean_text)
    rc0, _ = _run_cli([str(clean_draft)], env)
    assert rc0 == 0, rc0
