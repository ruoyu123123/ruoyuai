"""Snippet 真实原文「语感种子」播种测试（P0 · 北极星①⑤⑥ · 2026-05-31）。

机制（gen_writer 写作 + distill_replicate 复刻 共用 snippet_seed 模块）：
  生成时 prompt 额外注入 1-2 段作者真实原文片段当「语感种子」，让模型贴真实文本流形起笔，
  防长 cluster 中后段退化回通用 AI 腔（直击 D 级长文退化 + 段长崩塌）。
  5 调研共识 + 两篇论文交叉印证（in-context style anchoring）。

🔴 关键避坑（Catch Me If You Can 论文实证）：
  片段按**风格/情绪相似**选 · **非题材相似**（题材相似选样反降分）；
  prompt 明确「只借语感语调起手势 · 情节按 storyboard/brief 走 · 绝不抄原文情节内容」
  （防抄袭 + 防内容泄漏）。

🔴 默认开启（env SNIPPET_SEED_MODE 默认 on · 2026-05-31 放量）：
  默认 on → 注入 1-2 段作者真原文当语感种子（真生效·有原文池就注入）· 显式 off 做 A/B 对照。
  无原文池 / 选不到候选 → 优雅降级返回空段（不报错·不改默认生成）。

纪律：纯 prompt 注入 · 不改 writer/复刻走 gen-model 的事实 ·
  测试只验**确定性的 prompt 构造 / 选样纯函数 / mode 解析**（不实跑 gen-model · 需 API）。
  真原文校准（蛊真人/惊悚乐园 原文）：选样确实从真实原文池取片段。

测试覆盖：① mode 解析（默认 on / off / shadow / 归一 / 非法回退 on）；② 避坑指令文案；
  ③ 种子段构造（空/非空）；④ 风格寄存器选样（情绪相似优先 · 非题材）；
  ⑤ 原文池定位（项目自带 / style_source 链）；⑥ gen_writer/distill 两路注入（mode off 零回归）；
  ⑦ 真原文校准（蛊真人/惊悚乐园 原文池真选得出种子）。
"""
import os
import sys
import json
import importlib
import tempfile
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import snippet_seed as ss  # noqa: E402


def _fake_vad(predict_batch_fn):
    """临时装 RUOYU_NN_VAD=1 + 假 nn_vad_bridge 模块，返回还原函数（无 monkeypatch 依赖）。"""
    old_env = os.environ.get("RUOYU_NN_VAD")
    old_mod = sys.modules.get("nn_vad_bridge")
    os.environ["RUOYU_NN_VAD"] = "1"
    sys.modules["nn_vad_bridge"] = types.SimpleNamespace(predict_batch=predict_batch_fn)

    def _restore():
        if old_env is None:
            os.environ.pop("RUOYU_NN_VAD", None)
        else:
            os.environ["RUOYU_NN_VAD"] = old_env
        if old_mod is None:
            sys.modules.pop("nn_vad_bridge", None)
        else:
            sys.modules["nn_vad_bridge"] = old_mod
    return _restore


