#!/usr/bin/env bash
# First baseline commit. Run from a shell that can write to .git/
# (the sandboxed agent session currently cannot create .git/index.lock).
set -euo pipefail
cd /Users/ethan/Documents/isstech

git check-ignore -v captures/raw/auth_purchase_requisition.html

uv run pytest -q
uv run ruff check .
uv run python tools/export_openapi.py --check
uv run python tools/verify_no_secrets.py
uv run python tools/verify_evidence.py

git add -A

if git diff --cached --name-only \
  | rg '^(captures/raw/|captures/playwright/|captures/login_fail_|\.env$)' \
  | rg -v '^captures/raw/\.gitkeep$'; then
  echo "ERROR: sensitive path staged" >&2
  git diff --cached --name-only
  exit 1
fi

git commit -m "$(cat <<'MSG'
Implement policy-gated Purchase Requisition replay baseline.

Add exact-origin guarded transport, browser-independent login, evidence-backed
application view parsing, bounded attachment reads, non-sendable write previews,
FastAPI routes, generated OpenAPI, redacted evidence inventory, and adversarial
safety tests. Raw captures remain gitignored and mode 0600.
MSG
)"

git status --short --branch
git log -1 --stat
