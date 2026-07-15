#!/usr/bin/env python3
"""Redact a Chrome CDP login capture into a commit-safe protocol summary."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urljoin, urlparse

try:
    from tools.redact_login_har import (
        SAFE_HOSTS,
        _cookie_names_from_headers,
        _redact_url,
        _safe_query_value,
    )
except ModuleNotFoundError:  # Direct `python tools/redact_login_cdp.py ...` execution.
    from redact_login_har import (  # type: ignore[no-redef]
        SAFE_HOSTS,
        _cookie_names_from_headers,
        _redact_url,
        _safe_query_value,
    )


def _header_list(headers: dict | None) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for name, value in (headers or {}).items():
        values = str(value).splitlines() if name.lower() == "set-cookie" else [str(value)]
        output.extend({"name": name, "value": item} for item in values if item)
    return output


def _post_field_names(post_data: str) -> list[str]:
    return list(dict.fromkeys(key for key, _ in parse_qsl(post_data, keep_blank_values=True)))


_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _request_cookie_names(
    headers: dict | None,
    sensitive_values: set[str],
) -> list[str]:
    names: list[str] = []
    for header_name, header_value in (headers or {}).items():
        if header_name.lower() != "cookie":
            continue
        for pair in str(header_value).split(";"):
            name, separator, value = pair.strip().partition("=")
            if not separator or not _COOKIE_NAME.fullmatch(name):
                continue
            if value:
                sensitive_values.add(value)
            if name not in names:
                names.append(name)
    return names


def redact_cdp(path: Path) -> dict:
    capture = json.loads(path.read_text(encoding="utf-8"))
    events = capture.get("events") or []
    steps: list[dict] = []
    current_by_request: dict[str, dict] = {}
    cookies: list[dict] = []
    login_fields: list[str] = []
    sensitive_values: set[str] = set()
    request_cookie_names: list[str] = []
    authenticated_page: dict[str, str | int] | None = None

    def add_request_cookies(headers: dict | None) -> None:
        for name in _request_cookie_names(headers, sensitive_values):
            if name not in request_cookie_names:
                request_cookie_names.append(name)

    def add_cookies(headers: dict | None) -> list[str]:
        found = _cookie_names_from_headers(_header_list(headers), sensitive_values)
        for cookie in found:
            if cookie not in cookies:
                cookies.append(cookie)
        return [str(cookie["name"]) for cookie in found]

    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        request_id = str(params.get("requestId") or "")

        if method == "Network.requestWillBeSent" and params.get("type") == "Document":
            request = params.get("request") or {}
            request_url = str(request.get("url") or "")
            host = (urlparse(request_url).hostname or "").lower()
            previous = current_by_request.get(request_id)
            redirect = params.get("redirectResponse") or {}
            if previous is not None and redirect:
                previous["status"] = redirect.get("status")
                location = (redirect.get("headers") or {}).get("location") or (
                    redirect.get("headers") or {}
                ).get("Location")
                if location:
                    redirect_url = str(redirect.get("url") or request_url)
                    previous["location"] = _redact_url(
                        urljoin(redirect_url, str(location)), sensitive_values
                    )["url"]
                previous["setCookieNames"] = add_cookies(redirect.get("headers"))

            if host not in SAFE_HOSTS:
                current_by_request.pop(request_id, None)
                continue

            url_info = _redact_url(request_url, sensitive_values)
            step = {
                "method": str(request.get("method") or ""),
                "url": url_info["url"],
                "host": url_info["host"],
                "path": url_info["path"],
                "status": None,
                "location": None,
                "setCookieNames": [],
            }
            post_data = str(request.get("postData") or "")
            if post_data:
                fields = _post_field_names(post_data)
                step["postFieldNames"] = fields
                if "emp_DomainName" in fields or "emp_Password" in fields:
                    login_fields = fields
                for key, value in parse_qsl(post_data, keep_blank_values=True):
                    if key in {"emp_DomainName", "emp_Password"} and value:
                        sensitive_values.add(value)
                    elif _safe_query_value(key, value) is None and key != "RemeberMe" and len(value) >= 8:
                        sensitive_values.add(value)
            for name, value in (request.get("headers") or {}).items():
                if name.lower() in {"authorization", "cookie"} and len(str(value)) >= 8:
                    sensitive_values.add(str(value))
            add_request_cookies(request.get("headers"))
            steps.append(step)
            current_by_request[request_id] = step
            continue

        if method == "Network.requestWillBeSentExtraInfo":
            add_request_cookies(params.get("headers"))
            continue

        if method == "Network.responseReceived" and params.get("type") == "Document":
            response = params.get("response") or {}
            step = current_by_request.get(request_id)
            if step is not None:
                step["status"] = response.get("status")
                step["setCookieNames"] = add_cookies(response.get("headers"))
            response_url = str(response.get("url") or "")
            parsed = urlparse(response_url)
            if (
                (parsed.hostname or "").lower() == "ipsapro.isstech.com"
                and response.get("status") == 200
                and parsed.path.lower() in {
                    "/portal",
                    "/portal/",
                    "/webtp/purchaserequisition",
                    "/webtp/purchaserequisition/",
                }
            ):
                authenticated_page = {
                    "url": str(_redact_url(response_url, sensitive_values)["url"]),
                    "status": int(response["status"]),
                }
            continue

        if method == "Network.responseReceivedExtraInfo":
            add_cookies(params.get("headers"))

    summary = {
        "sourceCapture": path.name,
        "capturedAt": capture.get("capturedAt"),
        "stepCount": len(steps),
        "steps": steps,
        "loginFormFieldsObserved": login_fields,
        "requestCookieNamesObserved": request_cookie_names,
        "cookies": cookies,
        "authenticatedPage": authenticated_page,
        "notes": [
            "Cookie values, credentials, ticket values, and unknown paths/query values are omitted.",
            "A manual credential POST and authenticated Portal response were captured through CDP.",
            "This browser capture does not by itself prove ticket issuance from a clean HTTP session.",
        ],
    }
    serialized = json.dumps(summary, ensure_ascii=False)
    leaked = sorted(value for value in sensitive_values if value and value in serialized)
    if leaked or "emp_Password=" in serialized or ".iPSA=" in serialized:
        raise ValueError("Redaction verification failed: a source value survived")
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: redact_login_cdp.py <path-to-cdp-json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 2
    json.dump(redact_cdp(path), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
