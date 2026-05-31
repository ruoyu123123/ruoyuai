"""skill 硬约束 primacy 重排 + style_fp 下沉 —— 第8轮 ctx 同族补完回归测试。

守护两件事（位置层优化 · 直击 skill 长文鲁棒 D 级根因）：

(a) **style_fp 下沉**（第8轮 note）：第8轮 ctx 重排只下沉了风格 skill + 语感种子锚，量化指纹
    style_fp_block 仍留在 prompt 顶部（task_intro 后）= lost-in-the-middle dead zone。量化数值约束
    比叙述性 skill 更怕稀释（4 源验证），本批把 style_fp_block **也下沉到生成点近邻**（RoPE 高位 ·
    风格锚区），紧跟风格 skill 之后。

(b) **skill 硬约束 primacy**（IFScale 实证）：长 skill 中段的硬约束维（段长契约 / 禁用词 / 对话格式）
    衰减。本批在生成点近邻补一段**精简硬约束 primacy 重述**（轻量重述非整 prompt 复制 · 黑箱零成本），
    把硬约束维 primacy 提到显著位置。

env 默认 active（PROFILE_INJECT 由 manifest 字段驱动 · SKILL_PRIMACY_MODE 默认 active）；
off/shadow 零回归；不破坏第8轮 ctx 重排（风格 skill / seed / manifest 相对位置不变）。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import gen_writer as gw


# ───────────────────── 公共桩 ─────────────────────

_STYLE_MARKER = "【风格SKILL锚_PRIM_XYZ】"
_MANIFEST_MARKER = "【MANIFEST事实索引锚_PRIM_ABC】"
# style_fp directive 里埋一个独特锚，便于确认指纹内容真进 prompt。
# ⚠️ 注意：该锚会同时出现在 (1) 原始 manifest JSON dump（author_style_fingerprint 是 manifest 字段，
#   {manifest} 段会把整 JSON 注入）与 (2) 解析后的「作者量化风格指纹」段。定位指纹段位置须用下面的
#   唯一段标题 _STYLE_FP_HEADING，而非本锚。
_STYLE_FP_MARKER = "【量化指纹锚_PRIM_FP_777】"
# 解析后的量化指纹段唯一标题（_build_style_fingerprint_section 产出 · 不会出现在原始 manifest dump）
_STYLE_FP_HEADING = "作者量化风格指纹"
# primacy 段的稳定标题（_build_hard_constraint_primacy_block 产出）
_PRIMACY_HEADING = "## ⚙️ 硬约束维 primacy 重述"
_GEN_POINT = "# 现在请写正文"


def _make_project(tmp: Path, with_prev_chapter: bool = False,
                  with_style_fp: bool = True) -> Path:
    """造一个能让 build_prompt 跑通的最小项目目录。

    with_style_fp=True → manifest 里塞 author_style_fingerprint（含独特锚的 directive），
    模拟 PROFILE_INJECT_MODE=active 下 build_manifest 注入的量化指纹。
    """
    root = tmp
    db = root / "_数据库"
    (db / ".manifest").mkdir(parents=True, exist_ok=True)

    manifest_obj = {"note": _MANIFEST_MARKER, "facts": ["地点=沙盒"]}
    if with_style_fp:
        manifest_obj["author_style_fingerprint"] = {
            "source": "author_profile",
            "n_chapters": 200,
            "directives": [
                f"句长均值目标 19 字（{_STYLE_FP_MARKER}）",
                "对话占比目标 25%",
            ],
        }
    for _ci in (1, 2):
        (db / ".manifest" / f"ch_{_ci:03d}.json").write_text(
            json.dumps(manifest_obj, ensure_ascii=False), encoding="utf-8")

    (db / "作者风格_skill.md").write_text(
        f"# 作者风格档\n\n{_STYLE_MARKER}\n\n句长偏短，对话用「」。\n",
        encoding="utf-8")

    (db / "进度.json").write_text(
        json.dumps({"cluster_blueprint": {
            "cluster_001": {"scene_storyboard": [{"ch": 1, "beat": "开场"}]}
        }}, ensure_ascii=False), encoding="utf-8")

    (db / "人物卡.json").write_text(json.dumps({"主角": {"name": "阿渝"}}, ensure_ascii=False),
                                  encoding="utf-8")
    (db / "用户偏好.json").write_text(json.dumps({"tone": "冷峻"}, ensure_ascii=False),
                                    encoding="utf-8")
    (db / "事件簇.json").write_text(json.dumps({"clusters": [
        {"cluster_id": "cluster_001", "scope_summary": "开场对白场景。"}
    ]}, ensure_ascii=False), encoding="utf-8")

    if with_prev_chapter:
        ch_dir = root / "章节" / "第001章"
        ch_dir.mkdir(parents=True, exist_ok=True)
        (ch_dir / "第001章.txt").write_text("前一章的最后一段正文，用于衔接。", encoding="utf-8")

    return root


def _build(root: Path, ctx_mode: str = "active", primacy_mode: str = "active",
           ch_start: int = 1):
    """在指定 CTX_REORDER_MODE + SKILL_PRIMACY_MODE 下调 build_prompt，返回 (system, user)。"""
    saved = {k: os.environ.get(k) for k in ("CTX_REORDER_MODE", "SKILL_PRIMACY_MODE")}
    os.environ["CTX_REORDER_MODE"] = ctx_mode
    os.environ["SKILL_PRIMACY_MODE"] = primacy_mode
    try:
        system, user, _trace = gw.build_prompt(root, cluster_id=1, ch_start=ch_start)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return system, user


# ───────────────────── env 开关默认 active ─────────────────────

def test_skill_primacy_mode_default_active():
    """env 缺省 → active（默认全开 · 真生效）。"""
    prev = os.environ.pop("SKILL_PRIMACY_MODE", None)
    try:
        assert gw._skill_primacy_mode() == "active"
    finally:
        if prev is not None:
            os.environ["SKILL_PRIMACY_MODE"] = prev


def test_skill_primacy_mode_off_and_case_insensitive():
    """off / 大小写 / 空白 都规整。"""
    os.environ["SKILL_PRIMACY_MODE"] = " OFF "
    try:
        assert gw._skill_primacy_mode() == "off"
    finally:
        os.environ.pop("SKILL_PRIMACY_MODE", None)


# ───────────────────── primacy 块纯函数 ─────────────────────

def test_primacy_block_active_lists_three_hard_dims():
    """active：primacy 块点名 3 个最易衰减的硬约束维（段长契约 / 禁用词 / 对话格式）。"""
    prev = os.environ.get("SKILL_PRIMACY_MODE")
    os.environ["SKILL_PRIMACY_MODE"] = "active"
    try:
        block = gw._build_hard_constraint_primacy_block()
    finally:
        if prev is None:
            os.environ.pop("SKILL_PRIMACY_MODE", None)
        else:
            os.environ["SKILL_PRIMACY_MODE"] = prev
    assert block
    assert "段长契约" in block
    assert "禁用词" in block
    assert "对话格式" in block
    # 轻量重述：结构性 AI 套话样例 + advisory 措辞（北极星⑤ 不硬锁 / 以 skill 为准）
    assert "与此同时" in block
    assert "advisory" in block
    assert "以上方作者风格 skill" in block or "以.*skill.*为准" in block


def test_primacy_block_off_and_shadow_empty():
    """off / shadow → 空串（零回归不注入）。"""
    for mode in ("off", "shadow", "garbage_then_off"):
        prev = os.environ.get("SKILL_PRIMACY_MODE")
        os.environ["SKILL_PRIMACY_MODE"] = mode if mode != "garbage_then_off" else "off"
        try:
            assert gw._build_hard_constraint_primacy_block() == ""
        finally:
            if prev is None:
                os.environ.pop("SKILL_PRIMACY_MODE", None)
            else:
                os.environ["SKILL_PRIMACY_MODE"] = prev


# ───────────────────── (a) style_fp 下沉位置 ─────────────────────

def test_active_style_fp_sinks_to_gen_point_near_zone():
    """active：量化指纹段必须下沉到生成点近邻——在 manifest 段之后、紧贴生成点，
    不再留在 prompt 顶部（task_intro 之后的 dead zone）。

    用唯一段标题 _STYLE_FP_HEADING 定位（directive 锚会同时漏进原始 manifest dump · 不唯一）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active")

        pos_fp = user.find(_STYLE_FP_HEADING)
        pos_manifest_sec = user.find("## manifest")  # manifest 段标题
        pos_gen = user.find(_GEN_POINT)
        pos_intro = user.find("# 写作任务")
        assert pos_fp != -1, "量化指纹段必须出现在 user prompt"
        assert -1 not in (pos_manifest_sec, pos_gen, pos_intro)

        # 核心断言①：量化指纹段下沉到 manifest 段之后（不再落顶部 dead zone）
        assert pos_fp > pos_manifest_sec, (
            f"active 量化指纹应在 manifest 段之后，实际 fp={pos_fp} manifest_sec={pos_manifest_sec}")
        # 核心断言②：量化指纹紧贴生成点（生成点之前）
        assert pos_fp < pos_gen, "量化指纹应在生成点之前"
        # 核心断言③：量化指纹不在 prompt 顶部区（task_intro 紧后的中段 dead zone）
        #   —— 用 cluster_blueprint 标题当顶部区的右边界：下沉后 fp 应在 blueprint 之后
        pos_blueprint = user.find("## cluster_blueprint")
        assert pos_blueprint != -1
        assert pos_fp > pos_blueprint, (
            f"量化指纹应已离开顶部区（在 cluster_blueprint 之后），实际 fp={pos_fp} bp={pos_blueprint}")
        # directive 内容确实进了 prompt（不止有标题）
        assert _STYLE_FP_MARKER in user


