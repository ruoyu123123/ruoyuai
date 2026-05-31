"""av_judge.py 测试 — AV-judge 解耦特质·作者验证配对判别（北极星①⑤⑥ · 2026-05-31）。

根因（本批任务说明 · 实证）：治「SFS 数值过了但读着不像」——SFS 是统计指纹（句长/虚词分布对齐
≠ 读者感知同作者），黑箱 LLM-judge 给整体分不解释哪维露馅。AV-judge 站中间：读者视角 + 解耦
4 维（词汇选择/句法/话语连接词/语用语气）+ 配对判别（A 作者锚 / B 仿写验，指证 B 哪维走味）。

必 shadow 上线（Catch Me If You GAN / Are We There Yet 实证：LLM-judge 对网文隐性风格会失准，
创意写作域约 1/4 难例翻转）：env AV_JUDGE_MODE 默认 off（零回归），shadow 只记录、active 仍 advisory。
绝不 hard_gate（AV_TRAIT_DRIFT 不进 audit_hub.HARD_GATE_CODES）。

纪律：只测**确定性的 prompt 构造 / rubric / 配对结构 / mode 解析 / 解析报告**，
  不实跑 gen-model（需 API · gen-model 调用复用 gen_model_loader 同款 fallback 管线）。
  真作者原文（蛊真人/惊悚乐园·「蛊」非「蛛」）当样本验证 prompt 正常构建（北极星纪律 3 金标准）。

测试覆盖：[A] mode 解析（默认 off / shadow / active / 非法回退）；[B] 4 维 rubric（全列 + 读者视角
  + 配对问法）；[C] 配对结构（A 锚在 B 前 + 锚定方向 + 不比内容）；[D] 输出 JSON 契约（4 维 verdict）；
  [E] 解析 verdict → advisory（走味/命中/宽容缺维/非走味保守）；[F] 报告分级（shadow 只记录 /
  active 上报 / 永远 advisory 不 hard_gate）；[G] 真作者原文金标准。
"""
import importlib
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "core" / "scripts"))
import av_judge as av  # noqa: E402


def _reload_av(mode):
    """以指定 AV_JUDGE_MODE reload av_judge（env 在函数内读 · reload 求稳）。"""
    if mode is None:
        os.environ.pop("AV_JUDGE_MODE", None)
    else:
        os.environ["AV_JUDGE_MODE"] = mode
    importlib.reload(av)
    return av


_AUTHOR = "他停下脚步。\n风很大。\n远处的灯一盏盏灭了，像有人在数着退场。\n他没回头。\n" * 8
_REPLICA = "她缓缓地停下了脚步，心中涌起一丝难以言喻的复杂情绪。\n然而，风很大。\n" * 8

# 4 维名（与 av.AV_TRAIT_DIMS 单一来源对齐）
_DIM_NAMES = ["词汇选择", "句法", "话语连接词", "语用语气"]


# ════════════════════════════════════════════════════════════════
# [A] mode 解析 _av_judge_mode
# ════════════════════════════════════════════════════════════════

def test_A_mode_default_active():
    """AV_JUDGE_MODE 未设 → 默认 active（2026-05-31 放量 · 走味维度作 advisory 上报 ·
    LLM-judge 失准故必 advisory + 报告标注人工复核 · code 绝不进 HARD_GATE_CODES）。"""
    ax = _reload_av(None)
    try:
        assert ax._av_judge_mode() == "active"
    finally:
        _reload_av(None)


def test_A_mode_shadow_and_active():
    """shadow / active / off 各自识别（大小写不敏感）。"""
    for v in ("shadow", "SHADOW", "Shadow"):
        try:
            assert _reload_av(v)._av_judge_mode() == "shadow", v
        finally:
            _reload_av(None)
    for v in ("active", "ACTIVE"):
        try:
            assert _reload_av(v)._av_judge_mode() == "active", v
        finally:
            _reload_av(None)
    for v in ("off", "OFF"):
        try:
            assert _reload_av(v)._av_judge_mode() == "off", v
        finally:
            _reload_av(None)


