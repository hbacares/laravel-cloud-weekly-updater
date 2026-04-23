"""End-to-end update pipeline for a single project."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from websiteupdater import db, git_ops, visual_diff
from websiteupdater.config import Settings, run_dir
from websiteupdater.email_report import send as send_email
from websiteupdater.laravel_cloud import LaravelCloudClient, LaravelCloudError
from websiteupdater.lockfile import project_lock, LockError
from websiteupdater.notify import macos_notify, write_log
from websiteupdater.reports import RunSummary, subject_line
from websiteupdater.tests_runner import run as run_tests
from websiteupdater.updaters import composer as composer_updater
from websiteupdater.updaters import npm as npm_updater


STAGES = [
    "fetch",
    "cleanup",
    "update",
    "tests",
    "commit_push",
    "deploy",
    "screenshot_diff",
    "merge",
]


@dataclass
class RunOutcome:
    status: str                   # success | failed | no_updates | skipped
    stage: Optional[str]
    failure_reason: Optional[str]
    merged: bool
    summary: RunSummary


def _log(artifacts: Path, *lines: str) -> None:
    write_log(artifacts / "run.log", [f"[{datetime.now().isoformat(timespec='seconds')}] " + l for l in lines])


def _fail(summary: RunSummary, stage: str, reason: str) -> RunOutcome:
    summary.status = "failed"
    summary.stage = stage
    summary.failure_reason = reason
    return RunOutcome(status="failed", stage=stage, failure_reason=reason,
                      merged=False, summary=summary)


def run_project(project: db.Project, settings: Settings, *, dry_run: bool = False) -> RunOutcome:
    """Execute the whole pipeline for one project, persisting progress to the DB."""
    # Acquire exclusive lock to prevent concurrent runs of the same project
    try:
        with project_lock(project.name):
            return _run_project_impl(project, settings, dry_run=dry_run)
    except LockError as e:
        # Project already running — return a skipped outcome
        return RunOutcome(
            status="skipped",
            stage=None,
            failure_reason=str(e),
            merged=False,
            summary=RunSummary(
                project_name=project.name,
                run_id=None,
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                finished_at=None,
                status="skipped",
                stage=None,
                failure_reason=str(e),
                commit_sha=None,
                merged=False,
                artifacts_path=None,
                unit_test_cmd=None,
            ),
        )


def _run_project_impl(project: db.Project, settings: Settings, *, dry_run: bool = False) -> RunOutcome:
    """Internal implementation of run_project (wrapped by lock)."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifacts = run_dir(project.name, started)
    run_id = db.start_run(project.id, artifacts)
    _log(artifacts, f"=== run start (project={project.name}, dry_run={dry_run}) ===")

    summary = RunSummary(
        project_name=project.name,
        run_id=run_id,
        started_at=started,
        finished_at=None,
        status="running",
        stage=None,
        failure_reason=None,
        commit_sha=None,
        merged=False,
        artifacts_path=str(artifacts),
        unit_test_cmd=project.unit_test_cmd,
    )

    lc: Optional[LaravelCloudClient] = None
    ephemeral_env_id: Optional[str] = None
    app_id: Optional[str] = None

    try:
        local_path = Path(project.local_path).expanduser()

        # ---- 1. fetch ----
        db.update_run(run_id, stage="fetch")
        _log(artifacts, f"fetch: ensuring {local_path} tracks {project.main_branch}")
        try:
            git_ops.clone_or_fetch(project.repo_url, local_path, project.main_branch)
        except git_ops.GitError as e:
            return _fail(summary, "fetch", str(e))

        # ---- 2. cleanup existing autoupdate branch ----
        db.update_run(run_id, stage="cleanup")
        _log(artifacts, f"cleanup: removing existing {project.update_branch} branch (local + remote)")
        try:
            git_ops.cleanup_branch(local_path, project.update_branch, project.main_branch)
        except git_ops.GitError as e:
            return _fail(summary, "cleanup", str(e))

        _log(artifacts, "cleanup: waiting 120s for Laravel Cloud to process branch deletion...")
        time.sleep(120)

        # ---- 3. update on autoupdate branch ----
        db.update_run(run_id, stage="update")
        _log(artifacts, f"update: recreating branch {project.update_branch}")
        git_ops.recreate_branch(local_path, project.update_branch, project.main_branch)

        composer_res = composer_updater.run(local_path, artifacts / "composer.out", composer_bin=settings.composer_bin)
        summary.composer_ok = composer_res.ok
        summary.composer_updates = composer_res.updates
        _log(artifacts, f"composer update: rc={composer_res.returncode}, "
                        f"{len(composer_res.updates)} package(s) bumped")
        if not composer_res.ok:
            return _fail(summary, "update", "composer update failed — see composer.out")

        npm_update = npm_updater.run_update(local_path, artifacts / "npm.out", npm_bin=settings.npm_bin)
        summary.npm_update_ok = npm_update.ok
        _log(artifacts, f"npm update: rc={npm_update.returncode}")
        if not npm_update.ok:
            return _fail(summary, "update", "npm update failed — see npm.out")

        audit = npm_updater.run_audit(
            local_path, artifacts / "npm-audit.json",
            gate=project.npm_audit_gate,
            npm_bin=settings.npm_bin,
        )
        summary.npm_audit = audit
        db.update_run(run_id, npm_audit_summary={
            "counts": audit.counts, "total": audit.total,
            "gate_tripped": audit.gate_tripped, "gate_reason": audit.gate_reason,
        })
        _log(artifacts, f"npm audit: {audit.summary_line}")
        if audit.gate_tripped:
            return _fail(summary, "update", audit.gate_reason or "npm audit gate tripped")

        # ---- 3. unit tests (locally, before pushing) ----
        db.update_run(run_id, stage="tests")
        tests = run_tests(
            local_path, project.unit_test_cmd, artifacts / "tests.out",
            skip=project.skip_unit_tests,
        )
        summary.unit_tests_skipped = tests.skipped
        summary.unit_tests_passed = tests.ok if not tests.skipped else None
        _log(artifacts, f"tests: ok={tests.ok} skipped={tests.skipped}")
        if not tests.ok:
            return _fail(summary, "tests", "unit tests failed — see tests.out")

        # ---- 4. commit & push ----
        db.update_run(run_id, stage="commit_push")
        summary.lockfile_diff = git_ops.lockfile_diff_summary(local_path, project.main_branch)

        commit_msg_lines = ["autoupdate: bump composer + npm dependencies", ""]
        for u in composer_res.updates:
            commit_msg_lines.append(f"  composer: {u.name} {u.from_version} -> {u.to_version}")
        commit_msg = "\n".join(commit_msg_lines)

        sha = git_ops.commit_all(local_path, commit_msg,
                                 paths=["composer.lock", "package-lock.json"])
        if sha is None:
            # Nothing changed in the lockfiles — mark as no_updates and exit clean.
            summary.status = "no_updates"
            summary.stage = "commit_push"
            _log(artifacts, "commit_push: no lockfile changes — recording no_updates")
            return RunOutcome(status="no_updates", stage="commit_push",
                              failure_reason=None, merged=False, summary=summary)
        summary.commit_sha = sha

        if dry_run:
            _log(artifacts, "dry_run: skipping push/deploy/diff/merge")
            summary.status = "success"
            summary.stage = "commit_push"
            return RunOutcome(status="success", stage="commit_push",
                              failure_reason=None, merged=False, summary=summary)

        git_ops.force_push(local_path, project.update_branch)
        _log(artifacts, f"pushed {project.update_branch}@{sha[:12]}")

        # ---- 5. Laravel Cloud: wait for auto-created ephemeral env + deploy ----
        db.update_run(run_id, stage="deploy")
        lc = LaravelCloudClient(settings)

        # Resolve application slug to ID if needed
        try:
            app_id = lc.resolve_application_id(project.laravel_cloud_project_id)
            main_env_id = lc.resolve_environment_id(
                project_id=app_id, name_or_id=project.laravel_cloud_main_env
            )
        except LaravelCloudError as e:
            return _fail(summary, "deploy", f"resolve IDs: {e}")

        try:
            # Laravel Cloud auto-creates environments when branch is pushed
            _log(artifacts, f"waiting for Laravel Cloud to auto-create environment for {project.update_branch}...")
            env = lc.wait_for_environment_by_branch(
                project_id=app_id,
                branch=project.update_branch,
                timeout=float(settings.lc_env_create_timeout),
                poll_interval=5.0,
            )
        except LaravelCloudError as e:
            return _fail(summary, "deploy", f"wait for auto-created env: {e}")
        ephemeral_env_id = env.id
        _log(artifacts, f"found ephemeral env id={env.id} name={env.name}")

        try:
            deployment = lc.wait_for_deployment(
                project_id=app_id,
                env_id=env.id,
            )
            update_url = deployment.url or lc.resolve_environment_url(
                project_id=app_id, env_id=env.id,
            )
            summary.deploy_state = deployment.state
            summary.deploy_url = update_url
        except LaravelCloudError as e:
            return _fail(summary, "deploy", str(e))

        # Main URL comes from the main env (stable in Laravel Cloud).
        try:
            main_url = lc.resolve_environment_url(
                project_id=app_id,
                env_id=main_env_id,
            )
        except LaravelCloudError as e:
            return _fail(summary, "deploy", f"resolve main env url: {e}")

        _log(artifacts, f"main_url={main_url} update_url={update_url}")

        # ---- 6. screenshots + visual diff ----
        db.update_run(run_id, stage="screenshot_diff")
        try:
            diff_report = visual_diff.run(
                main_base_url=main_url,
                update_base_url=update_url,
                paths=project.visual_diff_paths,
                mask_selectors=project.mask_selectors,
                tolerance_pct=project.diff_tolerance_pct,
                artifacts_dir=artifacts,
                viewport_w=settings.screenshot_width,
                viewport_h=settings.screenshot_height,
                navigation_timeout=settings.playwright_navigation_timeout,
                mask_wait=settings.playwright_mask_wait,
            )
        except Exception as e:
            return _fail(summary, "screenshot_diff",
                         f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        summary.diff_report = diff_report
        _log(artifacts, f"visual diff: passed={diff_report.passed} "
                        f"paths={len(diff_report.results)}")
        if not diff_report.passed:
            failed = [f"{r.path} ({r.diff_pct:.2f}%)" for r in diff_report.failed_paths]
            return _fail(summary, "screenshot_diff",
                         f"visual regression on: {', '.join(failed)}")

        # ---- 7. merge to main ----
        db.update_run(run_id, stage="merge")
        merge_sha = git_ops.merge_into_main(
            local_path, project.update_branch, project.main_branch,
            message=f"autoupdate: {project.name} {started}",
        )
        _log(artifacts, f"merged to {project.main_branch}@{merge_sha[:12]}")
        summary.merged = True

        # Clean up the autoupdate branch remotely.
        git_ops.delete_remote_branch(local_path, project.update_branch)

        summary.status = "success"
        summary.stage = "merge"
        return RunOutcome(status="success", stage="merge",
                          failure_reason=None, merged=True, summary=summary)

    except Exception as e:
        reason = f"unexpected {type(e).__name__}: {e}\n{traceback.format_exc()}"
        _log(artifacts, "FATAL: " + reason)
        return _fail(summary, summary.stage or "unknown", reason)

    finally:
        # Tear down ephemeral env (skip if KEEP_FAILED_ENVS=1 and run failed).
        should_teardown = True
        if settings.keep_failed_envs and summary.status == "failed":
            should_teardown = False
            _log(artifacts, f"keeping ephemeral env {ephemeral_env_id} for debugging (KEEP_FAILED_ENVS=1)")

        if lc is not None and ephemeral_env_id and app_id and should_teardown:
            try:
                lc.delete_environment(
                    project_id=app_id,
                    env_id=ephemeral_env_id,
                )
                _log(artifacts, f"torn down ephemeral env {ephemeral_env_id}")
            except LaravelCloudError as e:
                _log(artifacts, f"tear-down failed (non-fatal): {e}")
        if lc is not None:
            lc.close()

        # Persist final state + dispatch notifications.
        summary.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.finish_run(
            run_id,
            status=summary.status,
            stage=summary.stage,
            failure_reason=summary.failure_reason,
            merged=summary.merged,
        )
        db.update_run(run_id, commit_sha=summary.commit_sha)
        _log(artifacts, f"=== run finish: status={summary.status} stage={summary.stage} ===")

        try:
            send_email(summary, settings)
        except Exception as e:
            _log(artifacts, f"email send failed (non-fatal): {type(e).__name__}: {e}")

        if settings.notify_macos and summary.status not in ("success", "no_updates"):
            macos_notify(
                f"websiteupdater: {project.name}",
                subject_line(summary),
            )
