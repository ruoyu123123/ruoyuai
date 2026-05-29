"""atomic_json.py — 原子写 JSON + 简易 file lock（v19.4 新增）"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def with_file_lock(target: Path, timeout: float = 10.0, poll: float = 0.1):
    lockfile = target.parent / f".{target.name}.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()} ts={time.time()}".encode())
            break
        except FileExistsError:
            if time.time() - start > timeout:
                # 2026-05-29 修：过期锁抢占 TOCTOU 修复。
                # 旧实现 unlink + continue → 两进程可能同时通过 stat 检查后各自 unlink + 创建，双方都拿到锁。
                # 新实现：unlink 过期锁后立即用 O_CREAT|O_EXCL 原子抢占，成功才算拿到锁；
                # 失败（FileExistsError）说明别人已抢先，放弃抢占抛超时，绝不双方都成功。
                try:
                    mtime = lockfile.stat().st_mtime
                except OSError:
                    # stat 失败：锁可能刚被别人释放，重新走循环尝试正常获取
                    time.sleep(poll)
                    continue
                if time.time() - mtime > 300:
                    # 删除该过期锁（若已被别人删则忽略），随后原子抢占
                    try:
                        os.unlink(str(lockfile))
                    except OSError:
                        pass
                    try:
                        fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.write(fd, f"pid={os.getpid()} ts={time.time()}".encode())
                        break  # 抢占成功
                    except FileExistsError:
                        # 别人已经抢先创建 → 本进程认输，不再 continue 死磨，直接超时
                        raise TimeoutError(f"获取 file lock 超时 ({timeout}s)，过期锁被他人抢占: {lockfile}")
                raise TimeoutError(f"获取 file lock 超时 ({timeout}s): {lockfile}")
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        lockfile.unlink(missing_ok=True)


def atomic_write_json(target: Path, data: dict, indent: int = 2, ensure_ascii: bool = False):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        # 2026-05-29 修：os.replace 前先 flush + fsync 落盘，否则崩溃时目标文件可能 0 字节。
        payload = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
        with open(str(tmp), "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(target))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def safe_update_json(target: Path, update_fn, default: dict = None, timeout: float = 10.0):
    if default is None:
        default = {}
    with with_file_lock(target, timeout=timeout):
        if target.exists():
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                current = default
        else:
            current = default
        new = update_fn(current)
        atomic_write_json(target, new)
        return new


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.json"
        atomic_write_json(p, {"a": 1, "中文": "ok"})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "中文": "ok"}
        safe_update_json(p, lambda d: {**d, "b": 2})
        result = json.loads(p.read_text(encoding="utf-8"))
        assert result["b"] == 2
        with with_file_lock(p, timeout=1.0):
            try:
                with with_file_lock(p, timeout=0.5):
                    pass
                assert False, "应该超时"
            except TimeoutError:
                pass
        print("✓ atomic_json smoke test 通过")
