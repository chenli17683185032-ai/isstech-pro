#!/usr/bin/env python3
"""Interactively configure the text assistant provider in macOS Keychain."""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Sequence

from isstech_replay.ai.briefing import (
    ASSISTANT_API_KEY_SERVICE,
    ASSISTANT_ENDPOINT_SERVICE,
    ASSISTANT_MODEL_SERVICE,
    HttpChatBriefingProvider,
)
from isstech_replay.scheduler import (
    DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    local_account_name,
    read_keychain_value,
)


SECURITY = "/usr/bin/security"
INTERACTIVE_TIMEOUT_SECONDS = 120.0
SERVICES = (
    ASSISTANT_ENDPOINT_SERVICE,
    ASSISTANT_MODEL_SERVICE,
    ASSISTANT_API_KEY_SERVICE,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store a chat-capable assistant provider using secure Keychain prompts."
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
    endpoint = read_keychain_value(
        ASSISTANT_ENDPOINT_SERVICE,
        account,
        DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    model = read_keychain_value(
        ASSISTANT_MODEL_SERVICE,
        account,
        DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    api_key = read_keychain_value(
        ASSISTANT_API_KEY_SERVICE,
        account,
        DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
    )
    HttpChatBriefingProvider(endpoint=endpoint, model=model, api_key=api_key)
    api_key = ""
    model = ""
    endpoint = ""


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
            for service in SERVICES:
                _delete(account, service, timeout_seconds=args.timeout_seconds)
            print("Assistant Keychain items removed; local briefs were retained.")
            return 0
        if args.verify_only:
            _verify(account)
            print("Assistant Keychain items are present and valid.")
            return 0

        print("At the first secure prompt, enter the full Chat Completions endpoint.")
        _store_interactively(
            ASSISTANT_ENDPOINT_SERVICE,
            account,
            timeout_seconds=args.timeout_seconds,
        )
        print("At the second secure prompt, enter the chat-capable model name.")
        _store_interactively(
            ASSISTANT_MODEL_SERVICE,
            account,
            timeout_seconds=args.timeout_seconds,
        )
        print("At the third secure prompt, enter the API key.")
        _store_interactively(
            ASSISTANT_API_KEY_SERVICE,
            account,
            timeout_seconds=args.timeout_seconds,
        )
        _verify(account)
        print("Assistant Keychain items configured and verified.")
        return 0
    except Exception as error:
        print(
            f"ASSISTANT_KEYCHAIN_CONFIGURATION_FAILED {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
