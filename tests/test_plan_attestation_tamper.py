# -*- coding: utf-8 -*-
"""plan_tracker HMAC attestation 伪造防御回归（第四轮 Workflow #4·2026-06-17 安全修复）。

漏洞：_load_plan 对 legacy（旧式纯 sha256·_compute_legacy_sha256 公开无密钥）/ unattested（无
_attestation）在 for_write 不拦 → 攻击者篡改 plan 后公开重算盖章 / 删 _attestation 即可绕过跳步防御。

修复（非对称硬化）：
  · for_write=True（step/end/abort 写路径）：legacy/unattested 同 tampered → raise PlanTamperedError
  · for_write=False（get_plan/监控读路径）：legacy 自动迁移·unattested 放行（tolerant·不破坏观测）
  · legit 老 plan 走 reattest（RAW load + 重盖 HMAC）恢复

零依赖范式（__main__ 自跑）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import plan_tracker as pt  # noqa: E402


def _mk_plan(td, kind="hmac"):
    """造 plan json·kind: hmac(合法 HMAC) / legacy(公开 sha256·forged) / none(删 attestation·unattested)。"""
    p = Path(td) / "plan.json"
    plan = {"id": "test1", "command": "x", "steps": [{"n": 1, "name": "s1"}]}
    if kind == "hmac":
        pt._attest(plan)
    elif kind == "legacy":
        plan[pt.ATTESTATION_KEY] = {"sha256": pt._compute_legacy_sha256(plan)}  # 公开无密钥盖章
    # none: 不盖 → unattested
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return p


def test_legacy_for_write_raises():
    """🔴 forged-legacy（公开 sha256 重盖）写路径 raise（堵无密钥伪造通道）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "legacy")
        try:
            pt._load_plan(p, for_write=True)
            assert False, "legacy for_write 必须 raise PlanTamperedError"
        except pt.PlanTamperedError:
            pass


def test_legacy_for_read_tolerant_auto_migrate():
    """legacy 读路径自动迁移 HMAC（tolerant·不破坏 get_plan/监控观测）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "legacy")
        pt._load_plan(p, for_write=False)  # 不 raise·自动迁移
        after = json.loads(p.read_text(encoding="utf-8"))
        assert pt.verify_attestation(after) == "ok"  # 迁移后变 HMAC ok


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


def test_legit_legacy_reattest_recovers_for_write():
    """🔴 legit 老 legacy plan → reattest（RAW load + 重盖 HMAC）后 for_write ok（恢复路径·锁非对称边界）。"""
    with tempfile.TemporaryDirectory() as td:
        p = _mk_plan(td, "legacy")
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
