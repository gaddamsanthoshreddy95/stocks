"""Kernel-held workspace lock, released by Windows/Linux when a worker exits."""
from contextlib import contextmanager
from pathlib import Path
import os

@contextmanager
def worker_lock(database):
    path=Path(str(Path(database).resolve())+'.workspace.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b')
    acquired=False
    try:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                acquired=True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name=='nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()
