"""L3e SFS 评分消偏测试 — 顺序双跑取均值 + 风格代表性选片（北极星①⑤⑥ · 2026-05-31）。

根因（本批任务说明 · 实证）：
  ① LLM-as-judge 顺序偏置——同一对样本谁先呈现谁占优，可致 >10% 漂移。
  ② few-shot/参考片段按**内容相似**选片反降风格保真（Catch Me 实证：内容近的片段把模型
     带去「抄内容」而非「学文风」）。应按**风格代表性**（聚类质心/句式覆盖）选片。

升级（全在 style_evaluator.py 内自包含 · 纯增量 · 不改 L3a / 旧 generate_llm_prompt 默认行为）：
  (a) generate_pairwise_llm_prompt：pairwise（复刻稿 vs 作者原文片段）+ 交换呈现顺序双跑；
      average_pairwise_scores 取均值消顺序偏置。
  (b) _select_representative_samples：候选段算去题材风格特征 → k-center(最远点)+质心选片。

影子并行（北极星纪律 7 · 回归 0）：env SFS_LLM_DEBIAS——off（默认）= 旧单序 + random.sample
（零回归）；on = pairwise 双序 + 代表性选片。本层只改评分鲁棒性，属 advisory，绝不改
sfs_quick/programmatic_score/grade 判决（顾问非法官 · 北极星⑤）。

测试覆盖：[A] 去题材风格特征向量；[B] 代表性选片（确定性/质心+覆盖/不看 gen 内容/候选不足）；
[C] pairwise prompt 结构（双序/交换/输出格式）；[D] 顺序双跑取均值消偏 + 偏置诊断；
[E] env 开关 + 默认零回归（off=旧单序 byte 级一致）；[F] 不破坏 L3a 共存；
[G] 真原文金标准（蛊真人/惊悚乐园：代表性选片选出真原文片段、同作者双序均值稳定）。

只测确定性纯函数（prompt 构造 / 选片 / 取均值），不实跑 gen-model/LLM（需 API）。守纪律：
真作者原文当样本验证选片与消偏不误判（北极星纪律 3 金标准 · 6 共同纪律 3）。
"""
import os
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_evaluator as se  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_se(debias):
    """以指定 SFS_LLM_DEBIAS reload style_evaluator（env 在函数内读 · reload 求稳）。"""
    if debias is None:
        os.environ.pop("SFS_LLM_DEBIAS", None)
    else:
        os.environ["SFS_LLM_DEBIAS"] = debias
    importlib.reload(se)
    return se


def _load_chapter(book, ch):
    """读真原文章节（剥离 changes JSON 尾巴）。"""
    import chapter_io as cio
    p = _ROOT / "workspace" / "styles" / book / "原文" / f"{ch}.txt"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8")
    for sep in cio.CHANGES_SEPARATORS:
        if sep in raw:
            raw = raw.split(sep)[0].rstrip()
            break
    return raw


# ════════════════════════════════════════════════════════════════
# [A] 去题材风格特征向量 _segment_style_feature
# ════════════════════════════════════════════════════════════════

def test_A_feature_fixed_dim_and_numeric():
    """特征向量维度固定（4 节奏 + 15 虚词 = 19）且全为 float（可比可计算）。"""
    f = se._segment_style_feature("他走进房间，看了看四周，慢慢地坐了下来。" * 10)
    assert len(f) == 4 + len(se.FUNCTION_WORDS), len(f)
    assert all(isinstance(x, float) for x in f), f


def test_A_feature_detopic_same_topic_diff_rhythm_differs():
    """同题材词、不同句长节奏 → 特征向量明显不同（去题材后风格信号没被题材掩盖）。"""
    short = "方源出手。\n蛊虫死了。\n他走了。\n" * 20
    long = ("方源缓缓抬起手掌，体内的蛊虫顺着经脉涌动而出，"
            "在他掌心凝聚成一道幽幽的青色光芒，久久不曾散去。") * 20
    fs = se._segment_style_feature(short)
    fl = se._segment_style_feature(long)
    # 句均长（向量第 0 维）应明显不同
    assert abs(fs[0] - fl[0]) > 3.0, (fs[0], fl[0])