def test_A_mode_garbage_falls_back_active():
    """空 / 非法值回退 active（放量默认）· 只有显式 off 才关掉（离线/无 gen-model 逃生口）。"""
    for v in ("", "on", "1", "true", "garbage"):
        try:
            assert _reload_av(v)._av_judge_mode() == "active", v
        finally:
            _reload_av(None)


# ════════════════════════════════════════════════════════════════
# [B] 4 维解耦 rubric AV_TRAIT_DIMS + prompt 全列
# ════════════════════════════════════════════════════════════════

def test_B_four_decoupled_dims_exact():
    """4 维解耦特质单一来源 = 词汇选择/句法/话语连接词/语用语气（authorship verification 拆解）。"""
    names = [n for n, _d, _a in av.AV_TRAIT_DIMS]
    assert names == _DIM_NAMES, names


def test_B_each_dim_has_desc_and_pairwise_ask():
    """每维带读者视角说明 + 配对判别问法（解耦 · 不混成总分）。"""
    for name, desc, ask in av.AV_TRAIT_DIMS:
        assert desc and ask, name
        # 配对问法以 A 为锚（问法里出现「A」锚定）
        assert "A" in ask, (name, ask)


def test_B_prompt_lists_all_four_dims():
    """prompt 逐维列出 4 个解耦维度（每维一个标题块）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    for i, name in enumerate(_DIM_NAMES, 1):
        assert f"维度 {i} · {name}" in p, name


def test_B_prompt_is_reader_perspective():
    """读者视角（不是查统计指标 · 凭语感判是不是同一个人）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert "读者" in p
    # system prompt 也声明读者视角
    assert "读者" in av.AV_JUDGE_SYSTEM_PROMPT


# ════════════════════════════════════════════════════════════════
# [C] 配对判别结构（A 锚 → B 验 · 方向固定 · 不比内容）
# ════════════════════════════════════════════════════════════════

def test_C_pairwise_author_anchor_before_replica():
    """配对结构：A=作者真迹（锚）呈现在 B=仿写（待验）之前（锚定方向固定）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert "A 段 · 作者真迹" in p and "B 段 · 仿写" in p
    assert p.index("A 段 · 作者真迹") < p.index("B 段 · 仿写")


def test_C_pairwise_anchor_semantics():
    """明确「以 A 为锚做相对判别」（配对相对判别 > 绝对打分 · 任务核心）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert "锚" in p and "配对" in p
    # system prompt 声明「不是给 B 打一个总分」而是配对判别
    assert "总分" in av.AV_JUDGE_SYSTEM_PROMPT and "配对" in av.AV_JUDGE_SYSTEM_PROMPT


def test_C_not_compare_content():
    """只比写法风格 · 不比内容（A/B 人物情节不同是正常 · 防误判）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert "不比内容" in p
    assert "不比" in av.AV_JUDGE_SYSTEM_PROMPT


def test_C_both_texts_injected():
    """A / B 两段文本都注入 prompt（配对判别两段都要在场）。"""
    p = av.build_av_judge_prompt("作者锚文本ABC", "仿写文本XYZ")
    assert "作者锚文本ABC" in p
    assert "仿写文本XYZ" in p


def test_C_sample_limit_trims():
    """sample_limit 裁长文（防 prompt 过长 · 截断留痕）。"""
    long_a = "甲" * 5000
    p = av.build_av_judge_prompt(long_a, "乙乙乙", sample_limit=100)
    assert "截断于 100 字" in p
    assert "甲" * 5000 not in p


# ════════════════════════════════════════════════════════════════
# [D] 输出 JSON 契约（4 维 verdict 命中/走味）
# ════════════════════════════════════════════════════════════════

def test_D_output_json_has_all_dims_verdict():
    """输出 JSON 契约：4 维各要 verdict（命中/走味 二选一）+ reason。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert '"dimensions"' in p
    for name in _DIM_NAMES:
        assert f'"{name}"' in p
    assert '"verdict"' in p and '"reason"' in p
    # verdict 取值是命中/走味（不是 1-10 绝对打分 · 配对定性判别）
    assert av.MATCH_VERDICT in p and av.DRIFT_VERDICT in p


