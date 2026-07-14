#!/usr/bin/env python3
"""Redact a login HAR into a commit-safe protocol summary (stdout).

Usage:
  uv run python tools/redact_login_har.py captures/raw/YYYYMMDD-login-success.har

Never prints cookie values, password fields, or Set-Cookie value segments.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlencode, urlparse, urlunparse


SAFE_HOSTS = {"ipsapro.isstech.com", "passport.isstech.com"}
SAFE_EXACT_PATHS = {
    "/",
    "/Portal",
    "/Portal/",
    "/WebTP/PurchaseRequisition",
    "/WebTP/PurchaseRequisition/",
    "/WebTP/PurchaseRequisition/Index",
}
SAFE_PATH_PREFIXES = ("/Content/", "/Scripts/", "/Login/")


def _safe_query_value(key: str, value: str) -> str | None:
    key_l = key.lower()
    if key_l == "domainurl":
        nested = urlparse(value)
        if (
            nested.scheme == "http"
            and (nested.hostname or "").lower() == "ipsapro.isstech.com"
            and nested.username is None
            and nested.password is None
        ):
            return "http://ipsapro.isstech.com"
        return None
    if key_l == "returnurl":
        nested = urlparse(value)
        path = nested.path or "/"
        if not nested.scheme and not nested.netloc and (
            path == "/Portal"
            or path == "/Portal/"
            or path == "/WebTP/PurchaseRequisition"
            or path == "/WebTP/PurchaseRequisition/"
        ):
            return path
        return None
    return None


def _safe_path(host: str, path: str) -> str:
    path = path or "/"
    if host in SAFE_HOSTS and (
        path in SAFE_EXACT_PATHS or path.startswith(SAFE_PATH_PREFIXES)
    ):
        return path
    return "/<redacted-path>" if path != "/" else "/"


def _redact_url(url: str, sensitive_values: set[str] | None = None) -> dict[str, str | None]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    safe_q: dict[str, list[str]] = {}
    for key, values in qs.items():
        safe_values: list[str] = []
        for value in values:
            safe_value = _safe_query_value(key, value)
            if safe_value is None:
                safe_values.append("<redacted>")
                if sensitive_values is not None and len(value) >= 4:
                    sensitive_values.add(value)
            else:
                safe_values.append(safe_value)
        safe_q[key] = safe_values

    flat = [(k, v) for k, vs in safe_q.items() for v in vs]
    host = (parsed.hostname or "").lower()
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    safe_path = _safe_path(host, parsed.path)
    redacted = urlunparse(
        (
            parsed.scheme,
            netloc,
            safe_path,
            parsed.params,
            urlencode(flat),
            "",
        )
    )
    return {
        "url": redacted,
        "host": host or None,
        "path": safe_path,
    }


def _cookie_names_from_headers(
    headers: list[dict],
    sensitive_values: set[str] | None = None,
) -> list[dict[str, str | bool | None]]:
    out: list[dict[str, str | bool | None]] = []
    for h in headers:
        if h.get("name", "").lower() != "set-cookie":
            continue
        raw = h.get("value") or ""
        # name=value; attr=...
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        if not parts:
            continue
        name = parts[0].split("=", 1)[0].strip()
        if sensitive_values is not None and "=" in parts[0]:
            value = parts[0].split("=", 1)[1]
            if len(value) >= 4:
                sensitive_values.add(value)
        attrs = {
            "name": name,
            "domain": None,
            "path": None,
            "secure": False,
            "httpOnly": False,
            "sameSite": None,
            "session": True,
        }
        for attr in parts[1:]:
            lower = attr.lower()
            if lower == "secure":
                attrs["secure"] = True
            elif lower == "httponly":
                attrs["httpOnly"] = True
            elif lower.startswith("domain="):
                attrs["domain"] = attr.split("=", 1)[1]
            elif lower.startswith("path="):
                attrs["path"] = attr.split("=", 1)[1]
            elif lower.startswith("samesite="):
                attrs["sameSite"] = attr.split("=", 1)[1]
            elif lower.startswith("expires=") or lower.startswith("max-age="):
                attrs["session"] = False
        out.append(attrs)
    return out


def _post_field_names(request: dict) -> list[str]:
    post = request.get("postData") or {}
    text = post.get("text") or ""
    mime = (post.get("mimeType") or "").lower()
    names: list[str] = []
    if "x-www-form-urlencoded" in mime or text:
        for pair in text.split("&"):
            if not pair:
                continue
            key = unquote_plus(pair.split("=", 1)[0])
            # skip values entirely
            if key and key not in names:
                names.append(key)
    params = post.get("params") or []
    for p in params:
        name = p.get("name")
        if name and name not in names:
            names.append(name)
    return names


def redact_har(path: Path) -> dict:
    har = json.loads(path.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    steps = []
    cookies: list[dict] = []
    login_fields: list[str] = []
    sensitive_values: set[str] = set()
    for entry in entries:
        req = entry.get("request", {})
        res = entry.get("response", {})
        method = req.get("method", "")
        url_info = _redact_url(req.get("url", ""), sensitive_values)
        status = res.get("status")
        location = None
        for h in res.get("headers") or []:
            if h.get("name", "").lower() == "location":
                location = _redact_url(h.get("value") or "", sensitive_values).get("url")
                break
        set_cookies = _cookie_names_from_headers(
            res.get("headers") or [],
            sensitive_values,
        )
        for c in set_cookies:
            if c not in cookies:
                cookies.append(c)
        step = {
            "method": method,
            "url": url_info["url"],
            "host": url_info["host"],
            "path": url_info["path"],
            "status": status,
            "location": location,
            "setCookieNames": [c["name"] for c in set_cookies],
        }
        if method == "POST":
            fields = _post_field_names(req)
            step["postFieldNames"] = fields
            if "emp_Password" in fields or "emp_DomainName" in fields:
                login_fields = fields
        steps.append(step)

        post = req.get("postData") or {}
        mime = (post.get("mimeType") or "").lower()
        if "x-www-form-urlencoded" in mime:
            for pair in (post.get("text") or "").split("&"):
                if "=" not in pair:
                    continue
                raw_key, raw_value = pair.split("=", 1)
                key = unquote_plus(raw_key)
                value = unquote_plus(raw_value)
                if _safe_query_value(key, value) is None and len(value) >= 4:
                    sensitive_values.add(value)

        for header in req.get("headers") or []:
            if (header.get("name") or "").lower() in {"authorization", "cookie"}:
                value = header.get("value") or ""
                if len(value) >= 4:
                    sensitive_values.add(value)

    summary = {
        "sourceHar": path.name,
        "stepCount": len(steps),
        "steps": steps,
        "loginFormFieldsObserved": login_fields,
        "cookies": cookies,
        "notes": [
            "Values for cookies, passwords, and sensitive query params were stripped.",
            "Validate authenticatedPage by checking final business HTML separately if needed.",
        ],
    }
    serialized = json.dumps(summary, ensure_ascii=False)
    leaked = sorted(value for value in sensitive_values if value in serialized)
    if leaked:
        raise ValueError("Redaction verification failed: a source value survived")
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: redact_login_har.py <path-to-har>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 2
    summary = redact_har(path)
    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