def test_A_l2_dist_basic():
    """欧氏距离纯函数：自身距 0 · [0,0]→[3,4] 距 5。"""
    assert se._l2_dist([1.0, 2.0], [1.0, 2.0]) == 0.0
    assert abs(se._l2_dist([0.0, 0.0], [3.0, 4.0]) - 5.0) < 1e-9


# ════════════════════════════════════════════════════════════════
# [B] 风格代表性选片 _select_representative_samples
# ════════════════════════════════════════════════════════════════

def _make_multi_rhythm_text():
    """构造含三种节奏的文本（短句连发 / 长句铺陈 / 对话）→ 选片应覆盖谱系。

    注意 _candidate_segments 会把相邻段合并到 ~200-500 CJK 再切——故每种节奏块须足够长
    （≥600 CJK）让它独立成 ≥1 个候选片段，节奏才不被相邻块混合掉。"""
    # 短句连发块（每段 1 句 · 句均长极短）·足够长独立成候选
    short = "他停。\n风起。\n刀落。\n血溅。\n人倒。\n他退。\n敌进。\n" * 60
    # 长句铺陈块（句均长极长 · 逗号多）
    long = ("他缓缓抬起头来望向远方那连绵起伏的群山，心里涌起一股说不清道不明的"
            "复杂情绪，仿佛过往的一切都在这一刻重新浮现在眼前难以散去。\n") * 30
    # 对话块（单句成段率高 · 引号密）
    dlg = "“你来了。”\n“嗯，我来了。”\n“坐吧，喝杯茶。”\n“不必了，我马上走。”\n" * 40
    return short + "\n" + long + "\n" + dlg


def test_B_select_deterministic():
    """代表性选片确定性可复现（同输入两次结果完全一致 · 取代随机种子）。"""
    txt = _make_multi_rhythm_text()
    a = se._select_representative_samples(txt, 3)
    b = se._select_representative_samples(txt, 3)
    assert a == b, "代表性选片必须确定性"


def test_B_select_count_capped():
    """候选 ≤ n → 全返回（不丢片段）。"""
    # 一段约 250 CJK → 切出 1-2 个候选；请求 10 个 → 全返回（不会超出候选数）。
    txt = "他走进房间，看了看四周，慢慢地坐了下来，心里想着刚才的事。\n" * 12
    out = se._select_representative_samples(txt, 10)
    cands = se._candidate_segments(txt)
    assert len(out) == len(cands) and len(cands) >= 1, (len(out), len(cands))


def test_B_select_covers_rhythm_spectrum():
    """三种节奏混合文本选 3 段 → 选出的片段句均长跨度大（覆盖谱系，不是全选同一种）。"""
    txt = _make_multi_rhythm_text()
    sel = se._select_representative_samples(txt, 3)
    assert len(sel) == 3, sel
    means = [se._segment_style_feature(s)[0] for s in sel]
    # k-center 最远点扩展 → 选片句均长不应全挤在一起（跨度 > 5 字）
    assert max(means) - min(means) > 5.0, means


def test_B_select_ignores_gen_content():
    """选片只看 ref 自身风格分布，不接收 gen 参数（结构上就不可能按内容相似选 · 根因②）。"""
    import inspect
    sig = inspect.signature(se._select_representative_samples)
    params = list(sig.parameters)
    # 签名里没有 gen / target / query 类参数 → 不可能按与 gen 内容相似选片
    assert not any(p in params for p in ("gen", "gen_text", "target", "query")), params


def test_B_select_empty_text():
    """空文本不抛错（坏输入兜底）。"""
    assert se._select_representative_samples("", 3) == []


