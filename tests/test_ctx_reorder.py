"""CTX_REORDER（P0 · 位置层北极星偏移修）回归测试。

守护两件事：
1. build_prompt 在 CTX_REORDER_MODE=active（默认）时，把**第一权威风格 skill** + 语感种子锚
   重排到「现在请写正文」生成点的**近邻**（RoPE 高位），manifest 事实索引留中段；
   off/shadow 时退回原版 join 顺序（零回归）。
2. _stream_once 截断续写回合在 active 模式下注入**精简风格锚**（签名句长/对话格式/禁套话 3 条），
   防 recency 漂移；off/shadow 时续写消息不含该锚（零回归）。

lost-in-the-middle / RoPE recency 实证：context 中段注意力最弱（U 型），紧贴生成点最强。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw


# ───────────────────── 公共桩 ─────────────────────

# 风格 skill 里埋一个独特锚字符串，便于在 prompt 里定位它的位置
_STYLE_MARKER = "【风格SKILL锚_UNIQUE_XYZ】"
# manifest 里埋另一个独特锚，便于定位 manifest 段位置
_MANIFEST_MARKER = "【MANIFEST事实索引锚_UNIQUE_ABC】"
_GEN_POINT = "# 现在请写正文"


def _make_project(tmp: Path, with_prev_chapter: bool = False) -> Path:
    """造一个能让 build_prompt 跑通的最小项目目录。"""
    root = tmp
    db = root / "_数据库"
    (db / ".manifest").mkdir(parents=True, exist_ok=True)

    # manifest（ch_NNN.json · 按 ch_start 命名）—— 埋 manifest 锚
    # build_prompt 读 .manifest/ch_{ch_start:03d}.json，故 ch_001/ch_002 都备一份
    for _ci in (1, 2):
        (db / ".manifest" / f"ch_{_ci:03d}.json").write_text(
            json.dumps({"note": _MANIFEST_MARKER, "facts": ["地点=沙盒"]}, ensure_ascii=False),
            encoding="utf-8")

    # 作者风格 skill —— 埋风格锚
    (db / "作者风格_skill.md").write_text(
        f"# 作者风格档\n\n{_STYLE_MARKER}\n\n句长偏短，对话用「」。\n",
        encoding="utf-8")

    # 进度.json（build_prompt 必读 · 无 guard）
    (db / "进度.json").write_text(
        json.dumps({"cluster_blueprint": {
            "cluster_001": {"scene_storyboard": [{"ch": 1, "beat": "开场"}]}
        }}, ensure_ascii=False), encoding="utf-8")

    # 人物卡 / 用户偏好
    (db / "人物卡.json").write_text(json.dumps({"主角": {"name": "阿渝"}}, ensure_ascii=False),
                                  encoding="utf-8")
    (db / "用户偏好.json").write_text(json.dumps({"tone": "冷峻"}, ensure_ascii=False),
                                    encoding="utf-8")

    # 事件簇.json（可选，但给一个让 cluster_brief 注入）
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "scope_summary": "开场对白场景。"}
    ]}, ensure_ascii=False), encoding="utf-8")

    if with_prev_chapter:
        ch_dir = root / "章节" / "第001章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "第001章.txt").write_text("前一章的最后一段正文，用于衔接。", encoding="utf-8")

    return root


def _build(root: Path, mode: str, ch_start: int = 1):
    """在指定 CTX_REORDER_MODE 下调 build_prompt，返回 (system, user)。"""
    prev = os.environ.get("CTX_REORDER_MODE")
    os.environ["CTX_REORDER_MODE"] = mode
    # 关掉可能引入噪声的并发功能（snippet 种子默认 on，但本测试项目没原文池 → 自然空，无碍）
    try:
        system, user, _trace = gw.build_prompt(root, cluster_id=1, ch_start=ch_start)
    finally:
        if prev is None:
            os.environ.pop("CTX_REORDER_MODE", None)
        else:
            os.environ["CTX_REORDER_MODE"] = prev
    return system, user


# ───────────────────── build_prompt 重排测试 ─────────────────────

def test_ctx_reorder_mode_default_active():
    """env 缺省 → active（默认全开）。"""
    prev = os.environ.pop("CTX_REORDER_MODE", None)
    try:
        assert gw._ctx_reorder_mode() == "active"
    finally:
        if prev is not None:
            os.environ["CTX_REORDER_MODE"] = prev


def test_ctx_reorder_mode_off_and_case_insensitive():
    """off / 大小写 / 空白 都规整。"""
    os.environ["CTX_REORDER_MODE"] = " OFF "
    try:
        assert gw._ctx_reorder_mode() == "off"
    finally:
        os.environ.pop("CTX_REORDER_MODE", None)


def test_active_style_skill_after_manifest_near_gen_point():
    """active：风格 skill 锚必须在 manifest 锚之后、且紧贴生成点（生成点近邻 RoPE 高位）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, "active")

        pos_style = user.find(_STYLE_MARKER)
        pos_manifest = user.find(_MANIFEST_MARKER)
        pos_gen = user.find(_GEN_POINT)
        assert pos_style != -1, "风格锚必须出现在 user prompt"
        assert pos_manifest != -1, "manifest 锚必须出现在 user prompt"
        assert pos_gen != -1, "生成点标记必须出现"

        # 核心断言①：风格锚在 manifest 锚之后（风格 skill 不再落中段，下沉到尾部）
        assert pos_style > pos_manifest, (
            f"active 模式风格 skill 应在 manifest 之后，实际 style={pos_style} manifest={pos_manifest}")
        # 核心断言②：风格锚紧贴生成点（生成点之前最后一个大块就是风格 skill）
        assert pos_style < pos_gen, "风格锚应在生成点之前"
        between = user[pos_style:pos_gen]
        # 风格锚到生成点之间不应再夹一个完整子系统大段（manifest/人物卡/调研/偏好）标题
        for other in ["## manifest", "## 人物卡", "## 调研 cache", "## 用户偏好"]:
            assert other not in between, (
                f"active 模式风格 skill 与生成点之间不应再夹「{other}」段（破坏 RoPE 高位贴合）")