def test_D_drift_must_cite():
    """判走味必须指证 B 哪处露馅（不空判 · 可定位回改）。"""
    p = av.build_av_judge_prompt(_AUTHOR, _REPLICA)
    assert "指证" in p or "露馅" in p


# ════════════════════════════════════════════════════════════════
# [E] 解析 verdict → 4 维 advisory parse_av_verdicts
# ════════════════════════════════════════════════════════════════

def _full_reply(verdicts):
    """构造一份 LLM 回复 JSON（verdicts: dim→verdict 字符串）。"""
    import json
    dims = {n: {"verdict": verdicts.get(n, av.MATCH_VERDICT), "reason": f"{n}理由"}
            for n in _DIM_NAMES}
    return "```json\n" + json.dumps({"dimensions": dims}, ensure_ascii=False) + "\n```"


def test_E_parse_drift_dims():
    """解析出走味维度（verdict 含「走味」→ drift=True · 进 drift_dims）。"""
    reply = _full_reply({"词汇选择": av.DRIFT_VERDICT, "句法": av.MATCH_VERDICT,
                         "话语连接词": av.DRIFT_VERDICT, "语用语气": av.MATCH_VERDICT})
    out = av.parse_av_verdicts(reply)
    assert out["parse_ok"] is True
    assert set(out["drift_dims"]) == {"词汇选择", "话语连接词"}
    assert out["dimensions"]["词汇选择"]["drift"] is True
    assert out["dimensions"]["句法"]["drift"] is False


def test_E_parse_all_match():
    """全命中 → drift_dims 空（读者认得出是作者 · 不误报）。"""
    reply = _full_reply({n: av.MATCH_VERDICT for n in _DIM_NAMES})
    out = av.parse_av_verdicts(reply)
    assert out["drift_dims"] == []


def test_E_parse_missing_dim_conservative():
    """LLM 漏某维 → 该维 verdict=None / drift=False（不臆造走味 · 保守不误伤）。"""
    import json
    reply = json.dumps({"dimensions": {"词汇选择": {"verdict": av.DRIFT_VERDICT}}},
                       ensure_ascii=False)
    out = av.parse_av_verdicts(reply)
    assert out["dimensions"]["句法"]["verdict"] is None
    assert out["dimensions"]["句法"]["drift"] is False
    assert out["drift_dims"] == ["词汇选择"]


def test_E_parse_non_drift_verdict_not_flagged():
    """非「走味」字样的 verdict 一律当未走味（保守 · 只有明确判走味才标记）。"""
    reply = _full_reply({"句法": "基本像", "词汇选择": av.MATCH_VERDICT,
                         "话语连接词": av.MATCH_VERDICT, "语用语气": av.MATCH_VERDICT})
    out = av.parse_av_verdicts(reply)
    assert out["dimensions"]["句法"]["drift"] is False
    assert out["drift_dims"] == []


def test_E_parse_garbage_no_crash():
    """坏输入（非 JSON / 空）不抛错 · parse_ok=False · 4 维齐全。"""
    for bad in ("", "not json at all", "{broken"):
        out = av.parse_av_verdicts(bad)
        assert out["parse_ok"] is False
        assert set(out["dimensions"]) == set(_DIM_NAMES)
        assert out["drift_dims"] == []


def test_E_extract_json_tolerates_wrapping():
    """_extract_json 宽容 ```json 围栏 + 尾部多余文本。"""
    raw = "好的，分析如下：\n```json\n{\"dimensions\": {\"句法\": {\"verdict\": \"命中\"}}}\n```\n以上。"
    d = av._extract_json(raw)
    assert d["dimensions"]["句法"]["verdict"] == "命中"


# ════════════════════════════════════════════════════════════════
# [F] 报告分级（shadow 只记录 / active 上报 / 永远 advisory 不 hard_gate）
# ════════════════════════════════════════════════════════════════

