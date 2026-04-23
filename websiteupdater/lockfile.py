"""File-based run locking to prevent concurrent runs of the same project."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from websiteupdater.config import DATA_DIR


class LockError(RuntimeError):
    pass


def _lock_path(project_name: str) -> Path:
    return DATA_DIR / "locks" / f"{project_name}.lock"


@contextmanager
def project_lock(project_name: str, timeout: float = 0) -> Iterator[None]:
    """Acquire an exclusive lock for a project run.

    Args:
        project_name: The project identifier
        timeout: How long to wait for the lock (0 = fail immediately)

    Raises:
        LockError: If the lock cannot be acquired within timeout

    The lock is automatically released when exiting the context.
    Stale locks (>24h old) are automatically cleaned up.
    """
    lock_file = _lock_path(project_name)
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout
    acquired = False

    try:
        while True:
            try:
                # Try to create the lock file exclusively
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                # Lock exists, check if stale
                if _is_stale(lock_file):
                    _force_unlock(lock_file)
                    continue

                # Not stale, check timeout
                if timeout == 0 or time.monotonic() >= deadline:
                    raise LockError(
                        f"Project {project_name!r} is already running (lock: {lock_file}). "
                        f"Wait for it to finish or remove the lock file manually if stale."
                    )
                time.sleep(0.5)

        yield

    finally:
        if acquired:
            _force_unlock(lock_file)


def _is_stale(lock_file: Path, max_age_hours: int = 24) -> bool:
    """Check if a lock file is stale (older than max_age_hours)."""
    try:
        stat = lock_file.stat()
        age = time.time() - stat.st_mtime
        return age > (max_age_hours * 3600)
    except (OSError, ValueError):
        return True


def _force_unlock(lock_file: Path) -> None:
    """Remove a lock file, ignoring errors."""
    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass
