"""learning_loop reflect 归因闭环测试（2026-05-31 · skill 段落级 reflective credit-assignment）。

测的是 learning_loop 的【持续风格失败 → 反射归因到 skill_vN.md 具体段落 → advisory 改写建议】，
闭合「writing-side 风格失败只 advisory 报出、从不归因到 skill 哪条段落」这一开环（唯一没闭的环）。
借 GEPA 的 reflective credit-assignment 一招（不做 Pareto 多版本 / merge 交叉·北极星⑥）。验证：

  1. STYLE_* code 持续复发（>= REFLECT_RECUR_THRESHOLD 章）→ 反射归因到 skill 最相关标题段落；
  2. 命中 skill 段落 → tighten_clause 建议；skill 无对应段落 → add_clause 建议；
  3. 一致性 / 文件契约类 code（非 STYLE_）不归因（北极星⑤：skill 段落只管风格）；
  4. 产出永远 advisory，绝不改写 skill 文件本身（不干涉模型 / 作者权威）；
  5. env LL_REFLECT_ATTRIB=off → 跳过（不动文件）；
  6. 跨 cluster 长程漂移 findings 单独证据流 → 归因「整体文风」段落；
  7. 缺 作者风格.json / style_source → 降级为「未定位·建议新增」（不报错）；
  8. 幂等：同 code 覆盖刷新（重跑不产重复）；--scan-recurring 末尾自动带跑。

只测确定性纯函数层（不碰 LLM / agent）·零依赖 stdlib·复用 learning_loop 框架。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import learning_loop as ll  # noqa: E402


# ═══════════════════════ 公共脚手架 ═══════════════════════

_SKILL_MD = """---
name: 测试风格-v1
---

# 写作风格 Skill：测试

## 章型识别

写前标注章型。

### 铁律 1：对话占比按章型

- 对话章：35-50%
- 全章平均 ≥ 26%

### 铁律 2：拟声词独段

- 战斗章 3-5 处
- 格式：拟声词 + 换行 + 空行

## 段落硬约束

- 平均段长 15-35 字
- 单段 > 80 字 warn / > 120 字 hard_gate
- 单句独行占比 40-65%

## 禁用词严控

- 绝禁 AI 套话：与此同时 / 然而 / 事实上
"""


def _mk_project(tmp: Path, with_style=True) -> Path:
    """造最小项目骨架：_数据库/ + 作者风格.json(style_source 指向写好的 skill_v1.md)。"""
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    if with_style:
        skill = tmp / "styles" / "skill_v1.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(_SKILL_MD, encoding="utf-8")
        # style_source 用绝对路径（_resolve_skill_path 兼容绝对/相对）
        (db / "作者风格.json").write_text(json.dumps(
            {"style_source": str(skill)}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _mk_audit(ch, code, dim="风格", severity="warning"):
    return {"chapter": ch,
            "issues": [{"code": code, "dimension": dim, "severity": severity,
                        "desc": f"{code} 样本第{ch}章", "waived": False}]}


def _scan_with_audits(tmp: Path, audits: list):
    """写多份 audit -> 跑 scan_recurring（末尾自动带跑 reflect_attribution）。"""
    adir = tmp / "_数据库" / ".audit"
    adir.mkdir(parents=True, exist_ok=True)
    for a in audits:
        (adir / f"ch_{a['chapter']:03d}_audit.json").write_text(
            json.dumps(a, ensure_ascii=False), encoding="utf-8")
    return ll.scan_recurring(tmp)


def _load_suggestions(tmp: Path) -> list:
    return ll.load_experience(tmp).get("skill_rewrite_suggestions", [])


def _by_code(sugs, code):
    return next((s for s in sugs if s.get("style_code") == code), None)


# ═══════════════════════ 1. 归因到具体 skill 段落 ═══════════════════════

def test_persistent_style_failure_attributed_to_skill_section():
    """STYLE_对话占比 连续复发 2 章 → 反射归因到 skill「铁律 1：对话占比按章型」段落。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_对话占比"),
                                _mk_audit(2, "STYLE_对话占比")])
        s = _by_code(_load_suggestions(tmp), "STYLE_对话占比")
        assert s is not None, "持续风格失败未产出 skill 改写建议"
        assert s["suggestion_type"] == "tighten_clause"
        assert s["attributed_section"] is not None
        assert "对话占比" in s["attributed_section"]["heading"]
        assert s["gate_level"] == "advisory"  # 永远 advisory
        assert s["style_dimension"] == "对话占比"
        assert s["evidence_source"] == "recurrence"


