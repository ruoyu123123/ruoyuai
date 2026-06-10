"""作者级叙事功能序列蒸馏测试 — 补 skill 唯一缺失的**结构语法层**（2026-05-31 · 北极星⑤）。

背景（2603.14430 实证）：中文网文同质化根因在**结构层**——LLM 只复现高频默认模板
（Save-the-Cat 通用编剧节拍），不复现某作者**专属因果叙事功能链**（如蛊真人
「设定投放→反高潮拒战→打脸」、惊悚乐园「危机→算计揭示」）。前 8 轮加固都在散文/
检测/评估/流程层。本模块从作者语料蒸馏功能序列，写入 skill 作 **advisory 软提示**——
绝不 hard_gate、不干涉模型创作判断、纯启发式黑箱（不调 LLM）。

核心防误判设计：z-score 相对显著度（vs 全语料 baseline）把 always-present 的世界观
词汇（蛊/真元）normalize 掉——小验证证实原始计数法 90% 章全 setting，z-score 法分布
均衡 + 命中真因果链。

测试覆盖：
  [A] narrative_function_raw_scores / _pstdev 纯函数正确性；
  [B] score_narrative_function_sequence 结构 + z-score 防误判 + env off 旁路；
  [C] confidence 分级 + baseline n-gram 剔除；
  [D] analyze_text 注入 narrative_function_raw_scores（不破坏既有字段）；
  [E] CLI --narrative-seq 模式端到端；
  [F] 真作者金标准——蛊真人/惊悚乐园原文功能序列有意义 + 作者可区分（不矫枉过正：
      纯 advisory 永不产 FAIL，且 off 完全旁路保零回归）。

只测确定性纯函数（程序化校验），不碰 LLM / agent。守纪律：真作者原文喂校验
确认改动后真作者不被误判（memory reference-system-validation-method）。
"""
import os
import sys
import json
import subprocess
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import style_analyzer as sa  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _reload_sa(mode):
    """以指定 NARRATIVE_SEQ_MODE reload style_analyzer（env 在函数内读·reload 无碍但保险）。"""
    if mode is None:
        os.environ.pop("NARRATIVE_SEQ_MODE", None)
    else:
        os.environ["NARRATIVE_SEQ_MODE"] = mode
    importlib.reload(sa)
    return sa


def _load_corpus(book, limit=None):
    """读某书原文目录的章节（按章号序）· 无样本环境返回 []。"""
    orig = _ROOT / "workspace" / "styles" / book / "原文"
    if not orig.is_dir():
        return []
    files = sorted(orig.glob("第*章.txt"))
    if limit:
        files = files[:limit]
    return [f.read_text(encoding="utf-8") for f in files]


# ════════════════════════════════════════════════════════════════
# [A] 纯函数：narrative_function_raw_scores / _pstdev
# ════════════════════════════════════════════════════════════════

def test_A_raw_scores_all_keys_present():
    """raw_scores 返回全部 7 功能维度键 · 每千字密度非负。"""
    sax = _reload_sa(None)
    try:
        s = sax.narrative_function_raw_scores("方源得到了第六块紫金石，突破到中阶，圆满收获。")
        for k in sax._NARRATIVE_FUNCTION_KEYS:
            assert k in s, (k, s)
            assert s[k] >= 0, (k, s[k])
        # 该句强命中 gain_reward（得到/突破/圆满/收获）
        assert s["gain_reward"] > 0, s
    finally:
        _reload_sa(None)


def test_A_raw_scores_empty_no_div_zero():
    """空文本 / 无 CJK → 不除零崩溃（长度兜底 1）· 全 0。"""
    sax = _reload_sa(None)
    try:
        assert all(v == 0 for v in sax.narrative_function_raw_scores("").values())
        assert all(v == 0 for v in sax.narrative_function_raw_scores("abc 123").values())
    finally:
        _reload_sa(None)


def test_A_pstdev_basic():
    """_pstdev：空/单值 → 极小正数（防除零）· 多值 → 真总体标准差。"""
    sax = _reload_sa(None)
    try:
        assert sax._pstdev([]) > 0 and sax._pstdev([]) < 1e-6
        assert sax._pstdev([5.0]) > 0 and sax._pstdev([5.0]) < 1e-6
        # [2,4,4,4,5,5,7,9] 总体 std = 2.0
        assert abs(sax._pstdev([2, 4, 4, 4, 5, 5, 7, 9]) - 2.0) < 1e-9
        # 全相同 → std=0 被兜底成极小正数（绝不返回 0·防后续除零）
        assert sax._pstdev([3.0, 3.0, 3.0]) > 0
    finally:
        _reload_sa(None)


# ════════════════════════════════════════════════════════════════
# [B] score_narrative_function_sequence：结构 + z-score 防误判 + env off
# ════════════════════════════════════════════════════════════════

def test_B_off_mode_short_circuits():
    """NARRATIVE_SEQ_MODE=off → 只回 {mode:off}（完全旁路·零开销·北极星纪律2 可关）。"""
    sax = _reload_sa("off")
    try:
        out = sax.score_narrative_function_sequence(["任意章节内容。" * 50])
        assert out["mode"] == "off", out
        assert "signature_bigrams" not in out, out
    finally:
        _reload_sa(None)


