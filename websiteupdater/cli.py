"""Typer-based CLI. Entry point is `wu` (installed via pyproject scripts)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from websiteupdater import db, scheduler
from websiteupdater.cleanup import cleanup_old_artifacts
from websiteupdater.config import Settings, DB_PATH, ROOT, ensure_dirs
from websiteupdater.runner import run_project

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Automated dependency updater for Laravel Cloud projects.",
)
console = Console()


# ---------- helpers ----------

DOW_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_dow(s: str) -> int:
    s = s.strip().lower()[:3]
    if s.isdigit():
        n = int(s)
        if 0 <= n <= 6:
            return n
    if s in DOW_NAMES:
        return DOW_NAMES.index(s)
    raise typer.BadParameter(f"Invalid day of week: {s!r} (use mon/tue/.../sun or 0-6)")


def _parse_paths(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def _require_project(name: str) -> db.Project:
    p = db.get_project(name)
    if not p:
        rprint(f"[red]No project named {name!r}. Use `wu list` to see registered projects.[/red]")
        raise typer.Exit(1)
    return p


def _init() -> None:
    ensure_dirs()
    db.init_schema()


# ---------- commands ----------

@app.command()
def add(
    name: str = typer.Argument(..., help="Short project id, e.g. 'mysite'"),
    repo: str = typer.Option(..., "--repo", help="Git URL (ssh or https)"),
    path: str = typer.Option(..., "--path", help="Absolute local checkout path"),
    lc_project: str = typer.Option(..., "--lc-project", help="Laravel Cloud project ID"),
    lc_main_env: str = typer.Option("main", "--lc-main-env", help="Laravel Cloud env ID/name for main branch"),
    day: str = typer.Option(..., "--day", help="mon/tue/wed/thu/fri/sat/sun or 0-6"),
    hour: int = typer.Option(..., "--hour", min=0, max=23, help="Hour of day (local, 0-23)"),
    paths: str = typer.Option(..., "--paths", help="Comma-separated list of URL paths to screenshot, e.g. '/, /tests'"),
    test_cmd: Optional[str] = typer.Option(None, "--test-cmd", help="Unit test command run locally (e.g. 'php artisan test')"),
    mask: Optional[str] = typer.Option(None, "--mask", help="Comma-separated CSS selectors to hide before screenshotting"),
    tolerance: float = typer.Option(0.5, "--tolerance", help="Max %% of pixels allowed to differ per path"),
    audit_gate: str = typer.Option("high", "--audit-gate", help="npm audit severity gate: none|low|moderate|high|critical"),
    main_branch: str = typer.Option("main", "--main-branch"),
    update_branch: str = typer.Option("autoupdate", "--update-branch"),
    skip_tests: bool = typer.Option(False, "--skip-tests", help="Don't run unit tests"),
) -> None:
    """Register a new project."""
    _init()
    if db.get_project(name):
        rprint(f"[red]Project {name!r} already exists — use `wu edit` to modify it.[/red]")
        raise typer.Exit(1)

    # Validate inputs
    if tolerance < 0 or tolerance > 100:
        rprint(f"[red]--tolerance must be between 0 and 100 (got {tolerance})[/red]")
        raise typer.Exit(1)

    audit_gate_lower = audit_gate.lower()
    if audit_gate_lower not in ("none", "low", "moderate", "high", "critical"):
        rprint(f"[red]--audit-gate must be one of: none|low|moderate|high|critical (got {audit_gate!r})[/red]")
        raise typer.Exit(1)

    if not repo.strip():
        rprint("[red]--repo cannot be empty[/red]")
        raise typer.Exit(1)

    # Basic git URL validation
    if not (repo.startswith("git@") or repo.startswith("https://") or repo.startswith("http://")):
        rprint(f"[yellow]Warning: --repo doesn't look like a git URL: {repo!r}[/yellow]")

    path_abs = str(Path(path).expanduser().resolve())
    parent = Path(path_abs).parent
    if not parent.exists():
        rprint(f"[red]Parent directory does not exist: {parent}[/red]")
        rprint("[yellow]Create it first or choose a different path.[/yellow]")
        raise typer.Exit(1)

    if not _parse_paths(paths):
        rprint("[red]--paths cannot be empty[/red]")
        raise typer.Exit(1)

    dow = _parse_dow(day)
    db.add_project(
        name=name,
        repo_url=repo,
        local_path=path_abs,
        schedule_dow=dow,
        schedule_hour=hour,
        laravel_cloud_project_id=lc_project,
        laravel_cloud_main_env=lc_main_env,
        visual_diff_paths=_parse_paths(paths),
        mask_selectors=_parse_paths(mask) if mask else [],
        diff_tolerance_pct=tolerance,
        unit_test_cmd=test_cmd,
        skip_unit_tests=skip_tests,
        npm_audit_gate=audit_gate,
        main_branch=main_branch,
        update_branch=update_branch,
    )
    rprint(f"[green]Added project {name!r}[/green] (runs {DOW_NAMES[dow]} @ {hour:02d}:00)")


@app.command("list")
def list_cmd() -> None:
    """List all registered projects."""
    _init()
    projects = db.list_projects()
    if not projects:
        rprint("[yellow]No projects registered. Use `wu add ...`[/yellow]")
        return
    t = Table(title="websiteupdater projects")
    t.add_column("name")
    t.add_column("schedule")
    t.add_column("paths")
    t.add_column("enabled")
    t.add_column("last run")
    for p in projects:
        recent = db.recent_runs(p.id, limit=1)
        last = f"{recent[0]['status']} @ {recent[0]['started_at']}" if recent else "—"
        t.add_row(
            p.name,
            f"{DOW_NAMES[p.schedule_dow]} {p.schedule_hour:02d}:00",
            ", ".join(p.visual_diff_paths),
            "yes" if p.enabled else "no",
            last,
        )
    console.print(t)


@app.command()
def show(name: str) -> None:
    """Print full config for one project."""
    _init()
    p = _require_project(name)
    data = {
        "name": p.name,
        "repo_url": p.repo_url,
        "local_path": p.local_path,
        "main_branch": p.main_branch,
        "update_branch": p.update_branch,
        "schedule": f"{DOW_NAMES[p.schedule_dow]} {p.schedule_hour:02d}:00",
        "laravel_cloud_project_id": p.laravel_cloud_project_id,
        "laravel_cloud_main_env": p.laravel_cloud_main_env,
        "visual_diff_paths": p.visual_diff_paths,
        "mask_selectors": p.mask_selectors,
        "diff_tolerance_pct": p.diff_tolerance_pct,
        "unit_test_cmd": p.unit_test_cmd,
        "skip_unit_tests": p.skip_unit_tests,
        "npm_audit_gate": p.npm_audit_gate,
        "enabled": p.enabled,
    }
    rprint(data)


@app.command()
def edit(
    name: str,
    hour: Optional[int] = typer.Option(None, "--hour", min=0, max=23),
    day: Optional[str] = typer.Option(None, "--day"),
    paths: Optional[str] = typer.Option(None, "--paths"),
    mask: Optional[str] = typer.Option(None, "--mask"),
    tolerance: Optional[float] = typer.Option(None, "--tolerance"),
    audit_gate: Optional[str] = typer.Option(None, "--audit-gate"),
    test_cmd: Optional[str] = typer.Option(None, "--test-cmd"),
    skip_tests: Optional[bool] = typer.Option(None, "--skip-tests"),
    main_branch: Optional[str] = typer.Option(None, "--main-branch"),
    update_branch: Optional[str] = typer.Option(None, "--update-branch"),
    lc_project: Optional[str] = typer.Option(None, "--lc-project"),
    lc_main_env: Optional[str] = typer.Option(None, "--lc-main-env"),
    repo: Optional[str] = typer.Option(None, "--repo"),
    path: Optional[str] = typer.Option(None, "--path"),
) -> None:
    """Update one or more fields on a project."""
    _init()
    _require_project(name)
    fields: dict = {}
    if hour is not None: fields["schedule_hour"] = hour
    if day is not None: fields["schedule_dow"] = _parse_dow(day)
    if paths is not None: fields["visual_diff_paths"] = _parse_paths(paths)
    if mask is not None: fields["mask_selectors"] = _parse_paths(mask)
    if tolerance is not None: fields["diff_tolerance_pct"] = tolerance
    if audit_gate is not None: fields["npm_audit_gate"] = audit_gate
    if test_cmd is not None: fields["unit_test_cmd"] = test_cmd
    if skip_tests is not None: fields["skip_unit_tests"] = skip_tests
    if main_branch is not None: fields["main_branch"] = main_branch
    if update_branch is not None: fields["update_branch"] = update_branch
    if lc_project is not None: fields["laravel_cloud_project_id"] = lc_project
    if lc_main_env is not None: fields["laravel_cloud_main_env"] = lc_main_env
    if repo is not None: fields["repo_url"] = repo
    if path is not None: fields["local_path"] = str(Path(path).expanduser().resolve())
    if not fields:
        rprint("[yellow]Nothing to change.[/yellow]")
        return
    db.update_project(name, **fields)
    rprint(f"[green]Updated {name!r}[/green]: {', '.join(fields.keys())}")


@app.command()
def enable(name: str) -> None:
    _init()
    _require_project(name)
    db.update_project(name, enabled=True)
    rprint(f"[green]Enabled {name!r}[/green]")


@app.command()
def disable(name: str) -> None:
    _init()
    _require_project(name)
    db.update_project(name, enabled=False)
    rprint(f"[yellow]Disabled {name!r}[/yellow]")


@app.command()
def remove(name: str) -> None:
    _init()
    _require_project(name)
    db.remove_project(name)
    rprint(f"[green]Removed {name!r}[/green]")


@app.command()
def run(
    name: Optional[str] = typer.Argument(None, help="Project to run; omit with --due"),
    due: bool = typer.Option(False, "--due", help="Run every project whose schedule window has come"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop before pushing/deploying/merging"),
) -> None:
    """Run the update pipeline now."""
    _init()
    settings = Settings.load()

    if due:
        due_list = list(scheduler.due_projects())
        if not due_list:
            rprint("[yellow]Nothing due.[/yellow]")
            return
        for p in due_list:
            rprint(f"[bold]→ running {p.name}[/bold]")
            outcome = run_project(p, settings, dry_run=dry_run)
            _print_outcome(outcome)
        return

    if not name:
        rprint("[red]Pass a project name, or use --due.[/red]")
        raise typer.Exit(1)
    p = _require_project(name)
    outcome = run_project(p, settings, dry_run=dry_run)
    _print_outcome(outcome)
    if outcome.status == "failed":
        raise typer.Exit(1)


def _print_outcome(outcome) -> None:
    s = outcome.summary
    color = {
        "success": "green",
        "no_updates": "cyan",
        "failed": "red",
        "skipped": "yellow",
    }.get(outcome.status, "white")
    rprint(f"[{color}]{s.project_name}: {outcome.status} "
           f"(stage={s.stage or '-'}, merged={s.merged})[/{color}]")
    if outcome.failure_reason:
        rprint(f"  reason: {outcome.failure_reason.splitlines()[0]}")
    rprint(f"  artifacts: {s.artifacts_path}")


@app.command()
def history(name: str, n: int = typer.Option(10, "-n", help="Number of runs to show")) -> None:
    """Recent runs for a project."""
    _init()
    p = _require_project(name)
    rows = db.recent_runs(p.id, limit=n)
    t = Table(title=f"{name} — last {len(rows)} runs")
    t.add_column("started")
    t.add_column("status")
    t.add_column("stage")
    t.add_column("merged")
    t.add_column("reason")
    for r in rows:
        t.add_row(
            r["started_at"],
            r["status"],
            r["stage"] or "-",
            "yes" if r["merged"] else "no",
            (r["failure_reason"] or "").splitlines()[0][:80] if r["failure_reason"] else "",
        )
    console.print(t)


@app.command()
def logs(
    name: str,
    run_id: Optional[int] = typer.Option(None, "--run", help="Specific run id; default = latest"),
) -> None:
    """Print the run.log for a given run."""
    _init()
    p = _require_project(name)
    if run_id:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ? AND project_id = ?",
                               (run_id, p.id)).fetchone()
    else:
        rows = db.recent_runs(p.id, limit=1)
        row = rows[0] if rows else None
    if not row:
        rprint("[yellow]No matching run.[/yellow]")
        raise typer.Exit(1)
    log_path = Path(row["artifacts_path"]) / "run.log"
    if not log_path.exists():
        rprint(f"[yellow]{log_path} missing.[/yellow]")
        raise typer.Exit(1)
    console.print(log_path.read_text())


@app.command()
def doctor() -> None:
    """Check that all required external tools + creds are present."""
    _init()
    settings = Settings.load()
    results: list[tuple[str, bool, str]] = []

    for tool in ["git", "composer", "npm", "php"]:
        p = shutil.which(tool)
        results.append((tool, bool(p), p or "not found on PATH"))

    # Playwright Chromium binary
    try:
        import playwright  # noqa: F401
        r = subprocess.run(
            ["python", "-c", "from playwright.sync_api import sync_playwright;"
                             "p=sync_playwright().start();"
                             "b=p.chromium.launch();b.close();p.stop();print('ok')"],
            capture_output=True, text=True, timeout=30,
        )
        results.append(("playwright chromium", r.returncode == 0,
                        r.stdout.strip() or r.stderr.strip()[:120]))
    except Exception as e:
        results.append(("playwright chromium", False, f"{type(e).__name__}: {e}"))

    results.append(("LARAVEL_CLOUD_API_TOKEN",
                    bool(settings.laravel_cloud_token),
                    "set" if settings.laravel_cloud_token else "missing in .env"))
    results.append(("SMTP configured",
                    bool(settings.smtp_host and settings.mail_to),
                    f"{settings.smtp_host or '-'} → {settings.mail_to or '-'}"))
    results.append(("sqlite db", DB_PATH.exists(), str(DB_PATH)))

    t = Table(title="wu doctor")
    t.add_column("check")
    t.add_column("ok")
    t.add_column("detail")
    for name, ok, detail in results:
        t.add_row(name, "[green]✓[/green]" if ok else "[red]✗[/red]", detail)
    console.print(t)
    if not all(ok for _, ok, _ in results):
        raise typer.Exit(1)


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite schema (safe to run repeatedly)."""
    _init()
    rprint(f"[green]Initialised DB at {DB_PATH}[/green]")


