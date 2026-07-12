"""atomic_json.py — 原子写 JSON + 简易 file lock。"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


# 跨平台原子锁创建标志：O_CREAT|O_EXCL 在 POSIX/Windows 都是原子「不存在才创建」语义；Windows 下
# 追加 O_BINARY 防 CRLF 转换破坏 pid/ts 元信息（虽是 ASCII 但显式 binary 更稳）。中文路径在
# Windows(UTF-16 NTFS) 与现代 Linux(UTF-8) 上 os.open(str(path)) 均可靠，无需额外编码处理。
_LOCK_OPEN_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
_LOCK_EXPIRE_SEC = 300.0


def _try_acquire_lock(lockfile: Path):
    """尝试原子创建锁文件并写入持有者元信息。

    成功返回 fd（已写入 pid/ts，调用方负责最终 close）。
    锁已存在抛 FileExistsError。其它异常路径保证不泄漏 fd（写元信息失败时立即 close + unlink，
    把锁还原成「未被占用」让真正的持有逻辑重试，绝不留下一个谁都关不掉的僵尸锁）。
    """
    fd = os.open(str(lockfile), _LOCK_OPEN_FLAGS)
    try:
        os.write(fd, f"pid={os.getpid()} ts={time.time()}".encode("ascii"))
    except OSError:
        # 写元信息失败：关 fd 并删掉这个半成品锁，避免泄漏 fd + 残留无主锁。
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(str(lockfile))
        except OSError:
            pass
        raise
    return fd


@contextmanager
def with_file_lock(target: Path, timeout: float = 10.0, poll: float = 0.1):
    lockfile = target.parent / f".{target.name}.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    fd = None
    while True:
        try:
            fd = _try_acquire_lock(lockfile)
            break
        except FileExistsError:
            if time.time() - start > timeout:
                # 过期锁抢占用「原子重命名」而非裸 unlink+continue，避免 TOCTOU 竞态——两个进程
                # 可能同时通过 stat 检查后各自 unlink + 创建，导致双方都拿到锁：
                #   1) 先把过期锁原子 rename 成本进程私有的 .stale.<pid>.<uuid> 名字；
                #      os.replace 是原子的——只有一个进程能把那个特定 inode 抢走，其余 rename 会失败/改到别处。
                #   2) 抢到（rename 成功）的进程删掉私有 stale 文件，再 O_CREAT|O_EXCL 创建新锁；
                #      仍可能被另一个刚正常释放又重抢的进程插队，故创建失败一律判超时，绝不双方都成功。
                try:
                    mtime = lockfile.stat().st_mtime
                except OSError:
                    # stat 失败：锁可能刚被别人释放，重新走循环尝试正常获取
                    time.sleep(poll)
                    continue
                if time.time() - mtime > _LOCK_EXPIRE_SEC:
                    stale = lockfile.parent / f"{lockfile.name}.stale.{os.getpid()}.{uuid.uuid4().hex}"
                    try:
                        # 原子抢占过期锁：把它改名到本进程私有路径。成功 = 本进程独占了那个旧锁文件。
                        os.replace(str(lockfile), str(stale))
                    except OSError:
                        # 别人抢先 rename/删除了过期锁 → 本进程认输，直接超时（不死磨）。
                        raise TimeoutError(
                            f"获取 file lock 超时 ({timeout}s)，过期锁被他人抢占: {lockfile}"
                        )
                    # 已独占旧锁文件，删掉私有 stale，再原子创建新锁。
                    try:
                        os.unlink(str(stale))
                    except OSError:
                        pass
                    try:
                        fd = _try_acquire_lock(lockfile)
                        break  # 抢占成功
                    except FileExistsError:
                        raise TimeoutError(
                            f"获取 file lock 超时 ({timeout}s)，抢占后新锁已被他人创建: {lockfile}"
                        )
                raise TimeoutError(f"获取 file lock 超时 ({timeout}s): {lockfile}")
            time.sleep(poll)
    try:
        yield
    finally:
        # 异常路径同样必须释放：close fd（吞 OSError）+ 删锁文件（missing_ok 容忍已被抢占进程删走）。
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lockfile.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8"):
    """原子写纯文本：tmp(pid+uuid) + fsync + os.replace。

    与 atomic_write_json 同一范式 —— 崩溃/断电时目标文件要么旧版完整、要么新版完整，
    绝不留半截。供草稿 txt / 章节正文 txt / pending_tail / plan JSON（自带序列化）等
    产物落盘复用；临时/日志类写盘无需走这里。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # tmp 名嵌入 os.getpid() + uuid4 hex（不用固定名——两进程并发写同一 target 时固定 tmp 文件名
    # 会互相交错损坏），进程间/进程内全局唯一，互不踩踏。也不用 with_suffix（会丢掉原后缀，且
    # .json.tmp 链式后缀语义不直观），直接拼父目录 + 唯一名。
    tmp = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        # os.replace 前先 flush + fsync 落盘，否则崩溃时目标文件可能 0 字节。
        with open(str(tmp), "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Windows 下若 target 句柄被别的进程短暂持有（杀软/索引器/另一写者刚 replace 完仍在收尾），
        # os.replace 抛 PermissionError。POSIX 的 rename 是原子的、一般不抛此错，但 Windows 必须
        # 重试。短退避重试若干次，仍失败才向上抛。
        last_err = None
        for attempt in range(10):
            try:
                os.replace(str(tmp), str(target))
                last_err = None
                break
            except PermissionError as e:  # Windows: 目标/源被占用
                last_err = e
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None:
            raise last_err
    finally:
        # 唯一 tmp 名 → 只清理本次自己的 tmp，绝不误删其他并发写者的 tmp。
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def atomic_write_json(target: Path, data: dict, indent: int = 2, ensure_ascii: bool = False):
    # 写盘核心（tmp pid+uuid + fsync + os.replace + PermissionError 退避）下沉到 atomic_write_text
    # 共用，本函数只负责 JSON 序列化（json.dumps 失败发生在建 tmp 之前，不留 tmp）。
    payload = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(target, payload, encoding="utf-8")


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
    import sys
    import tempfile
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
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
        # atomic_write_text smoke（写后读回一致 + 覆盖替换 + 无 tmp 残留）
        t = Path(td) / "test.txt"
        atomic_write_text(t, "第一章\n中文正文。\n")
        assert t.read_text(encoding="utf-8") == "第一章\n中文正文。\n"
        atomic_write_text(t, "覆盖后的正文\n")
        assert t.read_text(encoding="utf-8") == "覆盖后的正文\n"
        assert not [x for x in Path(td).iterdir() if x.name.endswith(".tmp")]
        print("✓ atomic_json smoke test 通过")
