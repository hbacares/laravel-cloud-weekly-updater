"""npm update + npm audit driver."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SEVERITY_ORDER = ["info", "low", "moderate", "high", "critical"]


@dataclass
class NpmUpdateResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class AuditResult:
    raw: dict
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    gate_tripped: bool = False
    gate_reason: Optional[str] = None

    @property
    def summary_line(self) -> str:
        if not self.raw:
            return "(no package-lock.json, audit skipped)"
        if self.total == 0:
            return "0 vulnerabilities"
        parts = [f"{self.counts.get(s, 0)} {s}" for s in SEVERITY_ORDER if self.counts.get(s)]
        return f"{self.total} vulnerabilities: " + ", ".join(parts)


def run_update(project_path: Path, log_path: Path, *, npm_bin: str = "/opt/homebrew/bin/npm") -> NpmUpdateResult:
    proc = subprocess.run(
        [npm_bin, "update"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
    )
    log_path.write_text(
        f"$ npm update\n\n{proc.stdout}\n---STDERR---\n{proc.stderr}\n"
    )
    return NpmUpdateResult(proc.returncode, proc.stdout, proc.stderr)


def run_audit(project_path: Path, log_path: Path, gate: str = "high", *, npm_bin: str = "/opt/homebrew/bin/npm") -> AuditResult:
    """Run `npm audit --json` and evaluate against `gate` (one of SEVERITY_ORDER or 'none')."""
    proc = subprocess.run(
        [npm_bin, "audit", "--json"],
        cwd=str(project_path),
        capture_output=True,
        text=True,
    )
    # npm audit exits non-zero when vulns exist — that's fine.
    log_path.write_text(proc.stdout)

    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        data = {"error": "failed to parse npm audit output", "raw": proc.stdout[:500]}

    counts: dict[str, int] = {}
    # npm v7+ shape: metadata.vulnerabilities = {info, low, moderate, high, critical, total}
    meta_vulns = (data.get("metadata") or {}).get("vulnerabilities") or {}
    for sev in SEVERITY_ORDER:
        counts[sev] = int(meta_vulns.get(sev, 0) or 0)
    total = int(meta_vulns.get("total", sum(counts.values())) or 0)

    gate_tripped = False
    gate_reason: Optional[str] = None
    if gate and gate.lower() != "none":
        gate = gate.lower()
        if gate not in SEVERITY_ORDER:
            gate = "high"
        threshold_idx = SEVERITY_ORDER.index(gate)
        for sev in SEVERITY_ORDER[threshold_idx:]:
            if counts.get(sev, 0) > 0:
                gate_tripped = True
                gate_reason = (
                    f"npm audit found {counts[sev]} {sev} vulnerabilities "
                    f"(gate: {gate}+)"
                )
                break

    return AuditResult(
        raw=data,
        counts=counts,
        total=total,
        gate_tripped=gate_tripped,
        gate_reason=gate_reason,
    )
