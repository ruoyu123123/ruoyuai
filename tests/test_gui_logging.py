#!/usr/bin/env python3
"""GUI 全局日志测试（持久化文件 sink + 时间戳 + 脱敏 + 分类 + 测试旁路）。

zero-dep（不需 nicegui）。运行：python tests/test_gui_logging.py
"""
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core" / "scripts"))

import core.gui.state as gs  # noqa: E402


def _tmp():
    return Path(tempfile.mkdtemp(prefix="guilog_"))


def test_log_file_none_is_pure_memory():
    """log_file=None → 纯内存（dev 测试 zero-dep 逐字节零回归）。"""
    lb = gs.LogBuffer()
    lb.append("x")
    assert lb.log_file_path is None
    assert len(lb) == 1


def test_file_sink_writes_timestamped_classified():
    tmp = _tmp()
    try:
        lb = gs.LogBuffer(log_file=tmp)
        lb.append("[gui:page] 打开 写作台")
        lb.append("[orchestrator] ▶ step 3: cluster-quality")
        f = lb.log_file_path
        assert f and f.exists()
        txt = f.read_text(encoding="utf-8")
        assert "| INFO  | page " in txt, "页面行未分类 page"
        assert "| orch " in txt, "must_fix#2: orchestrator 命令行未分类 orch"
        # 时间戳前缀（ISO·含 T 和 :）
        assert txt.splitlines()[0][:4].isdigit() and "T" in txt.splitlines()[0][:20]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_file_sink_redacts_key_in_url():
    """must_fix#1：落盘前过 secrets_store.redact（持久化比内存更危险）。"""
    tmp = _tmp()
    try:
        lb = gs.LogBuffer(log_file=tmp)
        lb.append("❌ 异常 https://api.x/v1?key=AIzaSECRETXYZ boom")
        txt = lb.log_file_path.read_text(encoding="utf-8")
        assert "AIzaSECRETXYZ" not in txt, "key 原文落盘了！"
        assert "key=***" in txt
        assert "| ERROR | " in txt
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_make_log_buffer_test_bypass():
    """must_fix#3：pytest in sys.modules → 纯内存（不往 repo/logs 撒文件污染 git）。"""
    saved = sys.modules.get("pytest")
    sys.modules["pytest"] = type(sys)("pytest")
    try:
        lb = gs._make_log_buffer()
        assert lb.log_file_path is None, "测试态应纯内存"
    finally:
        if saved is None:
            sys.modules.pop("pytest", None)
        else:
            sys.modules["pytest"] = saved


def test_make_log_buffer_env_override(monkeypatch=None):
    """RUOYUAI_GUI_LOG_DIR 指定目录（非测试态）。"""
    import os
    tmp = _tmp()
    saved_pt = sys.modules.pop("pytest", None)
    saved_env = os.environ.get("RUOYUAI_GUI_LOG_DIR")
    os.environ["RUOYUAI_GUI_LOG_DIR"] = str(tmp)
    try:
        lb = gs._make_log_buffer()
        assert lb.log_file_path is not None
        assert str(tmp) in str(lb.log_file_path)
    finally:
        if saved_pt is not None:
            sys.modules["pytest"] = saved_pt
        if saved_env is None:
            os.environ.pop("RUOYUAI_GUI_LOG_DIR", None)
        else:
            os.environ["RUOYUAI_GUI_LOG_DIR"] = saved_env
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_file_sink_failure_degrades_gracefully():
    """文件不可写 → 退化纯内存·绝不抛（日志故障不拖垮流水线）。"""
    lb = gs.LogBuffer(log_file="/nonexistent_root_xyz/\x00bad")
    lb.append("still works")  # 不抛
    assert len(lb) == 1


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
