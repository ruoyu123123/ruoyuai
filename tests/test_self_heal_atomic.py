"""self_heal_kb.json 原子写 + 并发安全回归测试 — 守护 [#5] 并发损坏 bug。

self_heal_kb.json 是**系统级跨项目**知识库（core/claude-home/runtime/）。
cluster-save-state step9 多本书并发跑 `self_heal_engine.py --ingest` 时，对同一份 KB 做
read-modify-write：

旧实现两处反模式：
  1. save_kb 自造**固定** `.json.tmp` 名 → write_text → os.replace，无 pid/uuid 唯一名 +
     无锁 → 两进程同时写同一 .json.tmp → 内容交错损坏（atomic_json 顶部已点名过的反模式）。
  2. cmd_ingest / cmd_resolve 是裸 load_kb → 改 → save_kb 三步无锁 → 两进程都读到旧 KB、
     各自累加、各自写回 → 后写覆盖先写，丢对方计数。

修复：
  - save_kb 改走 atomic_json.atomic_write_json（tmp 嵌 pid+uuid4 唯一名 + fsync + os.replace）。
  - cmd_ingest / cmd_resolve 整个「读-改-写」走 atomic_json.safe_update_json（with_file_lock 闭环），
    第二个写者必读到第一个写者的结果，零丢计数。

这组测试钉死「写盘走原子路径、目标永不残留半截、并发计数不丢」，**不碰任何学习/创作判断逻辑**。
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "core" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
import atomic_json  # noqa: E402
import self_heal_engine as she  # noqa: E402


def _write_incidents(root: Path, incidents):
    """把 incident dict 列表写进 <root>/core/claude-home/runtime/incidents.jsonl（jsonl）。"""
    inc = she._incidents_path(root)  # 内部会 mkdir runtime/
    with open(inc, "w", encoding="utf-8") as f:
        for d in incidents:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return inc


def _make_incident(sig, error_type="KeyError", ts="2026-05-30T10:00:00+08:00", msg="boom"):
    script, _, loc = sig.partition("::")
    return {"signature": sig, "script": script or "x.py", "error_type": error_type,
            "location": loc, "message": msg, "ts": ts, "raw": f"Traceback {sig}"}


def _no_stray_tmp_or_corrupt(runtime_dir: Path, target: Path):
    """目标是合法 JSON，且目录里没有残留 .tmp（原子写应清理本次 tmp）。"""
    json.loads(target.read_text(encoding="utf-8"))  # 解析失败 = 半截损坏
    strays = [p.name for p in runtime_dir.iterdir() if p.name.endswith(".tmp")]
    assert not strays, f"残留游离 tmp 文件: {strays}"


# ---------------------------------------------------------------- 原子写路径

def test_self_heal_engine_imports_atomic_json():
    """self_heal_engine 必须经由 atomic_json 落盘（不再自造固定 .json.tmp 名）。"""
    assert she.atomic_json is atomic_json


def test_save_kb_no_fixed_tmp_name():
    """save_kb 源码不得再出现固定 `.json.tmp` 写法（防回归到无 pid/uuid 唯一名）。"""
    src = Path(she.__file__).read_text(encoding="utf-8")
    # 旧反模式：p.with_suffix(".json.tmp") + 裸 os.replace。atomic_json 用唯一名，不会有这串。
    assert 'with_suffix(".json.tmp")' not in src, "save_kb 仍在用固定 tmp 名（并发会交错损坏）"


def test_save_kb_roundtrip_valid_json_no_stray_tmp():
    """save_kb 写出合法 JSON、可被 load_kb 往返、无游离 tmp。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        kb = she._empty_kb()
        kb["patterns"]["x.py::KeyError"] = {"signature": "x.py::KeyError", "count": 3,
                                            "status": "recurring"}
        p = she.save_kb(root, kb)
        assert p.is_file()
        _no_stray_tmp_or_corrupt(p.parent, p)
        reloaded = she.load_kb(root)
        assert reloaded["patterns"]["x.py::KeyError"]["count"] == 3