def test_off_style_skill_in_middle_legacy_order():
    """off：退回原版 join —— 风格 skill 在 manifest 之前（中段），生成点前紧贴的是 manifest。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, "off")

        pos_style = user.find(_STYLE_MARKER)
        pos_manifest = user.find(_MANIFEST_MARKER)
        pos_gen = user.find(_GEN_POINT)
        assert pos_style != -1 and pos_manifest != -1 and pos_gen != -1
        # 原版：风格 skill 在中段（manifest 之前）
        assert pos_style < pos_manifest, (
            f"off 模式应保持原版顺序：风格 skill 在 manifest 之前，实际 style={pos_style} manifest={pos_manifest}")
        assert pos_manifest < pos_gen


def test_reorder_does_not_drop_content():
    """重排不丢内容：active 与 off 的 user 含相同核心块（人物卡/偏好/风格锚/manifest 锚/生成点/自查项）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _s_a, user_a = _build(root, "active")
        _s_o, user_o = _build(root, "off")
        for marker in [_STYLE_MARKER, _MANIFEST_MARKER, _GEN_POINT,
                       "## 人物卡", "## 用户偏好", "## 调研 cache",
                       "## cluster_blueprint", "现在开始写。", "word_count_cjk"]:
            assert marker in user_a, f"active 缺失 {marker}"
            assert marker in user_o, f"off 缺失 {marker}"


def test_reorder_with_prev_chapter_anchor_after_prev():
    """active + 有前一章：顺序应为 manifest → 前一章末尾 → 风格 skill → 生成点。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), with_prev_chapter=True)
        _system, user = _build(root, "active", ch_start=2)
        pos_manifest = user.find(_MANIFEST_MARKER)
        pos_prev = user.find("前一章末尾")
        pos_style = user.find(_STYLE_MARKER)
        pos_gen = user.find(_GEN_POINT)
        assert -1 not in (pos_manifest, pos_prev, pos_style, pos_gen)
        # manifest 中段 → 前一章 → 风格锚 → 生成点
        assert pos_manifest < pos_prev < pos_style < pos_gen, (
            f"顺序错: manifest={pos_manifest} prev={pos_prev} style={pos_style} gen={pos_gen}")


# ───────────────────── 续写风格锚测试 ─────────────────────

def _capture_continuation_msg(mode: str):
    """在指定 mode 下跑一次续写 _stream_once，返回续写那条 user 消息内容。"""
    cap = {}

    class MockChoice:
        def __init__(s, c, fr):
            s.delta = type("D", (), {"content": c})()
            s.finish_reason = fr

    class MockChunk:
        def __init__(s, c, fr):
            s.choices = [MockChoice(c, fr)]

    class MockStream:
        def __iter__(s):
            return iter([MockChunk("续", "stop")])

    class MockCompletions:
        def create(s, **kw):
            cap["messages"] = kw["messages"]
            return MockStream()

    class MockClient:
        def __init__(s):
            s.chat = type("C", (), {"completions": MockCompletions()})()

    class _P:
        max_tokens = None
        model = "m"
        temperature = 0.8
        name = "mock"

    prev = os.environ.get("CTX_REORDER_MODE")
    os.environ["CTX_REORDER_MODE"] = mode
    try:
        gw._stream_once(MockClient(), _P(), "sys", "usr", 1000, prior_assistant="已写正文")
    finally:
        if prev is None:
            os.environ.pop("CTX_REORDER_MODE", None)
        else:
            os.environ["CTX_REORDER_MODE"] = prev
    # 续写那条 user 是 messages[3]
    return cap["messages"][3]["content"]


def test_continuation_active_injects_style_anchor():
    """active：续写指令必须带精简风格锚（句长/对话格式/禁结构套话 3 条）。"""
    msg = _capture_continuation_msg("active")
    assert "截断" in msg  # 原续写指令仍在
    # 3 条锚的关键标志词
    assert "句长" in msg, "续写锚应含『句长』节奏提醒"
    assert "对话" in msg, "续写锚应含『对话格式』提醒"
    assert "与此同时" in msg, "续写锚应含结构性 AI 套话禁令样例"


def test_continuation_off_no_style_anchor():
    """off/shadow：续写指令退回原版（不注入风格锚 · 零回归）。"""
    msg = _capture_continuation_msg("off")
    assert "截断" in msg
    assert "与此同时" not in msg, "off 模式续写不应注入风格锚"
    assert "句长" not in msg


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print(f"  [OK] {_n}")
    print("ctx_reorder tests all passed")
