"""save_state.apply_changes 伏笔兑现桥接回归测试 — [#6 门控短路修复]。

【根因】save_state.py 的 A-1 桥接（2026-05-30）把 writer 实产的 foreshadowing_paid/
planted 转成 action 喂伏笔表 resolve 逻辑，但整段被 `if not _fs_actions:` 门控——只当
foreshadowing_actions 为空才执行。真实项目「诡异接待处·第七窗口」cluster_005（ch12）同时有
foreshadowing_actions（sc_003 secret reveal）+ foreshadowing_paid（fs_018/fs_008/...）→
桥接被整体短路 → writer 明确申报兑现的伏笔永不标 resolved · 伏笔表停在 planted ·
下游 SECRET_NOT_REVEALED / FORESHADOWING_NOT_PAID 误判风险。

【修复】桥接不被任意 foreshadowing_actions 项整体短路：既处理显式 foreshadowing_actions，
也始终把 foreshadowing_paid/planted 桥接进 resolve，按 (category, type, id) 去重。

北极星边界：纯数据流回写（writer 申报什么就落什么），不碰创作判断（顾问非法官）。
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import save_state as ss  # noqa: E402


# ═══════════════════════ 脚手架 ═══════════════════════

def _mk_project(tmp: Path, fs: dict, changes: dict) -> Path:
    """建一个最小项目：写 伏笔表.json + 第ch章_parsed.json，供 apply_changes 消费。"""
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "伏笔表.json").write_text(json.dumps(fs, ensure_ascii=False), encoding="utf-8")
    return tmp


def _write_parsed(tmp: Path, ch: int, changes: dict) -> None:
    parsed_path = tmp / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    parsed_path.write_text(json.dumps({"chapter": ch, "changes": changes},
                                      ensure_ascii=False), encoding="utf-8")


def _write_foreshadower(tmp: Path, ch: int, payoff_scores: list) -> None:
    """🔴 2026-06-27 SYS-2/SYS-3：写本 cluster foreshadower JudgeReport（payoff_scores 带
    terminal/score）到 _resolve_cluster 解析的真实路径，供 apply_changes._load_foreshadower_maps 读。"""
    cid, _ = ss._resolve_cluster(tmp, ch)
    rpt_dir = tmp / "_数据库" / ".judge_reports"
    rpt_dir.mkdir(parents=True, exist_ok=True)
    (rpt_dir / f"{cid}_foreshadower.json").write_text(
        json.dumps({"judge_id": "foreshadower", "cluster_id": cid,
                    "specific_findings": {"payoff_scores": payoff_scores}}, ensure_ascii=False),
        encoding="utf-8")


def _read_fs(tmp: Path) -> dict:
    return json.loads((tmp / "_数据库" / "伏笔表.json").read_text(encoding="utf-8"))


def _empty_fs() -> dict:
    return {"promises": [], "deadlines": [], "pledges": [], "secrets": []}


# ═══════════════════════ 核心 bug：actions 非空时 paid 仍被桥接 ═══════════════════════

def test_paid_resolves_even_when_actions_nonempty():
    """[#6 核心] foreshadowing_actions 非空 + foreshadowing_paid 带 id →
    paid 仍被桥接 resolve（不再被门控短路）。复刻 cluster_005/ch12 真实场景。

    🔴 2026-06-27 SYS-2 迁移：terminal payoff（核心承诺兑现）才标 resolved。两条 paid 显式
    kind:"terminal"（按钮兑现 / 锦旗回收 = 核心承诺彻底兑现）→ resolved=True（不再无差别标）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [
                {"id": "fs_018", "setup_cluster": "cluster_004", "resolved": False},
                {"id": "fs_008", "setup_cluster": "cluster_004", "resolved": False},
            ],
            "deadlines": [], "pledges": [],
            # 显式 action 处理的 secret（与 paid 项不重叠）
            "secrets": [{"id": "fs_003", "status": "hidden", "established_cluster": "cluster_003"}],
        }, None)
        _write_parsed(tmp, 12, {
            # 与 cluster_005/ch12 同型：actions 处理 secret reveal，paid 是 promise 兑现
            "foreshadowing_actions": [
                {"category": "secret", "type": "reveal", "id": "fs_003",
                 "how": "1985 照片揭示", "reveal_scene": 5}],
            "foreshadowing_paid": [
                {"id": "fs_018", "kind": "terminal", "description": "师姐按按钮兑现"},
                {"id": "fs_008", "kind": "terminal", "description": "1985 锦旗回收"}],
        })
        rc = ss.apply_changes(tmp, 12)
        assert rc == 0
        fs = _read_fs(tmp)
        pmap = {p["id"]: p for p in fs["promises"]}
        # 关键断言：terminal paid 的两条 promise 被标 resolved（修复前停在 False）
        assert pmap["fs_018"]["resolved"] is True
        assert pmap["fs_018"]["resolved_at_ch"] == 12
        assert pmap["fs_008"]["resolved"] is True
        # 显式 action 的 secret reveal 仍生效
        assert fs["secrets"][0]["status"] == "revealed"


