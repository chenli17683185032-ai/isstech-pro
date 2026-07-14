"""Pure HTTP login: form parsing, redirect handling, auth detection, safety."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.auth import (
    AuthenticationError,
    assert_authenticated,
    build_passport_entry_url,
    fetch_login_form,
    login,
)
from isstech_replay.client import IsstechClient
from isstech_replay.config import Settings
from isstech_replay.models.auth import LoginForm
from isstech_replay.parsers.login import (
    extract_login_error,
    is_authenticated_business_page,
    is_login_page,
    parse_login_form,
)
from isstech_replay.policy import PolicyViolation

FIXTURES = Path(__file__).parent / "fixtures" / "auth"
BUSINESS = "http://ipsapro.isstech.com"
PASSPORT = "https://passport.isstech.com"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_login_form_uses_action_query_when_hidden_empty() -> None:
    page_url = (
        f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com"
        "&ReturnUrl=%2fWebTP%2fPurchaseRequisition"
    )
    form = parse_login_form(_html("passport_login.html"), page_url)
    assert form.username_field == "emp_DomainName"
    assert form.password_field == "emp_Password"
    assert form.remember_me_field == "RemeberMe"
    assert form.domain_url == "http://ipsapro.isstech.com"
    assert form.return_url == "/WebTP/PurchaseRequisition"
    assert "DomainUrl=" in form.action_url
    body = form.post_body("alice", "secret", remember_me=False)
    assert body["emp_DomainName"] == "alice"
    assert body["emp_Password"] == "secret"
    assert body["DomainUrl"] == ""
    assert body["ReturnUrl"] == ""
    assert "RemeberMe" not in body
    assert set(body) == {
        "emp_DomainName",
        "emp_Password",
        "DomainUrl",
        "ReturnUrl",
    }
    assert "secret" not in form.action_url


def test_parse_failed_login_form_ignores_status_fields_outside_form() -> None:
    html = _html("passport_login_failed.html")
    form = parse_login_form(html, f"{PASSPORT}/")
    assert form.domain_url == "http://ipsapro.isstech.com"
    assert "flag" not in form.post_body("alice", "secret")
    assert extract_login_error(html) is not None
    assert is_login_page(html)


def test_authenticated_page_not_misread_as_login() -> None:
    html = _html("purchase_authenticated.html")
    assert is_authenticated_business_page(html)
    assert not is_login_page(html)


def test_login_page_not_misread_as_authenticated() -> None:
    html = _html("passport_login.html")
    assert is_login_page(html)
    assert not is_authenticated_business_page(html)


def test_successful_login_flow_with_mock_transport() -> None:
    """Empty jar → business 302 → passport form → POST → set .iPSA → business page."""
    login_html = _html("passport_login.html")
    auth_html = _html("purchase_authenticated.html")
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url.rstrip("/").endswith("/WebTP/PurchaseRequisition"):
            # After cookies present, return business page
            if request.headers.get("cookie") and ".iPSA" in request.headers.get("cookie", ""):
                return httpx.Response(200, text=auth_html, request=request)
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com"
                        "&ReturnUrl=%2fWebTP%2fPurchaseRequisition"
                    )
                },
                request=request,
            )
        if request.method == "GET" and "passport.isstech.com" in url:
            return httpx.Response(
                200,
                text=login_html,
                headers={"Set-Cookie": "ASP.NET_SessionId=session-passport; path=/; HttpOnly; SameSite=Lax"},
                request=request,
            )
        if request.method == "POST" and "passport.isstech.com" in url:
            posts.append(request)
            body = request.content.decode("utf-8")
            assert "emp_DomainName=alice" in body
            assert "emp_Password=TEST_PASSWORD" in body
            assert "DomainUrl=" in body
            assert "flag=" not in body
            assert "ctip=" not in body
            # Simulate SSO: set business auth cookie on parent domain via redirect chain
            return httpx.Response(
                302,
                headers={
                    "Location": f"{BUSINESS}/WebTP/PurchaseRequisition",
                    "Set-Cookie": ".iPSA=TEST_TICKET; domain=.isstech.com; path=/; HttpOnly; SameSite=Lax",
                },
                request=request,
            )
        return httpx.Response(404, text="missing", request=request)

    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    with IsstechClient(settings=settings, transport=httpx.MockTransport(handler)) as client:
        result = login(client, "alice", "TEST_PASSWORD")

    assert result.success is True
    assert result.session.authenticated is True
    assert result.session.has_ipsa_cookie is True
    assert result.error_message is None
    assert len(posts) == 1
    # Password must not appear in LoginResult / AuthSession repr paths
    assert "TEST_PASSWORD" not in repr(result)
    assert "TEST_PASSWORD" not in repr(result.session)


def test_failed_login_detects_error_and_stays_unauthenticated() -> None:
    login_html = _html("passport_login.html")
    fail_html = _html("passport_login_failed.html")
    seen_post = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_post
        host = request.url.host or ""
        path = request.url.path or "/"
        if host == "ipsapro.isstech.com" and request.method == "GET":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com"
                        "&ReturnUrl=%2fWebTP%2fPurchaseRequisition"
                    )
                },
                request=request,
            )
        if host == "passport.isstech.com" and request.method == "POST":
            seen_post = True
            return httpx.Response(
                302,
                headers={"Location": f"{PASSPORT}/"},
                request=request,
            )
        if host == "passport.isstech.com" and request.method == "GET":
            return httpx.Response(
                200,
                text=fail_html if seen_post else login_html,
                request=request,
            )
        return httpx.Response(404, text=f"unhandled {host}{path}", request=request)

    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    with IsstechClient(settings=settings, transport=httpx.MockTransport(handler)) as client:
        result = login(client, "alice", "wrong")

    assert result.success is False
    assert result.session.authenticated is False
    assert result.session.has_ipsa_cookie is False
    assert result.error_message is not None
    assert "错误" in result.error_message


def test_fetch_login_form_from_empty_session() -> None:
    login_html = _html("passport_login.html")

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        if host == "ipsapro.isstech.com" and request.method == "GET":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com"
                        "&ReturnUrl=%2fWebTP%2fPurchaseRequisition"
                    )
                },
                request=request,
            )
        if host == "passport.isstech.com":
            return httpx.Response(200, text=login_html, request=request)
        return httpx.Response(404, text="unhandled", request=request)

    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    with IsstechClient(settings=settings, transport=httpx.MockTransport(handler)) as client:
        form = fetch_login_form(client)
    assert isinstance(form, LoginForm)
    assert form.domain_url == "http://ipsapro.isstech.com"


def test_assert_authenticated_requires_ipsa_cookie_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        session = assert_authenticated(client)
        assert session.authenticated is False


def test_build_passport_entry_url() -> None:
    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    url = build_passport_entry_url(settings)
    assert url.startswith(f"{PASSPORT}/?")
    assert "DomainUrl=" in url
    assert "ReturnUrl=" in url


def test_login_rejects_unapproved_return_url() -> None:
    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    with pytest.raises(AuthenticationError):
        build_passport_entry_url(settings, return_url="/Portal")


def test_login_rejects_form_action_on_passport_subdomain_before_post() -> None:
    login_html = _html("passport_login.html").replace(
        'action="/?DomainUrl=',
        'action="https://evil.passport.isstech.com/?DomainUrl=',
    )
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(str(request.url))
        if request.url.host == "ipsapro.isstech.com":
            return httpx.Response(
                302,
                headers={
                    "Location": (
                        f"{PASSPORT}/?DomainUrl=http://ipsapro.isstech.com"
                        "&ReturnUrl=%2fWebTP%2fPurchaseRequisition"
                    )
                },
                request=request,
            )
        return httpx.Response(200, text=login_html, request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthenticationError):
            login(client, "alice", "secret")
    assert posts == []


def test_login_does_not_bypass_policy_for_writes() -> None:
    """Auth module still cannot send Delete through the client."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_html("passport_login.html"), request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PolicyViolation):
            client.get(f"{BUSINESS}/WebTP/PurchaseRequisition/Delete/1")


def test_login_requires_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x", request=request)

    with IsstechClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthenticationError):
            login(client, "", "")
