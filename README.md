# websiteupdater

> Automated dependency-update-and-verify pipeline for Laravel projects deployed on Laravel Cloud.

**websiteupdater** is a comprehensive automation tool that keeps your Laravel applications up-to-date by running scheduled dependency updates, testing them locally and in ephemeral environments, performing visual regression tests, and automatically merging safe updates.

## Table of Contents

- [Features](#features)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Project Setup](#project-setup)
- [Usage](#usage)
  - [CLI Commands](#cli-commands)
  - [Managing Projects](#managing-projects)
  - [Scheduling](#scheduling)
- [Visual Regression Testing](#visual-regression-testing)
- [Email Reports](#email-reports)
- [Advanced Configuration](#advanced-configuration)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [License](#license)
- [Credits](#credits)

## Features

- **Automated Dependency Updates**: Runs `composer update` and `npm update` on a schedule
- **Comprehensive Testing**: Executes unit tests locally and performs `npm audit` security checks
- **Ephemeral Environments**: Creates temporary Laravel Cloud environments for testing updates
- **Visual Regression Testing**: Compares screenshots using Playwright and pixelmatch to catch UI regressions
- **Smart Auto-Merge**: Automatically merges updates that pass all tests and visual diffs
- **HTML Email Reports**: Detailed reports with inline screenshots and diff heatmaps
- **Run Locking**: Prevents concurrent runs of the same project
- **Automatic Retry**: Handles transient network errors with exponential backoff
- **Artifact Management**: Configurable retention for logs, screenshots, and test results
- **Timezone-Aware Scheduling**: Works across timezones and DST transitions

## How It Works

For each registered project, on its scheduled day/hour:

1. **Fetch & Reset**: Pulls the latest code from the repository
2. **Create Branch**: Creates/recreates an `autoupdate` branch from `main`
3. **Update Dependencies**: Runs `composer update --no-dev`, `npm update`, and `npm audit`
4. **Local Testing**: Executes your project's unit test suite with updated dependencies
5. **Deploy to Ephemeral**: Pushes to GitHub and creates a temporary Laravel Cloud environment
6. **Visual Regression**: Takes Playwright screenshots of both `main` and `autoupdate` branches, compares them pixel-by-pixel
7. **Auto-Merge**: If tests pass and visual diffs are within tolerance, merges to `main` and deploys
8. **Report**: Emails a detailed HTML report with screenshots, logs, and results

If any step fails, the process stops and notifies you with details about what went wrong.

## Requirements

- **Python**: 3.11 or higher
- **Git**: For repository management
- **Composer**: PHP dependency manager
- **npm**: Node.js package manager
- **PHP**: Compatible with your Laravel projects
- **Laravel Cloud Account**: With API access
- **SMTP Server**: For email reports (Gmail, Postmark, Mailgun, etc.)

## Installation

### Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/websiteupdater.git
cd websiteupdater

# 2. Install dependencies
./scripts/bootstrap.sh

# 3. Configure credentials
cp .env.example .env
chmod 600 .env
nano .env  # Add LARAVEL_CLOUD_API_TOKEN, SMTP settings

# 4. Add your projects
cp projects.json.example projects.json
nano projects.json  # Configure your projects
wu sync

# 5. Verify setup
wu doctor
wu list

# 6. Test run (optional)
wu run example-site
```

### Detailed Installation

**Step 1: Bootstrap Environment**

The bootstrap script creates a Python virtual environment and installs all dependencies including Playwright:

```bash
./scripts/bootstrap.sh
```

This will:
- Create `.venv/` directory with Python virtual environment
- Install all Python dependencies from `pyproject.toml`
- Install Playwright Chromium browser

**Step 2: Configure Environment Variables**

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill in required values:

- `LARAVEL_CLOUD_API_TOKEN`: Generate at [Laravel Cloud Account Settings](https://cloud.laravel.com/) → API Tokens
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`: Your email provider settings
- `MAIL_FROM`, `MAIL_TO`: Email addresses for reports

**Step 3: Verify Installation**

```bash
./.venv/bin/wu doctor
```

This command checks:
- Git availability and version
- Composer availability
- npm availability
- PHP availability
- Playwright installation
- SMTP connectivity
- Laravel Cloud API authentication

## Configuration

### Environment Variables

Create a `.env` file from the example template:

```bash
cp .env.example .env
```

#### Required Settings

```bash
# Laravel Cloud API token (required)
LARAVEL_CLOUD_API_TOKEN=your_token_here

# SMTP settings for email reports (required)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
MAIL_FROM=websiteupdater <reports@example.com>
MAIL_TO=you@example.com
```

#### Optional Settings

```bash
# Laravel Cloud API base URL (defaults to production)
LARAVEL_CLOUD_API_BASE=https://cloud.laravel.com/api/v1

# macOS notifications on failure
NOTIFY_MACOS=1

# Keep ephemeral environments after failed runs for debugging
KEEP_FAILED_ENVS=0

# Laravel Cloud timeouts (seconds)
LC_POLL_INTERVAL=10
LC_ENV_CREATE_TIMEOUT=300
LC_DEPLOY_TIMEOUT=900

# Playwright settings
SCREENSHOT_WIDTH=1440
SCREENSHOT_HEIGHT=900
PLAYWRIGHT_NAVIGATION_TIMEOUT=45000
PLAYWRIGHT_MASK_WAIT=250

# Artifact retention (0 = keep forever)
ARTIFACT_RETENTION_DAYS=30

# Binary paths (useful for cron/launchd)
COMPOSER_BIN=/opt/homebrew/bin/composer
NPM_BIN=/opt/homebrew/bin/npm
```

### Project Setup

You can configure projects in two ways:

#### Option 1: JSON File (Recommended)

Create `projects.json` from the example:

```bash
cp projects.json.example projects.json
```

Edit the file with your projects:

```json
[
  {
    "name": "mysite",
    "repo_url": "git@github.com:username/mysite.git",
    "local_path": "/dev/mysite",
    "main_branch": "main",
    "update_branch": "autoupdate",
    "schedule": {
      "day": "mon",
      "hour": 3
    },
    "laravel_cloud": {
      "project_id": "prj_abc123",
      "main_env": "main"
    },
    "visual_diff": {
      "paths": ["/", "/login", "/dashboard"],
      "mask_selectors": [".live-counter", "#timestamp"],
      "tolerance_pct": 0.5
    },
    "testing": {
      "unit_test_cmd": "php artisan test",
      "skip_unit_tests": false,
      "npm_audit_gate": "high"
    },
    "enabled": true
  }
]
```

Import into the database:

```bash
wu sync
wu list  # Verify projects loaded
```

#### Option 2: CLI Commands

Add projects directly via command line:

```bash
wu add mysite \
  --repo git@github.com:username/mysite.git \
  --path ~/dev/mysite \
  --lc-project prj_abc123 \
  --lc-main-env main \
  --day mon --hour 3 \
  --paths "/, /login, /dashboard" \
  --test-cmd "php artisan test" \
  --mask ".live-counter, #timestamp" \
  --tolerance 0.5 \
  --audit-gate high
```

## Usage

### CLI Commands

**Running Updates**

```bash
# Run a specific project immediately (ignore schedule)
wu run mysite

# Run all projects that are due based on schedule
wu run --due

# Retry the last failed run for a project
wu retry mysite
```

**Managing Projects**

```bash
# List all registered projects
wu list

# Show detailed configuration for a project
wu show mysite

# View run history for a project
wu history mysite

# Edit project settings
wu edit mysite --hour 4
wu edit mysite --paths "/, /admin"

# Remove a project
wu remove mysite
```

**JSON Import/Export**

```bash
# Export all projects to JSON
wu export -o projects.json

# Import projects from JSON (skip existing)
wu import projects.json

# Import and update existing projects
wu import projects.json --update

# Sync database with projects.json (add/update all)
wu sync
```

**Maintenance**

```bash
# Check system dependencies and configuration
wu doctor

# View artifacts for a project
wu artifacts mysite

# Clean up old run artifacts
wu cleanup --days 30
```

### Managing Projects

#### Workflow: JSON as Source of Truth

1. Export current configuration:
   ```bash
   wu export -o projects.json
   ```

2. Edit `projects.json` in your preferred editor

3. Sync changes back to database:
   ```bash
   wu sync
   ```

4. Commit `projects.json` to version control (optional)

### Scheduling

#### Setting Up Cron

Install the hourly cron job:

```bash
./scripts/install-cron.sh
```

This adds an entry that runs `wu run --due` every hour.

**How "Due" Works:**
- Runs when: `weekday matches AND hour >= scheduled_hour AND no successful run this week`
- Missed updates (laptop closed) run when the system wakes up
- Each project runs once per week on its scheduled day

#### Setting Up launchd (macOS)

For macOS, you can use launchd instead of cron:

```bash
./scripts/install-launchd.sh
```

#### Manual Scheduling

You can also trigger updates manually or use your own scheduling system:

```bash
# Run specific project on-demand
wu run mysite

# Run all due projects (cron/script entry point)
wu run --due
```

#### Timezone Handling

- Scheduled times use your system's **local timezone**
- Database timestamps stored in UTC
- "Monday 3am" means 3am in your current timezone
- DST transitions handled automatically

## Visual Regression Testing

### Recommended Pattern: `/tests` Page

Create a dedicated route (e.g., `/tests`) that showcases key UI components:

- Primary forms
- Data tables
- Charts and graphs
- Card grids
- Flash messages
- Navigation elements
- Authenticated sections
- Custom Blade components

Keep the page deterministic:
- No timestamps or dynamic dates
- No random content
- Use CSS masks for unavoidable dynamic regions

**Example Configuration:**

```json
{
  "visual_diff": {
    "paths": ["/", "/tests"],
    "mask_selectors": [".footer-year", "#csrf-token"],
    "tolerance_pct": 0.5
  }
}
```

### Masking Dynamic Elements

Use CSS selectors to hide dynamic content before screenshots:

```bash
wu edit mysite --mask ".live-counter, #timestamp, .animated-banner"
```

### Tolerance Settings

- `tolerance_pct`: Maximum percentage of pixels allowed to differ (default: 0.5%)
- Lower values = stricter matching
- Higher values = more permissive (useful for anti-aliasing differences)

## Email Reports

Reports are sent via SMTP after each run, including:

- **Summary**: Overall status (success/failure)
- **Stage Details**: Step-by-step progress and timing
- **Screenshots**: Inline comparison images (main vs. autoupdate)
- **Diff Heatmaps**: Visual representation of pixel differences
- **Logs**: Composer output, npm output, test results
- **Attachments**: Full-resolution PNG files

### Supported SMTP Providers

- Gmail (with app passwords)
- Postmark
- Resend
- Mailgun
- SendGrid
- AWS SES
- Any standard SMTP server

## Advanced Configuration

### Data Layout

```
data/
├── websiteupdater.db              # SQLite database (projects, runs)
├── runs/
│   └── mysite-20260422-030000/    # Per-run artifacts
│       ├── run.log                # Main run log
│       ├── composer.out           # Composer update output
│       ├── npm.out                # npm update output
│       ├── npm-audit.json         # npm audit results
│       ├── tests.out              # Unit test output
│       └── screens/
│           ├── main/              # Screenshots from main branch
│           ├── autoupdate/        # Screenshots from autoupdate branch
│           └── diff/              # Diff heatmaps
├── baselines/                     # Baseline screenshots (future)
└── locks/                         # Run lock files
```

Everything under `data/` is gitignored.

### Laravel Cloud API

The tool uses the [Laravel Cloud REST API](https://cloud.laravel.com/docs/api/introduction). The `LaravelCloudClient` in `websiteupdater/laravel_cloud.py` handles:

- Creating ephemeral environments
- Monitoring deployment status
- Retrieving environment URLs
- Cleaning up environments

### Artifact Retention

Configure automatic cleanup:

```bash
# In .env
ARTIFACT_RETENTION_DAYS=30
```

Or run cleanup manually:

```bash
wu cleanup --days 30
```

### Debugging Failed Runs

Keep ephemeral environments for debugging:

```bash
# In .env
KEEP_FAILED_ENVS=1
```

View artifacts:

```bash
wu artifacts mysite
```

View run history:

```bash
wu history mysite
```

## Development

### Running Tests

Install development dependencies:

```bash
pip install -e ".[dev]"
```

Run test suite:

```bash
# All tests
pytest

# Specific test file
pytest tests/test_scheduler.py

# With coverage report
pytest --cov=websiteupdater --cov-report=html
```

### Test Organization

- `test_scheduler.py` - Scheduling logic and due project detection
- `test_cleanup.py` - Artifact cleanup functionality
- `test_lockfile.py` - File-based locking mechanism
- `test_retry.py` - Retry decorator and backoff logic

### Project Structure

```
websiteupdater/
├── cli.py              # Typer-based CLI commands
├── config.py           # Environment-backed settings
├── db.py               # SQLite database operations
├── runner.py           # Main update pipeline orchestration
├── laravel_cloud.py    # Laravel Cloud API client
├── visual_diff.py      # Playwright screenshot & comparison
├── email_report.py     # HTML email generation
├── git_ops.py          # Git operations (clone, branch, merge)
├── tests_runner.py     # Unit test execution
├── scheduler.py        # Schedule logic and due detection
├── cleanup.py          # Artifact cleanup
├── lockfile.py         # File-based locking
├── retry.py            # Retry decorator with backoff
├── notify.py           # macOS notification support
├── reports.py          # Report data structures
└── updaters/
    ├── composer.py     # Composer update logic
    └── npm.py          # npm update and audit logic
```

## Troubleshooting

### Common Issues

**"Laravel Cloud API authentication failed"**
- Verify your `LARAVEL_CLOUD_API_TOKEN` in `.env`
- Check token hasn't expired at [cloud.laravel.com](https://cloud.laravel.com/)

**"Playwright browser not found"**
- Run `playwright install chromium`
- Or re-run `./scripts/bootstrap.sh`

**"SMTP connection failed"**
- Verify SMTP credentials in `.env`
- For Gmail, use [app passwords](https://support.google.com/accounts/answer/185833)
- Check firewall settings

**"Project already running" error**
- Another instance is running (check processes)
- Or stale lock file in `data/locks/`
- Remove lock manually if process is dead

**Visual diffs failing**
- Increase `tolerance_pct` if minor differences
- Add more `mask_selectors` for dynamic elements
- Check screenshots in `data/runs/<project>-<date>/screens/`

### Getting Help

Run the doctor command to diagnose issues:

```bash
wu doctor
```

Check run logs:

```bash
wu artifacts mysite
cat data/runs/mysite-<timestamp>/run.log
```

## Uninstalling

```bash
# Remove cron job (or launchd agent)
./scripts/install-cron.sh --uninstall
# or
./scripts/install-launchd.sh --uninstall

# Remove the project directory
# (adjust path to where you installed it)
rm -rf /path/to/websiteupdater
```

## License

MIT License - feel free to use and modify for your projects.

## Credits

**Author**: Hans Bacares ([hans@bacares.com](mailto:hans@bacares.com))

**Development Assistant**: Built with assistance from [Claude Code](https://claude.ai/code) by Anthropic

---

**websiteupdater** - Keep your Laravel applications fresh and regression-free
