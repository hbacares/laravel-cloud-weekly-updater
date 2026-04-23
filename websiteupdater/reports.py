"""Render the per-run summary that goes into logs, DB, and emails."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from websiteupdater.updaters import npm as npm_updater
from websiteupdater.updaters import composer as composer_updater
from websiteupdater.visual_diff import DiffReport


@dataclass
class RunSummary:
    """Everything needed to render a log line, email body, or notification."""
    project_name: str
    run_id: int
    started_at: str
    finished_at: Optional[str]
    status: str                      # success | failed | no_updates | skipped
    stage: Optional[str]             # last stage reached
    failure_reason: Optional[str]
    commit_sha: Optional[str]
    merged: bool
    artifacts_path: str

    composer_updates: list[composer_updater.PackageUpdate] = field(default_factory=list)
    npm_audit: Optional[npm_updater.AuditResult] = None
    npm_update_ok: Optional[bool] = None
    composer_ok: Optional[bool] = None
    unit_tests_passed: Optional[bool] = None
    unit_tests_skipped: bool = False
    unit_test_cmd: Optional[str] = None

    deploy_url: Optional[str] = None
    deploy_state: Optional[str] = None

    diff_report: Optional[DiffReport] = None
    lockfile_diff: str = ""


def subject_line(s: RunSummary) -> str:
    tag = f"[websiteupdater] {s.project_name} — "
    if s.status == "success":
        n = len(s.composer_updates)
        return tag + f"success ({n} composer package{'s' if n != 1 else ''} bumped)"
    if s.status == "no_updates":
        return tag + "no updates available"
    if s.status == "skipped":
        return tag + f"skipped ({s.failure_reason or 'unknown'})"
    # failed
    stage = s.stage or "unknown stage"
    return tag + f"FAILED at {stage}"
