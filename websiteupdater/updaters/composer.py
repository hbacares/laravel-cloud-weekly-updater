"""Composer update driver."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ComposerResult:
    returncode: int
    stdout: str
    stderr: str
    updates: list["PackageUpdate"]

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class PackageUpdate:
    name: str
    from_version: str
    to_version: str


# Matches lines like:
#   - Upgrading vendor/package (1.2.3 => 1.2.4)
#   - Downgrading vendor/package (1.2.4 => 1.2.3)
_PKG_RE = re.compile(
    r"^\s*-\s+(?:Upgrading|Downgrading|Updating)\s+([^\s]+)\s+\(([^\s]+)\s*=>\s*([^\)]+)\)",
    re.MULTILINE,
)


def run(project_path: Path, log_path: Path, *, composer_bin: str = "/opt/homebrew/bin/composer") -> ComposerResult:
    """`composer update --no-dev` inside project_path. Writes stdout+stderr to log_path."""
    proc = subprocess.run(
        [composer_bin, "update", "--no-dev", "--no-interaction", "--no-progress"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
    )
    combined = f"$ composer update --no-dev\n\n{proc.stdout}\n---STDERR---\n{proc.stderr}\n"
    log_path.write_text(combined)

    updates: list[PackageUpdate] = []
    # Composer often puts the "Upgrading" lines in stderr because it uses it
    # for progress output. Parse both streams.
    for stream in (proc.stdout, proc.stderr):
        for m in _PKG_RE.finditer(stream):
            updates.append(PackageUpdate(m.group(1), m.group(2).strip(), m.group(3).strip()))

    return ComposerResult(proc.returncode, proc.stdout, proc.stderr, updates)