def test_load_kb_tolerant_reader_on_corrupt():
    """KB 损坏时 load_kb 不崩、返回空骨架、旧库旁置 .corrupt（不删数据）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = she._kb_path(root)
        p.write_text("{not valid json", encoding="utf-8")
        kb = she.load_kb(root)
        assert kb["patterns"] == {} and kb["_total_incidents"] == 0
        assert p.with_suffix(".json.corrupt").exists()


# ---------------------------------------------------------------- ingest 正确性

def test_ingest_counts_and_offset():
    """单次 ingest：计数正确、offset 推进到文件末尾、不重复消费。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_make_incident("a.py::KeyError"),
                                _make_incident("a.py::KeyError"),
                                _make_incident("b.py::TypeError", error_type="TypeError")])
        assert she.cmd_ingest(root) == 0
        kb = she.load_kb(root)
        assert kb["patterns"]["a.py::KeyError"]["count"] == 2
        assert kb["patterns"]["b.py::TypeError"]["count"] == 1
        assert kb["_total_incidents"] == 3
        size = she._incidents_path(root).stat().st_size
        assert kb["_ingest_offset"] == size
        # 再 ingest 一次：offset 已到末尾，0 新增（不重复计数）
        assert she.cmd_ingest(root) == 0
        kb2 = she.load_kb(root)
        assert kb2["_total_incidents"] == 3
        assert kb2["patterns"]["a.py::KeyError"]["count"] == 2


def test_ingest_uses_safe_update_json_lock():
    """cmd_ingest 必须经由 safe_update_json（锁内读-改-写），通过打桩确认调用路径。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_make_incident("c.py::KeyError")])
        called = {"n": 0}
        orig = atomic_json.safe_update_json

        def _spy(target, update_fn, default=None, timeout=10.0):
            called["n"] += 1
            return orig(target, update_fn, default=default, timeout=timeout)

        atomic_json.safe_update_json = _spy
        try:
            assert she.cmd_ingest(root) == 0
        finally:
            atomic_json.safe_update_json = orig
        assert called["n"] == 1, "cmd_ingest 未走 safe_update_json（绕过了锁）"


# ---------------------------------------------------------------- 并发不丢计数（核心 bug #5）

def test_concurrent_ingest_no_lost_counts():
    """N 个线程并发 cmd_ingest 同一份系统级 KB，总计数不丢、目标始终合法 JSON。

    模拟多本书 cluster-save-state step9 并发 --ingest。锁内闭环后第二个写者必读到第一个写者结果。
    每个线程消费一份独立 incidents（写到各自 runtime 目录的 KB 是同一个 root → 同一 KB 文件）。

    注意：所有线程指向同一 root → 同一 incidents.jsonl + 同一 KB。incidents 共 N*K 条，
    但 offset 增量游标只让第一个成功的 ingest 消费全部、其余读到 0 新增。因此正确断言是：
    无论哪个线程先跑，最终 _total_incidents == 实际唯一消费的 incident 总数，且 KB 不损坏。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        K = 30
        # 同一文件 N 条不同指纹，确保即便单进程消费也能验证计数完整。
        incidents = [_make_incident(f"s{i}.py::KeyError") for i in range(K)]
        _write_incidents(root, incidents)

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()  # 尽量同时发车，最大化撞锁概率
                she.cmd_ingest(root)
            except Exception as e:  # 任何崩溃都是 bug
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 ingest 崩溃: {errors}"
        p = she._kb_path(root)
        _no_stray_tmp_or_corrupt(p.parent, p)
        kb = she.load_kb(root)
        # offset 增量语义：K 条 incident 跨多线程合计恰好被消费一次（不重不漏）。
        assert kb["_total_incidents"] == K, f"丢/重复计数: {kb['_total_incidents']} != {K}"
        # 每个唯一指纹恰好 count==1（消费一次）。
        for i in range(K):
            sig = f"s{i}.py::KeyError"
            assert kb["patterns"][sig]["count"] == 1, f"{sig} 计数错: {kb['patterns'][sig]['count']}"


