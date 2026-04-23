"""Logging + optional macOS notification center."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip() + "\n")


def macos_notify(title: str, message: str) -> None:
    """Fire-and-forget osascript notification. Silent on non-mac or failure."""
    try:
        escaped_msg = message.replace('"', '\\"')[:300]
        escaped_title = title.replace('"', '\\"')[:100]
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{escaped_msg}" with title "{escaped_title}"',
            ],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        logger.debug(f"macOS notification failed (non-fatal): {type(e).__name__}: {e}")
