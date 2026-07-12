#!/usr/bin/env python3
"""plan 生命周期、产物校验与 HMAC attestation 回归测试。"""
import json
import re
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


def test_unattested_when_missing_or_nondict():
    assert pt.verify_attestation(_sample_plan()) == "unattested"  # 无 _attestation
    assert pt.verify_attestation({AK: {"by": "x"}}) == "unattested"  # 章里无 sha256
    assert pt.verify_attestation("not a dict") == "unattested"
    assert pt.verify_attestation(None) == "unattested"


def test_unattested_plan_is_rejected_before_write():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "plan.json"
        path.write_text(json.dumps(_sample_plan(), ensure_ascii=False), encoding="utf-8")
        try:
            pt._load_plan(path, for_write=True)
        except pt.PlanTamperedError:
            pass
        else:
            raise AssertionError("缺少 attestation 的 plan 不得进入写路径")


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


# ════════════════════════════════════════════════════════════════
# 占位符替换 _substitute / _walk_substitute
# ════════════════════════════════════════════════════════════════
def test_substitute_basic_placeholders():
    r = pt._substitute("{project}/{cluster_id}/{key}", "书名", "001")
    assert r == "书名/cluster_001/001"
    assert pt._substitute("{cluster_id}", "p", "cluster_012") == "cluster_012"


def test_substitute_rejects_legacy_ch_placeholders():
    for text in ("{ch}", "{ch:03d}", "{ch+1:03d}", "{ch-2}"):
        try:
            pt._substitute(text, "p", "001")
        except ValueError as exc:
            assert "旧章号占位符" in str(exc)
        else:
            raise AssertionError(f"旧章号占位符应被硬拒: {text}")


def test_substitute_next_key():
    assert pt._substitute("cluster_{next_key}_x", "p", "001") == "cluster_002_x"
    # key 含前缀数字 → 取数字段递增 + 强制 03d
    assert pt._substitute("{next_key}", "p", "cluster_009") == "010"
    # key 无数字 → 保守 fallback
    assert pt._substitute("{next_key}", "p", "abc") == "abc_next"


def test_walk_substitute_nested():
    node = {"a": "{project}", "b": ["{cluster_id}", {"c": "{key}"}], "n": 5}
    r = pt._walk_substitute(node, "书", "007")
    assert r == {"a": "书", "b": ["cluster_007", {"c": "007"}], "n": 5}


def test_walk_substitute_plan_id():
    node = {"agent_input": {"PLAN_ID": "{plan_id}"},
            "scripts": ["tool --plan-id {plan_id}"]}
    assert pt._walk_substitute(node, "书", "007", "plan-007") == {
        "agent_input": {"PLAN_ID": "plan-007"},
        "scripts": ["tool --plan-id plan-007"],
    }


def test_create_plan_records_microsecond_created_at():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        saved_projects = pt.PROJECTS_DIR
        saved_global = pt.GLOBAL_PLANS_DIR
        saved_key_path = pt.ATTEST_KEY_PATH
        pt.PROJECTS_DIR = root / "novels"
        pt.GLOBAL_PLANS_DIR = root / "plans"
        pt.ATTEST_KEY_PATH = root / "plans" / ".attest_key"
        project = root / "novel"
        project.mkdir()
        try:
            plan_id = pt.create_plan("cluster-save-state", str(project), "1")
            created = pt.get_plan(plan_id)["created_at"]
            assert re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", created,
            )
        finally:
            pt.PROJECTS_DIR = saved_projects
            pt.GLOBAL_PLANS_DIR = saved_global
            pt.ATTEST_KEY_PATH = saved_key_path


# ════════════════════════════════════════════════════════════════
# make_plan_id
# ════════════════════════════════════════════════════════════════
def test_make_plan_id_format_and_uniqueness():
    a = pt.make_plan_id("cluster-write", "书", "001")
    b = pt.make_plan_id("cluster-write", "书", "001")
    assert a.startswith("书_001_cluster-write_")
    assert a != b, "随机后缀应保证同参连续 create 不碰撞"
    # 无 project/key → noproject/main
    d = pt.make_plan_id("outline", "", None)
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
        assert plan["cluster_key"] == "001"
        assert plan["cluster_id"] == "cluster_001"
        assert len(plan.get("steps", [])) >= 1
        # 运行时字段补全
        assert all(s.get("status") == pt.STATUS_PENDING for s in plan["steps"])
        # 创建即盖章 → 防篡改校验 ok
        assert pt.verify_plan(pid) == "ok"


