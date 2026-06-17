"""atomic_json.py 专属回归测试 — 锁住「文件锁 + 锁内读-改-写」核心不变量。

被测脚本：core/scripts/atomic_json.py。它已被多处间接 import，但既有测试
（test_atomic_text / test_save_state_atomic / test_self_heal_atomic /
test_experience_atomic_write）只覆盖了两类东西：
  · atomic_write_text / atomic_write_json 的「写后读回一致 / 原子替换 / replace 阶段崩溃保旧文件 / 无 tmp 残留」；
  · 各上游模块「确实路由到了 atomic_json.*」的打桩调用路径。

**尚未被任何测试直接覆盖**的核心确定性逻辑（本文件聚焦补齐）：
  · with_file_lock —— lockfile 路径/命名约定、写入真实持有者元信息(pid/ts)、
    被占用时的 contention 超时、正常退出与异常退出后都释放锁、释放后可再次获取。
  · safe_update_json —— 锁内「读-改-写」语义：缺省创建 / 损坏 JSON 回退 default /
    update_fn 收到当前内容 / 返回值==落盘内容 / 调用后锁已释放。
  · _try_acquire_lock —— 已存在锁抛 FileExistsError；写元信息失败时不泄漏 fd
    且把半成品锁删干净（还原成「未占用」）。
  · atomic_write_text —— tmp 唯一命名约定、os.replace 抛 PermissionError 的退避重试路径。

零依赖范式：仅标准库；test_* 无参；__main__ 自跑打 [OK]/[FAIL]。Windows 平台。
铁律：绝不修改被测脚本，绝不改其它已有测试。
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "scripts"))
import atomic_json as aj  # noqa: E402


# ----------------------------------------------------------------- with_file_lock

def test_lockfile_path_naming_and_metadata():
    """锁文件命名约定 = 父目录 / .{target.name}.lock，且持有期间写入 pid/ts 元信息。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "状态.json"
        lockfile = target.parent / ".状态.json.lock"
        assert not lockfile.exists()
        with aj.with_file_lock(target, timeout=2.0):
            # 持有期间锁文件存在，且内容含本进程 pid + 时间戳（真实元信息，非空占位）
            assert lockfile.exists(), "持锁期间锁文件应存在"
            meta = lockfile.read_text(encoding="ascii")
            assert f"pid={os.getpid()}" in meta, f"锁未写入本进程 pid: {meta!r}"
            assert "ts=" in meta, f"锁未写入时间戳: {meta!r}"
        # 退出 with 后锁必须释放（删除）
        assert not lockfile.exists(), "退出后锁文件未释放"


def test_lock_creates_parent_dir():
    """target 父目录不存在时 with_file_lock 自建（mkdir parents），不报错。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "深" / "层" / "data.json"
        assert not target.parent.exists()
        with aj.with_file_lock(target, timeout=2.0):
            assert target.parent.is_dir(), "未自建父目录"


def test_lock_released_after_normal_exit_allows_reacquire():
    """正常退出后锁释放 → 同一 target 能立刻再次获取（不残留导致后续超时）。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "x.json"
        with aj.with_file_lock(target, timeout=2.0):
            pass
        # 第二次获取若锁未释放会 TimeoutError；能正常进入即证明已释放
        entered = False
        with aj.with_file_lock(target, timeout=2.0):
            entered = True
        assert entered, "正常退出后无法再次获取锁（锁未释放）"


def test_lock_released_after_exception_in_body():
    """with 体内抛异常 → finally 仍释放锁（异常路径不留僵尸锁）。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "y.json"
        lockfile = target.parent / ".y.json.lock"
        try:
            with aj.with_file_lock(target, timeout=2.0):
                raise ValueError("boom in body")
        except ValueError:
            pass
        assert not lockfile.exists(), "异常退出后锁未释放（僵尸锁）"
        # 且能再次获取
        with aj.with_file_lock(target, timeout=2.0):
            pass


def test_lock_contention_times_out():
    """锁被占用且未过期 → 第二个获取者在 timeout 内拿不到 → 抛 TimeoutError。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "z.json"
        with aj.with_file_lock(target, timeout=2.0):
            t0 = time.time()
            try:
                with aj.with_file_lock(target, timeout=0.3, poll=0.05):
                    raise AssertionError("第二次获取本应超时，却拿到了锁")
            except TimeoutError:
                pass
            # 大致尊重 timeout（不秒退也不无限等）；给宽松上界防 Windows 调度抖动
            assert time.time() - t0 >= 0.25, "未真正等待 timeout 就超时了"


