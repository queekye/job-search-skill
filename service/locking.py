"""One reentrant, crash-released gate for a local database and its managed files."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import time

_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


def paths(base):
    base = Path(base).resolve()
    key = hashlib.sha256(os.path.normcase(str(base)).encode()).hexdigest()[:24]
    return base.parent / f".career-gate-{key}.lock", base.parent / f".career-swap-{key}.json", base.parent / f".career-before-sync-{key}"


@contextmanager
def gate(base):
    base = Path(base).resolve()
    lock_path, journal, _ = paths(base)
    with _REGISTRY_LOCK:
        lock, local = _REGISTRY.setdefault(os.path.normcase(str(lock_path)), (threading.RLock(), threading.local()))
    with lock:
        if getattr(local, "depth", 0):
            local.depth += 1
            try:
                yield
            finally:
                local.depth -= 1
            return
        base.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0"); handle.flush()
            deadline = time.monotonic() + 20
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        from .app import AppError
                        raise AppError("storage_busy", "本机正在同步或修改资料，请稍后重试。", 409) from None
                    time.sleep(0.05)
            local.depth = 1
            try:
                if journal.exists():
                    from .sync import recover
                    recover(base)
                yield
            finally:
                local.depth = 0
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def locked(method):
    def run(self, *args, **kwargs):
        with gate(self.base_data_dir):
            return method(self, *args, **kwargs)
    return run