def test_B_active_default_mode():
    """默认（未设 env）→ active · narrative_seq_mode() 非法值回退 active。"""
    sax = _reload_sa(None)
    try:
        assert sax.narrative_seq_mode() == "active"
        sax = _reload_sa("garbage")
        assert sax.narrative_seq_mode() == "active"
        sax = _reload_sa("off")
        assert sax.narrative_seq_mode() == "off"
    finally:
        _reload_sa(None)


def test_B_empty_corpus_low_confidence_no_crash():
    """空语料 / 全空章 → n_chapters=0 · confidence=low · 不抛错（永不判决）。"""
    sax = _reload_sa(None)
    try:
        out = sax.score_narrative_function_sequence([])
        assert out["n_chapters"] == 0 and out["confidence"] == "low", out
        out2 = sax.score_narrative_function_sequence(["", "  ", "123 abc"])
        assert out2["n_chapters"] == 0, out2
    finally:
        _reload_sa(None)


def test_B_zscore_normalizes_world_vocab():
    """z-score 防误判核心：构造「世界观词每章恒高 + 某章独有结构功能」语料 →
    主功能标签应是该章的 OVER-index 功能（face_slap），而非恒高的 setting。
    （原始计数法会让 setting 恒赢——这正是小验证暴露的根因。）"""
    sax = _reload_sa(None)
    try:
        # 每章都堆「设定/规则/机制」（世界观恒高），但只有 ch3 额外强命中 face_slap
        base = "设定规则机制本质所谓体系。设定规则机制本质所谓体系。" * 5
        chs = [base, base, base + "震惊不可能怎么会瞠目难以置信倒吸没想到脸色大变。" * 3,
               base, base]
        out = sax.score_narrative_function_sequence(chs)
        # ch3（索引 2）的主功能应是 face_slap（OVER-index），不是恒高的 setting
        assert out["per_chapter_primary"][2] == "face_slap", out["per_chapter_primary"]
        # 恒高的 setting 在所有章 z-score≈0 → 不会成为任何章主功能
        assert out["per_chapter_primary"].count("setting_injection") == 0, out["per_chapter_primary"]
    finally:
        _reload_sa(None)


def test_B_ngram_excludes_baseline():
    """含 baseline 的 n-gram 被剔除（baseline=无 over-index·不构成功能链）。"""
    sax = _reload_sa(None)
    try:
        # ch1 强 gain · ch2 baseline（无任何强命中）· ch3 强 gain
        # → bigram 不应出现 (gain,baseline) / (baseline,gain)
        gain = "得到获得收获炼成突破晋级到手圆满如愿大功告成。" * 4
        flat = "他走在路上看着远方的天空慢慢地想着今天的事情然后回到了住处。" * 4
        chs = [gain, flat, gain, gain]
        out = sax.score_narrative_function_sequence(chs)
        for b in out["signature_bigrams"]:
            assert "baseline" not in b["seq"], b
        for t in out["signature_trigrams"]:
            assert "baseline" not in t["seq"], t
    finally:
        _reload_sa(None)


# ════════════════════════════════════════════════════════════════
# [C] confidence 分级
# ════════════════════════════════════════════════════════════════

def test_C_confidence_tiers():
    """confidence：>=30 章 high · >=8 mid · <8 low（自报抽取准确率短板）。"""
    sax = _reload_sa(None)
    try:
        ch = "得到获得收获突破。震惊不可能怎么会。危险危机杀机追杀。" * 3
        assert sax.score_narrative_function_sequence([ch] * 5)["confidence"] == "low"
        assert sax.score_narrative_function_sequence([ch] * 10)["confidence"] == "mid"
        assert sax.score_narrative_function_sequence([ch] * 30)["confidence"] == "high"
    finally:
        _reload_sa(None)


def test_C_note_marks_advisory():
    """_note 明确标 advisory / 顾问非法官 / 绝不 hard_gate（北极星⑤ 契约）。"""
    sax = _reload_sa(None)
    try:
        out = sax.score_narrative_function_sequence(["得到突破。" * 20] * 10)
        note = out["_note"]
        assert "advisory" in note and "hard_gate" in note, note
        assert "不干涉" in note, note
    finally:
        _reload_sa(None)


# ════════════════════════════════════════════════════════════════
# [D] analyze_text 注入 narrative_function_raw_scores（向后兼容）
# ════════════════════════════════════════════════════════════════

def test_D_analyze_text_injects_raw_scores_no_break():
    """analyze_text 输出含 narrative_function_raw_scores（7 键）· 既有字段不破坏。"""
    sax = _reload_sa(None)
    try:
        prof = sax.analyze_text("他得到了宝物，突破到新境界。\n“真不错。”他笑道。")
        assert "narrative_function_raw_scores" in prof
        assert set(prof["narrative_function_raw_scores"].keys()) == set(sax._NARRATIVE_FUNCTION_KEYS)
        # 既有关键字段仍在（回归保护）
        for k in ("sentence_stats", "dialogue_ratio", "function_word_fingerprint_per_1000"):
            assert k in prof, k
    finally:
        _reload_sa(None)


