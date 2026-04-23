"""SQLite storage for projects and run history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from websiteupdater.config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id                          INTEGER PRIMARY KEY,
    name                        TEXT UNIQUE NOT NULL,
    repo_url                    TEXT NOT NULL,
    local_path                  TEXT NOT NULL,
    main_branch                 TEXT NOT NULL DEFAULT 'main',
    update_branch               TEXT NOT NULL DEFAULT 'autoupdate',
    schedule_dow                INTEGER NOT NULL,
    schedule_hour               INTEGER NOT NULL,
    laravel_cloud_project_id    TEXT NOT NULL,
    laravel_cloud_main_env      TEXT NOT NULL,
    visual_diff_paths           TEXT NOT NULL,    -- JSON array of URL paths
    mask_selectors              TEXT NOT NULL DEFAULT '[]',  -- JSON array of CSS selectors
    diff_tolerance_pct          REAL NOT NULL DEFAULT 0.5,
    unit_test_cmd               TEXT,
    skip_unit_tests             INTEGER NOT NULL DEFAULT 0,
    npm_audit_gate              TEXT NOT NULL DEFAULT 'high',
    enabled                     INTEGER NOT NULL DEFAULT 1,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,
    stage             TEXT,
    failure_reason    TEXT,
    commit_sha        TEXT,
    merged            INTEGER NOT NULL DEFAULT 0,
    artifacts_path    TEXT,
    npm_audit_summary TEXT
);

CREATE INDEX IF NOT EXISTS runs_by_project ON runs(project_id, started_at);
"""


@dataclass
class Project:
    id: Optional[int]
    name: str
    repo_url: str
    local_path: str
    main_branch: str
    update_branch: str
    schedule_dow: int
    schedule_hour: int
    laravel_cloud_project_id: str
    laravel_cloud_main_env: str
    visual_diff_paths: list[str]
    mask_selectors: list[str]
    diff_tolerance_pct: float
    unit_test_cmd: Optional[str]
    skip_unit_tests: bool
    npm_audit_gate: str
    enabled: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        return cls(
            id=row["id"],
            name=row["name"],
            repo_url=row["repo_url"],
            local_path=row["local_path"],
            main_branch=row["main_branch"],
            update_branch=row["update_branch"],
            schedule_dow=row["schedule_dow"],
            schedule_hour=row["schedule_hour"],
            laravel_cloud_project_id=row["laravel_cloud_project_id"],
            laravel_cloud_main_env=row["laravel_cloud_main_env"],
            visual_diff_paths=json.loads(row["visual_diff_paths"]),
            mask_selectors=json.loads(row["mask_selectors"]),
            diff_tolerance_pct=row["diff_tolerance_pct"],
            unit_test_cmd=row["unit_test_cmd"],
            skip_unit_tests=bool(row["skip_unit_tests"]),
            npm_audit_gate=row["npm_audit_gate"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Run:
    id: Optional[int]
    project_id: int
    started_at: str
    finished_at: Optional[str] = None
    status: str = "running"
    stage: Optional[str] = None
    failure_reason: Optional[str] = None
    commit_sha: Optional[str] = None
    merged: bool = False
    artifacts_path: Optional[str] = None
    npm_audit_summary: Optional[dict] = field(default=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------------- projects ----------------

def add_project(
    *,
    name: str,
    repo_url: str,
    local_path: str,
    schedule_dow: int,
    schedule_hour: int,
    laravel_cloud_project_id: str,
    laravel_cloud_main_env: str,
    visual_diff_paths: list[str],
    mask_selectors: list[str] | None = None,
    main_branch: str = "main",
    update_branch: str = "autoupdate",
    diff_tolerance_pct: float = 0.5,
    unit_test_cmd: Optional[str] = None,
    skip_unit_tests: bool = False,
    npm_audit_gate: str = "high",
    enabled: bool = True,
) -> int:
    now = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO projects (
                name, repo_url, local_path, main_branch, update_branch,
                schedule_dow, schedule_hour,
                laravel_cloud_project_id, laravel_cloud_main_env,
                visual_diff_paths, mask_selectors, diff_tolerance_pct,
                unit_test_cmd, skip_unit_tests, npm_audit_gate,
                enabled, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                name, repo_url, local_path, main_branch, update_branch,
                schedule_dow, schedule_hour,
                laravel_cloud_project_id, laravel_cloud_main_env,
                json.dumps(visual_diff_paths),
                json.dumps(mask_selectors or []),
                diff_tolerance_pct,
                unit_test_cmd, int(skip_unit_tests), npm_audit_gate,
                int(enabled), now, now,
            ),
        )
        return cur.lastrowid


def get_project(name: str) -> Optional[Project]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()
        return Project.from_row(row) if row else None


def list_projects(*, enabled_only: bool = False) -> list[Project]:
    sql = "SELECT * FROM projects"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY name"
    with connect() as conn:
        return [Project.from_row(r) for r in conn.execute(sql).fetchall()]


def update_project(name: str, **fields) -> None:
    if not fields:
        return
    cols = []
    values: list = []
    for k, v in fields.items():
        if k in ("visual_diff_paths", "mask_selectors") and not isinstance(v, str):
            v = json.dumps(v)
        if k in ("skip_unit_tests", "enabled") and isinstance(v, bool):
            v = int(v)
        cols.append(f"{k} = ?")
        values.append(v)
    cols.append("updated_at = ?")
    values.append(now_iso())
    values.append(name)
    with connect() as conn:
        conn.execute(f"UPDATE projects SET {', '.join(cols)} WHERE name = ?", values)


def remove_project(name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE name = ?", (name,))


# ---------------- runs ----------------

def start_run(project_id: int, artifacts_path: Path) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO runs (project_id, started_at, status, artifacts_path)
               VALUES (?, ?, 'running', ?)""",
            (project_id, now_iso(), str(artifacts_path)),
        )
        return cur.lastrowid


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    if "npm_audit_summary" in fields and not isinstance(fields["npm_audit_summary"], str):
        fields["npm_audit_summary"] = json.dumps(fields["npm_audit_summary"])
    if "merged" in fields and isinstance(fields["merged"], bool):
        fields["merged"] = int(fields["merged"])
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    with connect() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE id = ?", values)


def finish_run(run_id: int, *, status: str, stage: Optional[str],
               failure_reason: Optional[str] = None, merged: bool = False) -> None:
    update_run(
        run_id,
        finished_at=now_iso(),
        status=status,
        stage=stage,
        failure_reason=failure_reason,
        merged=int(merged),
    )


def recent_runs(project_id: int, limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()


def last_successful_run_within_week(project_id: int, week_start_iso: str) -> Optional[sqlite3.Row]:
    """Returns the most recent successful (or no_updates) run this week, if any."""
    with connect() as conn:
        return conn.execute(
            """
            SELECT * FROM runs
            WHERE project_id = ?
              AND started_at >= ?
              AND status IN ('success', 'no_updates')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (project_id, week_start_iso),
        ).fetchone()