@app.command()
def cleanup(
    days: Optional[int] = typer.Option(None, "--days", help="Keep runs from last N days (default: from .env)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
) -> None:
    """Clean up old run artifacts based on retention policy."""
    _init()
    settings = Settings.load()
    retention = days if days is not None else settings.artifact_retention_days

    if retention <= 0:
        rprint("[yellow]Retention is 0 (keep forever) — nothing to clean up.[/yellow]")
        return

    rprint(f"[cyan]Cleaning up artifacts older than {retention} days...[/cyan]")
    removed, bytes_freed = cleanup_old_artifacts(retention, dry_run=dry_run)

    if not removed:
        rprint("[green]No old artifacts to clean up.[/green]")
        return

    mb_freed = bytes_freed / (1024 * 1024)
    action = "Would delete" if dry_run else "Deleted"
    rprint(f"[green]{action} {len(removed)} run(s), freed {mb_freed:.1f} MB[/green]")
    if dry_run:
        rprint("[yellow]Run without --dry-run to actually delete.[/yellow]")
    for name in removed[:10]:
        rprint(f"  - {name}")
    if len(removed) > 10:
        rprint(f"  ... and {len(removed) - 10} more")


@app.command()
def artifacts(
    name: str,
    run_id: Optional[int] = typer.Option(None, "--run", help="Specific run id; default = latest"),
) -> None:
    """Show artifact directory path and contents for a run."""
    _init()
    p = _require_project(name)
    if run_id:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ? AND project_id = ?",
                               (run_id, p.id)).fetchone()
    else:
        rows = db.recent_runs(p.id, limit=1)
        row = rows[0] if rows else None

    if not row:
        rprint("[yellow]No matching run.[/yellow]")
        raise typer.Exit(1)

    artifacts_path = Path(row["artifacts_path"])
    rprint(f"[bold]Artifacts for {name} run {row['id']}:[/bold]")
    rprint(f"  Path: {artifacts_path}")
    rprint(f"  Status: {row['status']}")
    rprint(f"  Started: {row['started_at']}")

    if not artifacts_path.exists():
        rprint("[yellow]  Artifacts directory does not exist (may have been cleaned up)[/yellow]")
        return

    rprint(f"\n[bold]Contents:[/bold]")
    for item in sorted(artifacts_path.rglob("*")):
        if item.is_file():
            rel = item.relative_to(artifacts_path)
            size_kb = item.stat().st_size / 1024
            rprint(f"  {rel} ({size_kb:.1f} KB)")