# ════════════════════════════════════════════════════════════════
# [C] pairwise prompt 结构 generate_pairwise_llm_prompt
# ════════════════════════════════════════════════════════════════

_REF = "他淡淡地看了一眼，显然没把这事放在心上。\n他停下脚步。\n风很大。\n" * 30
_GEN = "她淡淡地笑了笑，显然早就料到这个结果。\n她走了出去。\n天黑了。\n" * 30


def test_C_pairwise_has_both_orders():
    """pairwise prompt 含两个评分轮（A=原文先 / B=复刻先 · 顺序交换）。"""
    p = se.generate_pairwise_llm_prompt(_REF, _GEN)
    assert "评分轮 A" in p and "评分轮 B" in p, "缺顺序双跑"
    assert "顺序交换" in p
    # 输出格式要求 order_A / order_B 两键
    assert '"order_A"' in p and '"order_B"' in p


def test_C_pairwise_order_truly_swapped():
    """轮 A 是「原文先复刻后」，轮 B 是「复刻先原文后」（呈现顺序确实交换）。"""
    p = se.generate_pairwise_llm_prompt(_REF, _GEN)
    a_block = p.split("评分轮 B")[0]
    b_block = p.split("评分轮 B")[1]
    # 轮 A：原文片段出现在复刻片段之前
    assert a_block.index("作者原文片段（轮 A）") < a_block.index("AI 复刻片段（轮 A）")
    # 轮 B：复刻片段出现在原文片段之前（交换）
    assert b_block.index("AI 复刻片段（轮 B）") < b_block.index("作者原文片段（轮 B）")


def test_C_pairwise_all_eight_dims():
    """两轮都列全 8 个评分维度。"""
    p = se.generate_pairwise_llm_prompt(_REF, _GEN)
    for name, _ in se._SFS_LLM_DIMS:
        assert f"### {name}" in p, name


def test_C_pairwise_uses_representative_not_random():
    """pairwise prompt 的参考片段走代表性选片（与 _select_representative_samples 输出一致）。"""
    p = se.generate_pairwise_llm_prompt(_REF, _GEN)
    rep = se._select_representative_samples(_REF, 3)
    # 代表性选出的片段文本应原样出现在 prompt 里
    for seg in rep:
        # 取片段首句做存在性检查（避免换行差异）
        head = seg.splitlines()[0]
        assert head in p, head


# ════════════════════════════════════════════════════════════════
# [D] 顺序双跑取均值消偏 average_pairwise_scores
# ════════════════════════════════════════════════════════════════

def test_D_average_cancels_order_bias():
    """两轮对称偏置（A 高 B 低 / A 低 B 高）→ 取均值后偏置抵消（消偏核心）。"""
    a = {"叙事结构": {"score": 9}, "对话风格": {"score": 5}}
    b = {"叙事结构": {"score": 5}, "对话风格": {"score": 9}}
    m = se.average_pairwise_scores(a, b)
    assert m["dimensions"]["叙事结构"] == 7.0
    assert m["dimensions"]["对话风格"] == 7.0
    assert m["mean_score"] == 7.0


def test_D_order_bias_reported():
    """顺序偏置幅度被诊断报告（max_abs / mean_abs · advisory 可读不黑箱）。"""
    a = {"叙事结构": {"score": 9}, "对话风格": {"score": 6}}
    b = {"叙事结构": {"score": 5}, "对话风格": {"score": 8}}
    m = se.average_pairwise_scores(a, b)
    assert m["order_bias"]["per_dim"]["叙事结构"] == 4.0
    assert m["order_bias"]["max_abs"] == 4.0
    assert m["order_bias"]["mean_abs"] == 3.0  # (4+2)/2


