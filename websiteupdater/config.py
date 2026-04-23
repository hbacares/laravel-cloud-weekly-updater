"""Env-backed settings and filesystem paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of this package dir.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
DB_PATH = DATA_DIR / "websiteupdater.db"
ENV_PATH = ROOT / ".env"

# Load .env once at import time. Safe to call repeatedly.
load_dotenv(ENV_PATH)


def _bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    laravel_cloud_token: str
    laravel_cloud_base: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    mail_from: str
    mail_to: str
    notify_macos: bool
    keep_failed_envs: bool
    lc_poll_interval: int
    lc_env_create_timeout: int
    lc_deploy_timeout: int
    screenshot_width: int
    screenshot_height: int
    playwright_navigation_timeout: int
    playwright_mask_wait: int
    artifact_retention_days: int
    composer_bin: str
    npm_bin: str
    workspace_dir: str

    @classmethod
    def load(cls) -> "Settings":
        workspace = os.environ.get("WORKSPACE_DIR", "~/dev")
        # Expand ~ to home directory
        workspace = os.path.expanduser(workspace)

        return cls(
            laravel_cloud_token=os.environ.get("LARAVEL_CLOUD_API_TOKEN", ""),
            laravel_cloud_base=os.environ.get(
                "LARAVEL_CLOUD_API_BASE", "https://cloud.laravel.com/api"
            ).rstrip("/"),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=_int("SMTP_PORT", 587),
            smtp_username=os.environ.get("SMTP_USERNAME", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_use_tls=_bool("SMTP_USE_TLS", True),
            mail_from=os.environ.get("MAIL_FROM", "websiteupdater <reports@localhost>"),
            mail_to=os.environ.get("MAIL_TO", ""),
            notify_macos=_bool("NOTIFY_MACOS", False),
            keep_failed_envs=_bool("KEEP_FAILED_ENVS", False),
            lc_poll_interval=_int("LC_POLL_INTERVAL", 10),
            lc_env_create_timeout=_int("LC_ENV_CREATE_TIMEOUT", 300),
            lc_deploy_timeout=_int("LC_DEPLOY_TIMEOUT", 900),
            screenshot_width=_int("SCREENSHOT_WIDTH", 1440),
            screenshot_height=_int("SCREENSHOT_HEIGHT", 900),
            playwright_navigation_timeout=_int("PLAYWRIGHT_NAVIGATION_TIMEOUT", 45000),
            playwright_mask_wait=_int("PLAYWRIGHT_MASK_WAIT", 250),
            artifact_retention_days=_int("ARTIFACT_RETENTION_DAYS", 30),
            composer_bin=os.environ.get("COMPOSER_BIN", "/opt/homebrew/bin/composer"),
            npm_bin=os.environ.get("NPM_BIN", "/opt/homebrew/bin/npm"),
            workspace_dir=workspace,
        )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def run_dir(project_name: str, started_at_iso: str) -> Path:
    """`data/runs/<project>-<YYYYMMDD-HHMMSS>/`"""
    slug = started_at_iso.replace(":", "").replace("-", "").replace("T", "-").split(".")[0]
    d = RUNS_DIR / f"{project_name}-{slug}"
    (d / "screens" / "main").mkdir(parents=True, exist_ok=True)
    (d / "screens" / "autoupdate").mkdir(parents=True, exist_ok=True)
    (d / "screens" / "diff").mkdir(parents=True, exist_ok=True)
    return d