def test_F_report_always_advisory():
    """报告 gate_level 永远 advisory · code AV_TRAIT_DRIFT（北极星⑤ · 绝不 hard_gate）。"""
    parsed = av.parse_av_verdicts(_full_reply({"词汇选择": av.DRIFT_VERDICT}))
    for mode in ("shadow", "active"):
        r = av.build_report(mode, parsed, "a.txt", "b.txt")
        assert r["gate_level"] == "advisory"
        assert r["issue_code"] == "AV_TRAIT_DRIFT"


def test_F_shadow_records_only_no_verdict():
    """shadow：只记录 · 顶层 verdict=None · 不上报 issues（不进 audit_hub · 先校准）。"""
    parsed = av.parse_av_verdicts(_full_reply({"词汇选择": av.DRIFT_VERDICT}))
    r = av.build_report("shadow", parsed, "a.txt", "b.txt")
    assert r["shadow"] is True
    assert r["verdict"] is None
    assert r["issues"] == []
    # 维度判别仍在报告里（只是不上报）
    assert r["dimensions"]["词汇选择"]["drift"] is True


def test_F_active_reports_drift_as_advisory_issues():
    """active：每个走味维度作 advisory 待裁决项上报（仍 advisory · 仍可豁免）。"""
    parsed = av.parse_av_verdicts(_full_reply(
        {"词汇选择": av.DRIFT_VERDICT, "句法": av.DRIFT_VERDICT}))
    r = av.build_report("active", parsed, "a.txt", "b.txt")
    assert r["shadow"] is False
    assert r["verdict"] == "drift"
    assert len(r["issues"]) == 2
    for iss in r["issues"]:
        assert iss["code"] == "AV_TRAIT_DRIFT"
        assert iss["gate_level"] == "advisory"
        assert iss["dimension"] in ("词汇选择", "句法")


def test_F_active_all_match_verdict_match_no_issues():
    """active 全命中 → verdict=match · issues 空（不误报）。"""
    parsed = av.parse_av_verdicts(_full_reply({n: av.MATCH_VERDICT for n in _DIM_NAMES}))
    r = av.build_report("active", parsed, "a.txt", "b.txt")
    assert r["verdict"] == "match"
    assert r["issues"] == []


def test_F_error_report_advisory_no_crash():
    """gen-model 失败 → error 报告仍 advisory · verdict=None（advisory 不阻断流水线）。"""
    r = av.build_report("shadow", None, "a.txt", "b.txt", error="gen-model 全挂")
    assert r["gate_level"] == "advisory"
    assert r["error"] == "gen-model 全挂"
    assert r["verdict"] is None


def test_F_code_not_in_hard_gate():
    """AV_TRAIT_DRIFT 绝不进 audit_hub.HARD_GATE_CODES（北极星⑤ · 权威边界单一来源）。"""
    try:
        import audit_hub  # noqa: E402
    except Exception:
        return  # 无 audit_hub 则跳过
    codes = getattr(audit_hub, "HARD_GATE_CODES", set())
    assert av.ISSUE_CODE not in codes, "AV-judge 永远 advisory · 不可进 hard_gate 清单"


# ════════════════════════════════════════════════════════════════
# [G] 真作者原文金标准（北极星纪律 3 · 蛊真人/惊悚乐园 · 「蛊」非「蛛」）
# ════════════════════════════════════════════════════════════════

def _load_real(book, ch):
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def test_G_real_author_prompt_builds():
    """蛊真人/惊悚乐园真原文喂 AV-judge prompt → 正常构建（A 锚 + 4 维 + 配对结构）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_real(book, "第010章")
        b = _load_real(book, "第020章")
        if a is None or b is None:
            continue
        found = True
        p = av.build_av_judge_prompt(a, b)
        assert "A 段 · 作者真迹" in p and "B 段 · 仿写" in p
        for i, name in enumerate(_DIM_NAMES, 1):
            assert f"维度 {i} · {name}" in p
        # 真原文片段进 prompt（前若干字 · 避免截断差异）
        assert a[:30] in p
    assert found or _load_real("蛊真人", "第010章") is None