def test_lock_mutual_exclusion_serializes_writers():
    """真并发互斥：两线程各自在锁内对计数 +1，临界区交错会丢更新；
    串行化后结果必须精确 == 线程数（证明锁真的互斥而非摆设）。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "counter.json"
        target.write_text(json.dumps({"n": 0}), encoding="utf-8")
        n_threads = 6
        errors = []

        def worker():
            try:
                with aj.with_file_lock(target, timeout=10.0, poll=0.01):
                    cur = json.loads(target.read_text(encoding="utf-8"))
                    cur["n"] += 1
                    # 放大临界区，让未互斥的实现必然丢更新
                    time.sleep(0.01)
                    aj.atomic_write_json(target, cur)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ths = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        assert not errors, f"并发 worker 异常: {errors}"
        final = json.loads(target.read_text(encoding="utf-8"))
        assert final["n"] == n_threads, f"互斥失效丢更新: 期望 {n_threads} 实得 {final['n']}"


# ----------------------------------------------------------------- _try_acquire_lock

def test_try_acquire_lock_raises_on_existing():
    """锁文件已存在时 _try_acquire_lock 抛 FileExistsError（O_CREAT|O_EXCL 语义）。"""
    with tempfile.TemporaryDirectory() as td:
        lockfile = Path(td) / ".a.json.lock"
        fd = aj._try_acquire_lock(lockfile)
        try:
            try:
                aj._try_acquire_lock(lockfile)
                raise AssertionError("第二次创建同名锁本应 FileExistsError")
            except FileExistsError:
                pass
        finally:
            os.close(fd)


def test_try_acquire_lock_no_fd_leak_and_cleanup_on_write_failure():
    """写元信息失败 → 关闭 fd + 删掉半成品锁（还原成「未占用」），并把原异常上抛。
    用 monkeypatch os.write 模拟写失败：若实现泄漏 fd 或残留锁，后续获取会异常/超时。"""
    with tempfile.TemporaryDirectory() as td:
        lockfile = Path(td) / ".b.json.lock"
        orig_write = aj.os.write

        def _boom(fd, data):
            raise OSError("simulated write failure")

        aj.os.write = _boom
        try:
            try:
                aj._try_acquire_lock(lockfile)
                raise AssertionError("写元信息失败本应上抛 OSError")
            except OSError as e:
                assert "simulated write failure" in str(e)
        finally:
            aj.os.write = orig_write
        # 关键不变量：半成品锁被删掉（还原成未占用），不残留无主锁
        assert not lockfile.exists(), "写失败后残留了无主锁文件（未清理）"
        # 还原后能正常获取（证明既没泄漏 fd 也没留死锁）
        fd = aj._try_acquire_lock(lockfile)
        os.close(fd)
        lockfile.unlink()


# ----------------------------------------------------------------- safe_update_json

def test_safe_update_creates_with_default_when_missing():
    """目标不存在 → update_fn 收到 default（空 dict），结果落盘并返回。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "new.json"
        seen = {}

        def upd(cur):
            seen["cur"] = cur
            return {**cur, "created": True}

        ret = aj.safe_update_json(target, upd)
        assert seen["cur"] == {}, "缺省时 update_fn 未收到 default 空 dict"
        assert ret == {"created": True}
        assert json.loads(target.read_text(encoding="utf-8")) == {"created": True}


def test_safe_update_custom_default_used():
    """传入 default 时缺省内容用该 default（而非硬编码 {}）。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "withdef.json"
        seen = {}

        def upd(cur):
            seen["cur"] = dict(cur)
            cur["n"] = cur.get("n", 0) + 1
            return cur

        ret = aj.safe_update_json(target, upd, default={"n": 10})
        assert seen["cur"] == {"n": 10}, "未把传入 default 喂给 update_fn"
        assert ret["n"] == 11
        assert json.loads(target.read_text(encoding="utf-8"))["n"] == 11


def test_safe_update_reads_modifies_writes_existing():
    """已存在合法 JSON → update_fn 收到真实当前内容，merge 写回，返回==落盘。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "exist.json"
        aj.atomic_write_json(target, {"a": 1, "中文": "x"})
        ret = aj.safe_update_json(target, lambda d: {**d, "b": 2})
        assert ret == {"a": 1, "中文": "x", "b": 2}
        assert json.loads(target.read_text(encoding="utf-8")) == ret


