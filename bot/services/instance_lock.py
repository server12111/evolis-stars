import os
from pathlib import Path
from typing import BinaryIO

from bot.config import get_settings

_lock_file: BinaryIO | None = None


class BotAlreadyRunningError(RuntimeError):
    pass


def acquire_instance_lock() -> None:
    """Prevent two bot processes from polling with the same deployment."""
    global _lock_file
    path = Path(get_settings().instance_lock_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")

    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise BotAlreadyRunningError(
            f"another bot process already holds {path}"
        ) from exc

    _lock_file = handle


def release_instance_lock() -> None:
    global _lock_file
    handle = _lock_file
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
        _lock_file = None
