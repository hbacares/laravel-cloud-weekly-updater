"""Run the project's unit tests locally after the dep bump."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TestResult:
    skipped: bool
    returncode: int
    stdout: str
    stderr: str
    command: Optional[str]

    @property
    def ok(self) -> bool:
        return self.skipped or self.returncode == 0


def run(project_path: Path, cmd: Optional[str], log_path: Path,
        skip: bool = False) -> TestResult:
    if skip or not cmd:
        log_path.write_text("(unit tests skipped)\n")
        return TestResult(skipped=True, returncode=0, stdout="", stderr="", command=cmd)

    parts = shlex.split(cmd)
    proc = subprocess.run(
        parts,
        cwd=str(project_path),
        capture_output=True,
        text=True,
    )
    log_path.write_text(
        f"$ {cmd}\n\n{proc.stdout}\n---STDERR---\n{proc.stderr}\n"
    )
    return TestResult(
        skipped=False,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        command=cmd,
    )
