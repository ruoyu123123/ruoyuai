"""P2 走向卡候选打分透明化 + ME 依赖图健康校验 回归测试（2026-07-06）。

借鉴 PlotPilot storyline DAG / candidate scoring（research/open_source_writing_systems.md）。
钉死五条契约：
  1. decision_basis 新增 convergence_score（=dim-6 收敛子分·与总分内加分同源同值）
     + state_delta_preview（选中后涟漪规则只读预演·数值转述规则声明不硬造）。
  2. 确定性：同输入两次 emerge → candidates / dag_health 完全一致。
  3. preview 绝不写库：emerge 前后 世界状态/涟漪规则/事件簇/大势卡 字节不变。
  4. dag_health：环 / 悬挂各能检出（stderr 显式报告）·健康池两段皆空。
  5. advisory：dag 异常不硬失败（emerge 仍 ok·候选照常涌现）。
北极星⑤：只解释不裁决——排序逻辑/候选数量不因新字段改变，机器绝不替用户选。
"""
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import cluster_emergence_engine as cee  # noqa: E402
import emergence_transparency as et  # noqa: E402


# ───────────────────── fixtures ─────────────────────

def _mk_project(tmp: Path, major_events: list, volumes: list = None,
                ripple_rules: dict = None, clusters: list = None,
                world_state: dict = None) -> Path:
    db = tmp / "_数据库"
    db.mkdir(parents=True, exist_ok=True)
    dashishi = {"schema_version": "v27", "major_events": major_events}
    if volumes:
        dashishi["volumes"] = volumes
    (db / "大势卡.json").write_text(
        json.dumps(dashishi, ensure_ascii=False), encoding="utf-8")
    (db / "事件簇.json").write_text(
        json.dumps({"clusters": clusters or []}, ensure_ascii=False), encoding="utf-8")
    (db / "世界状态.json").write_text(
        json.dumps(world_state or {}, ensure_ascii=False), encoding="utf-8")
    (db / "character_arc_state.json").write_text("{}", encoding="utf-8")
    if ripple_rules is not None:
        (db / "涟漪规则.json").write_text(
            json.dumps(ripple_rules, ensure_ascii=False), encoding="utf-8")
    return tmp


def _read_emergence(result) -> dict:
    return json.loads(Path(result["emergence_path"]).read_text(encoding="utf-8"))


def _candidate_by_me(em: dict, me_id: str) -> dict:
    for c in em["candidates"]:
        if c["parent_me"] == me_id:
            return c
    raise AssertionError(f"候选中找不到 ME {me_id}：{[c['parent_me'] for c in em['candidates']]}")


# 一个强烈呼应卷收敛词（玄铁令/立序殿）、一个完全无关
_ME_POOL = [
    {"id": "ME-V1-01", "volume": 1, "title": "无关支线",
     "description": "主角在市集买菜遇到旧识闲聊家常", "status": "pending"},
    {"id": "ME-V1-02", "volume": 1, "title": "玄铁令争夺",
     "description": "围绕玄铁令的下落与立序殿正面冲突", "status": "pending"},
]

_VOLS = [
    {"vol": 1, "title": "立序殿风波",
     "volume_core_conflict": "夺回玄铁令镇压立序殿叛乱",
     "volume_thread": "玄铁令的下落贯穿本卷",
     "volume_finale_signal": "立序殿主现身"},
]

_RULES = {"ripple_rules": [
    {"id": "RR_FATE_V102", "trigger_type": "fate_event", "trigger_match": "ME-V1-02",
     "ripples": [
         {"target": "factions_state.立序殿.power", "delta": -10, "reason": "正面冲突削弱"},
         {"narrative": "立序殿对主角的关注度上升"},
     ]},
    {"id": "RR_MINOR_XTL", "trigger_type": "minor_event", "trigger_match": "玄铁令",
     "ripples": [{"target": "consequence_tracker", "add": {"event": "玄铁令下落曝光"}}]},
    # auto_tick 规则与具体候选无关 → 绝不该进 preview
    {"id": "RR_AUTO", "trigger_type": "auto_tick", "trigger_match": "every_cluster",
     "ripples": [{"target": "current_world_time.cluster", "set_to_current_cluster": True}]},
]}

_DB_FILES = ("大势卡.json", "事件簇.json", "世界状态.json", "character_arc_state.json", "涟漪规则.json")


