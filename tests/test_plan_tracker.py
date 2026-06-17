#!/usr/bin/env python3
"""plan_tracker 确定性单测（2026-06-17 · /loop 自主硬化补漏）。

batch2 workflow 的 plan_tracker author agent 撞瞬时 API 断连未落盘，手工补齐。
plan_tracker 是 Plan 强制规划层 L2 的安全核心（被 orchestrator/e2e 间接跑·无专属测试）。
本测试锁住 **HMAC attestation 防篡改**（memory project_cluster_lookup_keystone 记录的修复点）
+ 占位符替换 + plan 生命周期往返——这些是被间接覆盖掩盖的安全/正确性不变量。

attestation 安全模型（plan_tracker.py:190-269）：
- _attest 用 HMAC-SHA256(机器本地密钥, 规范化JSON) 盖章。
- verify_attestation → ok（HMAC符）/ legacy（旧式无密钥sha256符·向后兼容）/
  tampered（都不符=真篡改）/ unattested（无章）。
- 关键安全性质：没有机器密钥**无法伪造**有效 HMAC → 篡改内容必被抓。
"""
import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "scripts"))

import plan_tracker as pt  # noqa: E402

AK = pt.ATTESTATION_KEY


@contextmanager
def _fixed_key(key_bytes: bytes):
    """临时把 HMAC 密钥钉死成已知值（控制 attestation 可复现）·结束还原缓存。"""
    saved = pt._ATTEST_KEY_CACHE
    pt._ATTEST_KEY_CACHE = key_bytes
    try:
        yield
    finally:
        pt._ATTEST_KEY_CACHE = saved


def _sample_plan():
    return {"id": "p1", "command": "cluster-write", "project": "书",
            "steps": [{"n": 1, "name": "a", "status": "pending"},
                      {"n": 2, "name": "b", "status": "pending"}]}


# ════════════════════════════════════════════════════════════════
# attestation 防篡改安全核心
# ════════════════════════════════════════════════════════════════
def test_attestation_roundtrip_ok():
    """_attest 盖章 → verify_attestation 返回 ok（同一密钥）。"""
    with _fixed_key(b"K" * 32):
        plan = _sample_plan()
        pt._attest(plan)
        assert AK in plan and plan[AK]["sha256"]
        assert pt.verify_attestation(plan) == "ok"


def test_tamper_content_detected():
    """盖章后篡改任一内容字段（无密钥重算不了 HMAC）→ tampered。"""
    with _fixed_key(b"K" * 32):
        plan = _sample_plan()
        pt._attest(plan)
        assert pt.verify_attestation(plan) == "ok"
        # 攻击者伪造 step 状态（跳步）
        plan["steps"][0]["status"] = "completed"
        assert pt.verify_attestation(plan) == "tampered"


def test_hmac_key_dependency_no_forge():
    """密钥 A 盖的章，换密钥 B 校验 → tampered。证明是 keyed-HMAC 而非公开哈希
    （攻击者没有机器密钥就无法伪造有效 attestation·这正是 2026-05-29 安全修复要点）。"""
    plan = _sample_plan()
    with _fixed_key(b"A" * 32):
        pt._attest(plan)
        assert pt.verify_attestation(plan) == "ok"
    with _fixed_key(b"B" * 32):
        # 内容一字未改·仅换密钥 → 仍判 tampered（HMAC 依赖密钥）
        assert pt.verify_attestation(plan) == "tampered"


def test_legacy_pure_sha256_is_legacy_not_ok():
    """旧式无密钥纯 sha256 章 → legacy（向后兼容识别·非 ok 非 tampered）。
    legacy 是公开可算的弱章，写路径会按 tampered 拦（HMAC #4 非对称硬化）。"""
    with _fixed_key(b"K" * 32):
        plan = _sample_plan()
        legacy = pt._compute_legacy_sha256(plan)  # 无密钥·规范化排除 _attestation
        plan[AK] = {"sha256": legacy, "attested_at": "x", "by": "old"}
        assert pt.verify_attestation(plan) == "legacy"


def test_unattested_when_missing_or_nondict():
    assert pt.verify_attestation(_sample_plan()) == "unattested"  # 无 _attestation
    assert pt.verify_attestation({AK: {"by": "x"}}) == "unattested"  # 章里无 sha256
    assert pt.verify_attestation("not a dict") == "unattested"
    assert pt.verify_attestation(None) == "unattested"


def test_canonical_excludes_attestation_field():
    """_compute_attestation 排除 _attestation 字段本身 → 盖章前后算出的值一致。"""
    with _fixed_key(b"K" * 32):
        plan = _sample_plan()
        before = pt._compute_attestation(plan)
        pt._attest(plan)  # 加了 _attestation 字段
        after = pt._compute_attestation(plan)
        assert before == after, "attestation 计算应排除 _attestation 字段自身"


def test_canonical_key_order_independent():
    """规范化 JSON 用 sort_keys → dict 键序不影响 attestation（防顺序抖动误判篡改）。"""
    with _fixed_key(b"K" * 32):
        p1 = {"a": 1, "b": 2, "steps": [{"n": 1, "x": 9}]}
        p2 = {"steps": [{"x": 9, "n": 1}], "b": 2, "a": 1}  # 同内容·键序不同
        assert pt._compute_attestation(p1) == pt._compute_attestation(p2)