@app.command()
def retry(
    name: str,
    run_id: Optional[int] = typer.Option(None, "--run", help="Specific run id to retry; default = latest failed"),
) -> None:
    """Retry a failed run (re-runs the entire pipeline)."""
    _init()
    p = _require_project(name)
    settings = Settings.load()

    if run_id:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ? AND project_id = ?",
                               (run_id, p.id)).fetchone()
    else:
        # Find latest failed run
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE project_id = ? AND status = 'failed' "
                "ORDER BY started_at DESC LIMIT 1",
                (p.id,)
            ).fetchone()

    if not row:
        rprint("[yellow]No matching failed run found.[/yellow]")
        raise typer.Exit(1)

    rprint(f"[cyan]Retrying {name} (original run {row['id']} from {row['started_at']})[/cyan]")
    rprint(f"  Original failure: {row['failure_reason'][:100] if row['failure_reason'] else 'unknown'}")
    rprint()

    outcome = run_project(p, settings, dry_run=False)
    _print_outcome(outcome)
    if outcome.status == "failed":
        raise typer.Exit(1)


@app.command()
def export(
    output: str = typer.Option("projects.json", "--output", "-o", help="Output JSON file path"),
    pretty: bool = typer.Option(True, "--pretty/--compact", help="Pretty-print JSON"),
) -> None:
    """Export all projects to a JSON file for easy editing."""
    _init()
    projects = db.list_projects()

    if not projects:
        rprint("[yellow]No projects to export.[/yellow]")
        return

    # Convert projects to JSON-serializable format
    projects_data = []
    for p in projects:
        projects_data.append({
            "name": p.name,
            "repo_url": p.repo_url,
            "local_path": p.local_path,
            "main_branch": p.main_branch,
            "update_branch": p.update_branch,
            "schedule": {
                "day": DOW_NAMES[p.schedule_dow],
                "hour": p.schedule_hour,
            },
            "laravel_cloud": {
                "project_id": p.laravel_cloud_project_id,
                "main_env": p.laravel_cloud_main_env,
            },
            "visual_diff": {
                "paths": p.visual_diff_paths,
                "mask_selectors": p.mask_selectors,
                "tolerance_pct": p.diff_tolerance_pct,
            },
            "testing": {
                "unit_test_cmd": p.unit_test_cmd,
                "skip_unit_tests": p.skip_unit_tests,
                "npm_audit_gate": p.npm_audit_gate,
            },
            "enabled": p.enabled,
        })

    output_path = Path(output)
    with output_path.open("w") as f:
        json.dump(projects_data, f, indent=2 if pretty else None)

    rprint(f"[green]Exported {len(projects_data)} project(s) to {output_path}[/green]")