# ════════════════════════════════════════════════════════════════
# [E] CLI --narrative-seq 模式端到端
# ════════════════════════════════════════════════════════════════

def test_E_cli_narrative_seq_mode(tmp_path=None):
    """CLI --narrative-seq <目录> → 输出 narrative_function_sequence 段（按章号序读）。"""
    import tempfile
    d = Path(tempfile.mkdtemp())
    for i, body in enumerate([
        "得到获得收获突破晋级。" * 8,
        "危险危机杀机追杀险些。" * 8,
        "震惊不可能怎么会瞠目。" * 8,
    ], 1):
        (d / f"第{i:03d}章.txt").write_text(body, encoding="utf-8")
    script = _ROOT / "core" / "scripts" / "style_analyzer.py"
    # Windows 下子进程管道 stdout 默认 locale 编码（GBK）——中文 JSON 会让 utf-8
    # 严格解码在 reader 线程崩掉（stdout=None）。强制子进程 UTF-8 输出。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, str(script), str(d), "--narrative-seq"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    seq = out["narrative_function_sequence"]
    assert seq["mode"] == "active" and seq["n_chapters"] == 3, seq
    assert out["chapter_files"] == ["第001章.txt", "第002章.txt", "第003章.txt"], out["chapter_files"]


def test_E_cli_off_mode_env():
    """CLI 下 NARRATIVE_SEQ_MODE=off → 输出 mode=off（env 旁路贯通到 CLI）。"""
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "第001章.txt").write_text("得到突破收获。" * 10, encoding="utf-8")
    script = _ROOT / "core" / "scripts" / "style_analyzer.py"
    env = dict(os.environ, NARRATIVE_SEQ_MODE="off", PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, str(script), str(d), "--narrative-seq"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["narrative_function_sequence"]["mode"] == "off"


# ════════════════════════════════════════════════════════════════
# [F] 真作者金标准：序列有意义 + 作者可区分 + 不矫枉过正
# ════════════════════════════════════════════════════════════════

def test_F_gu_zhenren_sequence_meaningful():
    """蛊真人真原文（686 章）：confidence=high · 功能分布均衡（无单功能 >50%·
    证明 z-score 把世界观词 normalize 掉了）· 有签名功能链。"""
    sax = _reload_sa(None)
    try:
        corpus = _load_corpus("蛊真人")
        if not corpus:
            return  # CI 无样本则跳过
        out = sax.score_narrative_function_sequence(corpus)
        assert out["confidence"] == "high", out["n_chapters"]
        # 无单一功能（含 baseline）占比 > 0.5 → z-score 防误判生效（不会全是 setting）
        assert max(out["function_distribution"].values()) < 0.5, out["function_distribution"]
        # setting_injection 不应畸高（原始计数法会让它 >0.9）
        assert out["function_distribution"].get("setting_injection", 0) < 0.3, out["function_distribution"]
        # 有可观的签名功能链
        assert len(out["signature_bigrams"]) >= 3, out["signature_bigrams"]
        assert len(out["signature_trigrams"]) >= 2, out["signature_trigrams"]
    finally:
        _reload_sa(None)


def test_F_authors_differentiable():
    """蛊真人 vs 惊悚乐园功能分布可区分（不同作者签名不同·验证非千篇一律）。
    蛊真人=升级文 gain_reward 偏高 · 惊悚乐园=恐怖文 crisis/scheme 偏高。"""
    sax = _reload_sa(None)
    try:
        gu = _load_corpus("蛊真人")
        js = _load_corpus("惊悚乐园")
        if not gu or not js:
            return
        gd = sax.score_narrative_function_sequence(gu)["function_distribution"]
        jd = sax.score_narrative_function_sequence(js)["function_distribution"]
        # 两书功能分布不应完全相同（若提取无区分力则会雷同）
        assert gd != jd, (gd, jd)
        # 蛊真人（升级文）gain_reward 高于惊悚乐园（实证：0.14 vs 0.088）
        assert gd.get("gain_reward", 0) > jd.get("gain_reward", 0), (gd, jd)
    finally:
        _reload_sa(None)


def test_F_pure_advisory_never_gates():
    """纯 advisory 不矫枉过正：返回结构里**没有任何**判决/状态字段
    （status/FAIL/PASS/gate）· 只有数据 + advisory note。真作者永不被毙。"""
    sax = _reload_sa(None)
    try:
        corpus = _load_corpus("蛊真人", limit=40) or ["得到突破收获。" * 20] * 10
        out = sax.score_narrative_function_sequence(corpus)
        flat = json.dumps(out, ensure_ascii=False)
        for forbidden in ('"status"', '"FAIL"', '"PASS"', '"gate_level"', '"hard_gate":'):
            assert forbidden not in flat, (forbidden, "advisory 结构混入判决字段")
    finally:
        _reload_sa(None)
