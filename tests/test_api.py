"""FastAPI facade: sessions, lists, previews, error codes."""

from __future__ import annotations

from pathlib import Path
import json

import httpx
from fastapi.testclient import TestClient

from isstech_replay.api import create_app
from isstech_replay.auth import login
from isstech_replay.client import IsstechClient
from isstech_replay.config import Settings
from isstech_replay.session_store import SessionStore

FIXTURES_AUTH = Path(__file__).parent / "fixtures" / "auth"
FIXTURES_PR = Path(__file__).parent / "fixtures" / "purchase"
BUSINESS = "http://ipsapro.isstech.com"
PASSPORT = "https://passport.isstech.com"


def _login_html() -> str:
    return (FIXTURES_AUTH / "passport_login.html").read_text(encoding="utf-8")


def _auth_html() -> str:
    return (FIXTURES_AUTH / "purchase_authenticated.html").read_text(encoding="utf-8")


def _list_html() -> str:
    return (FIXTURES_PR / "list_application.html").read_text(encoding="utf-8")


def _edit_html() -> str:
    return (FIXTURES_PR / "edit_detail.html").read_text(encoding="utf-8")


def _upstream_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host or ""
    path = request.url.path or "/"
    if host == "ipsapro.isstech.com" and path.rstrip("/") == "/WebTP/PurchaseRequisition":
        # unauth redirect unless cookie present
        cookie = request.headers.get("cookie", "")
        if ".iPSA=" in cookie:
            return httpx.Response(200, text=_list_html(), request=request)
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
    if host == "passport.isstech.com" and request.method == "GET":
        return httpx.Response(200, text=_login_html(), request=request)
    if host == "passport.isstech.com" and request.method == "POST":
        return httpx.Response(
            302,
            headers={
                "Location": f"{BUSINESS}/WebTP/PurchaseRequisition",
                "Set-Cookie": ".iPSA=TEST_TICKET; domain=.isstech.com; path=/; HttpOnly",
            },
            request=request,
        )
    if host == "ipsapro.isstech.com" and "/WebTP/PurchaseRequisition/" in path:
        if path.endswith("/Edit/10001") or "/Edit/" in path:
            return httpx.Response(200, text=_edit_html(), request=request)
        return httpx.Response(200, text=_list_html(), request=request)
    if host == "ipsapro.isstech.com" and path.startswith("/WebTP/Attachment/Download/"):
        return httpx.Response(
            200,
            content=b"FILEBYTES",
            headers={"content-type": "application/pdf"},
            request=request,
        )
    return httpx.Response(404, text=f"no {host}{path}", request=request)


def _authed_client() -> tuple[TestClient, str]:
    """Build app and inject a pre-authenticated session without live network."""
    store = SessionStore(ttl_seconds=3600)
    settings = Settings(base_url=BUSINESS, passport_url=PASSPORT)
    upstream = IsstechClient(
        settings=settings,
        transport=httpx.MockTransport(_upstream_handler),
    )
    result = login(upstream, "alice", "TEST_PASSWORD")
    assert result.success
    record = store.create(upstream, username="alice")
    app = create_app(session_store=store)
    return TestClient(app), record.token


def _mock_client_factory() -> IsstechClient:
    return IsstechClient(
        settings=Settings(base_url=BUSINESS, passport_url=PASSPORT),
        transport=httpx.MockTransport(_upstream_handler),
    )


def test_health() -> None:
    app = create_app(session_store=SessionStore())
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_session_required() -> None:
    app = create_app(session_store=SessionStore())
    with TestClient(app) as client:
        r = client.get("/v1/purchase-requisitions")
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "AUTH_EXPIRED"


def test_create_session_route_returns_local_token_without_upstream_ticket() -> None:
    app = create_app(
        session_store=SessionStore(),
        client_factory=_mock_client_factory,
    )
    with TestClient(app) as client:
        r = client.post(
            "/v1/sessions",
            json={"username": "alice", "password": "TEST_PASSWORD"},
        )
    assert r.status_code == 200
    payload = r.json()
    assert payload["authenticated"] is True
    assert payload["token"]
    serialized = json.dumps(payload)
    assert "TEST_PASSWORD" not in serialized
    assert "TEST_TICKET" not in serialized


def test_uncaptured_view_returns_not_captured() -> None:
    client, token = _authed_client()
    with client:
        r = client.get(
            "/v1/purchase-requisitions?view=approval",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 501
    assert r.json()["detail"]["code"] == "NOT_CAPTURED"


def test_list_and_detail_and_attachments() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.get("/v1/session", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["authenticated"] is True
        assert body["username"] == "alice"
        assert body["token"] is None  # never echo bearer

        r = client.get("/v1/purchase-requisitions?view=application", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_count"] == 2
        assert data["items"][0]["id"] == "10001"

        r = client.get("/v1/purchase-requisitions/10001", headers=headers)
        assert r.status_code == 200
        assert r.json()["fields"]["PR_ID"] == "10001"

        r = client.get("/v1/purchase-requisitions/10001/attachments", headers=headers)
        assert r.status_code == 200
        assert r.json()[0]["id"] == "9001"

        r = client.get("/v1/attachments/9001/content?meta_only=true", headers=headers)
        assert r.status_code == 200
        meta = r.json()
        assert meta["sha256"]
        assert meta["content_length"] == len(b"FILEBYTES")


def test_preview_delete_not_sendable() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.post(
            "/v1/previews/purchase-requisitions/10001/delete",
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["sendable"] is False
        assert body["action"] == "pr.delete"
        assert body["method"] == "GET"
        assert "Delete/10001" in body["url"]


def test_preview_create_and_upload() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.post(
            "/v1/previews/purchase-requisitions/create",
            headers=headers,
            json={"fields": {"PR_PrjName": "REDACTED"}},
        )
        assert r.status_code == 200
        assert r.json()["sendable"] is False

        r = client.post(
            "/v1/previews/attachments/upload",
            headers=headers,
            json={"doc_id": "10001", "filename": "a.pdf"},
        )
        assert r.status_code == 200
        assert r.json()["body_kind"] == "multipart"
        assert r.json()["body_summary"]["bytes_omitted"] is True


def test_logout() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.delete("/v1/session", headers=headers)
        assert r.status_code == 200
        r = client.get("/v1/session", headers=headers)
        assert r.status_code == 401


def test_openapi_available() -> None:
    app = create_app(session_store=SessionStore())
    with TestClient(app) as client:
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        assert "/v1/sessions" in paths
        assert "/v1/purchase-requisitions" in paths
        assert "/v1/previews/purchase-requisitions/{requisition_id}/delete" in paths


def test_committed_openapi_matches_runtime() -> None:
    app = create_app(session_store=SessionStore())
    committed = json.loads((Path(__file__).parents[1] / "docs" / "openapi.json").read_text())
    assert committed == app.openapi()