def test_planted_bridged_when_actions_nonempty():
    """actions 非空时 planted（带 id）仍被桥接成 setup → 新建 promise 记录。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs(), None)
        _write_parsed(tmp, 12, {
            "foreshadowing_actions": [
                {"category": "secret", "type": "reveal", "id": "sc_999",
                 "how": "x"}],  # 引用不存在 secret → 不影响 planted 桥接
            "foreshadowing_planted": [
                {"id": "fs_019", "tier": 2, "description": "审查员 3 人组合"}],
            "secrets": [],
        })
        # secrets 列表空，reveal 找不到只是 no-op；planted 应仍建 promise
        rc = ss.apply_changes(tmp, 12)
        assert rc == 0
        fs = _read_fs(tmp)
        pmap = {p["id"]: p for p in fs["promises"]}
        assert "fs_019" in pmap
        assert pmap["fs_019"]["resolved"] is False
        assert pmap["fs_019"]["tier"] == 2


# ═══════════════════════ 去重：显式 action 已覆盖同一 fs → 不重复 ═══════════════════════

def test_dedup_explicit_action_covers_paid():
    """显式 foreshadowing_actions 已有 (promise, payoff, fs_018) → paid 里同 fs 不重复构造。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_018", "setup_cluster": "cluster_004", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_parsed(tmp, 12, {
            "foreshadowing_actions": [
                {"category": "promise", "type": "payoff", "id": "fs_018"}],
            # 🔴 2026-06-27 SYS-2：paid 带 kind:"terminal" → writer_kind_map 供 dedup 后的显式 action 读
            "foreshadowing_paid": [
                {"id": "fs_018", "kind": "terminal", "description": "同一 fs · 应被去重不重复处理"}],
        })
        rc = ss.apply_changes(tmp, 12)
        assert rc == 0
        fs = _read_fs(tmp)
        # 只有一条 promise，resolved 一次（去重后 payoff 只命中一次，applied 不重复）
        assert len(fs["promises"]) == 1
        assert fs["promises"][0]["resolved"] is True
        applied = json.loads((tmp / "_数据库" / ".wal" / "第12章_applied.json")
                             .read_text(encoding="utf-8"))["applied"]
        assert applied.count("伏笔 payoff(terminal): fs_018") == 1  # 不重复


# ═══════════════════════ 向后兼容：无 actions 时 paid/planted 仍桥接 ═══════════════════════

def test_paid_terminal_bridged_when_no_actions():
    """foreshadowing_actions 缺省（v27 freestyle 默认）→ terminal paid 照常桥接 resolve。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_005", "setup_cluster": "cluster_002", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_parsed(tmp, 10, {
            "foreshadowing_paid": [{"id": "fs_005", "kind": "terminal",
                                    "description": "林若昭身世真相揭晓·核心承诺兑现"}],
        })
        rc = ss.apply_changes(tmp, 10)
        assert rc == 0
        assert _read_fs(tmp)["promises"][0]["resolved"] is True


def test_paid_progressive_keeps_open_records_progress():
    """🔴 2026-06-27 SYS-2 迁移：progressive paid（推进/扩散/阶段数值）→ 不标 resolved，
    记 payoff_progress 保持 open。治『918 逐章 re-apply 把每条 paid 无差别盖 resolved』根因。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_005", "setup_cluster": "cluster_002", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_parsed(tmp, 10, {
            # 无 kind + 词面含「推进」→ 词面默认 progressive
            "foreshadowing_paid": [{"id": "fs_005", "description": "林若昭被派来监督（阶段推进）"}],
        })
        rc = ss.apply_changes(tmp, 10)
        assert rc == 0
        p = _read_fs(tmp)["promises"][0]
        assert p["resolved"] is False               # progressive 不标 resolved
        assert p["last_advanced_at_ch"] == 10
        assert any(e.get("ch") == 10 for e in p.get("payoff_progress", []))
        # re-apply 幂等：payoff_progress 同 ch 不重复 append（(fs_id,ch) 去重）
        ss.apply_changes(tmp, 10)
        p2 = _read_fs(tmp)["promises"][0]
        assert len([e for e in p2.get("payoff_progress", []) if e.get("ch") == 10]) == 1


