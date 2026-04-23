"""Decide which projects are 'due' right now.

Timezone handling:
- All schedule times (schedule_dow, schedule_hour) are interpreted in the system's
  local timezone, as returned by datetime.now().astimezone().
- Database timestamps (started_at, finished_at) are stored in UTC (ISO 8601).
- The scheduler converts both to local time for comparison, so DST transitions
  are handled automatically by the system.
- If you move to a different timezone, scheduled times will shift accordingly
  (e.g., "Monday 3am" means 3am in whatever timezone the laptop is currently in).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from websiteupdater import db


def start_of_current_week(now: datetime) -> datetime:
    """Monday 00:00 of the current week, in the same tz as `now`."""
    dow = now.weekday()  # Monday=0
    monday = (now - timedelta(days=dow)).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday


def due_projects(now: datetime | None = None) -> Iterator[db.Project]:
    """
    Yield projects whose scheduled window has opened this week and haven't yet
    run successfully (or reported no_updates) this week.

    Window rule: weekday matches `schedule_dow` AND hour >= `schedule_hour`.
    Once the window opens, the project stays 'due' for the rest of the week until
    it runs successfully. This makes us robust to the laptop being asleep when
    cron would have fired.

    The `now` parameter defaults to the system's local time. Scheduled hours are
    interpreted in local time, so 3am means "3am wherever this laptop currently is."
    """
    now = now or datetime.now(timezone.utc).astimezone()  # local tz
    week_start = start_of_current_week(now).isoformat(timespec="seconds")

    for p in db.list_projects(enabled_only=True):
        if p.schedule_dow > now.weekday():
            continue
        if p.schedule_dow == now.weekday() and now.hour < p.schedule_hour:
            continue
        # Window has opened this week. Have we already completed successfully?
        if db.last_successful_run_within_week(p.id, week_start):
            continue
        yield p
