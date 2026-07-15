"""FastAPI facade: sessions, lists, previews, error codes."""

from __future__ import annotations

from pathlib import Path
import json

import httpx
from fastapi.testclient import TestClient

from isstech_replay.api import create_app
from isstech_replay.auth import login
from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.config import Settings
from isstech_replay.session_store import SessionStore
from isstech_replay.storage import WorkflowStorage

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
    return (FIXTURES_PR / "detail_readonly.html").read_text(encoding="utf-8")


def _search_html() -> str:
    return (FIXTURES_PR / "list_search.html").read_text(encoding="utf-8")


def _approval_html() -> str:
    return (FIXTURES_PR / "list_approval_empty.html").read_text(encoding="utf-8")


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
    if host == "ipsapro.isstech.com" and path.startswith(
        "/WebTP/PurchaseRequisition/Download/"
    ):
        return httpx.Response(
            200,
            content=b"FILEBYTES",
            headers={"content-type": "application/pdf"},
            request=request,
        )
    if host == "ipsapro.isstech.com" and "/WebTP/PurchaseRequisition/" in path:
        if "/Detail/" in path:
            return httpx.Response(200, text=_edit_html(), request=request)
        if "/ApprovalIndex" in path:
            return httpx.Response(200, text=_approval_html(), request=request)
        if "/SearchIndex" in path:
            return httpx.Response(200, text=_search_html(), request=request)
        return httpx.Response(200, text=_list_html(), request=request)
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
        for method, path in (
            ("GET", "/v1/purchase-requisitions"),
            ("GET", "/v1/work-items"),
            ("POST", "/v1/sync/work-items"),
            ("POST", "/v1/materials"),
            ("GET", "/v1/extractions/missing"),
            ("GET", "/v1/drafts/missing"),
        ):
            r = client.request(method, path)
            assert r.status_code == 401
            assert r.json()["detail"]["code"] == "AUTH_EXPIRED"
        extraction = client.post(
            "/v1/materials/missing/extractions",
            json={},
        )
        assert extraction.status_code == 401
        assert extraction.json()["detail"]["code"] == "AUTH_EXPIRED"
        draft = client.post("/v1/extractions/missing/drafts")
        assert draft.status_code == 401
        assert draft.json()["detail"]["code"] == "AUTH_EXPIRED"


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