def test_legacy_sha256_is_keyless_stable():
    """旧式 sha256 不依赖密钥（这正是它弱、被按 tampered 拦的原因）。"""
    plan = _sample_plan()
    with _fixed_key(b"A" * 32):
        a = pt._compute_legacy_sha256(plan)
    with _fixed_key(b"B" * 32):
        b = pt._compute_legacy_sha256(plan)
    assert a == b  # 与密钥无关
    # 且确实是纯 sha256(规范化字节)
    assert a == hashlib.sha256(pt._canonical_plan_bytes(plan)).hexdigest()


# ════════════════════════════════════════════════════════════════
# 占位符替换 _substitute / _walk_substitute
# ════════════════════════════════════════════════════════════════
def test_substitute_basic_placeholders():
    r = pt._substitute("{project}/第{ch:03d}章/{key}", "书名", 7, "001")
    assert r == "书名/第007章/001"
    assert pt._substitute("{ch}", "p", 12, None) == "12"


def test_substitute_arithmetic():
    assert pt._substitute("{ch+1:03d}", "p", 5, None) == "006"
    assert pt._substitute("{ch-2}", "p", 5, None) == "3"
    assert pt._substitute("{ch+10:03d}", "p", 95, None) == "105"


def test_substitute_next_key():
    assert pt._substitute("cluster_{next_key}_x", "p", None, "001") == "cluster_002_x"
    # key 含前缀数字 → 取数字段递增 + 强制 03d
    assert pt._substitute("{next_key}", "p", None, "cluster_009") == "010"
    # key 无数字 → 保守 fallback
    assert pt._substitute("{next_key}", "p", None, "abc") == "abc_next"


def test_substitute_chapter_none_strips_ch():
    assert pt._substitute("第{ch:03d}章", "p", None, None) == "第章"
    assert pt._substitute("{ch+1:03d}", "p", None, None) == ""


def test_walk_substitute_nested():
    node = {"a": "{project}", "b": ["第{ch}章", {"c": "{key}"}], "n": 5}
    r = pt._walk_substitute(node, "书", 3, "k")
    assert r == {"a": "书", "b": ["第3章", {"c": "k"}], "n": 5}


# ════════════════════════════════════════════════════════════════
# make_plan_id
# ════════════════════════════════════════════════════════════════
def test_make_plan_id_format_and_uniqueness():
    a = pt.make_plan_id("cluster-write", "书", None, "001")
    b = pt.make_plan_id("cluster-write", "书", None, "001")
    assert a.startswith("书_001_cluster-write_")
    assert a != b, "随机后缀应保证同参连续 create 不碰撞"
    # chapter 优先于 key 进 keypart
    c = pt.make_plan_id("outline", "书", 5, "001")
    assert "_ch5_" in c
    # 无 project/key → noproject/main
    d = pt.make_plan_id("outline", "", None, None)
    assert d.startswith("noproject_main_outline_")


# ════════════════════════════════════════════════════════════════
# create_plan / get_plan / verify_plan / reattest（沙盒重定向 plan 存储）
# ════════════════════════════════════════════════════════════════
@contextmanager
def _sandbox():
    """重定向 GLOBAL_PLANS_DIR + ATTEST_KEY_PATH 到 tmp + 重置密钥缓存（不碰真 .plans）。"""
    tmp = Path(tempfile.mkdtemp())
    saved = {
        "GLOBAL_PLANS_DIR": pt.GLOBAL_PLANS_DIR,
        "ATTEST_KEY_PATH": pt.ATTEST_KEY_PATH,
        "_ATTEST_KEY_CACHE": pt._ATTEST_KEY_CACHE,
    }
    pt.GLOBAL_PLANS_DIR = tmp / ".plans"
    pt.GLOBAL_PLANS_DIR.mkdir(parents=True)
    pt.ATTEST_KEY_PATH = pt.GLOBAL_PLANS_DIR / ".attest_key"
    pt._ATTEST_KEY_CACHE = None  # 让它在 tmp 路径新生成机器密钥
    try:
        yield tmp
    finally:
        for k, v in saved.items():
            setattr(pt, k, v)


def test_create_get_roundtrip_and_verify_ok():
    """create_plan 用真 cluster-write 模板 → get_plan 取回含 steps → verify_plan ok。"""
    with _sandbox():
        pid = pt.create_plan("cluster-write", "测试书", key="001")
        assert pid.startswith("测试书_001_cluster-write_")
        plan = pt.get_plan(pid)
        assert plan["command"] == "cluster-write"
        assert plan["key"] == "001"
        assert len(plan.get("steps", [])) >= 1
        # 运行时字段补全
        assert all(s.get("status") == pt.STATUS_PENDING for s in plan["steps"])
        # 创建即盖章 → 防篡改校验 ok
        assert pt.verify_plan(pid) == "ok"


def test_reattest_after_manual_edit_recovers():
    """手改 plan 文件破坏 attestation → verify_plan tampered → reattest → 恢复 ok。"""
    with _sandbox():
        pid = pt.create_plan("cluster-write", "测试书", key="001")
        assert pt.verify_plan(pid) == "ok"
        # 模拟「plan_tracker 之外途径」直接改文件（注入/旁路）
        path = pt._find_plan_path(pid)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["steps"][0]["status"] = "completed"      # 伪造跳步
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        assert pt.verify_plan(pid) == "tampered", "篡改应被抓"
        # 合法手改后重新盖章
        pt.reattest_plan(pid)
        assert pt.verify_plan(pid) == "ok", "reattest 后应恢复 ok"


if __name__ == "__main__":
    fails = 0
    for _n in sorted(k for k in dict(globals()) if k.startswith("test_")):
        try:
            globals()[_n]()
            print("OK", _n)
        except Exception as e:
            fails += 1
            import traceback
            print("FAIL", _n, e)
            traceback.print_exc()
    sys.exit(1 if fails else 0)