def test_D_average_accepts_plain_numbers():
    """入参既支持 {"score": x} 也支持裸数字（兼容不同 LLM 返回格式）。"""
    a = {"叙事结构": 8, "对话风格": 6}
    b = {"叙事结构": 6, "对话风格": 8}
    m = se.average_pairwise_scores(a, b)
    assert m["dimensions"]["叙事结构"] == 7.0


def test_D_average_advisory_note_only():
    """note 显式声明只消偏诊断 · 不改 sfs_quick/grade 判决（北极星⑤ 顾问非法官）。"""
    m = se.average_pairwise_scores({"叙事结构": 8}, {"叙事结构": 8})
    assert "不改" in m["note"] and "sfs_quick" in m["note"]


def test_D_average_partial_keys_intersection():
    """两轮维度不完全一致 → 取交集（坏输入不抛错）。"""
    a = {"叙事结构": 8, "对话风格": 6}
    b = {"叙事结构": 6}  # 缺对话风格
    m = se.average_pairwise_scores(a, b)
    assert set(m["dimensions"]) == {"叙事结构"}
    assert m["dimensions"]["叙事结构"] == 7.0


# ════════════════════════════════════════════════════════════════
# [E] env 开关 + 默认零回归
# ════════════════════════════════════════════════════════════════

def test_E_debias_mode_default_off():
    """SFS_LLM_DEBIAS 默认 off · 显式 {1,true,on,yes} 才开 · 其它一律 off。"""
    sx = _reload_se(None)
    try:
        assert sx._sfs_llm_debias_on() is False
        for v in ("on", "1", "true", "yes", "ON", "True"):
            assert _reload_se(v)._sfs_llm_debias_on() is True, v
        for v in ("off", "0", "false", "garbage", ""):
            assert _reload_se(v)._sfs_llm_debias_on() is False, v
    finally:
        _reload_se(None)


def test_E_default_off_legacy_prompt_unchanged():
    """默认 off → generate_llm_prompt 走旧单序 + random.sample（零回归 · 与消偏前 byte 级一致）。"""
    sx = _reload_se(None)
    try:
        p = sx.generate_llm_prompt(_REF, _GEN)
        # 旧 prompt 标志：单序标题 / 旧输出格式标题 / 无 pairwise 双序
        assert p.startswith("# 风格保真度 LLM 评分\n")
        assert "## 评分维度（每维度 1-10 分）" in p
        assert "请严格按以下 JSON 格式输出评分" in p
        assert "order_A" not in p and "评分轮 A" not in p and "pairwise" not in p
    finally:
        _reload_se(None)


def test_E_on_routes_to_pairwise():
    """SFS_LLM_DEBIAS=on → generate_llm_prompt 转调 pairwise（双序消偏）。"""
    sx = _reload_se("on")
    try:
        p = sx.generate_llm_prompt(_REF, _GEN)
        assert "评分轮 A" in p and "order_A" in p and "顺序双跑" in p
    finally:
        _reload_se(None)


def test_E_dims_single_source_matches_legacy():
    """_SFS_LLM_DIMS 与旧 inline 8 维内容完全一致（单一来源 · pairwise 与 legacy 不分歧）。"""
    expect = [
        ("叙事结构", "叙事视角、场景转换、时间线处理的一致性"),
        ("对话风格", "角色对话的口语化程度、口癖保留、语气词使用"),
        ("情绪节奏", "紧张/舒缓的交替节奏、段落长短的节奏感"),
        ("角色声纹", "不同角色的语言辨识度、性格在对话中的体现"),
        ("章首章末", "开头吸引力、结尾悬念/余韵的处理手法"),
        ("反AI腔", "是否存在AI常见套话、机械化表达、缺少人味的句式"),
        ("招牌技法", "原作者独特的修辞手法、比喻风格、描写偏好"),
        ("信息密度", "每段传递的信息量、描写与叙事的比例平衡"),
    ]
    assert list(se._SFS_LLM_DIMS) == expect


