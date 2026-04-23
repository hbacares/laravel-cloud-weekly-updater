"""Render and send the per-run email report via SMTP.

HTML body embeds main/autoupdate/diff screenshots inline using CID attachments
and also attaches the raw PNGs.
"""

from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, BaseLoader, select_autoescape

from websiteupdater.config import Settings
from websiteupdater.reports import RunSummary, subject_line

logger = logging.getLogger(__name__)


_HTML_TMPL = """\
<html>
<head>
  <meta charset="utf-8">
  <style>
    body { font-family: -apple-system, "Helvetica Neue", sans-serif; color: #222; font-size: 14px; line-height: 1.5; }
    h1, h2, h3 { margin: 1em 0 0.3em; }
    .status-success { color: #0a7b34; }
    .status-failed  { color: #b3261e; }
    .status-noup    { color: #555; }
    .kv { border-collapse: collapse; margin: 0.5em 0; }
    .kv td { padding: 2px 10px 2px 0; vertical-align: top; }
    .kv td:first-child { color: #666; }
    pre { background: #f5f5f5; padding: 10px; border-radius: 4px;
          font-family: "SF Mono", ui-monospace, monospace; font-size: 12px;
          white-space: pre-wrap; word-break: break-word; overflow-x: auto; }
    .screens { display: block; margin: 0.5em 0 1em; }
    .screens img { max-width: 100%; border: 1px solid #ddd; display: block; margin: 6px 0; }
    .path-block { margin-bottom: 24px; padding: 10px; background: #fafafa;
                  border: 1px solid #eee; border-radius: 4px; }
    .pass { color: #0a7b34; font-weight: 600; }
    .fail { color: #b3261e; font-weight: 600; }
  </style>
</head>
<body>
  <h1>
    {{ project }}
    {% if status == 'success' %}<span class="status-success">— SUCCESS</span>
    {% elif status == 'no_updates' %}<span class="status-noup">— no updates</span>
    {% elif status == 'skipped' %}<span class="status-noup">— skipped</span>
    {% else %}<span class="status-failed">— FAILED{% if stage %} at {{ stage }}{% endif %}</span>
    {% endif %}
  </h1>

  <table class="kv">
    <tr><td>Run ID</td><td>{{ run_id }}</td></tr>
    <tr><td>Started</td><td>{{ started_at }}</td></tr>
    <tr><td>Finished</td><td>{{ finished_at or '(still running)' }}</td></tr>
    {% if commit_sha %}<tr><td>Commit</td><td><code>{{ commit_sha[:12] }}</code></td></tr>{% endif %}
    <tr><td>Merged</td><td>{{ 'yes' if merged else 'no' }}</td></tr>
    <tr><td>Artifacts</td><td><code>{{ artifacts_path }}</code></td></tr>
    {% if deploy_url %}<tr><td>Preview URL</td><td>{{ deploy_url }}</td></tr>{% endif %}
    {% if deploy_state %}<tr><td>Deploy state</td><td>{{ deploy_state }}</td></tr>{% endif %}
  </table>

  {% if failure_reason %}
    <h2>Failure reason</h2>
    <pre>{{ failure_reason }}</pre>
  {% endif %}

  <h2>Composer</h2>
  {% if composer_ok is none %}
    <p>(not run)</p>
  {% elif composer_updates %}
    <p>{{ composer_updates|length }} package(s) updated:</p>
    <pre>{% for u in composer_updates %}{{ u.name }}: {{ u.from_version }} → {{ u.to_version }}
{% endfor %}</pre>
  {% else %}
    <p>No composer packages bumped.</p>
  {% endif %}

  <h2>npm audit</h2>
  {% if npm_audit is none %}
    <p>(not run)</p>
  {% else %}
    <p>{{ npm_audit.summary_line }}
       {% if npm_audit.gate_tripped %}<span class="fail">— gate tripped: {{ npm_audit.gate_reason }}</span>{% endif %}
    </p>
  {% endif %}

  <h2>Unit tests</h2>
  {% if unit_tests_skipped %}
    <p>Skipped (no test command configured).</p>
  {% elif unit_tests_passed is none %}
    <p>(not run — earlier stage failed)</p>
  {% elif unit_tests_passed %}
    <p class="pass">PASSED — <code>{{ unit_test_cmd }}</code></p>
  {% else %}
    <p class="fail">FAILED — <code>{{ unit_test_cmd }}</code></p>
  {% endif %}

  {% if lockfile_diff %}
    <h2>Lockfile diff</h2>
    <pre>{{ lockfile_diff }}</pre>
  {% endif %}

  <h2>Visual regression</h2>
  {% if diff_report is none %}
    <p>(not run)</p>
  {% else %}
    <p>Tolerance: {{ "%.2f"|format(diff_report.tolerance_pct) }}% of pixels per path.
    Overall: {% if diff_report.passed %}<span class="pass">PASS</span>{% else %}<span class="fail">FAIL</span>{% endif %}</p>

    {% for r in diff_report.results %}
      <div class="path-block">
        <h3>
          <code>{{ r.path }}</code>
          {% if r.passed %}<span class="pass">pass</span>{% else %}<span class="fail">fail</span>{% endif %}
          — {{ "%.3f"|format(r.diff_pct) }}% differing
          {% if r.error %}<span class="fail">({{ r.error }})</span>{% endif %}
        </h3>
        <div class="screens">
          <strong>main</strong>
          {% if cid_map.get('main:' ~ r.path) %}<img src="cid:{{ cid_map['main:' ~ r.path] }}" alt="main">{% endif %}
          <strong>autoupdate</strong>
          {% if cid_map.get('upd:' ~ r.path) %}<img src="cid:{{ cid_map['upd:' ~ r.path] }}" alt="autoupdate">{% endif %}
          <strong>diff</strong>
          {% if cid_map.get('diff:' ~ r.path) %}<img src="cid:{{ cid_map['diff:' ~ r.path] }}" alt="diff">{% endif %}
        </div>
      </div>
    {% endfor %}
  {% endif %}

  <hr>
  <p style="color:#888;font-size:12px;">
    Generated by websiteupdater. Artifacts: <code>{{ artifacts_path }}</code>
  </p>
</body>
</html>
"""

