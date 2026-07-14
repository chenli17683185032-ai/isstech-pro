#!/usr/bin/env python3
"""Credentialed pure-HTTP smoke against the authorized target.

Credentials only via env (never argv, never files under the repo):

  export ISSTECH_USERNAME='...'
  export ISSTECH_PASSWORD='...'
  uv run python tools/live_smoke.py

Exits non-zero on failure. Prints cookie *names* and list counts only.
"""

from __future__ import annotations

import os
import sys

from isstech_replay.auth import AuthenticationError, login_with_settings
from isstech_replay.models.purchase import PurchaseView
from isstech_replay.policy import PolicyViolation


def main() -> int:
    user = os.environ.get("ISSTECH_USERNAME", "").strip()
    password = os.environ.get("ISSTECH_PASSWORD", "")
    if not user or not password:
        print("Set ISSTECH_USERNAME and ISSTECH_PASSWORD in the environment.", file=sys.stderr)
        return 2

    try:
        client, result = login_with_settings(user, password)
    except AuthenticationError as exc:
        print(f"LOGIN_FAIL {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"LOGIN_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        print("login_success", result.success)
        print("has_ipsa", result.session.has_ipsa_cookie)
        print("cookie_names", ",".join(result.session.cookie_names_present))
        host = result.final_url.split("/")[2] if "://" in result.final_url else result.final_url
        print("final_host", host)

        listing = client.list_view(PurchaseView.APPLICATION)
        print("list_items", len(listing.items))
        print("list_total", listing.total_count)

        # Write must not leave the process
        try:
            client.get(
                f"{client.settings.base_url}/WebTP/PurchaseRequisition/Delete/0"
            )
            print("WRITE_ESCAPE delete reached transport", file=sys.stderr)
            return 1
        except PolicyViolation as exc:
            print("delete_blocked", exc.decision.rule_id)

        print("SMOKE_OK")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