def test_active_style_fp_after_style_skill():
    """active：量化指纹段紧跟风格 skill 之后（数值约束最贴生成点 · 最怕稀释 → RoPE 最高位）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active")
        pos_style = user.find(_STYLE_MARKER)
        pos_fp = user.find(_STYLE_FP_HEADING)
        assert pos_style != -1 and pos_fp != -1
        assert pos_style < pos_fp, (
            f"量化指纹应在风格 skill 之后（更贴生成点），实际 style={pos_style} fp={pos_fp}")


# ───────────────────── (b) 硬约束 primacy 位置 ─────────────────────

def test_active_primacy_block_near_gen_point():
    """active：硬约束 primacy 段出现在生成点近邻（manifest 之后、生成点之前）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active", primacy_mode="active")
        pos_prim = user.find(_PRIMACY_HEADING)
        pos_manifest = user.find(_MANIFEST_MARKER)
        pos_gen = user.find(_GEN_POINT)
        assert pos_prim != -1, "primacy 段必须出现"
        assert pos_manifest < pos_prim < pos_gen, (
            f"primacy 应在 manifest 后、生成点前，实际 manifest={pos_manifest} "
            f"prim={pos_prim} gen={pos_gen}")


def test_primacy_off_zero_regression_no_block():
    """SKILL_PRIMACY_MODE=off → user prompt 不含 primacy 段（零回归 · 关闭手段）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active", primacy_mode="off")
        assert _PRIMACY_HEADING not in user, "off 模式不应注入 primacy 段"


def test_primacy_in_off_ctx_branch_too():
    """SKILL_PRIMACY_MODE 与 CTX_REORDER_MODE 正交：CTX off（原版 join）下 primacy 仍注入近生成点。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="off", primacy_mode="active")
        pos_prim = user.find(_PRIMACY_HEADING)
        pos_gen = user.find(_GEN_POINT)
        assert pos_prim != -1, "CTX off 分支也应注入 primacy"
        assert pos_prim < pos_gen


# ───────────────────── 不破坏第8轮 ctx 重排 ─────────────────────

def test_ctx_reorder_relative_order_unbroken():
    """不破坏第8轮 ctx 重排：active 下 manifest → 风格 skill → 生成点 的相对顺序仍成立，
    且风格 skill 与生成点之间不再夹整段 manifest/人物卡/调研/偏好。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active")
        pos_style = user.find(_STYLE_MARKER)
        pos_manifest = user.find(_MANIFEST_MARKER)
        pos_gen = user.find(_GEN_POINT)
        assert pos_manifest < pos_style < pos_gen, (
            f"第8轮重排被破坏：manifest={pos_manifest} style={pos_style} gen={pos_gen}")
        between = user[pos_style:pos_gen]
        for other in ["## manifest", "## 人物卡", "## 调研 cache", "## 用户偏好"]:
            assert other not in between, (
                f"风格 skill 与生成点之间不应再夹「{other}」段（破坏 RoPE 高位贴合）")


def test_no_content_dropped_active():
    """重排+下沉不丢内容：active 下所有核心块仍齐全。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active")
        for marker in [_STYLE_MARKER, _MANIFEST_MARKER, _STYLE_FP_MARKER, _PRIMACY_HEADING,
                       _GEN_POINT, "## 人物卡", "## 用户偏好", "## 调研 cache",
                       "## cluster_blueprint", "现在开始写。", "word_count_cjk"]:
            assert marker in user, f"active 缺失 {marker}"


def test_style_fp_block_not_duplicated_top_in_active():
    """下沉后量化指纹不应在顶部区重复出现（只在生成点近邻出现一次 · 避免重复稀释）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td))
        _system, user = _build(root, ctx_mode="active")
        # 量化指纹段标题在 active 下只出现一次
        assert user.count("作者量化风格指纹") == 1, "量化指纹段应只出现一次（下沉后不在顶部重复）"


def test_no_style_fp_no_block_zero_regression():
    """manifest 无 author_style_fingerprint（PROFILE_INJECT off/shadow）→ 不注入指纹段（零回归）。"""
    with tempfile.TemporaryDirectory() as td:
        root = _make_project(Path(td), with_style_fp=False)
        _system, user = _build(root, ctx_mode="active")
        assert "作者量化风格指纹" not in user
        assert _STYLE_FP_MARKER not in user
        # 风格 skill / manifest / primacy / 生成点仍齐全（不连带丢）
        for marker in [_STYLE_MARKER, _MANIFEST_MARKER, _PRIMACY_HEADING, _GEN_POINT]:
            assert marker in user


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print(f"  [OK] {_n}")
    print("primacy_stylefp tests all passed")