def test_safe_update_corrupt_json_falls_back_to_default():
    """目标存在但内容损坏（非法 JSON）→ 不崩，回退 default 后再 update（数据自愈）。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "corrupt.json"
        target.write_text("{这不是合法 JSON,,,", encoding="utf-8")
        seen = {}

        def upd(cur):
            seen["cur"] = cur
            return {**cur, "recovered": True}

        ret = aj.safe_update_json(target, upd, default={"base": 1})
        assert seen["cur"] == {"base": 1}, "损坏 JSON 未回退到 default"
        assert ret == {"base": 1, "recovered": True}
        # 落盘后是合法 JSON（损坏内容被原子覆盖修复）
        assert json.loads(target.read_text(encoding="utf-8")) == ret


def test_safe_update_releases_lock_after_call():
    """safe_update_json 退出后锁已释放：紧接着再调一次不会超时。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "seq.json"
        lockfile = target.parent / ".seq.json.lock"
        aj.safe_update_json(target, lambda d: {**d, "k": 1})
        assert not lockfile.exists(), "safe_update_json 后锁未释放"
        # 连续第二次（若锁残留会超时抛异常）
        ret = aj.safe_update_json(target, lambda d: {**d, "k": d.get("k", 0) + 1})
        assert ret["k"] == 2


# ----------------------------------------------------------------- atomic_write_text 边界

def test_tmp_name_uniqueness_convention():
    """tmp 命名嵌 pid+uuid 唯一：截获 open 路径，确认形如 .{name}.{pid}.<hex>.tmp，
    且唯一性保证并发不踩踏（不同次调用 hex 不同）。"""
    import builtins
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "draft.txt"
        captured = []
        orig_open = builtins.open  # 模块用内建 open，打 builtins 才拦得到

        def _spy_open(path, *a, **k):
            sp = str(path)
            if sp.endswith(".tmp"):
                captured.append(sp)
            return orig_open(path, *a, **k)

        builtins.open = _spy_open
        try:
            aj.atomic_write_text(target, "甲")
            aj.atomic_write_text(target, "乙")
        finally:
            builtins.open = orig_open
        assert len(captured) == 2, f"应各产生一个 tmp，实得 {captured}"
        for sp in captured:
            name = Path(sp).name
            assert name.startswith(".draft.txt."), f"tmp 命名前缀错: {name}"
            assert name.endswith(".tmp")
            assert str(os.getpid()) in name, f"tmp 名未含 pid: {name}"
        assert captured[0] != captured[1], "两次 tmp 名相同（uuid 未保证唯一）"
        # 最终结果是最后一次写入，无 tmp 残留
        assert target.read_text(encoding="utf-8") == "乙"
        assert not list(Path(td).glob("*.tmp"))


def test_replace_permission_error_retried_then_succeeds():
    """os.replace 头几次抛 PermissionError（Windows 文件被占用）→ 退避重试后成功。"""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "p.txt"
        orig_replace = aj.os.replace
        state = {"calls": 0}

        def _flaky(src, dst):
            state["calls"] += 1
            if state["calls"] <= 3:
                raise PermissionError("占用中")
            return orig_replace(src, dst)

        aj.os.replace = _flaky
        try:
            aj.atomic_write_text(target, "重试后落盘\n")
        finally:
            aj.os.replace = orig_replace
        assert state["calls"] == 4, f"未按预期退避重试，replace 调用 {state['calls']} 次"
        assert target.read_text(encoding="utf-8") == "重试后落盘\n"
        assert not list(Path(td).glob("*.tmp")), "重试成功后 tmp 未清理"


if __name__ == "__main__":
    fails = 0
    for nm in sorted(dir()):
        if nm.startswith("test_"):
            try:
                globals()[nm]()
                print(f"  [OK] {nm}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                print(f"  [FAIL] {nm}: {e}")
                traceback.print_exc()
    sys.exit(1 if fails else 0)
