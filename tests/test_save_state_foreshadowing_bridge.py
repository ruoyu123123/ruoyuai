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


def _read_fs(tmp: Path) -> dict:
    return json.loads((tmp / "_数据库" / "伏笔表.json").read_text(encoding="utf-8"))


def _empty_fs() -> dict:
    return {"promises": [], "deadlines": [], "pledges": [], "secrets": []}


# ═══════════════════════ 核心 bug：actions 非空时 paid 仍被桥接 ═══════════════════════

def test_paid_resolves_even_when_actions_nonempty():
    """[#6 核心] foreshadowing_actions 非空 + foreshadowing_paid 带 id →
    paid 仍被桥接 resolve（不再被门控短路）。复刻 cluster_005/ch12 真实场景。"""
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
                {"id": "fs_018", "description": "师姐按按钮兑现"},
                {"id": "fs_008", "description": "1985 锦旗半回收"}],
        })
        rc = ss.apply_changes(tmp, 12)
        assert rc == 0
        fs = _read_fs(tmp)
        pmap = {p["id"]: p for p in fs["promises"]}
        # 关键断言：paid 的两条 promise 被标 resolved（修复前停在 False）
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
            "foreshadowing_paid": [
                {"id": "fs_018", "description": "同一 fs · 应被去重不重复处理"}],
        })
        rc = ss.apply_changes(tmp, 12)
        assert rc == 0
        fs = _read_fs(tmp)
        # 只有一条 promise，resolved 一次（去重后 payoff 只命中一次，applied 不重复）
        assert len(fs["promises"]) == 1
        assert fs["promises"][0]["resolved"] is True
        applied = json.loads((tmp / "_数据库" / ".wal" / "第12章_applied.json")
                             .read_text(encoding="utf-8"))["applied"]
        assert applied.count("伏笔 payoff: fs_018") == 1  # 不重复


# ═══════════════════════ 向后兼容：无 actions 时 paid/planted 仍桥接 ═══════════════════════

def test_paid_bridged_when_no_actions():
    """foreshadowing_actions 缺省（v27 freestyle 默认）→ paid 照常桥接 resolve。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = _mk_project(Path(d), {
            "promises": [{"id": "fs_005", "setup_cluster": "cluster_002", "resolved": False}],
            "deadlines": [], "pledges": [], "secrets": [],
        }, None)
        _write_parsed(tmp, 10, {
            "foreshadowing_paid": [{"id": "fs_005", "description": "林若昭被派来监督"}],
        })
        rc = ss.apply_changes(tmp, 10)
        assert rc == 0
        assert _read_fs(tmp)["promises"][0]["resolved"] is True


# ═══════════════════════ 无 id 描述串 → warning（可见非静默断裂）═══════════════════════

def test_no_id_string_payloads_warn():
    """planted/paid 全是无 id 描述串 + 无显式 actions → 记 warning（不静默吞）。"""
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
        assert any("无 id 描述串" in w for w in applied["warnings"])
        # 无 id → 伏笔表不动
        assert _read_fs(tmp)["promises"] == []


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
