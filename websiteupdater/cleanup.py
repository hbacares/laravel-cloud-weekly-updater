"""Artifact cleanup utilities."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from websiteupdater.config import RUNS_DIR, Settings


def cleanup_old_artifacts(retention_days: int, *, dry_run: bool = False) -> tuple[list[str], int]:
    """Remove run artifact directories older than retention_days.

    Args:
        retention_days: Keep runs from last N days (0 = keep all)
        dry_run: If True, only report what would be deleted

    Returns:
        (list of removed directories, total bytes freed)
    """
    if retention_days <= 0:
        return ([], 0)

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: list[str] = []
    bytes_freed = 0

    if not RUNS_DIR.exists():
        return ([], 0)

    for run_dir in RUNS_DIR.iterdir():
        if not run_dir.is_dir():
            continue

        # Check modification time
        mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            size = _dir_size(run_dir)
            if not dry_run:
                shutil.rmtree(run_dir)
            removed.append(run_dir.name)
            bytes_freed += size

    return (removed, bytes_freed)


def _dir_size(path: Path) -> int:
    """Calculate total size of directory in bytes."""
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except (OSError, PermissionError):
        pass
    return total