def test_paragraph_code_attributed_to_paragraph_section():
    """STYLE_单段超长 复发 → 归因到「段落硬约束」段落（关键词命中标题）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_单段超长"),
                                _mk_audit(2, "STYLE_单段超长")])
        s = _by_code(_load_suggestions(tmp), "STYLE_单段超长")
        assert s is not None
        assert s["attributed_section"]["heading"] == "段落硬约束"
        assert s["attributed_section"]["line"] > 0  # 带行号定位


def test_banned_word_attributed_to_banned_section():
    """STYLE_禁用词 复发 → 归因到「禁用词严控」段落。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_禁用词"),
                                _mk_audit(2, "STYLE_禁用词")])
        s = _by_code(_load_suggestions(tmp), "STYLE_禁用词")
        assert s is not None
        assert "禁用词" in s["attributed_section"]["heading"]


# ═══════════════════════ 2. 未命中 → 建议新增 skill 约束 ═══════════════════════

def test_unlocated_code_suggests_add_clause():
    """skill 里没有对应段落的风格 code → suggestion_type=add_clause（建议新增）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        # STYLE_逗句比 的关键词（逗号/逗句比/长句/句长/句式节奏）在测试 skill 里都没有
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_逗句比"),
                                _mk_audit(2, "STYLE_逗句比")])
        s = _by_code(_load_suggestions(tmp), "STYLE_逗句比")
        assert s is not None
        assert s["suggestion_type"] == "add_clause"
        assert s["attributed_section"] is None
        assert s["gate_level"] == "advisory"


# ═══════════════════════ 3. 非风格 code 不归因（北极星⑤）═══════════════════════

def test_non_style_code_not_attributed():
    """一致性 / 文件契约类 code（LOCKED_FACT_CONFLICT）持续复发 → 不进 reflect 归因。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _scan_with_audits(tmp, [_mk_audit(1, "LOCKED_FACT_CONFLICT", dim="结构", severity="error"),
                                _mk_audit(2, "LOCKED_FACT_CONFLICT", dim="结构", severity="error")])
        sugs = _load_suggestions(tmp)
        assert _by_code(sugs, "LOCKED_FACT_CONFLICT") is None
        # skill 改写建议里不该出现任何非 STYLE_ code
        assert all(s["style_code"].startswith("STYLE_") or "DRIFT" in s["style_code"]
                   for s in sugs)


# ═══════════════════════ 4. 复发不足阈值不归因 ═══════════════════════

def test_single_occurrence_not_attributed():
    """单章命中（< REFLECT_RECUR_THRESHOLD=2）→ 不算持续风格失败·不归因。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_对话占比")])  # 只 1 章
        assert _by_code(_load_suggestions(tmp), "STYLE_对话占比") is None


# ═══════════════════════ 5. env 关闭 ═══════════════════════

def test_env_off_skips_attribution():
    """LL_REFLECT_ATTRIB=off → reflect_attribution 跳过·不产建议·不动文件。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        # 先攒复发证据（scan 会带跑 reflect，但我们要测显式关闭路径）
        adir = tmp / "_数据库" / ".audit"
        adir.mkdir(parents=True, exist_ok=True)
        for ch in (1, 2):
            (adir / f"ch_{ch:03d}_audit.json").write_text(
                json.dumps(_mk_audit(ch, "STYLE_对话占比"), ensure_ascii=False), encoding="utf-8")
        old = os.environ.get("LL_REFLECT_ATTRIB")
        os.environ["LL_REFLECT_ATTRIB"] = "off"
        try:
            produced = ll.reflect_attribution(tmp)
        finally:
            if old is None:
                os.environ.pop("LL_REFLECT_ATTRIB", None)
            else:
                os.environ["LL_REFLECT_ATTRIB"] = old
        assert produced == []
        assert _load_suggestions(tmp) == []  # 没写任何建议


# ═══════════════════════ 6. 长程漂移证据流 ═══════════════════════

def test_longrange_drift_finding_attributed():
    """cross-cluster LONGRANGE_STYLE_DRIFT advisory finding → 归因「整体文风」+ source 标记。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        (tmp / "_数据库").mkdir(parents=True, exist_ok=True)
        findings = [{"code": "LONGRANGE_STYLE_DRIFT", "gate_level": "advisory",
                     "metric": {"n_points": 5, "last": 0.42},
                     "message": "跨 cluster 长程作者文风漂移：相似度单调下行"}]
        produced = ll.reflect_attribution(tmp, drift_findings=findings)
        s = _by_code(produced, "LONGRANGE_STYLE_DRIFT")
        assert s is not None
        assert s["evidence_source"] == "longrange_drift"
        assert s["gate_level"] == "advisory"
        assert "文风" in s["style_dimension"]


# ═══════════════ 6b. source-2 自动接线：reflect_attribution(None) live 收集长程漂移 ═══════════════
# 两个真实调用点（scan_recurring / --reflect-attribution）都以 drift_findings=None 调用 →
# reflect_attribution 内部应 in-process 复跑 cross_cluster scanner 自动抓 LONGRANGE_STYLE_DRIFT。
# 用 monkeypatch 桩住 scanner.scan（不依赖真 embedding / 真 cluster 草稿·测纯接线逻辑）。

def _patch_scanner_scan(report):
    """把 cross_cluster_style_drift_scanner.scan 临时替换成返回固定 report 的桩。返回 restore()。"""
    import cross_cluster_style_drift_scanner as ccsd
    orig = ccsd.scan
    ccsd.scan = lambda pr, **kw: report

    def restore():
        ccsd.scan = orig
    return restore


_FAKE_DRIFT = {"code": "LONGRANGE_STYLE_DRIFT", "gate_level": "advisory",
               "severity": "advisory", "metric": {"n_points": 5, "last": 0.42},
               "message": "跨 cluster 长程作者文风漂移：相似度单调下行"}


def test_default_none_autocollects_drift_from_shadow_findings():
    """drift_findings=None（两真实调用点的传法）→ 自动收集 scanner shadow_findings 里的漂移 →
    产 longrange_drift 来源的 advisory 建议。证明 source-2 在 live 默认路径上接通。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        restore = _patch_scanner_scan(
            {"mode": "shadow", "issues": [], "shadow_findings": [dict(_FAKE_DRIFT)]})
        try:
            produced = ll.reflect_attribution(tmp)  # drift_findings 缺省 = None
        finally:
            restore()
        s = _by_code(produced, "LONGRANGE_STYLE_DRIFT")
        assert s is not None, "默认 None 路径未自动接通 source-2 长程漂移收集"
        assert s["evidence_source"] == "longrange_drift"
        assert s["gate_level"] == "advisory"


def test_default_none_autocollects_drift_from_active_issues():
    """scanner active 模式 findings 落在 report['issues'] → 同样被自动收集（闸不屏蔽归因信号）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        restore = _patch_scanner_scan(
            {"mode": "active", "issues": [dict(_FAKE_DRIFT)], "shadow_findings": []})
        try:
            produced = ll.reflect_attribution(tmp)
        finally:
            restore()
        assert _by_code(produced, "LONGRANGE_STYLE_DRIFT") is not None


def test_explicit_empty_list_disables_autocollect():
    """显式 drift_findings=[] → 明示「无漂移」·不再 live 扫（即便 scanner 桩有 finding 也不取）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        restore = _patch_scanner_scan(
            {"mode": "shadow", "issues": [], "shadow_findings": [dict(_FAKE_DRIFT)]})
        try:
            produced = ll.reflect_attribution(tmp, drift_findings=[])
        finally:
            restore()
        assert _by_code(produced, "LONGRANGE_STYLE_DRIFT") is None


def test_autocollect_degrades_on_scanner_error():
    """scanner.scan 抛错 → _collect_live_drift_findings 降级返回 []·reflect 不崩（北极星⑥）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        import cross_cluster_style_drift_scanner as ccsd
        orig = ccsd.scan

        def boom(pr, **kw):
            raise RuntimeError("scanner offline")
        ccsd.scan = boom
        try:
            produced = ll.reflect_attribution(tmp)  # 应平稳返回（无 drift 证据）
        finally:
            ccsd.scan = orig
        assert _by_code(produced, "LONGRANGE_STYLE_DRIFT") is None


def test_collect_live_drift_findings_filters_non_attrib_codes():
    """_collect_live_drift_findings 只抓 _STYLE_CODE_ATTRIB 内的 code（杂 code 不混入归因）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        restore = _patch_scanner_scan({
            "mode": "active",
            "issues": [dict(_FAKE_DRIFT),
                       {"code": "SOME_OTHER_CODE", "gate_level": "advisory"}],
            "shadow_findings": [],
        })
        try:
            got = ll._collect_live_drift_findings(tmp)
        finally:
            restore()
        codes = [f["code"] for f in got]
        assert "LONGRANGE_STYLE_DRIFT" in codes
        assert "SOME_OTHER_CODE" not in codes