_env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
_template = _env.from_string(_HTML_TMPL)


def _plaintext(summary: RunSummary) -> str:
    lines: list[str] = []
    lines.append(subject_line(summary))
    lines.append("")
    lines.append(f"Run ID: {summary.run_id}")
    lines.append(f"Started: {summary.started_at}")
    lines.append(f"Finished: {summary.finished_at}")
    lines.append(f"Status: {summary.status}")
    if summary.stage:
        lines.append(f"Last stage: {summary.stage}")
    if summary.failure_reason:
        lines.append(f"Failure: {summary.failure_reason}")
    if summary.deploy_url:
        lines.append(f"Preview URL: {summary.deploy_url}")

    lines.append("")
    lines.append("Composer:")
    if summary.composer_updates:
        for u in summary.composer_updates:
            lines.append(f"  {u.name}: {u.from_version} -> {u.to_version}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("npm audit:")
    if summary.npm_audit:
        lines.append(f"  {summary.npm_audit.summary_line}")
    else:
        lines.append("  (not run)")

    lines.append("")
    lines.append("Unit tests:")
    if summary.unit_tests_skipped:
        lines.append("  skipped")
    elif summary.unit_tests_passed is None:
        lines.append("  (not run)")
    else:
        lines.append("  PASSED" if summary.unit_tests_passed else "  FAILED")

    lines.append("")
    lines.append("Visual diff:")
    if summary.diff_report:
        for r in summary.diff_report.results:
            flag = "pass" if r.passed else "FAIL"
            lines.append(f"  [{flag}] {r.path}  {r.diff_pct:.3f}% differing"
                         + (f"  error={r.error}" if r.error else ""))
    else:
        lines.append("  (not run)")

    lines.append("")
    lines.append(f"Artifacts: {summary.artifacts_path}")
    return "\n".join(lines)


def _collect_inline_images(summary: RunSummary) -> tuple[dict[str, str], list[tuple[str, Path, str]]]:
    """Return (cid_map, [(cid, path, key)]). key identifies the image slot in the template."""
    cid_map: dict[str, str] = {}
    images: list[tuple[str, Path, str]] = []
    if not summary.diff_report:
        return cid_map, images
    for r in summary.diff_report.results:
        for kind, p in (("main", r.main_png), ("upd", r.update_png), ("diff", r.diff_png)):
            if not p or not Path(p).exists():
                continue
            cid_domain = make_msgid(domain="websiteupdater.local")[1:-1]  # strip < >
            key = f"{kind}:{r.path}"
            cid_map[key] = cid_domain
            images.append((cid_domain, Path(p), key))
    return cid_map, images


def send(summary: RunSummary, settings: Settings, *, attach_pngs: bool = True) -> None:
    if not settings.smtp_host or not settings.mail_to:
        # Silently skip if email isn't configured — notify.py still logs.
        return

    msg = EmailMessage()
    msg["Subject"] = subject_line(summary)
    msg["From"] = settings.mail_from
    msg["To"] = settings.mail_to

    cid_map, images = _collect_inline_images(summary)

    html = _template.render(
        project=summary.project_name,
        run_id=summary.run_id,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        status=summary.status,
        stage=summary.stage,
        failure_reason=summary.failure_reason,
        commit_sha=summary.commit_sha,
        merged=summary.merged,
        artifacts_path=summary.artifacts_path,
        composer_updates=summary.composer_updates,
        composer_ok=summary.composer_ok,
        npm_audit=summary.npm_audit,
        unit_tests_passed=summary.unit_tests_passed,
        unit_tests_skipped=summary.unit_tests_skipped,
        unit_test_cmd=summary.unit_test_cmd,
        diff_report=summary.diff_report,
        deploy_url=summary.deploy_url,
        deploy_state=summary.deploy_state,
        lockfile_diff=summary.lockfile_diff,
        cid_map=cid_map,
    )

    msg.set_content(_plaintext(summary))
    msg.add_alternative(html, subtype="html")

    # CID-inline images go onto the HTML alternative.
    html_part = msg.get_payload()[-1]
    for cid, path, _key in images:
        try:
            data = path.read_bytes()
            ctype, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (ctype or "image/png").split("/", 1)
            html_part.add_related(
                data, maintype=maintype, subtype=subtype, cid=f"<{cid}>", filename=path.name,
            )
            # Explicitly set base64 encoding for binary image data
            related_part = html_part.get_payload()[-1]
            related_part.replace_header('Content-Transfer-Encoding', 'base64')
        except Exception as e:
            logger.warning(f"Failed to inline-attach image {path.name}: {type(e).__name__}: {e}")

    if attach_pngs:
        for cid, path, _key in images:
            try:
                data = path.read_bytes()
                ctype, _ = mimetypes.guess_type(path.name)
                maintype, subtype = (ctype or "image/png").split("/", 1)
                msg.add_attachment(
                    data,
                    maintype=maintype,
                    subtype=subtype,
                    filename=f"{path.parent.name}-{path.name}",
                )
            except Exception as e:
                logger.warning(f"Failed to attach PNG {path.name}: {type(e).__name__}: {e}")

    _smtp_send(msg, settings)


def _smtp_send(msg: EmailMessage, settings: Settings) -> None:
    ctx = ssl.create_default_context()
    # Port 465 = implicit TLS, port 587 = STARTTLS (respect SMTP_USE_TLS).
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx) as s:
            if settings.smtp_username:
                s.login(settings.smtp_username, settings.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
            s.ehlo()
            if settings.smtp_use_tls:
                s.starttls(context=ctx)
                s.ehlo()
            if settings.smtp_username:
                s.login(settings.smtp_username, settings.smtp_password)
            s.send_message(msg)