def test_skillopt_is_registered_multistep_command():
    assert "distill-style-skillopt" in pt.KNOWN_COMMANDS
    template = pt.load_template("distill-style-skillopt")
    assert template["command"] == "distill-style-skillopt"
    assert template["required_steps"] == list(range(1, template["total_steps"] + 1))


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


# ════════════════════════════════════════════════════════════════
# --output 路径解析（2026-07-09 真机 cluster_002 save-state 抓修）
# ════════════════════════════════════════════════════════════════
# 根因：step() 对非绝对 --output 值按「project_root 相对路径」拼接（与 expected_outputs
# 同口径，见 resolve_project_root() 用途一致）；但 cluster-save-state.md/cluster-write.md
# 里给的示例是 --output "<项目路径>/_数据库/..."——<项目路径> 本身在文档里就等于
# workspace/novels/<书名>，照抄示例会把 project_root 拼两遍产出
# ".../workspace/novels/<书名>/workspace/novels/<书名>/_数据库/..." 这种双重路径，
# 导致 --output 校验假报「文件不存在」（文件其实已经在正确单层路径上生成）。
# 修复 = 文档统一改成 --output "_数据库/..."（不含 <项目路径>/ 前缀），与
# expected_outputs 的既有约定一致。本节钉死两件事：文档不再犯 + 代码本身按正确口径工作。

def test_command_docs_no_doubled_project_path_in_output_flag():
    """cluster-write.md / cluster-save-state.md 的 --output 值不得再带 <项目路径>/ 前缀。"""
    _repo = Path(__file__).resolve().parent.parent
    docs = [
        _repo / ".claude" / "commands" / "cluster-write.md",
        _repo / ".claude" / "commands" / "cluster-save-state.md",
    ]
    offenders = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "--output" in line and "<项目路径>/" in line:
                offenders.append(f"{doc.name}:{i} {line.strip()}")
    assert not offenders, (
        "plan_tracker.py step --output 按 project_root 相对路径拼接（同 expected_outputs "
        "口径），示例里再带 <项目路径>/ 前缀会拼出双重路径导致假『文件不存在』：\n"
        + "\n".join(offenders)
    )


def test_step_output_resolves_relative_to_project_root_without_doubling():
    """--output 传 project-root 相对路径（不含项目本身路径前缀）→ 正确单层拼接，不重复。

    skip_output=True 只为绕开本测试无关的模板 expected_outputs 校验（沙盒项目没有那些
    骨架文件）；--output 自身的存在性判定不受 skip_output 影响（op.exists() 无条件跑），
    本测试真正锁住的是 --output 路径解析逻辑本身。
    """
    with _sandbox():
        proj_tmp = Path(tempfile.mkdtemp())
        (proj_tmp / "_数据库" / ".wal").mkdir(parents=True)
        out_file = proj_tmp / "_数据库" / ".wal" / "002_apply_cluster.json"
        out_file.write_text("{}", encoding="utf-8")

        pid = pt.create_plan("cluster-write", str(proj_tmp), key="001")
        plan = pt.get_plan(pid)
        assert plan["project"] == str(proj_tmp)

        # 正确用法：project-root 相对路径，不带项目自身路径前缀
        pt.step_complete(pid, 1, output="_数据库/.wal/002_apply_cluster.json", skip_output=True)
        plan = pt.get_plan(pid)
        step1 = next(s for s in plan["steps"] if s["n"] == 1)
        assert step1["status"] == pt.STATUS_COMPLETED
        verified = step1.get("verified_outputs", [])
        assert any(str(out_file).replace("\\", "/") in v for v in verified), (
            f"verified_outputs 应含单层拼接路径，得 {verified}"
        )
        # 双重前缀（模拟旧文档误用）不该恰好也存在，否则测试本身失去意义
        doubled = proj_tmp / proj_tmp.name / "_数据库" / ".wal" / "002_apply_cluster.json"
        assert not doubled.exists()


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