def test_scan_recurring_autowires_source2_drift():
    """端到端：--scan-recurring（scan_recurring）以 None 调 reflect_attribution → 自动带上 source-2
    漂移建议。返回的 skill_rewrite 段含 LONGRANGE_STYLE_DRIFT（证明真实调用点 1234 已接通）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        restore = _patch_scanner_scan(
            {"mode": "shadow", "issues": [], "shadow_findings": [dict(_FAKE_DRIFT)]})
        try:
            res = _scan_with_audits(tmp, [_mk_audit(1, "STYLE_禁用词"),
                                          _mk_audit(2, "STYLE_禁用词")])
        finally:
            restore()
        codes = [s["style_code"] for s in res["skill_rewrite"]]
        assert "LONGRANGE_STYLE_DRIFT" in codes, "scan_recurring 未把 source-2 漂移带进 reflect"
        assert "STYLE_禁用词" in codes  # 复发 source-1 仍在


# ═══════════════════════ 7. 缺 作者风格.json → 降级（不报错）═══════════════════════

def test_missing_style_json_degrades_gracefully():
    """无 作者风格.json → skill 无法定位 → 全部降级为 add_clause·不抛异常。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), with_style=False)  # 不建 作者风格.json
        _scan_with_audits(tmp, [_mk_audit(1, "STYLE_对话占比"),
                                _mk_audit(2, "STYLE_对话占比")])
        s = _by_code(_load_suggestions(tmp), "STYLE_对话占比")
        assert s is not None
        assert s["suggestion_type"] == "add_clause"  # 无 skill 可定位
        assert s["attributed_section"] is None
        assert s["skill_path"] is None


# ═══════════════════════ 8. 幂等 + scan 自动带跑 ═══════════════════════

def test_idempotent_no_duplicate():
    """同 code 重跑覆盖刷新（不产重复条目）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        audits = [_mk_audit(1, "STYLE_对话占比"), _mk_audit(2, "STYLE_对话占比")]
        _scan_with_audits(tmp, audits)
        ll.reflect_attribution(tmp)  # 再跑一次
        sugs = _load_suggestions(tmp)
        codes = [s["style_code"] for s in sugs if s["style_code"] == "STYLE_对话占比"]
        assert len(codes) == 1, f"幂等失败·产生重复: {codes}"


def test_scan_recurring_auto_runs_reflect():
    """--scan-recurring 末尾自动带跑 reflect_attribution → 返回 skill_rewrite 段。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d))
        res = _scan_with_audits(tmp, [_mk_audit(1, "STYLE_单段超长"),
                                      _mk_audit(2, "STYLE_单段超长")])
        assert "skill_rewrite" in res
        assert any(s["style_code"] == "STYLE_单段超长" for s in res["skill_rewrite"])


# ═══════════════════════ 9. 纯函数：段落解析 + 归因打分 ═══════════════════════

def test_parse_skill_sections_basic():
    """_parse_skill_sections 切出标题段（带 heading/level/line/body）。"""
    secs = ll._parse_skill_sections(_SKILL_MD)
    headings = [s["heading"] for s in secs]
    assert "段落硬约束" in headings
    assert "禁用词严控" in headings
    para = next(s for s in secs if s["heading"] == "段落硬约束")
    assert para["level"] == 2
    assert "单段" in para["body"]


def test_attribute_scoring_prefers_heading_match():
    """_attribute_to_skill_section：标题命中权重 > 正文命中（找最相关段）。"""
    secs = ll._parse_skill_sections(_SKILL_MD)
    # 「拟声」关键词应命中「铁律 2：拟声词独段」标题（×3），而非别处正文
    hit = ll._attribute_to_skill_section(secs, ("拟声", "拟声词独段", "拟声段", "战斗描写"))
    assert hit is not None
    assert "拟声" in hit["heading"]


def test_attribute_no_match_returns_none():
    """关键词全不命中 → 返回 None（不强行乱归因·北极星⑤）。"""
    secs = ll._parse_skill_sections(_SKILL_MD)
    assert ll._attribute_to_skill_section(secs, ("根本不存在的词xyz",)) is None


# ═══════════════════ 10. embedding 接线（2026-07-02 · 真后端门控 + 关键词计数 fallback）═══════════

def test_has_real_embedding_backend_false_by_default():
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        assert ll._has_real_embedding_backend() is False
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v