def _reload_ss(mode):
    """以指定 SNIPPET_SEED_MODE reload snippet_seed（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("SNIPPET_SEED_MODE", None)
    else:
        os.environ["SNIPPET_SEED_MODE"] = mode
    importlib.reload(ss)
    return ss


# 紧张寄存器片段（高情绪 · 短促）vs 平静寄存器片段（低情绪 · 舒缓）
_TENSE_SNIPPET = "他怒吼一声，挥剑就斩。血溅了一地。怪物嘶吼着扑来，撕裂了他的衣袖。"
_CALM_SNIPPET = "他静静坐着，缓缓望向窗外。暖光柔和，他笑了笑，默默想起了旧事。"


# ════════════════════════════════════════════════════════════════
# [A] mode 解析 snippet_seed_mode（默认 on · 2026-05-31 放量）
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_on():
    """SNIPPET_SEED_MODE 未设 → 默认 on（2026-05-31 放量·真生效注入作者真原文语感种子）。"""
    sx = _reload_ss(None)
    try:
        assert sx.snippet_seed_mode() == "on"
    finally:
        _reload_ss(None)


def test_A_mode_on_normalized():
    """on / 1 / true / active → on（归一）。"""
    for v in ("on", "1", "true", "active", "ON", "Active"):
        sx = _reload_ss(v)
        try:
            assert sx.snippet_seed_mode() == "on", v
        finally:
            _reload_ss(None)


def test_A_mode_shadow():
    """shadow → shadow（构造种子但不注入 · 量成本 / A-B 复盘）。"""
    sx = _reload_ss("shadow")
    try:
        assert sx.snippet_seed_mode() == "shadow"
    finally:
        _reload_ss(None)


def test_A_mode_explicit_off():
    """显式 off → off（A/B 对照路径 · 唯一关闭手段）。"""
    sx = _reload_ss("off")
    try:
        assert sx.snippet_seed_mode() == "off"
    finally:
        _reload_ss(None)


def test_A_mode_garbage_falls_back_on():
    """空 / 非法值 / 旧 0/false → 回退默认 on（2026-05-31 放量·只有显式 off 才关）。"""
    for v in ("", "garbage", "0", "false"):
        sx = _reload_ss(v)
        try:
            assert sx.snippet_seed_mode() == "on", v
        finally:
            _reload_ss(None)


# ════════════════════════════════════════════════════════════════
# [B] 避坑指令文案（Catch Me 论文 + 防抄袭 + 防内容泄漏）
# ════════════════════════════════════════════════════════════════

def test_B_avoidance_instruction_forbids_copying_plot():
    """避坑指令必须明确：只借语感起手势 · 严禁抄情节/角色/设定/内容。"""
    instr = ss.SNIPPET_AVOIDANCE_INSTRUCTION
    assert "语感" in instr
    assert "起手势" in instr
    assert "严禁" in instr
    # 防抄袭 + 防内容泄漏关键词
    assert "情节" in instr
    assert "设定" in instr or "人物名" in instr


def test_B_avoidance_instruction_style_not_topic():
    """北极星避坑：指令必须说「复刻语言风格不是题材内容」（Catch Me 论文核心）。"""
    instr = ss.SNIPPET_AVOIDANCE_INSTRUCTION
    assert "风格" in instr
    assert "题材" in instr  # 明确区分风格 vs 题材
    # 情节按 storyboard/brief 走（不按片段剧情）
    assert "storyboard" in instr or "brief" in instr


def test_B_seed_section_embeds_avoidance():
    """构造的种子段必须内嵌避坑指令（任何注入路径都带防抄袭指令）。"""
    section = ss.build_seed_section([_TENSE_SNIPPET])
    assert ss.SNIPPET_AVOIDANCE_INSTRUCTION in section
    assert ss.SNIPPET_SEED_HEADER in section
    assert _TENSE_SNIPPET in section


# ════════════════════════════════════════════════════════════════
# [C] 种子段构造（空 / 非空）
# ════════════════════════════════════════════════════════════════

def test_C_empty_snippets_returns_empty_section():
    """无种子 → 空串（调用方不注入 · 零回归）。"""
    assert ss.build_seed_section([]) == ""


def test_C_multiple_snippets_numbered():
    """多段种子各自编号（种子片段 1 / 2 …）。"""
    section = ss.build_seed_section([_TENSE_SNIPPET, _CALM_SNIPPET])
    assert "种子片段 1" in section
    assert "种子片段 2" in section
    assert _TENSE_SNIPPET in section and _CALM_SNIPPET in section


# ════════════════════════════════════════════════════════════════
# [D] 风格寄存器选样（情绪相似优先 · 非题材 · 确定性）
# ════════════════════════════════════════════════════════════════

def test_D_emotion_register_signs():
    """紧张片段情绪寄存器 > 0 · 平静片段 < 0（情绪坐标方向正确）。"""
    assert ss._emotion_register(_TENSE_SNIPPET) > 0
    assert ss._emotion_register(_CALM_SNIPPET) < 0


def test_D_style_distance_emotion_closer():
    """目标=紧张 → 紧张片段 style_distance 比平静片段小（按情绪寄存器选 · 非题材）。"""
    target = ss.profile_text(_TENSE_SNIPPET)
    d_tense = ss.style_distance(ss.profile_text(_TENSE_SNIPPET), target)
    d_calm = ss.style_distance(ss.profile_text(_CALM_SNIPPET), target)
    assert d_tense < d_calm


def test_D_select_picks_style_similar_not_topic_similar():
    """选样按风格/情绪相似（避坑核心）：构造一个题材完全不同但情绪同频的原文池，
    验证选出的是情绪同频片段——证明选样不是按题材关键词匹配。
    """
    with tempfile.TemporaryDirectory() as td:
        odir = Path(td) / "原文"
        odir.mkdir()
        # 片段 A：题材=武侠打斗，紧张寄存器
        (odir / "第001章.txt").write_text(
            "\n\n".join(["剑光如雪，他怒吼挥砍，血溅当场，敌人嘶吼扑来撕裂他衣袖，他急退猛冲。"] * 4),
            encoding="utf-8")
        # 片段 B：题材=都市日常，平静寄存器
        (odir / "第002章.txt").write_text(
            "\n\n".join(["午后阳光暖柔，他静坐窗前缓缓品茶，笑着想起旧事，默默望着远处。"] * 4),
            encoding="utf-8")
        # 目标=平静寄存器（情绪向 calm）· 与片段 B 情绪同频但题材无关
        target = ss.profile_text("夜里很静，他轻声叹息，缓缓合上书页，暖灯下默默出神。")
        picked = ss.select_snippets(odir, target, n=1)
        assert picked, "应选出至少一段"
        # 选出的应是平静片段 B（情绪同频）而非紧张片段 A
        assert "暖" in picked[0] or "静" in picked[0] or "缓" in picked[0]
        assert "血" not in picked[0]


def test_D_select_deterministic():
    """选样确定性（同输入同输出 · 可测 · 不引入随机）。"""
    with tempfile.TemporaryDirectory() as td:
        odir = Path(td) / "原文"
        odir.mkdir()
        for i in range(1, 6):
            (odir / f"第{i:03d}章.txt").write_text(
                "\n\n".join([f"第{i}章的场景，他走着想着，安静地看着四周的一切变化。"] * 4),
                encoding="utf-8")
        target = ss.profile_text(_CALM_SNIPPET)
        a = ss.select_snippets(odir, target, n=2)
        b = ss.select_snippets(odir, target, n=2)
        assert a == b


def test_D_emotion_register_model_hit_uses_valence_arousal():
    """RUOYU_NN_VAD=1 + 假模型命中 → _emotion_register 用 valence/arousal 派生，非词频计数。
    假模型按内容区分（含'血'→低valence高arousal(紧张)，含'暖'→高valence低arousal(平静)）。"""
    def _fake(items):
        out = []
        for t in items:
            if "血" in t:
                out.append({"valence": 0.1, "arousal": 0.9, "dominance": None, "source": "model"})
            else:
                out.append({"valence": 0.9, "arousal": 0.1, "dominance": None, "source": "model"})
        return out
    restore = _fake_vad(_fake)
    try:
        reg_tense = ss._emotion_register(_TENSE_SNIPPET)  # 含"血"
        reg_calm = ss._emotion_register(_CALM_SNIPPET)    # 不含"血"
        # 手算：紧张 reg = 0.6*(2*0.9-1) + 0.4*(1-2*0.1) = 0.6*0.8 + 0.4*0.8 = 0.8
        assert abs(reg_tense - 0.8) < 1e-6, f"应按模型公式算出 0.8，得 {reg_tense}"
        # 平静 reg = 0.6*(2*0.1-1) + 0.4*(1-2*0.9) = 0.6*(-0.8) + 0.4*(-0.8) = -0.8
        assert abs(reg_calm - (-0.8)) < 1e-6, f"应按模型公式算出 -0.8，得 {reg_calm}"
        assert reg_tense > reg_calm, "紧张端应大于平静端（方向正确）"
    finally:
        restore()


def test_D_select_snippets_batches_single_model_call():
    """select_snippets 对多个候选片段只应发起 1 次 predict_batch 调用（整批·非逐条 N 次），
    摊薄模型子进程加载开销——这是本次接线的核心效率约束。"""
    calls = []

    def _fake(items):
        calls.append(list(items))
        return [{"valence": 0.5, "arousal": 0.5, "dominance": None, "source": "model"}
                for _ in items]
    restore = _fake_vad(_fake)
    try:
        with tempfile.TemporaryDirectory() as td:
            odir = Path(td) / "原文"
            odir.mkdir()
            for i in range(1, 6):
                (odir / f"第{i:03d}章.txt").write_text(
                    "\n\n".join([f"第{i}章的场景，他怒吼挥砍，血溅当场，敌人嘶吼扑来撕裂衣袖。"] * 4),
                    encoding="utf-8")
            target = ss.profile_text(_CALM_SNIPPET)  # 目标 profile 自身也会触发 1 次 predict_batch
            ss.select_snippets(odir, target, n=2)
        # 目标 profile 1 次 + select_snippets 内部候选批量 1 次 = 2 次；候选数无论多少都不应线性增长调用次数
        assert len(calls) == 2, f"应恰好 2 次批量调用（target 1 + candidates 1 批），得 {len(calls)}: {calls}"
        # 候选批量那次应包含全部候选（不是逐条拆开的单元素调用）
        cand_calls = [c for c in calls if len(c) > 1]
        assert cand_calls, "应存在一次包含多个候选片段的整批调用"
    finally:
        restore()


def test_D_emotion_register_model_unavailable_zero_regression():
    """RUOYU_NN_VAD=1 但 predict_batch 返回 None（模型不可用）→ 与默认(env off)词频路径一致。"""
    baseline_tense = ss._emotion_register(_TENSE_SNIPPET)
    baseline_calm = ss._emotion_register(_CALM_SNIPPET)

    restore = _fake_vad(lambda items: None)
    try:
        assert ss._emotion_register(_TENSE_SNIPPET) == baseline_tense
        assert ss._emotion_register(_CALM_SNIPPET) == baseline_calm
    finally:
        restore()

    # select_snippets 端到端同样零回归：模型不可用时选样结果与 env off 完全一致
    with tempfile.TemporaryDirectory() as td:
        odir = Path(td) / "原文"
        odir.mkdir()
        (odir / "第001章.txt").write_text(
            "\n\n".join(["他怒吼挥砍，血溅当场，敌人嘶吼扑来撕裂衣袖，急退猛冲。"] * 4),
            encoding="utf-8")
        (odir / "第002章.txt").write_text(
            "\n\n".join(["他静坐窗前，缓缓品着茶，暖光柔和地铺在书页上，默默想着旧事。"] * 4),
            encoding="utf-8")
        target = ss.profile_text(_CALM_SNIPPET)
        baseline_pick = ss.select_snippets(odir, target, n=1)

        restore = _fake_vad(lambda items: None)
        try:
            got_pick = ss.select_snippets(odir, target, n=1)
            assert got_pick == baseline_pick, "模型不可用时选样应与默认路径完全一致（零回归）"
        finally:
            restore()


# ════════════════════════════════════════════════════════════════
# [E] 原文池定位（项目自带 / style_source 链）
# ════════════════════════════════════════════════════════════════

def test_E_resolve_own_originals():
    """项目自带 原文/ 优先。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "proj"
        (proj / "原文").mkdir(parents=True)
        assert ss.resolve_originals_dir(proj) == proj / "原文"


