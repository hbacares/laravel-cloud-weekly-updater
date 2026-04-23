"""Tests for scheduler logic."""

from datetime import datetime, timezone

from websiteupdater.scheduler import start_of_current_week, due_projects
from websiteupdater import db


def test_start_of_current_week():
    """Test that start_of_current_week returns Monday 00:00."""
    # Wednesday, 2026-04-15 14:30
    dt = datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc).astimezone()
    monday = start_of_current_week(dt)

    assert monday.weekday() == 0  # Monday
    assert monday.hour == 0
    assert monday.minute == 0
    assert monday.second == 0
    # Should be April 13, 2026 (the Monday of that week)
    assert monday.day == 13
    assert monday.month == 4
    assert monday.year == 2026


def test_start_of_current_week_on_monday():
    """Test start_of_current_week when called on a Monday."""
    # Monday, 2026-04-13 10:00
    dt = datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc).astimezone()
    monday = start_of_current_week(dt)

    assert monday.weekday() == 0
    assert monday.day == 13
    assert monday.hour == 0


def test_start_of_current_week_on_sunday():
    """Test start_of_current_week when called on a Sunday."""
    # Sunday, 2026-04-19 23:59
    dt = datetime(2026, 4, 19, 23, 59, tzinfo=timezone.utc).astimezone()
    monday = start_of_current_week(dt)

    assert monday.weekday() == 0
    # Should be April 13 (the Monday that started this week)
    assert monday.day == 13
    assert monday.month == 4
