"""CDP login captures redact values while preserving protocol structure."""

from __future__ import annotations

import json
from pathlib import Path

from tools.redact_login_cdp import redact_cdp


def test_redact_cdp_login_capture(tmp_path: Path) -> None:
    source = tmp_path / "login.cdp.json"
    secret_user = "LIVE_USER_SECRET"
    secret_password = "LIVE_PASSWORD_SECRET"
    secret_ticket = "LIVE_TICKET_SECRET"
    source.write_text(
        json.dumps(
            {
                "capturedAt": "2026-07-15T03:51:00Z",
                "events": [
                    {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "1",
                            "type": "Document",
                            "request": {
                                "method": "POST",
                                "url": (
                                    "https://passport.isstech.com/?DomainUrl="
                                    "http%3A%2F%2Fipsapro.isstech.com&ReturnUrl="
                                    "%2FWebTP%2FPurchaseRequisition&auth=HIDE_THIS_TOKEN"
                                ),
                                "postData": (
                                    "emp_DomainName="
                                    + secret_user
                                    + "&emp_Password="
                                    + secret_password
                                    + "&RemeberMe=true&DomainUrl="
                                    + "http%3A%2F%2Fipsapro.isstech.com"
                                    + "&ReturnUrl=%2FWebTP%2FPurchaseRequisition"
                                ),
                                "headers": {
                                    "Cookie": (
                                        ".iPSA"
                                        + "="
                                        + secret_ticket
                                        + "; uname=HIDE_ME"
                                    )
                                },
                            },
                        },
                    },
                    {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "1",
                            "type": "Document",
                            "redirectResponse": {
                                "status": 302,
                                "headers": {
                                    "location": "http://ipsapro.isstech.com/Portal",
                                    "set-cookie": (
                                        ".iPSA"
                                        + "="
                                        + secret_ticket
                                        + "; Domain=.isstech.com; Path=/; HttpOnly"
                                    ),
                                },
                            },
                            "request": {
                                "method": "GET",
                                "url": "http://ipsapro.isstech.com/Portal",
                                "headers": {},
                            },
                        },
                    },
                    {
                        "method": "Network.responseReceived",
                        "params": {
                            "requestId": "1",
                            "type": "Document",
                            "response": {
                                "url": "http://ipsapro.isstech.com/Portal",
                                "status": 200,
                                "headers": {},
                            },
                        },
                    },
                    {
                        "method": "Network.requestWillBeSent",
                        "params": {
                            "requestId": "third-party",
                            "type": "Document",
                            "request": {
                                "method": "GET",
                                "url": "https://example.test/private/path?ticket=DO_NOT_KEEP",
                            },
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = redact_cdp(source)
    rendered = json.dumps(result)
    assert result["capturedAt"] == "2026-07-15T03:51:00Z"
    assert result["stepCount"] == 2
    assert result["authenticatedPage"]["status"] == 200
    assert result["loginFormFieldsObserved"] == [
        "emp_DomainName",
        "emp_Password",
        "RemeberMe",
        "DomainUrl",
        "ReturnUrl",
    ]
    assert result["requestCookieNamesObserved"] == [".iPSA", "uname"]
    assert result["cookies"][0]["name"] == ".iPSA"
    assert "example.test" not in rendered
    assert "HIDE_THIS_TOKEN" not in rendered
    assert secret_user not in rendered
    assert secret_password not in rendered
    assert secret_ticket not in rendered
