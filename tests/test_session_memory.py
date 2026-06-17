"""session_memory.py 确定性单元测试（零依赖·标准库）。

钉死跨会话记忆模块的纯逻辑：
  - _safe_load / _safe_save 容错文件 IO（损坏/缺失 → None，永不抛）
  - start_session 建 marker（含必填字段）
  - end_session 状态机（无 marker → ok=False；有 marker → 落盘 + 清 marker）
  - recall 倒序 / 限量 / 跳损坏文件
  - prune 按 mtime 删除 + days 钳到 ≥1
  - _get_arg argv 解析（缺值/非整数 → default）
  - _build_summary 时长计算 + 来源失败降级（永不抛）

策略：把模块级常量 SESSIONS_DIR / MARKER_FILE 指到临时目录，
绝不触碰真实 core/claude-home/.sessions。重定向的是常量不是被测函数本身。
"""
import contextlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import session_memory as sm  # noqa: E402


@contextlib.contextmanager
def _tmp_sessions():
    """临时 .sessions 目录上下文：重定向 SESSIONS_DIR / MARKER_FILE，退出后还原。"""
    d = Path(tempfile.mkdtemp())
    sessions = d / ".sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    old_dir, old_marker = sm.SESSIONS_DIR, sm.MARKER_FILE
    sm.SESSIONS_DIR = sessions
    sm.MARKER_FILE = sessions / ".current_session"
    try:
        yield sessions
    finally:
        sm.SESSIONS_DIR = old_dir
        sm.MARKER_FILE = old_marker


@contextlib.contextmanager
def _patched_argv(argv):
    old = sys.argv
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = old


# ---------- _safe_load / _safe_save ----------

def test_safe_load_missing_and_corrupt():
    """缺失文件 → None；损坏 JSON → None（不抛）。"""
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        assert sm._safe_load(missing) is None

        corrupt = Path(d) / "bad.json"
        corrupt.write_text("{not valid json", encoding="utf-8")
        assert sm._safe_load(corrupt) is None


def test_safe_save_roundtrip():
    """写入后能原样读回（含中文，ensure_ascii=False）。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "x.json"  # 父目录不存在 → _safe_save 应自动建
        payload = {"k": "值", "n": 3, "lst": [1, 2]}
        assert sm._safe_save(p, payload) is True
        assert p.is_file()
        assert sm._safe_load(p) == payload


def test_safe_save_non_serializable_returns_false():
    """不可 JSON 序列化对象 → 返回 False，不抛。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.json"
        assert sm._safe_save(p, {"bad": {1, 2, 3}}) is False  # set 不可序列化


# ---------- start_session ----------

def test_start_session_creates_marker_with_required_fields():
    with _tmp_sessions():
        sid = sm.start_session()
        assert isinstance(sid, str) and len(sid) == len("20260617T123456")
        marker = sm._safe_load(sm.MARKER_FILE)
        assert marker is not None
        assert marker["session_id"] == sid
        assert marker["ended_at"] is None
        assert marker["summary"] is None
        assert isinstance(marker["started_at_unix"], int)
        assert marker["started_at_unix"] > 0


# ---------- end_session 状态机 ----------

def test_end_session_no_marker_returns_not_ok():
    """从未 start / 已 end → 无 marker → ok=False，不抛。"""
    with _tmp_sessions():
        res = sm.end_session()
        assert res["ok"] is False
        assert "reason" in res


def test_end_session_persists_and_removes_marker():
    """start → end：落盘 <sid>.json + 清 marker，summary 含三段。"""
    with _tmp_sessions() as sessions:
        sid = sm.start_session()
        res = sm.end_session()
        assert res["ok"] is True
        assert res["session_id"] == sid

        # marker 已被移除
        assert not sm.MARKER_FILE.exists()
        # session 文件已落盘
        target = sessions / f"{sid}.json"
        assert target.is_file()
        saved = sm._safe_load(target)
        assert saved["ended_at"] is not None
        assert saved["summary"] is not None
        for key in ("commits", "active_plans", "duration_min"):
            assert key in saved["summary"]

        # 再次 end → 已无 marker → ok=False（状态机幂等收尾）
        again = sm.end_session()
        assert again["ok"] is False


# ---------- _build_summary ----------

def test_build_summary_zero_start_is_empty():
    """started_at_unix=0（坏 marker）→ duration_min=0、空段，不抛。"""
    with _tmp_sessions():
        s = sm._build_summary(0)
        assert s["duration_min"] == 0
        assert s["commits"] == []
        assert isinstance(s["active_plans"], list)


def test_build_summary_duration_positive_for_past_start():
    """传一个过去时间戳 → duration_min > 0（时长由 now - start 计算）。"""
    with _tmp_sessions():
        past = sm._now_unix() - 600  # 10 分钟前
        s = sm._build_summary(past)
        assert s["duration_min"] >= 9.0  # ~10 分钟，留容差
        assert isinstance(s["commits"], list)
        assert isinstance(s["active_plans"], list)


# ---------- recall ----------

