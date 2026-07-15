#!/usr/bin/env python3
"""Interactively configure or verify scheduled-sync values in macOS Keychain."""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Sequence

from isstech_replay.scheduler import (
    DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    KEYCHAIN_PASSWORD_SERVICE,
    KEYCHAIN_USERNAME_SERVICE,
    local_account_name,
    read_keychain_value,
)


SECURITY = "/usr/bin/security"
INTERACTIVE_TIMEOUT_SECONDS = 120.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store scheduled iPSA credentials using secure Keychain prompts."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--verify-only", action="store_true")
    action.add_argument("--delete", action="store_true")
    parser.add_argument("--account", default=None, help="Local Keychain account name.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=INTERACTIVE_TIMEOUT_SECONDS,
    )
    return parser


def _store_interactively(
    service: str,
    account: str,
    *,
    timeout_seconds: float,
) -> None:
    try:
        completed = subprocess.run(
            [
                SECURITY,
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Keychain prompt timed out for service {service}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"Keychain update failed for service {service}")


def _verify(account: str) -> None:
    username = read_keychain_value(
        KEYCHAIN_USERNAME_SERVICE,
        account,
        DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    password = read_keychain_value(
        KEYCHAIN_PASSWORD_SERVICE,
        account,
        DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    if not username.strip() or not password:
        raise RuntimeError("scheduled Keychain values are empty")
    username = ""
    password = ""


def _delete(account: str, service: str, *, timeout_seconds: float) -> None:
    try:
        completed = subprocess.run(
            [SECURITY, "delete-generic-password", "-a", account, "-s", service],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Keychain delete timed out for service {service}") from exc
    if completed.returncode not in {0, 44}:
        raise RuntimeError(f"Keychain delete failed for service {service}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        print("--timeout-seconds must be positive", file=sys.stderr)
        return 2
    account = (args.account or local_account_name()).strip()
    if not account:
        print("Keychain account is required", file=sys.stderr)
        return 2
    try:
        if args.delete:
            for service in (KEYCHAIN_USERNAME_SERVICE, KEYCHAIN_PASSWORD_SERVICE):
                _delete(account, service, timeout_seconds=args.timeout_seconds)
            print("Scheduled-sync Keychain items removed; logs and snapshots were retained.")
            return 0
        if args.verify_only:
            _verify(account)
            print("Scheduled-sync Keychain items are present and non-empty.")
            return 0

        print("At the first secure prompt, enter the iPSA username.")
        _store_interactively(
            KEYCHAIN_USERNAME_SERVICE,
            account,
            timeout_seconds=args.timeout_seconds,
        )
        print("At the second secure prompt, enter the iPSA password.")
        _store_interactively(
            KEYCHAIN_PASSWORD_SERVICE,
            account,
            timeout_seconds=args.timeout_seconds,
        )
        _verify(account)
        print("Scheduled-sync Keychain items configured and verified.")
        return 0
    except Exception as error:
        print(f"KEYCHAIN_CONFIGURATION_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
