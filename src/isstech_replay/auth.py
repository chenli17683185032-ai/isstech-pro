"""Pure HTTP authentication against passport + iPSA.

No Chrome cookie import. A fresh process obtains an upstream session only by
posting runtime credentials through the policy-gated transport.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from .client import IsstechClient
from .config import Settings
from .models.auth import AuthSession, LoginForm, LoginResult
from .parsers.login import (
    cookie_names,
    extract_login_error,
    is_authenticated_business_page,
    is_login_page,
    parse_login_form,
)


class AuthenticationError(RuntimeError):
    """Raised when login cannot complete with a usable business session."""


ALLOWED_RETURN_URL = "/WebTP/PurchaseRequisition"


def _validate_return_url(return_url: str) -> str:
    if return_url != ALLOWED_RETURN_URL:
        raise AuthenticationError(
            f"return_url is not live-enabled; expected {ALLOWED_RETURN_URL}"
        )
    return return_url


def _validate_login_form(client: IsstechClient, form: LoginForm, return_url: str) -> None:
    expected_passport = urlparse(client.settings.passport_url)
    actual_action = urlparse(form.action_url)
    expected_business = client.settings.base_url.rstrip("/")

    if (
        actual_action.scheme != expected_passport.scheme
        or actual_action.hostname != expected_passport.hostname
        or actual_action.port not in {None, expected_passport.port or 443}
        or actual_action.username is not None
        or actual_action.password is not None
        or actual_action.path not in {"", "/"}
    ):
        raise AuthenticationError("Passport form action does not match the exact login origin")
    if form.domain_url.rstrip("/") != expected_business:
        raise AuthenticationError("Passport form DomainUrl does not match the business origin")
    if form.return_url != return_url:
        raise AuthenticationError("Passport form ReturnUrl does not match the requested path")


def build_passport_entry_url(
    settings: Settings,
    *,
    return_url: str = "/WebTP/PurchaseRequisition",
) -> str:
    return_url = _validate_return_url(return_url)
    domain = settings.base_url.rstrip("/")
    # Passport expects DomainUrl without path and ReturnUrl as a path
    return (
        f"{settings.passport_url}/"
        f"?DomainUrl={quote(domain, safe='')}"
        f"&ReturnUrl={quote(return_url, safe='')}"
    )


def fetch_login_form(client: IsstechClient, *, return_url: str = "/WebTP/PurchaseRequisition") -> LoginForm:
    """GET passport login page from an empty session and parse the form."""
    return_url = _validate_return_url(return_url)
    # Prefer following the business unauth redirect so DomainUrl/ReturnUrl match production.
    entry = f"{client.settings.base_url}/WebTP/PurchaseRequisition"
    response = client.get(entry)
    # If already authenticated, this may be the business page.
    if is_authenticated_business_page(response.text):
        raise AuthenticationError("Session already authenticated; refusing to re-parse as login form")
    if not is_login_page(response.text):
        # Fall back to direct passport URL
        response = client.get(build_passport_entry_url(client.settings, return_url=return_url))
    if not is_login_page(response.text):
        raise AuthenticationError("Could not load passport login page")
    form = parse_login_form(response.text, str(response.url))
    _validate_login_form(client, form, return_url)
    return form


def _looks_like_success(response: httpx.Response, names: tuple[str, ...]) -> bool:
    if ".iPSA" in names:
        return True
    if is_authenticated_business_page(response.text):
        return True
    # Landed on business host without login markers
    host = (urlparse(str(response.url)).hostname or "").lower()
    if host == "ipsapro.isstech.com" and not is_login_page(response.text):
        if "PurchaseRequisition" in response.text or "main_content" in response.text:
            return True
    return False


def login(
    client: IsstechClient,
    username: str,
    password: str,
    *,
    return_url: str = "/WebTP/PurchaseRequisition",
    remember_me: bool = False,
) -> LoginResult:
    """Perform pure-HTTP login. Never logs password or cookie values."""
    if not username or not password:
        raise AuthenticationError("username and password are required at runtime")

    return_url = _validate_return_url(return_url)
    form = fetch_login_form(client, return_url=return_url)

    body = form.post_body(username, password, remember_me=remember_me)
    # Do not pass password further after this call
    response = client.post(
        form.action_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    names = cookie_names(client._client.cookies.jar)
    error = extract_login_error(response.text)
    success = _looks_like_success(response, names) and error is None and not is_login_page(response.text)

    # Some deployments finish login with a hop that still needs one business GET
    if not success and ".iPSA" in names:
        probe = client.get(f"{client.settings.base_url}{return_url}")
        names = cookie_names(client._client.cookies.jar)
        if is_authenticated_business_page(probe.text) or (
            not is_login_page(probe.text) and probe.status_code == 200
        ):
            success = True
            response = probe
            error = None

    if success and is_login_page(response.text):
        # Guard against false positive: login page with leftover cookies
        success = False
        error = error or "Still on login page after POST"

    session = AuthSession(
        authenticated=success,
        username=username if success else None,
        cookie_names_present=names,
        business_host=urlparse(client.settings.base_url).hostname or "ipsapro.isstech.com",
        notes=[] if success else [error or "login failed"],
    )

    return LoginResult(
        success=success,
        session=session,
        final_url=str(response.url),
        status_code=response.status_code,
        error_message=None if success else (error or "login failed"),
    )


def login_with_settings(
    username: str,
    password: str,
    settings: Settings | None = None,
    **kwargs: Any,
) -> tuple[IsstechClient, LoginResult]:
    """Create a fresh client, login, return (client, result). Caller must close client."""
    client = IsstechClient(settings=settings)
    try:
        result = login(client, username, password, **kwargs)
    except Exception:
        client.close()
        raise
    if not result.success:
        client.close()
        raise AuthenticationError(result.error_message or "login failed")
    return client, result


def assert_authenticated(client: IsstechClient, html: str | None = None) -> AuthSession:
    """Detect auth from cookie jar and optional page HTML."""
    names = cookie_names(client._client.cookies.jar)
    ok = ".iPSA" in names
    if html is not None:
        if is_login_page(html):
            ok = False
        elif is_authenticated_business_page(html):
            ok = True
    return AuthSession(
        authenticated=ok,
        cookie_names_present=names,
        notes=[] if ok else ["missing .iPSA or still on login page"],
    )


def absolute_url(base: str, path_or_url: str) -> str:
    return urljoin(base if base.endswith("/") else base + "/", path_or_url)
