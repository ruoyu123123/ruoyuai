"""save_state 伏笔回库新契约回归测试 — 🔴 2026-06-28 审计清理B类。

【契约变更】原本组测「writer 自报 foreshadowing_planted/paid → 伏笔表 promises/resolved 桥接」
（A-1 / [#6] / SYS-2 / fs_auto 那一整套）。审计判该桥接为 B 类违规——消费侧读 writer 自报
factual 当权威状态源。**已从 save_state.apply_changes 整段移除**。

新契约（本文件钉死）：
  1. writer 的 changes.foreshadowing_planted / foreshadowing_paid / foreshadowing_actions
     **不再触碰伏笔表**（apply_changes 跑通但 promises/secrets/resolved 一概不动）。
  2. 伏笔注册走 Claude 权威路径 `_register_brief_foreshadowings`：读
     事件簇.clusters[].foreshadowing_to_plant（outline-planner 规划·带 fs_id）→ 注册 promises。
  3. 伏笔兑现走 Claude 权威路径 `_apply_foreshadower_payoffs`：读 foreshadower JudgeReport 的
     payoff_scores（terminal→resolved / progressive→payoff_progress / score==0 门控不 resolved）。

北极星边界：save_state 不再当 writer 自报状态的回库者；factual 状态由 archive（apply_archive.py）
与 foreshadower/brief（Claude 读正文/读规划）回库。
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

def _mk_project(tmp: Path, fs: dict, clusters: dict | None = None) -> Path:
    """建一个最小项目：写 伏笔表.json + 事件簇.json（含 chapter_range，供 cluster 反查）。"""
    db = tmp / "_数据库"
    (db / ".wal").mkdir(parents=True, exist_ok=True)
    (db / "伏笔表.json").write_text(json.dumps(fs, ensure_ascii=False), encoding="utf-8")
    if clusters is None:
        clusters = {"clusters": [{"cluster_id": "cluster_001", "chapter_range": [1, 3]}]}
    (db / "事件簇.json").write_text(json.dumps(clusters, ensure_ascii=False), encoding="utf-8")
    return tmp


def _write_parsed(tmp: Path, ch: int, changes: dict) -> None:
    parsed_path = tmp / "_数据库" / ".wal" / f"第{ch}章_parsed.json"
    parsed_path.write_text(json.dumps({"chapter": ch, "changes": changes},
                                      ensure_ascii=False), encoding="utf-8")


def _write_foreshadower(tmp: Path, cid: str, payoff_scores: list) -> None:
    """写 cluster 级 foreshadower JudgeReport（payoff_scores 带 verdict/terminal/score）到
    _apply_foreshadower_payoffs 读取的路径 _数据库/.judge_reports/<cid>_foreshadower.json。"""
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


# ═══════════ 1. writer 自报 foreshadowing 不再回库伏笔表（B 类清理核心） ═══════════

def test_writer_paid_does_not_resolve_promise():
    """writer changes.foreshadowing_paid（即便带 id + kind:terminal）→ apply_changes 不再把
    对应 promise 标 resolved（writer 自报 factual 不回库）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_018", "setup_cluster": "cluster_001", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        _write_parsed(tmp, 2, {
            "foreshadowing_paid": [{"id": "fs_018", "kind": "terminal", "description": "按钮兑现"}],
        })
        rc = ss.apply_changes(tmp, 2)
        assert rc == 0
        # 新契约：writer 自报 paid 不再触碰伏笔表 → 仍 unresolved
        assert _read_fs(tmp)["promises"][0]["resolved"] is False


def test_writer_planted_does_not_create_promise():
    """writer changes.foreshadowing_planted（带 id 或纯描述串）→ 不再新建 promise（含旧 fs_auto 派 id）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        _write_parsed(tmp, 2, {
            "foreshadowing_planted": [
                {"id": "fs_019", "tier": 2, "description": "审查员 3 人组合"},
                "另一条纯描述串没有结构化 id",
            ],
        })
        rc = ss.apply_changes(tmp, 2)
        assert rc == 0
        # 新契约：planted 不回库（既不建带 id 的，也不再自动派 fs_auto_<hash>）
        assert _read_fs(tmp)["promises"] == []


def test_writer_foreshadowing_actions_secret_reveal_no_longer_applied():
    """writer changes.foreshadowing_actions（secret reveal）→ 不再改 secret.status。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [], "deadlines": [], "pledges": [],
            "secrets": [{"id": "fs_003", "status": "hidden", "established_cluster": "cluster_001"}],
        })
        _write_parsed(tmp, 2, {
            "foreshadowing_actions": [
                {"category": "secret", "type": "reveal", "id": "fs_003", "how": "照片揭示"}],
        })
        rc = ss.apply_changes(tmp, 2)
        assert rc == 0
        # 新契约：secret reveal 不再由 writer 自报落地 → 仍 hidden
        assert _read_fs(tmp)["secrets"][0]["status"] == "hidden"