def _db_snapshot(root: Path) -> dict:
    out = {}
    for fn in _DB_FILES:
        p = root / "_数据库" / fn
        out[fn] = p.read_bytes() if p.exists() else None
    return out


# ───────────────────── 1. convergence_score（同源同值） ─────────────────────

def test_decision_basis_has_convergence_score_matching_dim6():
    """呼应收敛词的候选 convergence_score>0 且 == emergence_transparency 公式复算值；
    无关候选 == 0。字段只解释不改排序（呼应者仍排第一）。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=_RULES)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True, r
        em = _read_emergence(r)

        conv_cand = _candidate_by_me(em, "ME-V1-02")
        basis = conv_cand["decision_basis"]
        assert "convergence_score" in basis and "state_delta_preview" in basis
        # 复算：与引擎 milestone_kw 构建口径一致（kms 空 + 新 producer 字段全捞）
        me = next(m for m in _ME_POOL if m["id"] == "ME-V1-02")
        me_kw = cee._keyword_set(cee._me_text(me))
        conv_text = " ".join(str(_VOLS[0].get(k, "")) for k in (
            "ending_state", "volume_core_conflict", "volume_thread", "volume_finale_signal"))
        milestone_kw = cee._keyword_set(" " + conv_text)
        expected, hits = et.convergence_subscore(me_kw, milestone_kw)
        assert expected > 0 and hits
        assert basis["convergence_score"] == expected
        # dim-6 加分确实进了总分（收敛理由在 rank_reasons 里·同源展示）
        assert any("大势收敛" in rr for rr in basis["rank_reasons"])

        unrelated = _candidate_by_me(em, "ME-V1-01")
        assert unrelated["decision_basis"]["convergence_score"] == 0
        # 排序未被新字段改变：收敛+涟漪呼应者仍是第一候选
        assert em["candidates"][0]["parent_me"] == "ME-V1-02"


# ───────────────────── 2. state_delta_preview（只读预演·不硬造数值） ─────────────────────

def test_state_delta_preview_lists_matched_rules_verbatim():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=_RULES)
        r = cee.emerge_next_cluster(root, "cluster_001")
        em = _read_emergence(r)
        preview = _candidate_by_me(em, "ME-V1-02")["decision_basis"]["state_delta_preview"]
        by_id = {m["rule_id"]: m for m in preview["matched_rules"]}
        # fate_event 按 ME id 命中 · minor_event 按候选文本（含「玄铁令」）命中
        assert "RR_FATE_V102" in by_id and by_id["RR_FATE_V102"]["matched_via"] == ["fate_event"]
        assert "RR_MINOR_XTL" in by_id and by_id["RR_MINOR_XTL"]["matched_via"] == ["minor_event"]
        # auto_tick 与候选无关 → 不进预览
        assert "RR_AUTO" not in by_id
        # 数值转述规则声明（delta=-10），不硬造 old/new
        effects = by_id["RR_FATE_V102"]["effects"]
        delta_eff = next(e for e in effects if e["op"] == "delta")
        assert delta_eff["target"] == "factions_state.立序殿.power"
        assert delta_eff["delta"] == -10
        assert not any(k in delta_eff for k in ("old", "new"))
        assert any(e["op"] == "narrative" for e in effects)


def test_state_delta_preview_no_match_and_no_rules_file():
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=_RULES)
        r = cee.emerge_next_cluster(root, "cluster_001")
        em = _read_emergence(r)
        # 买菜支线：无规则命中 → matched_rules 空 + note 说明（不静默）
        preview = _candidate_by_me(em, "ME-V1-01")["decision_basis"]["state_delta_preview"]
        assert preview["matched_rules"] == []
        assert "无规则命中" in preview.get("note", "")
    with tempfile.TemporaryDirectory() as d:
        # 无 涟漪规则.json → 显式 note，不崩不硬失败
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=None)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        preview = em["candidates"][0]["decision_basis"]["state_delta_preview"]
        assert preview["matched_rules"] == []
        assert "无涟漪规则" in preview.get("note", "")


# ───────────────────── 3. 确定性 + 不写库 ─────────────────────

def test_transparency_deterministic_same_input_same_output():
    """同一项目连跑两次 emerge → candidates（含 decision_basis 全量）与 dag_health 逐字节一致。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=_RULES)
        em1 = _read_emergence(cee.emerge_next_cluster(root, "cluster_001"))
        em2 = _read_emergence(cee.emerge_next_cluster(root, "cluster_001"))
        assert em1["candidates"] == em2["candidates"]
        assert em1["dag_health"] == em2["dag_health"]