def test_concurrent_ingest_appended_incidents_all_counted():
    """更贴近真实：每个线程往同一 incidents 追加自己的 incident 再 ingest（顺序追加+并发消费）。

    用线程串行追加（避免 jsonl 自身追加竞态——那是 incidents 写入侧的问题，不在本修复范围），
    每追加一条立刻并发触发多个 ingest。验证：所有追加的 incident 最终都被计入，零丢失。
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inc_path = she._incidents_path(root)
        total_appended = 0
        for round_i in range(5):
            # 本轮追加 3 条
            with open(inc_path, "a", encoding="utf-8") as f:
                for j in range(3):
                    f.write(json.dumps(_make_incident(f"r{round_i}c{j}.py::TypeError",
                                                       error_type="TypeError"),
                                       ensure_ascii=False) + "\n")
                    total_appended += 1
            # 多个并发 ingest 抢着消费这批新增
            errors = []
            barrier = threading.Barrier(4)

            def worker():
                try:
                    barrier.wait()
                    she.cmd_ingest(root)
                except Exception as e:
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors, f"第 {round_i} 轮并发 ingest 崩溃: {errors}"

        p = she._kb_path(root)
        _no_stray_tmp_or_corrupt(p.parent, p)
        kb = she.load_kb(root)
        assert kb["_total_incidents"] == total_appended, \
            f"追加 {total_appended} 条但只计 {kb['_total_incidents']}（丢计数）"


def test_resolve_uses_lock_and_survives_concurrent_ingest():
    """cmd_resolve 走锁内读-改-写：与并发 ingest 互不覆盖，且 status 正确落库。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 先建一个达 known 阈值的 pattern
        _write_incidents(root, [_make_incident("d.py::KeyError") for _ in range(6)])
        she.cmd_ingest(root)
        assert she.load_kb(root)["patterns"]["d.py::KeyError"]["count"] == 6

        # 并发：一个线程 resolve，另几个线程对新增 incident ingest
        with open(she._incidents_path(root), "a", encoding="utf-8") as f:
            for _ in range(4):
                f.write(json.dumps(_make_incident("e.py::ValueError", error_type="ValueError"),
                                   ensure_ascii=False) + "\n")
        errors = []
        barrier = threading.Barrier(3)

        def do_resolve():
            try:
                barrier.wait()
                she.cmd_resolve(root, "d.py::KeyError")
            except Exception as ex:
                errors.append(repr(ex))

        def do_ingest():
            try:
                barrier.wait()
                she.cmd_ingest(root)
            except Exception as ex:
                errors.append(repr(ex))

        threads = [threading.Thread(target=do_resolve),
                   threading.Thread(target=do_ingest),
                   threading.Thread(target=do_ingest)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"resolve/ingest 并发崩溃: {errors}"
        kb = she.load_kb(root)
        # resolve 必须落库（没被并发 ingest 覆盖丢失）
        assert kb["patterns"]["d.py::KeyError"]["status"] == "resolved"
        # ingest 的新 pattern 也必须落库（没被 resolve 覆盖丢失）
        assert kb["patterns"]["e.py::ValueError"]["count"] == 4
        p = she._kb_path(root)
        _no_stray_tmp_or_corrupt(p.parent, p)


def test_resolve_missing_signature_returns_1():
    """resolve 不存在的指纹返回 1，且不破坏 KB。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_incidents(root, [_make_incident("f.py::KeyError")])
        she.cmd_ingest(root)
        assert she.cmd_resolve(root, "ghost.py::KeyError") == 1
        kb = she.load_kb(root)
        assert kb["patterns"]["f.py::KeyError"]["count"] == 1  # 原样未损