def test_apply_changes_still_runs_kept_paths():
    """删 writer-factual 回库后 apply_changes 仍跑通：time_advance 落 time_log + summary 写盘。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs())
        (tmp / "_数据库" / "时间线.json").write_text(
            json.dumps({"current_time": {"period": "黎明", "chapter": 1}, "time_log": []},
                       ensure_ascii=False), encoding="utf-8")
        _write_parsed(tmp, 2, {
            "time_advance": {"period": "正午", "elapsed": "半日", "key_events": ["渡江"]},
            "foreshadowing_paid": [{"id": "fs_x", "kind": "terminal", "description": "x"}],  # 被忽略
        })
        rc = ss.apply_changes(tmp, 2)
        assert rc == 0
        tl = json.loads((tmp / "_数据库" / "时间线.json").read_text(encoding="utf-8"))
        assert tl["time_log"][-1]["ch"] == 2
        assert (tmp / "_数据库" / ".wal" / "第2章_applied.json").is_file()


# ═══════════ 2. Claude 权威路径：_register_brief_foreshadowings（读 outline brief） ═══════════

def test_register_brief_foreshadowings_from_cluster_brief():
    """事件簇.clusters[].foreshadowing_to_plant（带 fs_id）→ 注册进伏笔表 promises（_source=brief）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), _empty_fs(), clusters={"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3],
             "foreshadowing_to_plant": [
                 {"id": "FS_014", "tier": 2, "desc": "墙上的旧照片"},
                 {"fs_id": "FS_015", "description": "瘸腿男孩的失踪"},
             ]},
        ]})
        ss._register_brief_foreshadowings(tmp, "cluster_001")
        pmap = {p["id"]: p for p in _read_fs(tmp)["promises"]}
        assert "FS_014" in pmap and "FS_015" in pmap
        assert pmap["FS_014"]["_source"] == "brief"
        assert pmap["FS_014"]["resolved"] is False
        assert pmap["FS_014"]["setup_cluster"] == "cluster_001"


def test_register_brief_idempotent_and_no_overwrite():
    """重复注册不重复建；已存在（含已 resolved）的 fs_id 不被覆盖。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "FS_014", "resolved": True, "_source": "manual"}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, clusters={"clusters": [
            {"cluster_id": "cluster_001", "chapter_range": [1, 3],
             "foreshadowing_to_plant": [{"id": "FS_014", "desc": "x"}, {"id": "FS_016", "desc": "y"}]},
        ]})
        ss._register_brief_foreshadowings(tmp, "cluster_001")
        ss._register_brief_foreshadowings(tmp, "cluster_001")  # 再跑一次
        promises = _read_fs(tmp)["promises"]
        ids = [p["id"] for p in promises]
        assert ids.count("FS_014") == 1 and ids.count("FS_016") == 1
        # 已存在的 FS_014 未被覆盖（仍 resolved=True·_source=manual）
        fs014 = next(p for p in promises if p["id"] == "FS_014")
        assert fs014["resolved"] is True and fs014["_source"] == "manual"


# ═══════════ 3. Claude 权威路径：_apply_foreshadower_payoffs（读 foreshadower JudgeReport） ═══════════

def test_foreshadower_terminal_resolves_promise():
    """foreshadower payoff_scores: terminal:true + score>0 → promise 标 resolved（_resolved_by=foreshadower）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_006", "setup_cluster": "cluster_001", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_006", "score": 5, "terminal": True, "verdict": "paid_terminal",
             "reason": "登记册正式成立"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p = _read_fs(tmp)["promises"][0]
        assert p["resolved"] is True
        assert p["_resolved_by"] == "foreshadower"
        assert p["resolved_at_ch"] == 3  # cluster_001 末章


def test_foreshadower_progressive_keeps_open():
    """foreshadower verdict=paid_progressive / terminal:false → 不 resolved，记 payoff_progress(cid)。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_007", "setup_cluster": "cluster_001", "resolved": False, "tier": 1}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_007", "score": 5, "terminal": False, "verdict": "paid_progressive",
             "reason": "关联标的扩散 ×5→×7"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p = _read_fs(tmp)["promises"][0]
        assert p["resolved"] is False
        assert "cluster_001" in p.get("payoff_progress", [])
        # 幂等：再跑不重复 append
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        p2 = _read_fs(tmp)["promises"][0]
        assert p2["payoff_progress"].count("cluster_001") == 1


def test_foreshadower_score_zero_does_not_resolve():
    """score==0（声明 paid 但正文 0 痕迹/谎报）→ 即便 terminal 也不 resolved（门控）。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_004", "setup_cluster": "cluster_001", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        _write_foreshadower(tmp, "cluster_001", [
            {"fs_id": "fs_004", "score": 0, "terminal": True, "verdict": "paid_terminal",
             "reason": "声明兑现但正文 0 痕迹"}])
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")
        assert _read_fs(tmp)["promises"][0]["resolved"] is False


def test_foreshadower_report_missing_is_noop():
    """foreshadower 报告不存在（apply 前调用是常态）→ 静默跳过·不崩·不改伏笔表。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_009", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        })
        ss._apply_foreshadower_payoffs(tmp, "cluster_001")  # 无报告
        assert _read_fs(tmp)["promises"][0]["resolved"] is False


if __name__ == "__main__":
    import traceback
    g = dict(globals())
    fails = 0
    for n in sorted(g):
        if n.startswith("test_"):
            try:
                g[n]()
                print("OK", n)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", n, e)
                traceback.print_exc()
    sys.exit(1 if fails else 0)
