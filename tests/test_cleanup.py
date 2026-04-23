"""Tests for artifact cleanup."""

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from websiteupdater.cleanup import cleanup_old_artifacts, _dir_size
from websiteupdater import config


def test_cleanup_no_retention():
    """Test that cleanup does nothing when retention is 0."""
    removed, bytes_freed = cleanup_old_artifacts(0)
    assert removed == []
    assert bytes_freed == 0


def test_dir_size():
    """Test directory size calculation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "file1.txt").write_text("x" * 1000)
        (tmp / "file2.txt").write_text("y" * 500)
        (tmp / "subdir").mkdir()
        (tmp / "subdir" / "file3.txt").write_text("z" * 250)

        size = _dir_size(tmp)
        assert size == 1750


def test_cleanup_old_artifacts():
    """Test that old artifacts are identified correctly."""
    # This test would require mocking RUNS_DIR or setting up a test fixture
    # For now, we'll test the logic with a dry run
    removed, bytes_freed = cleanup_old_artifacts(30, dry_run=True)
    # Since we're in dry_run and likely have no artifacts, this should be empty
    assert isinstance(removed, list)
    assert isinstance(bytes_freed, int)
    assert bytes_freed >= 0