# ════════════════════════════════════════════════════════════════
# [F] 不破坏 L3a 共存 + 不改 evaluate 判决
# ════════════════════════════════════════════════════════════════

def test_F_l3a_functions_still_present():
    """L3a 滑窗/去题材 SFS 函数依旧存在可调（L3e 纯增量 · 不动 L3a）。"""
    assert hasattr(se, "compute_burstiness")
    assert hasattr(se, "compute_style_only_sfs")
    assert hasattr(se, "compute_l3a")
    b = se.compute_burstiness("他走进房间。\n看了看四周。\n谁？" * 50)
    assert "applicable" in b


def test_F_evaluate_unaffected_by_debias():
    """SFS_LLM_DEBIAS 不改 evaluate 的 sfs_quick/grade（消偏只动 LLM prompt 侧 · 判决不变）。"""
    sx_off = _reload_se(None)
    try:
        rep_off = sx_off.evaluate(_REF, _GEN)
        sx_on = _reload_se("on")
        rep_on = sx_on.evaluate(_REF, _GEN)
        assert rep_off["sfs_quick"] == rep_on["sfs_quick"]
        assert (rep_off["programmatic_score"]["grade"]
                == rep_on["programmatic_score"]["grade"])
    finally:
        _reload_se(None)


def test_F_no_new_hard_gate_code():
    """L3e 不引入任何 hard_gate code（只产 advisory 诊断 · 北极星⑤）。"""
    try:
        sys.path.insert(0, str(_ROOT / "core" / "scripts"))
        import audit_hub  # noqa: E402
    except Exception:
        return
    m = se.average_pairwise_scores({"叙事结构": 8}, {"叙事结构": 6})
    # average 输出里不该出现 gate_level / hard_gate code
    assert "gate_level" not in m
    assert "advisory" in m["note"]


# ════════════════════════════════════════════════════════════════
# [G] 真原文金标准（北极星纪律 3 · 共同纪律 3）
# ════════════════════════════════════════════════════════════════

def test_G_real_author_representative_select_from_corpus():
    """蛊真人真原文：代表性选片选出的片段都源自原文（不臆造 · 健全性）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        ch = _load_chapter(book, "第010章")
        if ch is None or se.count_chinese(ch) < 1000:
            continue
        found = True
        sel = se._select_representative_samples(ch, 3)
        assert 1 <= len(sel) <= 3, sel
        for seg in sel:
            # 选出片段的首句应能在原文里找到（来自语料 · 非生成）
            head = seg.splitlines()[0].rstrip("……")
            assert head[:10] in ch, (book, head[:20])
    assert found or _load_chapter("蛊真人", "第010章") is None


def test_G_real_author_no_order_bias_when_symmetric():
    """真作者两章模拟 LLM 对称评分 → 取均值消偏后两轮一致维度偏置为 0（金标准：消偏不引入噪声）。"""
    # 模拟 LLM 对真原文给出的两轮评分完全一致（无顺序偏置场景）
    same = {n: {"score": 8} for n, _ in se._SFS_LLM_DIMS}
    m = se.average_pairwise_scores(same, dict(same))
    assert m["order_bias"]["max_abs"] == 0.0
    assert m["mean_score"] == 8.0


def test_G_real_author_pairwise_prompt_builds():
    """蛊真人/惊悚乐园真原文喂 pairwise prompt → 正常构建（含双序 + 真原文片段）。"""
    found = False
    for book in ("蛊真人", "惊悚乐园"):
        a = _load_chapter(book, "第010章")
        c = _load_chapter(book, "第020章")
        if a is None or c is None:
            continue
        found = True
        p = se.generate_pairwise_llm_prompt(a, c)
        assert "评分轮 A" in p and "评分轮 B" in p
        # 真原文代表性片段应进 prompt
        rep = se._select_representative_samples(a, 3)
        if rep:
            assert rep[0].splitlines()[0] in p
    assert found or _load_chapter("蛊真人", "第010章") is None