def _write_session(sessions: Path, sid: str, summary=None):
    sm._safe_save(sessions / f"{sid}.json", {
        "session_id": sid,
        "started_at": sid,
        "ended_at": sid,
        "summary": summary if summary is not None else {"commits": [], "active_plans": [], "duration_min": 1.0},
    })


def test_recall_empty_when_no_dir():
    """SESSIONS_DIR 不存在 → []（不抛）。"""
    with _tmp_sessions() as sessions:
        # 删掉目录以触发 not is_dir 分支
        import shutil
        shutil.rmtree(sessions)
        assert sm.recall() == []


def test_recall_orders_desc_and_limits():
    """倒序 + 限量 N（不含损坏文件时取最新 N 个）。"""
    with _tmp_sessions() as sessions:
        for sid in ["20260101T000000", "20260102T000000", "20260103T000000"]:
            _write_session(sessions, sid)
        out = sm.recall(n=2)
        ids = [o["session_id"] for o in out]
        # 文件名倒序后取前 2 → 最新两个
        assert ids == ["20260103T000000", "20260102T000000"]
        assert all("summary" in o for o in out)


def test_recall_skips_corrupt_within_slice():
    """损坏 JSON 落在 top-N 切片内 → 被 _safe_load 跳过（不崩，结果短少而非补位）。

    注意契约：recall 先按文件名倒序切 [:n]，再逐个 load；
    切片内的损坏文件被跳过后不会回填更早的有效文件。
    """
    with _tmp_sessions() as sessions:
        for sid in ["20260101T000000", "20260102T000000", "20260103T000000"]:
            _write_session(sessions, sid)
        # 最新的文件损坏，落在 n=2 的切片内
        (sessions / "20260104T000000.json").write_text("{broken", encoding="utf-8")
        # marker 文件不应进结果（被 glob *.json 之外 / name 过滤排除）
        (sessions / ".current_session").write_text("{}", encoding="utf-8")

        out = sm.recall(n=2)
        ids = [o["session_id"] for o in out]
        # 切片=[0104(坏), 0103]；0104 跳过 → 只剩 0103（不回填 0102）
        assert ids == ["20260103T000000"]
        # marker 绝不出现
        assert ".current_session" not in str(out)


def test_recall_n_clamped_to_at_least_one():
    """n<=0 → 被 max(1, n) 钳到至少返回 1 个。"""
    with _tmp_sessions() as sessions:
        for sid in ["20260101T000000", "20260102T000000"]:
            _write_session(sessions, sid)
        out = sm.recall(n=0)
        assert len(out) == 1
        assert out[0]["session_id"] == "20260102T000000"  # 最新那个


# ---------- prune ----------

def test_prune_no_dir_returns_zero():
    with _tmp_sessions() as sessions:
        import shutil
        shutil.rmtree(sessions)
        assert sm.prune() == 0


def test_prune_deletes_only_old_files():
    """旧文件（mtime 超阈值）被删，新文件保留，marker 不动。"""
    with _tmp_sessions() as sessions:
        old = sessions / "old.json"
        new = sessions / "new.json"
        old.write_text("{}", encoding="utf-8")
        new.write_text("{}", encoding="utf-8")
        marker = sessions / ".current_session"
        marker.write_text("{}", encoding="utf-8")

        # 把 old 的 mtime 推到 100 天前
        old_ts = (datetime.now() - timedelta(days=100)).timestamp()
        os.utime(old, (old_ts, old_ts))

        deleted = sm.prune(days=30)
        assert deleted == 1
        assert not old.exists()
        assert new.exists()
        assert marker.exists()  # marker 永不被 prune


def test_prune_days_clamped_to_at_least_one():
    """days<=0 → max(1, days) 钳到 1；7 天前的文件在 1 天阈下应被删。"""
    with _tmp_sessions() as sessions:
        f = sessions / "weekold.json"
        f.write_text("{}", encoding="utf-8")
        ts = (datetime.now() - timedelta(days=7)).timestamp()
        os.utime(f, (ts, ts))
        deleted = sm.prune(days=0)  # 钳到 1 天
        assert deleted == 1
        assert not f.exists()


# ---------- _get_arg ----------

def test_get_arg_parsing():
    """缺 flag → default；有整数 → int；非整数 → default；末尾无值 → default。"""
    with _patched_argv(["session_memory.py", "recall", "--n", "5"]):
        assert sm._get_arg("--n", sm.DEFAULT_RECALL_N) == 5
    with _patched_argv(["session_memory.py", "recall"]):
        assert sm._get_arg("--n", sm.DEFAULT_RECALL_N) == sm.DEFAULT_RECALL_N
    with _patched_argv(["session_memory.py", "recall", "--n", "abc"]):
        assert sm._get_arg("--n", sm.DEFAULT_RECALL_N) == sm.DEFAULT_RECALL_N
    with _patched_argv(["session_memory.py", "recall", "--n"]):  # 末尾无值
        assert sm._get_arg("--n", sm.DEFAULT_RECALL_N) == sm.DEFAULT_RECALL_N
