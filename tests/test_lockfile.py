"""Tests for file-based locking."""

import tempfile
import time
from pathlib import Path

import pytest

from websiteupdater.lockfile import project_lock, LockError, _is_stale


def test_lock_acquire_and_release(tmp_path, monkeypatch):
    """Test basic lock acquisition and release."""
    # Patch DATA_DIR to use tmp_path
    monkeypatch.setattr("websiteupdater.lockfile.DATA_DIR", tmp_path)

    with project_lock("test_project"):
        lock_file = tmp_path / "locks" / "test_project.lock"
        assert lock_file.exists()

    # Lock should be released after context exit
    assert not lock_file.exists()


def test_lock_prevents_concurrent_access(tmp_path, monkeypatch):
    """Test that lock prevents concurrent access."""
    monkeypatch.setattr("websiteupdater.lockfile.DATA_DIR", tmp_path)

    with project_lock("test_project"):
        # Try to acquire the same lock again (should fail immediately)
        with pytest.raises(LockError, match="already running"):
            with project_lock("test_project", timeout=0):
                pass


def test_stale_lock_detection(tmp_path):
    """Test that stale locks are detected."""
    lock_file = tmp_path / "old.lock"
    lock_file.write_text("12345\n123456789.0\n")

    # Set mtime to 25 hours ago
    old_time = time.time() - (25 * 3600)
    lock_file.touch()
    import os
    os.utime(lock_file, (old_time, old_time))

    assert _is_stale(lock_file, max_age_hours=24)


def test_fresh_lock_not_stale(tmp_path):
    """Test that fresh locks are not considered stale."""
    lock_file = tmp_path / "fresh.lock"
    lock_file.write_text("12345\n123456789.0\n")

    assert not _is_stale(lock_file, max_age_hours=24)