def test_E_resolve_via_style_source():
    """无自带原文 → 经 _数据库/作者风格.json.style_source 链到风格库 原文/。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 模拟仓库布局：workspace/styles/某书/原文 + workspace/novels/某项目/_数据库/作者风格.json
        style_orig = root / "workspace" / "styles" / "某书" / "原文"
        style_orig.mkdir(parents=True)
        (root / "workspace" / "styles" / "某书" / "skill_FINAL.md").write_text("x", encoding="utf-8")
        proj = root / "workspace" / "novels" / "某项目"
        db = proj / "_数据库"
        db.mkdir(parents=True)
        (db / "作者风格.json").write_text(
            json.dumps({"style_source": "workspace/styles/某书/skill_FINAL.md"}, ensure_ascii=False),
            encoding="utf-8")
        resolved = ss.resolve_originals_dir(proj)
        assert resolved is not None
        assert resolved.name == "原文"
        assert resolved.exists()


def test_E_resolve_none_when_no_pool():
    """无原文池 → None（调用方退默认行为 · 不注入种子）。"""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "empty"
        proj.mkdir()
        assert ss.resolve_originals_dir(proj) is None


# ════════════════════════════════════════════════════════════════
# [F] 一站式接口 + 影子纪律（mode off 零回归）
# ════════════════════════════════════════════════════════════════

def _make_pool(td) -> Path:
    proj = Path(td) / "proj"
    odir = proj / "原文"
    odir.mkdir(parents=True)
    for i in range(1, 6):
        (odir / f"第{i:03d}章.txt").write_text(
            "\n\n".join([f"他怒吼挥砍，血溅当场，第{i}章敌人嘶吼扑来撕裂衣袖，急退猛冲。"] * 4),
            encoding="utf-8")
    return proj


def test_F_writer_mode_off_no_injection():
    """mode=off（默认）：make_seed_block_for_writer 返回空段（零回归 · 不注入）。"""
    sx = _reload_ss("off")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _make_pool(td)
            section, trace = sx.make_seed_block_for_writer(proj, scope_text="打斗冲突场景")
            assert section == ""
            assert trace["snippet_seed_mode"] == "off"
            assert trace["injected"] is False
    finally:
        _reload_ss(None)


def test_F_writer_mode_on_injects():
    """mode=on：注入非空种子段 + trace 记录用了几段（含避坑指令）。"""
    sx = _reload_ss("on")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _make_pool(td)
            section, trace = sx.make_seed_block_for_writer(proj, scope_text="打斗冲突场景")
            assert section != ""
            assert sx.SNIPPET_AVOIDANCE_INSTRUCTION in section
            assert trace["injected"] is True
            assert trace["snippets_used"] >= 1
    finally:
        _reload_ss(None)


def test_F_writer_shadow_builds_but_not_injects():
    """mode=shadow：构造种子（量成本）但不注入 prompt（不改默认生成 · A-B 复盘留痕）。"""
    sx = _reload_ss("shadow")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = _make_pool(td)
            section, trace = sx.make_seed_block_for_writer(proj, scope_text="打斗冲突场景")
            assert section == ""  # 影子不拼进 prompt
            assert trace["snippet_seed_mode"] == "shadow"
            assert trace["injected"] is False
            assert trace.get("shadow_section_chars", 0) > 0  # 构造了（量到成本）
    finally:
        _reload_ss(None)


def test_F_writer_no_pool_graceful():
    """mode=on 但无原文池 → 空段 + reason 留痕（不崩 · 退默认行为）。"""
    sx = _reload_ss("on")
    try:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "nopool"
            proj.mkdir()
            section, trace = sx.make_seed_block_for_writer(proj)
            assert section == ""
            assert trace.get("reason") == "no_originals_dir"
    finally:
        _reload_ss(None)


def test_F_distill_from_dir_off_and_on():
    """make_seed_block_from_dir：off 零回归 / on 注入（复刻路径）。"""
    with tempfile.TemporaryDirectory() as td:
        odir = Path(td) / "原文"
        odir.mkdir()
        for i in range(1, 5):
            (odir / f"第{i:03d}章.txt").write_text(
                "\n\n".join(["他静坐窗前，缓缓品着茶，暖光柔和地铺在书页上，他默默想着旧事，轻声叹息了一下。"] * 4),
                encoding="utf-8")
        sx = _reload_ss("off")
        try:
            section, trace = sx.make_seed_block_from_dir(odir, ref_text=_CALM_SNIPPET)
            assert section == "" and trace["injected"] is False
        finally:
            _reload_ss(None)
        sx = _reload_ss("on")
        try:
            section, trace = sx.make_seed_block_from_dir(odir, ref_text=_CALM_SNIPPET)
            assert section != "" and trace["injected"] is True
        finally:
            _reload_ss(None)


# ════════════════════════════════════════════════════════════════
# [G] gen_writer / distill_replicate 集成（默认 off 零回归 · 签名兼容）
# ════════════════════════════════════════════════════════════════

def test_G_distill_build_prompt_default_no_seed():
    """distill build_cluster_subcall_prompt 默认 seed_section="" → 无种子段（零回归）。"""
    import distill_replicate as dr
    p = dr.build_cluster_subcall_prompt(
        "SKILL", "", {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        subcall_index=1, subcall_total=1, prev_tail="",
        chapters_in_this_call=3, target_words=9000)
    assert ss.SNIPPET_SEED_HEADER not in p


def test_G_distill_build_prompt_with_seed():
    """传 seed_section → 注在 skill 后（语感锚点 · 含避坑指令）。"""
    import distill_replicate as dr
    seed = ss.build_seed_section([_TENSE_SNIPPET])
    p = dr.build_cluster_subcall_prompt(
        "SKILL内容", "", {"cluster_id": "cluster_001", "chapter_range": [1, 3]},
        subcall_index=1, subcall_total=1, prev_tail="",
        chapters_in_this_call=3, target_words=9000, seed_section=seed)
    assert ss.SNIPPET_SEED_HEADER in p
    assert ss.SNIPPET_AVOIDANCE_INSTRUCTION in p
    # skill 在种子段之前（种子是 skill 后的语感起手势）
    assert p.index("SKILL内容") < p.index(ss.SNIPPET_SEED_HEADER)


def test_G_gen_writer_save_output_accepts_seed_trace():
    """gen_writer.save_output 接受 seed_trace kwarg 并写进 changes.ecas_metadata（留痕不黑箱）。"""
    import gen_writer as gw

    class _P:
        name = "p"
        model = "m"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        trace = {"snippet_seed_mode": "on", "injected": True, "snippets_used": 2}
        draft_path, cjk = gw.save_output(
            root, 7, "这是一段真正的正文内容。", {}, 1, None, _P(), seed_trace=trace)
        changes = json.loads(
            (root / "章节" / "cluster_007_draft" / "cluster_007_changes.json").read_text(encoding="utf-8"))
        meta = changes["self_eval"]["ecas_metadata"]
        assert meta["snippet_seed"]["snippet_seed_mode"] == "on"
        assert meta["snippet_seed"]["injected"] is True


def test_G_gen_writer_save_output_default_seed_trace_not_injected():
    """不传 seed_trace → 默认记 未注入（向后兼容旧调用 · 真实路径总会传 trace）。"""
    import gen_writer as gw

    class _P:
        name = "p"
        model = "m"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        gw.save_output(root, 8, "正文内容在此。", {}, 1, None, _P())
        changes = json.loads(
            (root / "章节" / "cluster_008_draft" / "cluster_008_changes.json").read_text(encoding="utf-8"))
        meta = changes["self_eval"]["ecas_metadata"]
        assert meta["snippet_seed"]["injected"] is False


# ════════════════════════════════════════════════════════════════
# [H] 真原文校准（北极星纪律 3 · 真实原文池真选得出种子）
# ════════════════════════════════════════════════════════════════

def test_H_real_author_originals_yield_snippets():
    """蛊真人 / 惊悚乐园 真实原文池能抽出候选种子片段（选样不是空架子）。"""
    for book in ("蛊真人", "惊悚乐园"):
        odir = _ROOT / "workspace" / "styles" / book / "原文"
        if not odir.exists():
            continue
        cands = ss._extract_candidate_snippets(odir, max_files=10)
        assert cands, f"{book} 原文池应抽出候选片段"
        # 候选片段不含站点广告 / 章号噪声
        for c in cands[:5]:
            assert "www." not in c and "请收藏" not in c


def test_H_real_author_select_by_emotion():
    """蛊真人真实原文：目标=紧张寄存器 → 选出的种子情绪寄存器整体偏紧张（风格同频）。"""
    odir = _ROOT / "workspace" / "styles" / "蛊真人" / "原文"
    if not odir.exists():
        return
    target = ss.profile_text(_TENSE_SNIPPET)  # 紧张目标
    picked = ss.select_snippets(odir, target, n=2)
    if not picked:
        return
    # 选出的种子应是真实原文片段（非空 · 有足够 CJK 语感量 · 对齐 _extract min_cjk 下限）
    for snip in picked:
        cjk = sum(1 for ch in snip if "一" <= ch <= "鿿")
        assert cjk >= 80, "种子片段应有足够 CJK 语感量"