# ═══════════════════════ 无 id 描述串 → warning（可见非静默断裂）═══════════════════════

def test_no_id_string_payloads_auto_assign():
    """🔴 2026-06-28：planted 无 id 描述串 → 自动派 fs_auto_<hash> 记入伏笔表（数据不丢·治死路径）。

    旧行为是 warn+丢弃（test_no_id_string_payloads_warn）→ cluster_001 实测 3 伏笔全丢、伏笔表恒空、
    后续无从回收。改为自动派确定性 id 入 promises。paid 无 id 无对应 fs → 仍无法兑现（不凭空建）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs(), None)
        _write_parsed(tmp, 10, {
            "foreshadowing_paid": ["fs_005：纯描述串没有结构化 id"],
            "foreshadowing_planted": ["fs_012：另一条纯描述串"],
        })
        rc = ss.apply_changes(tmp, 10)
        assert rc == 0
        applied = json.loads((tmp / "_数据库" / ".wal" / "第10章_applied.json")
                             .read_text(encoding="utf-8"))
        # planted 无 id → 自动派 fs_auto_ 记入伏笔表（不再静默丢）
        promises = _read_fs(tmp)["promises"]
        assert len(promises) == 1, f"应自动派 1 条 promise, 实际 {promises}"
        assert promises[0]["id"].startswith("fs_auto_")
        assert "另一条纯描述串" in promises[0]["description"]
        assert not promises[0]["resolved"]
        # 警告提示自动派（透明·非静默）
        assert any("自动派" in w or "fs_auto" in w for w in applied["warnings"])


def test_no_id_auto_assign_idempotent_on_reapply():
    """🔴 2026-06-28：同 desc 重复 apply（split 平铺多章/re-apply）→ 确定性 fs_auto id 去重不重复建。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs(), None)
        _write_parsed(tmp, 10, {"foreshadowing_planted": ["同一条无 id 伏笔描述"]})
        ss.apply_changes(tmp, 10)
        ss.apply_changes(tmp, 10)  # 再 apply 一次
        promises = _read_fs(tmp)["promises"]
        assert len(promises) == 1, f"re-apply 应幂等去重, 实际 {len(promises)}"


