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
                try:
                    mtime = lockfile.stat().st_mtime
                    if time.time() - mtime > 300:
                        lockfile.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
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
        tmp.write_text(json.dumps(data, ensure_ascii=ensure_ascii, indent=indent), encoding="utf-8")
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