@app.command()
def import_cmd(
    input_file: str = typer.Argument("projects.json", help="JSON file to import from"),
    update: bool = typer.Option(False, "--update", help="Update existing projects instead of skipping them"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be imported without importing"),
) -> None:
    """Import projects from a JSON file."""
    _init()
    input_path = Path(input_file)

    if not input_path.exists():
        rprint(f"[red]File not found: {input_path}[/red]")
        raise typer.Exit(1)

    try:
        with input_path.open("r") as f:
            projects_data = json.load(f)
    except json.JSONDecodeError as e:
        rprint(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(1)

    if not isinstance(projects_data, list):
        rprint("[red]JSON file must contain an array of projects[/red]")
        raise typer.Exit(1)

    added = []
    updated = []
    skipped = []
    errors = []

    for proj in projects_data:
        try:
            name = proj["name"]
            existing = db.get_project(name)

            # Parse schedule
            schedule = proj.get("schedule", {})
            dow = _parse_dow(schedule.get("day", "mon"))
            hour = schedule.get("hour", 3)

            # Parse Laravel Cloud settings
            lc = proj.get("laravel_cloud", {})
            lc_project_id = lc.get("project_id", "")
            lc_main_env = lc.get("main_env", "main")

            # Parse visual diff settings
            vd = proj.get("visual_diff", {})
            paths = vd.get("paths", ["/"])
            mask_selectors = vd.get("mask_selectors", [])
            tolerance = vd.get("tolerance_pct", 0.5)

            # Parse testing settings
            testing = proj.get("testing", {})
            test_cmd = testing.get("unit_test_cmd")
            skip_tests = testing.get("skip_unit_tests", False)
            npm_audit_gate = testing.get("npm_audit_gate", "high")

            if dry_run:
                action = "update" if existing and update else "add" if not existing else "skip"
                rprint(f"[cyan]Would {action}: {name}[/cyan]")
                continue

            if existing:
                if update:
                    db.update_project(
                        name,
                        repo_url=proj.get("repo_url", existing.repo_url),
                        local_path=proj.get("local_path", existing.local_path),
                        main_branch=proj.get("main_branch", existing.main_branch),
                        update_branch=proj.get("update_branch", existing.update_branch),
                        schedule_dow=dow,
                        schedule_hour=hour,
                        laravel_cloud_project_id=lc_project_id,
                        laravel_cloud_main_env=lc_main_env,
                        visual_diff_paths=paths,
                        mask_selectors=mask_selectors,
                        diff_tolerance_pct=tolerance,
                        unit_test_cmd=test_cmd,
                        skip_unit_tests=skip_tests,
                        npm_audit_gate=npm_audit_gate,
                        enabled=proj.get("enabled", True),
                    )
                    updated.append(name)
                else:
                    skipped.append(name)
            else:
                db.add_project(
                    name=name,
                    repo_url=proj["repo_url"],
                    local_path=proj.get("local_path", f"{Settings.load().workspace_dir}/{name}"),
                    main_branch=proj.get("main_branch", "main"),
                    update_branch=proj.get("update_branch", "autoupdate"),
                    schedule_dow=dow,
                    schedule_hour=hour,
                    laravel_cloud_project_id=lc_project_id,
                    laravel_cloud_main_env=lc_main_env,
                    visual_diff_paths=paths,
                    mask_selectors=mask_selectors,
                    diff_tolerance_pct=tolerance,
                    unit_test_cmd=test_cmd,
                    skip_unit_tests=skip_tests,
                    npm_audit_gate=npm_audit_gate,
                    enabled=proj.get("enabled", True),
                )
                added.append(name)

        except KeyError as e:
            errors.append(f"{proj.get('name', '?')}: missing required field {e}")
        except Exception as e:
            errors.append(f"{proj.get('name', '?')}: {e}")

    # Print summary
    if added:
        rprint(f"[green]Added {len(added)} project(s): {', '.join(added)}[/green]")
    if updated:
        rprint(f"[cyan]Updated {len(updated)} project(s): {', '.join(updated)}[/cyan]")
    if skipped:
        rprint(f"[yellow]Skipped {len(skipped)} existing project(s): {', '.join(skipped)}[/yellow]")
        rprint("[yellow]Use --update to update existing projects[/yellow]")
    if errors:
        rprint(f"[red]Errors ({len(errors)}):[/red]")
        for err in errors:
            rprint(f"  - {err}")
        raise typer.Exit(1)

    if dry_run:
        rprint("[yellow]Dry run complete. Use without --dry-run to actually import.[/yellow]")


@app.command()
def sync(
    config_file: str = typer.Option("projects.json", "--config", "-c", help="Config file to sync from"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change without making changes"),
) -> None:
    """Sync database with projects.json file (imports/updates all projects from file)."""
    _init()
    config_path = Path(config_file)

    # If relative path, check project root first
    if not config_path.is_absolute():
        root_config = ROOT / config_path
        if root_config.exists():
            config_path = root_config

    if not config_path.exists():
        rprint(f"[yellow]Config file not found: {config_path}[/yellow]")
        rprint("[cyan]Create one with: wu export -o projects.json[/cyan]")
        return

    rprint(f"[cyan]Syncing from {config_path}...[/cyan]")

    try:
        with config_path.open("r") as f:
            projects_data = json.load(f)
    except json.JSONDecodeError as e:
        rprint(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(1)

    if not isinstance(projects_data, list):
        rprint("[red]JSON file must contain an array of projects[/red]")
        raise typer.Exit(1)

    # Get existing projects
    existing_projects = {p.name: p for p in db.list_projects()}
    config_project_names = {proj["name"] for proj in projects_data if "name" in proj}

    added = []
    updated = []
    removed = []
    errors = []

    # Import/update all projects from config
    for proj in projects_data:
        try:
            name = proj["name"]
            existing = existing_projects.get(name)

            # Parse all fields
            schedule = proj.get("schedule", {})
            dow = _parse_dow(schedule.get("day", "mon"))
            hour = schedule.get("hour", 3)

            lc = proj.get("laravel_cloud", {})
            lc_project_id = lc.get("project_id", "")
            lc_main_env = lc.get("main_env", "main")

            vd = proj.get("visual_diff", {})
            paths = vd.get("paths", ["/"])
            mask_selectors = vd.get("mask_selectors", [])
            tolerance = vd.get("tolerance_pct", 0.5)

            testing = proj.get("testing", {})
            test_cmd = testing.get("unit_test_cmd")
            skip_tests = testing.get("skip_unit_tests", False)
            npm_audit_gate = testing.get("npm_audit_gate", "high")

            if dry_run:
                action = "update" if existing else "add"
                rprint(f"[cyan]Would {action}: {name}[/cyan]")
                continue

            if existing:
                db.update_project(
                    name,
                    repo_url=proj.get("repo_url", existing.repo_url),
                    local_path=proj.get("local_path", existing.local_path),
                    main_branch=proj.get("main_branch", existing.main_branch),
                    update_branch=proj.get("update_branch", existing.update_branch),
                    schedule_dow=dow,
                    schedule_hour=hour,
                    laravel_cloud_project_id=lc_project_id,
                    laravel_cloud_main_env=lc_main_env,
                    visual_diff_paths=paths,
                    mask_selectors=mask_selectors,
                    diff_tolerance_pct=tolerance,
                    unit_test_cmd=test_cmd,
                    skip_unit_tests=skip_tests,
                    npm_audit_gate=npm_audit_gate,
                    enabled=proj.get("enabled", True),
                )
                updated.append(name)
            else:
                db.add_project(
                    name=name,
                    repo_url=proj["repo_url"],
                    local_path=proj.get("local_path", f"{Settings.load().workspace_dir}/{name}"),
                    main_branch=proj.get("main_branch", "main"),
                    update_branch=proj.get("update_branch", "autoupdate"),
                    schedule_dow=dow,
                    schedule_hour=hour,
                    laravel_cloud_project_id=lc_project_id,
                    laravel_cloud_main_env=lc_main_env,
                    visual_diff_paths=paths,
                    mask_selectors=mask_selectors,
                    diff_tolerance_pct=tolerance,
                    unit_test_cmd=test_cmd,
                    skip_unit_tests=skip_tests,
                    npm_audit_gate=npm_audit_gate,
                    enabled=proj.get("enabled", True),
                )
                added.append(name)

        except KeyError as e:
            errors.append(f"{proj.get('name', '?')}: missing required field {e}")
        except Exception as e:
            errors.append(f"{proj.get('name', '?')}: {e}")

    # Find projects in DB that aren't in config (optional: remove them)
    for name in existing_projects:
        if name not in config_project_names:
            removed.append(name)
            if dry_run:
                rprint(f"[yellow]Would remove (not in config): {name}[/yellow]")

    # Print summary
    if added:
        rprint(f"[green]Added {len(added)} project(s): {', '.join(added)}[/green]")
    if updated:
        rprint(f"[cyan]Updated {len(updated)} project(s): {', '.join(updated)}[/cyan]")
    if removed:
        rprint(f"[yellow]Found {len(removed)} project(s) in DB but not in config: {', '.join(removed)}[/yellow]")
        rprint("[yellow]Use 'wu remove <name>' to delete them if needed[/yellow]")
    if errors:
        rprint(f"[red]Errors ({len(errors)}):[/red]")
        for err in errors:
            rprint(f"  - {err}")
        raise typer.Exit(1)

    if dry_run:
        rprint("[yellow]Dry run complete. Use without --dry-run to actually sync.[/yellow]")
    elif not added and not updated:
        rprint("[green]Everything is in sync![/green]")


if __name__ == "__main__":
    app()