def test_preview_does_not_write_any_db_file():
    """emerge（含 state_delta_preview 预演）跑前跑后：5 个数据库文件内容字节不变。"""
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), _ME_POOL, volumes=_VOLS, ripple_rules=_RULES,
                           world_state={"factions_state": {"立序殿": {"power": 60}}})
        before = _db_snapshot(root)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        after = _db_snapshot(root)
        assert before == after, "preview 必须只读——数据库文件被改动"
        # 世界状态数值原样（未被 preview 应用 delta）
        ws = json.loads((root / "_数据库" / "世界状态.json").read_text(encoding="utf-8"))
        assert ws["factions_state"]["立序殿"]["power"] == 60


# ───────────────────── 4. dag_health：环 / 悬挂 / 健康 ─────────────────────

def test_dag_health_detects_cycle_advisory_not_fatal(capsys):
    """A↔B 互为前置 = 永不可触发 → cycles 检出（规范形：最小 id 开头）+ stderr 显式报告；
    emerge 仍 ok（advisory 不硬失败），独立 ME 照常涌现。"""
    pool = [
        {"id": "ME-V1-01", "volume": 1, "title": "环A", "description": "甲事件",
         "prerequisites": ["ME-V1-02"], "status": "pending"},
        {"id": "ME-V1-02", "volume": 1, "title": "环B", "description": "乙事件",
         "prerequisites": ["ME-V1-01"], "status": "pending"},
        {"id": "ME-V1-03", "volume": 1, "title": "独立走向", "description": "丙事件", "status": "pending"},
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pool)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True, "dag 环是 advisory·绝不阻断涌现"
        em = _read_emergence(r)
        assert em["dag_health"]["cycles"] == [["ME-V1-01", "ME-V1-02"]]
        assert em["dag_health"]["dangling"] == []
        assert "[emergence][dag_health]" in capsys.readouterr().err
        # 环上 ME 被打分层 -100 剔除 → 独立 ME 是候选
        assert "ME-V1-03" in {c["parent_me"] for c in em["candidates"]}


def test_dag_health_detects_dangling_reference(capsys):
    pool = [
        {"id": "ME-V1-01", "volume": 1, "title": "断链", "description": "甲事件",
         "prerequisites": ["ME-GHOST"], "status": "pending"},
        {"id": "ME-V1-02", "volume": 1, "title": "正常", "description": "乙事件", "status": "pending"},
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pool)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        assert em["dag_health"]["cycles"] == []
        assert em["dag_health"]["dangling"] == [{"me": "ME-V1-01", "missing": ["ME-GHOST"]}]
        assert "[emergence][dag_health]" in capsys.readouterr().err


def test_dag_health_empty_when_pool_healthy(capsys):
    """合法链（B 前置 A）→ cycles/dangling 皆空·stderr 无 dag 报告。"""
    pool = [
        {"id": "ME-V1-01", "volume": 1, "title": "起点", "description": "甲事件", "status": "pending"},
        {"id": "ME-V1-02", "volume": 1, "title": "承接", "description": "乙事件",
         "prerequisites": ["ME-V1-01"], "status": "pending"},
    ]
    with tempfile.TemporaryDirectory() as d:
        root = _mk_project(Path(d), pool)
        r = cee.emerge_next_cluster(root, "cluster_001")
        assert r["ok"] is True
        em = _read_emergence(r)
        assert em["dag_health"] == {"cycles": [], "dangling": []}
        assert "[emergence][dag_health]" not in capsys.readouterr().err


def test_me_dag_health_unit_self_loop_and_parent_me_field():
    """单元：自环检出；parent_me 单值字段与 prerequisites 同口径（_resolve_parents 归一）。"""
    pool = [
        {"id": "ME-A", "prerequisites": ["ME-A"]},           # 自环
        {"id": "ME-B", "parent_me": "ME-MISSING"},           # parent_me 悬挂
    ]
    health = et.me_dag_health(pool, get_id=cee._event_id, get_parents=cee._resolve_parents)
    assert health["cycles"] == [["ME-A"]]
    assert health["dangling"] == [{"me": "ME-B", "missing": ["ME-MISSING"]}]
