# -*- coding: utf-8 -*-
"""plan_tracker attestation 伪造防御回归。

_load_plan 对任何非 "ok" 状态（tampered / unattested）在写路径（step/end/abort 等
for_write=True）一律 raise PlanTamperedError。读路径（get_plan/监控 for_write=False）
只打印 stderr 警告，不阻断，也绝不把无效签名静默升级成 "ok"——读操作不是可用于洗白
伪造签名的旁路。唯一恢复路径是显式调用 reattest（RAW load + 重盖 HMAC）。

零依赖范式（__main__ 自跑）。
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import plan_tracker as pt  # noqa: E402


def _forge_keyless_sha256(plan: dict) -> str:
    """模拟无密钥攻击者：只用公开的规范化算法（_canonical_plan_bytes）算 SHA-256，
    不知道机器本地 HMAC 密钥——构造一个「看起来像」签名、实际未经 plan_tracker
    授权的伪造哈希。"""
    return hashlib.sha256(pt._canonical_plan_bytes(plan)).hexdigest()


def _mk_plan(td, kind="hmac"):
    """造 plan json·kind: hmac(合法 HMAC) / forged(无密钥伪造哈希) / none(删 attestation·unattested)。"""
    p = Path(td) / "plan.json"
    plan = {"id": "test1", "command": "x", "steps": [{"n": 1, "name": "s1"}]}
    if kind == "hmac":
        pt._attest(plan)
    elif kind == "forged":
        plan[pt.ATTESTATION_KEY] = {"sha256": _forge_keyless_sha256(plan)}
    # none: 不盖 → unattested
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return p


def test_forged_for_write_raises():
    """🔴 无密钥伪造签名写路径 raise（堵无密钥伪造通道）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "forged")
        try:
            pt._load_plan(p, for_write=True)
            assert False, "forged for_write 必须 raise PlanTamperedError"
        except pt.PlanTamperedError:
            pass


def test_forged_for_read_warns_without_autotrust():
    """🔴 无密钥伪造签名读路径不阻断，但也绝不静默升级成 ok（读一次不能洗白伪造签名·
    文件内容原样不动）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "forged")
        before = p.read_bytes()
        pt._load_plan(p, for_write=False)  # 不 raise
        after_bytes = p.read_bytes()
        assert after_bytes == before, "读路径不得改写磁盘上的 plan 文件"
        after = json.loads(after_bytes.decode("utf-8"))
        assert pt.verify_attestation(after) == "tampered"  # 仍是伪造，未被读路径信任


def test_unattested_for_write_raises():
    """🔴 删 _attestation（unattested）写路径 raise（堵删字段伪造通道）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "none")
        try:
            pt._load_plan(p, for_write=True)
            assert False, "unattested for_write 必须 raise PlanTamperedError"
        except pt.PlanTamperedError:
            pass


def test_unattested_for_read_allowed():
    """unattested 读路径放行（真 unattested 老 plan 观测兼容·不阻断）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "none")
        plan = pt._load_plan(p, for_write=False)  # 不 raise
        assert plan["id"] == "test1"


def test_hmac_for_write_ok_no_false_positive():
    """合法 HMAC plan 写路径正常通过（非对称硬化不误伤合法 plan）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "hmac")
        plan = pt._load_plan(p, for_write=True)  # 不 raise
        assert plan["id"] == "test1"


def test_reattest_recovers_forged_signature_for_write():
    """🔴 无密钥伪造签名的 plan 经 reattest（RAW load + 重盖 HMAC）后写路径变 ok（唯一恢复
    路径必须显式重盖章，不会被读路径悄悄代劳）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "forged")
        raw = json.loads(p.read_text(encoding="utf-8"))
        pt._save_plan(p, raw)  # 模拟 reattest：RAW load + 重盖 HMAC
        plan = pt._load_plan(p, for_write=True)  # reattest 后写路径 ok
        assert plan["id"] == "test1"


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
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
