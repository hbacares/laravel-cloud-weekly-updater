#!/usr/bin/env bash
# One-time setup: create venv, install deps, install Playwright Chromium.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install --upgrade pip
pip install -e .

# Playwright browser download (~140MB)
python -m playwright install chromium

# Initialise the DB
python -m websiteupdater init-db

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo
  echo "Created .env from .env.example. Edit it to fill in:"
  echo "  - LARAVEL_CLOUD_API_TOKEN"
  echo "  - SMTP_* and MAIL_FROM / MAIL_TO"
fi

echo
echo "Done. Next steps:"
echo "  1. \$EDITOR .env"
echo "  2. ./.venv/bin/wu doctor"
echo "  3. ./.venv/bin/wu add mysite --repo ... --path ... --lc-project ... \\"
echo "       --day mon --hour 3 --paths '/, /tests' --test-cmd 'php artisan test'"
echo "  4. ./scripts/install-cron.sh"
