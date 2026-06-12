#!/usr/bin/env python3
"""secrets_store --probe CLI 测试（BYOK 出货验证 gate）——dev 模式子进程跑真 --probe。

--probe 用临时 service=ruoyuai-probe-<pid> 走真后端回环（探活的意义所在·用完即删），
绝不碰真 service=ruoyuai-gen-model；本测试对真 Credential Manager 只做只读 get 校验清理。
in-process 隔离测试走 MemKeyring（与 test_secrets_store.py 同纪律）。
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "core" / "scripts" / "secrets_store.py"

sys.path.insert(0, str(_ROOT / "core" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import keyring  # noqa: E402
import secrets_store as ss  # noqa: E402
from _keyring_mem import MemKeyring  # noqa: E402


def _run_probe():
    """Popen（非 run）跑 --probe——需要子进程 pid 来反查临时 service 名。"""
    proc = subprocess.Popen([sys.executable, str(_SCRIPT), "--probe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ROOT))
    pid = proc.pid
    out, err = proc.communicate(timeout=60)
    return (proc.returncode, out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"), pid)


def test_probe_json_shape_and_exit_contract():
    rc, out, err, _pid = _run_probe()
    data = json.loads(out)                       # stdout 必须是合法 JSON
    assert set(data.keys()) == {"available", "roundtrip_ok"}, data
    assert isinstance(data["available"], bool), data
    assert isinstance(data["roundtrip_ok"], bool), data
    # 退出码契约：available+roundtrip_ok 全真 → 0，任一假 → 非零
    if data["available"] and data["roundtrip_ok"]:
        assert rc == 0, f"rc={rc} out={out!r}"
    else:
        assert rc != 0, f"rc={rc} out={out!r}"
    assert "Traceback" not in err, err[-300:]


def test_probe_cleans_up_temp_service():
    """probe 子进程退出后，其临时 service=ruoyuai-probe-<子进程pid> 必须已清空（只读 get）。"""
    _rc, out, _err, pid = _run_probe()
    data = json.loads(out)
    if not data["available"]:
        return                                   # 本机无可用后端 → 没有回环可清理（环境豁免）
    assert keyring.get_password(f"ruoyuai-probe-{pid}", ss._PROBE_USERNAME) is None


def test_probe_inprocess_never_touches_real_service():
    """MemKeyring 隔离验证：probe() 全程不读不写真 SERVICE=ruoyuai-gen-model 命名空间。"""
    orig = keyring.get_keyring()
    mem = MemKeyring()
    keyring.set_keyring(mem)
    try:
        mem.set_password(ss.SERVICE, "p1", "sk-REALUSERKEY999")   # 模拟用户已存的真 key
        result = ss.probe()
        assert result == {"available": True, "roundtrip_ok": True}, result
        # 真 service 的 key 原封不动·临时 service 全部清空
        assert mem.get_password(ss.SERVICE, "p1") == "sk-REALUSERKEY999"
        leftovers = [k for k in mem._s if k[0].startswith("ruoyuai-probe-")]
        assert leftovers == [], f"临时 service 未清理: {leftovers}"
    finally:
        keyring.set_keyring(orig)


def test_probe_unavailable_backend_reports_false():
    """keyring 不可用（None）→ {available:false, roundtrip_ok:false}·不抛。"""
    orig = ss._keyring
    ss._keyring = None
    try:
        assert ss.probe() == {"available": False, "roundtrip_ok": False}
    finally:
        ss._keyring = orig


def test_no_args_prints_help_exit_2():
    r = subprocess.run([sys.executable, str(_SCRIPT)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(_ROOT), timeout=60)
    assert r.returncode == 2, f"rc={r.returncode}"
    assert "--probe" in r.stderr                 # help 走 stderr·stdout 留给 JSON


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
