"""Unit test for HAR redaction helper (synthetic HAR, no secrets)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.redact_login_har import redact_har


def test_redact_login_har_strips_values(tmp_path: Path) -> None:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://passport.isstech.com/?DomainUrl=http://ipsapro.isstech.com",
                        "postData": {
                            "mimeType": "application/x-www-form-urlencoded",
                            "text": "emp_DomainName=alice&emp_Password=TEST_PASSWORD&DomainUrl=http://ipsapro.isstech.com",
                        },
                    },
                    "response": {
                        "status": 302,
                        "headers": [
                            {
                                "name": "Set-Cookie",
                                "value": ".iPSA=TEST_TICKET_VALUE; domain=.isstech.com; path=/; HttpOnly; SameSite=Lax",
                            },
                            {
                                "name": "Location",
                                "value": "http://ipsapro.isstech.com/WebTP/PurchaseRequisition",
                            },
                        ],
                    },
                }
            ]
        }
    }
    path = tmp_path / "sample.har"
    path.write_text(json.dumps(har), encoding="utf-8")
    summary = redact_har(path)
    text = json.dumps(summary)
    assert "TEST_PASSWORD" not in text
    assert "TEST_TICKET_VALUE" not in text
    assert ".iPSA" in text
    assert summary["steps"][0]["postFieldNames"] == [
        "emp_DomainName",
        "emp_Password",
        "DomainUrl",
    ]
    assert summary["cookies"][0]["name"] == ".iPSA"
    assert summary["cookies"][0]["httpOnly"] is True


def test_redact_login_har_hides_unknown_query_tickets_and_paths(tmp_path: Path) -> None:
    secret = "SHOULD_NOT_SURVIVE_ABC123"
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            "https://passport.isstech.com/callback/opaque-segment"
                            f"?SAMLResponse={secret}&DomainUrl=http://ipsapro.isstech.com"
                        ),
                    },
                    "response": {
                        "status": 302,
                        "headers": [
                            {
                                "name": "Location",
                                "value": (
                                    "http://ipsapro.isstech.com/unknown/callback"
                                    f"?auth={secret}&ReturnUrl=%2FPortal"
                                ),
                            }
                        ],
                    },
                }
            ]
        }
    }
    path = tmp_path / "unknown-ticket.har"
    path.write_text(json.dumps(har), encoding="utf-8")

    summary = redact_har(path)
    output = json.dumps(summary)

    assert secret not in output
    assert "SAMLResponse=%3Credacted%3E" in output
    assert "auth=%3Credacted%3E" in output
    assert "/<redacted-path>" in output
    assert "DomainUrl=http%3A%2F%2Fipsapro.isstech.com" in output
    assert "ReturnUrl=%2FPortal" in output


def test_redact_login_har_drops_url_userinfo(tmp_path: Path) -> None:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://user:pass@passport.isstech.com/",
                    },
                    "response": {"status": 400, "headers": []},
                }
            ]
        }
    }
    path = tmp_path / "userinfo.har"
    path.write_text(json.dumps(har), encoding="utf-8")
    output = json.dumps(redact_har(path))
    assert "user:pass" not in output