def test_captured_views_are_available() -> None:
    client, token = _authed_client()
    with client:
        r = client.get(
            "/v1/purchase-requisitions?view=approval",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert r.json()["view"] == "approval"
    assert r.json()["items"] == []


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
        assert r.json()["fields"]["PR_RequisitionNo"] == "XQ-REDACTED-101"
        assert len(r.json()["approval_steps"]) == 2

        r = client.get("/v1/purchase-requisitions/10001/attachments", headers=headers)
        assert r.status_code == 200
        assert r.json()[0]["id"] == "99001"

        r = client.get("/v1/attachments/99001/content?meta_only=true", headers=headers)
        assert r.status_code == 200
        meta = r.json()
        assert meta["sha256"]
        assert meta["content_length"] == len(b"FILEBYTES")


def test_unified_work_items_returns_only_pending_named_approvers() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.get("/v1/work-items", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 1
    assert body["items"][0]["workflow"] == "purchase_requisition"
    assert body["items"][0]["external_id"] == "20001"
    assert body["items"][0]["current_approver"] == "USER_APPROVER"
    assert body["items"][0]["status"] == "审批中"
    assert body["items"][0]["source_url"].endswith(
        "/WebTP/PurchaseRequisition/Detail/20001"
    )
    assert isinstance(body["items"][0]["waiting_days"], int)


def test_work_items_reports_incomplete_pagination_as_upstream_error() -> None:
    client, token = _authed_client()
    record = client.app.state.session_store.get(token)
    assert record is not None

    def fail_sync(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PaginationIncompleteError("pagination incomplete")

    record.client.list_all_purchase_requisitions = fail_sync  # type: ignore[method-assign]
    with client:
        response = client.get(
            "/v1/work-items",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "UPSTREAM_ERROR"
    assert "pagination incomplete" in response.json()["detail"]["message"]


def test_manual_sync_persists_snapshots_and_replay_has_no_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        first = client.post("/v1/sync/work-items", headers=headers)
        second = client.post("/v1/sync/work-items", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["observed_count"] == 2
    assert first.json()["actionable_count"] == 1
    assert first.json()["event_count"] == 2
    assert second.status_code == 200
    assert second.json()["event_count"] == 0
    storage = WorkflowStorage(database)
    assert storage.table_count("sync_runs") == 2
    assert storage.table_count("workflow_current") == 2
    assert storage.table_count("workflow_events") == 2


def test_manual_sync_dry_run_does_not_create_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "dry.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    client, token = _authed_client()
    with client:
        response = client.post(
            "/v1/sync/work-items?dry_run=true",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
    assert response.json()["database_path"] is None
    assert not database.exists()


def test_material_upload_list_detail_content_and_deduplication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    database = data_dir / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    pdf = b"%PDF-1.7\nREDACTED API MATERIAL\n%%EOF\n"
    files = {"file": ("proposal.pdf", pdf, "application/pdf")}
    with client:
        first = client.post("/v1/materials", headers=headers, files=files)
        second = client.post("/v1/materials", headers=headers, files=files)
        material_id = first.json()["material"]["id"]
        listing = client.get("/v1/materials", headers=headers)
        detail = client.get(f"/v1/materials/{material_id}", headers=headers)
        content = client.get(f"/v1/materials/{material_id}/content", headers=headers)

    assert first.status_code == 201
    assert first.json()["material"]["status"] == "ready"
    assert first.json()["blob_created"] is True
    assert second.status_code == 201
    assert second.json()["material"]["id"] == material_id
    assert second.json()["deduplicated"] is True
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert detail.json()["sha256"] == first.json()["material"]["sha256"]
    assert "original_path" not in detail.json()
    assert content.status_code == 200
    assert content.content == pdf


def test_material_upload_size_limit_and_status_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ISSTECH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(data_dir / "workflow.sqlite3"))
    monkeypatch.setenv("ISSTECH_MAX_MATERIAL_BYTES", "4")
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        too_large = client.post(
            "/v1/materials",
            headers=headers,
            files={"file": ("large.bin", b"12345", "application/octet-stream")},
        )
        bad_status = client.get(
            "/v1/materials?ingest_status=unknown",
            headers=headers,
        )
        missing = client.get("/v1/materials/missing", headers=headers)
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "PAYLOAD_TOO_LARGE"
    assert bad_status.status_code == 400
    assert bad_status.json()["detail"]["code"] == "BAD_REQUEST"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_material_extraction_api_persists_evidence_and_supports_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ISSTECH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(data_dir / "workflow.sqlite3"))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    text = "\n".join(
        (
            "项目编号：PRJ-001",
            "项目名称：REDACTED PROJECT",
            "采购方式：公开询价",
        )
    ).encode()
    with client:
        uploaded = client.post(
            "/v1/materials",
            headers=headers,
            files={"file": ("project.txt", text, "text/plain")},
        )
        material_id = uploaded.json()["material"]["id"]
        created = client.post(
            f"/v1/materials/{material_id}/extractions",
            headers=headers,
            json={},
        )
        extraction_id = created.json()["extraction_id"]
        fetched = client.get(
            f"/v1/extractions/{extraction_id}",
            headers=headers,
        )

    assert uploaded.status_code == 201
    assert created.status_code == 201
    assert created.json()["status"] == "succeeded"
    assert created.json()["can_advance"] is True
    assert created.json()["field_count"] == 3
    assert {field["review_status"] for field in created.json()["fields"]} == {
        "pending"
    }
    assert all(field["evidence"]["material_id"] == material_id for field in created.json()["fields"])
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_material_extraction_api_reports_missing_material_and_provider_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ISSTECH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(data_dir / "workflow.sqlite3"))
    monkeypatch.delenv("ISSTECH_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("ISSTECH_AI_MODEL", raising=False)
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        missing = client.post(
            "/v1/materials/missing/extractions",
            headers=headers,
            json={},
        )
        uploaded = client.post(
            "/v1/materials",
            headers=headers,
            files={"file": ("project.txt", b"REDACTED", "text/plain")},
        )
        material_id = uploaded.json()["material"]["id"]
        unconfigured = client.post(
            f"/v1/materials/{material_id}/extractions",
            headers=headers,
            json={"provider": "http_json"},
        )
        missing_run = client.get("/v1/extractions/missing", headers=headers)

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"
    assert unconfigured.status_code == 400
    assert unconfigured.json()["detail"]["code"] == "BAD_REQUEST"
    assert "ISSTECH_AI_ENDPOINT" in unconfigured.json()["detail"]["message"]
    assert missing_run.status_code == 404
    assert missing_run.json()["detail"]["code"] == "NOT_FOUND"


def test_draft_review_api_reaches_ready_with_audited_session_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ISSTECH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(data_dir / "workflow.sqlite3"))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    text = "项目编号：PRJ-001\n项目名称：REDACTED PROJECT\n采购方式：公开询价".encode()
    with client:
        uploaded = client.post(
            "/v1/materials",
            headers=headers,
            files={"file": ("review.txt", text, "text/plain")},
        )
        material_id = uploaded.json()["material"]["id"]
        extracted = client.post(
            f"/v1/materials/{material_id}/extractions",
            headers=headers,
            json={},
        )
        extraction_id = extracted.json()["extraction_id"]
        created = client.post(
            f"/v1/extractions/{extraction_id}/drafts",
            headers=headers,
        )
        duplicate = client.post(
            f"/v1/extractions/{extraction_id}/drafts",
            headers=headers,
        )
        draft = created.json()["draft"]
        draft_id = draft["draft_id"]
        premature = client.post(
            f"/v1/drafts/{draft_id}/ready",
            headers=headers,
            json={"expected_version": draft["version"]},
        )

        first_pending = next(
            field for field in draft["fields"] if field["proposed_value"] is not None
        )
        first_review = client.put(
            f"/v1/drafts/{draft_id}/fields/{first_pending['field_name']}",
            headers=headers,
            json={
                "decision": "confirmed",
                "confirmed_value": first_pending["proposed_value"],
                "expected_version": draft["version"],
            },
        )
        stale = client.put(
            f"/v1/drafts/{draft_id}/fields/PR_PrjName",
            headers=headers,
            json={
                "decision": "confirmed",
                "confirmed_value": "REDACTED PROJECT",
                "expected_version": draft["version"],
            },
        )
        current = first_review.json()
        for field in current["fields"]:
            if field["proposed_value"] is None or field["decision"] != "pending":
                continue
            reviewed = client.put(
                f"/v1/drafts/{draft_id}/fields/{field['field_name']}",
                headers=headers,
                json={
                    "decision": "confirmed",
                    "confirmed_value": field["proposed_value"],
                    "expected_version": current["version"],
                },
            )
            assert reviewed.status_code == 200
            current = reviewed.json()
        validated = client.post(
            f"/v1/drafts/{draft_id}/validate",
            headers=headers,
            json={"expected_version": current["version"]},
        )
        ready = client.post(
            f"/v1/drafts/{draft_id}/ready",
            headers=headers,
            json={"expected_version": validated.json()["version"]},
        )
        fetched = client.get(f"/v1/drafts/{draft_id}", headers=headers)

    assert created.status_code == 201
    assert created.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert duplicate.json()["draft"]["draft_id"] == draft_id
    assert premature.status_code == 409
    assert premature.json()["detail"]["code"] == "CONFLICT"
    assert first_review.status_code == 200
    first_field = next(
        field
        for field in first_review.json()["fields"]
        if field["field_name"] == first_pending["field_name"]
    )
    assert first_field["proposed_value"] == first_pending["proposed_value"]
    assert first_field["reviewed_by"] == "alice"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "CONFLICT"
    assert validated.status_code == 200
    assert validated.json()["state"] == "validated"
    assert ready.status_code == 200
    assert ready.json()["state"] == "ready"
    assert fetched.json() == ready.json()
    assert {event["actor"] for event in ready.json()["audit_events"]} == {"alice"}


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
    assert "/v1/work-items" in paths
    assert "/v1/sync/work-items" in paths
    assert "/v1/materials" in paths
    assert "/v1/materials/{material_id}/content" in paths
    assert "/v1/materials/{material_id}/extractions" in paths
    assert "/v1/extractions/{extraction_id}" in paths
    assert "/v1/extractions/{extraction_id}/drafts" in paths
    assert "/v1/drafts/{draft_id}" in paths
    assert "/v1/drafts/{draft_id}/fields/{field_name}" in paths
    assert "/v1/drafts/{draft_id}/validate" in paths
    assert "/v1/drafts/{draft_id}/ready" in paths
    assert "/v1/previews/purchase-requisitions/{requisition_id}/delete" in paths


def test_committed_openapi_matches_runtime() -> None:
    app = create_app(session_store=SessionStore())
    committed = json.loads((Path(__file__).parents[1] / "docs" / "openapi.json").read_text())
    assert committed == app.openapi()