def test_semantic_attribution_matches_synonym_with_zero_literal_overlap(monkeypatch):
    """真后端：查询关键词与目标段落零字面重叠的同义表述（"对话要简短" 与标题
    「铁律 1：对话占比按章型」无共享字符）仍应被余弦相似度正确归因——字面计数法在这种
    场景下会返回 None（无法定位）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    secs = ll._parse_skill_sections(_SKILL_MD)

    # 前置断言：字面计数法确实找不到（关键词与该段标题/正文零字面重叠）
    assert ll._attribute_to_skill_section(secs, ("对话要简短",)) is None

    import embedding_store

    def _mock_embed(text):
        if "对话要简短" in text or "对话占比" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]
    monkeypatch.setattr(embedding_store, "compute_embedding", _mock_embed)

    hit = ll._attribute_to_skill_section(secs, ("对话要简短",))
    assert hit is not None, "语义路径应命中同义表述"
    assert "对话占比" in hit["heading"]
    assert hit["method"] == "semantic"


def test_semantic_attribution_prefetches_all_sections_once(monkeypatch):
    """🔴 2026-07-03 Wave-4 批量改造回归锁：_semantic_attribute_to_skill_section 开头应对
    [query]+全部 skill 段落 blob 调用**一次** prefetch_embeddings（而非每段各自触发后端·
    ruoyu_style 等真后端下逐段各起一次子进程暖机不可用）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    import embedding_store
    embedding_store._BACKEND = None  # 重探测后端（隔离跨测试残留缓存）

    secs = ll._parse_skill_sections(_SKILL_MD)

    calls = []

    def _record_prefetch(texts):
        calls.append(list(texts))
        return {"total": len(texts), "unique": len(set(texts)), "cache_hits": 0, "computed": len(texts)}
    monkeypatch.setattr(embedding_store, "prefetch_embeddings", _record_prefetch)

    def _mock_embed(text):
        if "对话要简短" in text or "对话占比" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]
    monkeypatch.setattr(embedding_store, "compute_embedding", _mock_embed)

    hit = ll._attribute_to_skill_section(secs, ("对话要简短",))
    assert hit is not None

    assert len(calls) == 1, f"应且只应调用一次 prefetch_embeddings，实际 {len(calls)} 次"
    got = calls[0]
    assert got[0] == "对话要简短"  # query 在第一位
    expected_blobs = {(sec.get("heading", "") + " " + sec.get("body", "")).strip() for sec in secs}
    assert expected_blobs.issubset(set(got[1:])), "prefetch 文本集合应覆盖全部 skill 段落 blob"
    assert len(got) == len(secs) + 1  # query + 每段各一条（未去重·与后续逐条 compute_embedding 一一对应）


def test_semantic_attribution_falls_back_on_embedding_error(monkeypatch):
    """真后端配置但编码异常 → 回退关键词计数法（不崩·结果与门控关时一致）。"""
    monkeypatch.setenv("EMBED_BACKEND", "mock")
    secs = ll._parse_skill_sections(_SKILL_MD)

    import embedding_store

    def _boom(text):
        raise RuntimeError("模拟真后端编码失败")
    monkeypatch.setattr(embedding_store, "compute_embedding", _boom)

    hit = ll._attribute_to_skill_section(secs, ("拟声", "拟声词独段", "拟声段", "战斗描写"))
    assert hit is not None
    assert "拟声" in hit["heading"]
    assert "method" not in hit  # 走的是关键词计数 fallback，不带 semantic 标记


def test_attribute_default_no_backend_matches_pre_upgrade_behavior():
    """🔴 零回归锁：门控关（默认）→ _attribute_to_skill_section 与升级前关键词计数法
    结果逐字段一致（本用例复用既有 test_attribute_scoring_prefers_heading_match 场景）。"""
    old_eb = os.environ.pop("EMBED_BACKEND", None)
    gen_keys = [k for k in os.environ if k.startswith("GEN_EMBED__")]
    saved = {k: os.environ.pop(k) for k in gen_keys}
    try:
        secs = ll._parse_skill_sections(_SKILL_MD)
        hit = ll._attribute_to_skill_section(secs, ("拟声", "拟声词独段", "拟声段", "战斗描写"))
        assert hit is not None
        assert "拟声" in hit["heading"]
        assert "method" not in hit
        assert isinstance(hit["score"], int)  # 关键词计数法 score 恒为 int（语义路径是 float）
    finally:
        if old_eb is not None:
            os.environ["EMBED_BACKEND"] = old_eb
        for k, v in saved.items():
            os.environ[k] = v