def test_no_warn_when_actions_present_even_if_strings():
    """有显式 foreshadowing_actions 时，纯描述串的 paid/planted 不再误报 warning。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [], "deadlines": [], "pledges": [],
            "secrets": [{"id": "sc_002", "status": "hidden", "established_cluster": "cluster_002"}],
        }, None)
        _write_parsed(tmp, 10, {
            "foreshadowing_actions": [
                {"category": "secret", "type": "reveal", "id": "sc_002"}],
            "foreshadowing_paid": ["fs_005：纯描述串"],  # 无 id，但有显式 actions 兜底
        })
        rc = ss.apply_changes(tmp, 10)
        assert rc == 0
        applied = json.loads((tmp / "_数据库" / ".wal" / "第10章_applied.json")
                             .read_text(encoding="utf-8"))
        # 有显式 actions → 不报「无 id 描述串」warning（结构化路径已生效）
        assert not any("无 id 描述串" in w for w in applied["warnings"])
        assert _read_fs(tmp)["secrets"][0]["status"] == "revealed"


# ═══════════ 🔴 2026-06-27 SYS-2/SYS-3 金标准回放 + 优先级/门控 ═══════════

def test_golden_replay_cluster004_terminal_progressive_split():
    """SYS-2 金标准回放（cluster_004 型 changes+foreshadower）：foreshadower.terminal 权威分流——
      · fs_006「登记册成立」terminal:true → resolved=True（terminal 覆盖词面默认 progressive）
      · fs_010 tier1 finale 仅推进 terminal:false → resolved=False（保持 open + payoff_progress）
      · fs_007「×N」数值扩散 terminal:false → resolved=False（保持 open + payoff_progress）"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [
                {"id": "fs_006", "setup_cluster": "cluster_003", "resolved": False},
                {"id": "fs_007", "setup_cluster": "cluster_003", "resolved": False, "tier": 1},
                {"id": "fs_010", "setup_cluster": "cluster_002", "resolved": False, "tier": 1},
            ],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_foreshadower(tmp, 14, [
            {"fs_id": "fs_006", "score": 5, "terminal": True, "reason": "国运继承登记册正式成立"},
            {"fs_id": "fs_007", "score": 5, "terminal": False, "reason": "关联标的扩散 ×5→×7"},
            {"fs_id": "fs_010", "score": 5, "terminal": False, "reason": "渗透率推进·vol1 finale 仍远"},
        ])
        _write_parsed(tmp, 14, {
            "foreshadowing_paid": [
                # fs_006 词面「成立」无 terminal 关键词（词面默认 progressive）→ 靠 foreshadower.terminal 翻成 terminal
                {"id": "fs_006", "description": "国运继承登记册成立"},
                {"id": "fs_007", "description": "关联标的扩散（×5 → ×7）"},
                {"id": "fs_010", "description": "主体推进（渗透率 22.7% → 26.8%）"},
            ],
        })
        rc = ss.apply_changes(tmp, 14)
        assert rc == 0
        pmap = {p["id"]: p for p in _read_fs(tmp)["promises"]}
        assert pmap["fs_006"]["resolved"] is True       # terminal:true → resolved
        assert pmap["fs_010"]["resolved"] is False      # tier1 finale 仅推进 → 保持 open
        assert pmap["fs_007"]["resolved"] is False      # 数值扩散 → 保持 open
        assert any(e.get("ch") == 14 for e in pmap["fs_010"].get("payoff_progress", []))
        assert any(e.get("ch") == 14 for e in pmap["fs_007"].get("payoff_progress", []))


def test_score_zero_gate_keeps_unresolved():
    """SYS-3：foreshadower 判 score==0（声明 paid 但正文 0 痕迹/谎报）→ 即使 kind/词面像
    terminal 也不标 resolved（治『声明 paid 正文 0 落字 lie=0』）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_004", "setup_cluster": "cluster_003", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_foreshadower(tmp, 14, [
            {"fs_id": "fs_004", "score": 0, "terminal": True,
             "reason": "声明剑胚兑现但正文 0 出现剑胚/草籽 → 谎报"}])
        _write_parsed(tmp, 14, {
            "foreshadowing_paid": [{"id": "fs_004", "kind": "terminal",
                                    "description": "青青剑胚裂缝兑现"}],
        })
        rc = ss.apply_changes(tmp, 14)
        assert rc == 0
        p = _read_fs(tmp)["promises"][0]
        assert p["resolved"] is False  # score==0 门控压过 terminal
        applied = json.loads((tmp / "_数据库" / ".wal" / "第14章_applied.json")
                             .read_text(encoding="utf-8"))
        assert any("score=0" in w for w in applied["warnings"])


def test_word_surface_default_no_foreshadower_no_kind():
    """无 foreshadower 报告 + 无 writer kind → 纯词面默认：含「回收/兑现/揭晓/收束」→ terminal；
    含「推进/扩散/进展/数值」或两者皆无 → 保守 progressive。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [
                {"id": "fs_a", "setup_cluster": "cluster_001", "resolved": False},
                {"id": "fs_b", "setup_cluster": "cluster_001", "resolved": False},
                {"id": "fs_c", "setup_cluster": "cluster_001", "resolved": False},
            ],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_parsed(tmp, 5, {
            "foreshadowing_paid": [
                {"id": "fs_a", "description": "悬念最终揭晓收束"},   # terminal 词 → resolved
                {"id": "fs_b", "description": "势力扩散数值推进"},   # progressive 词 → open
                {"id": "fs_c", "description": "某人来了又走"},        # 皆无 → 保守 progressive
            ],
        })
        rc = ss.apply_changes(tmp, 5)
        assert rc == 0
        pmap = {p["id"]: p for p in _read_fs(tmp)["promises"]}
        assert pmap["fs_a"]["resolved"] is True
        assert pmap["fs_b"]["resolved"] is False
        assert pmap["fs_c"]["resolved"] is False
