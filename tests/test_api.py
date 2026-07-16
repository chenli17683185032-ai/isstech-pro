"""FastAPI facade: sessions, lists, previews, error codes."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3

import httpx
from fastapi.testclient import TestClient

from isstech_replay.account_scope import account_database_path
from isstech_replay.api import create_app
from isstech_replay.auth import login
from isstech_replay.client import IsstechClient, PaginationIncompleteError
from isstech_replay.config import Settings
from isstech_replay.models.procurement import PROCUREMENT_STREAM_BY_WORKFLOW
from isstech_replay.models.work_items import WorkflowKind
from isstech_replay.session_store import SessionStore
from isstech_replay.storage import WorkflowStorage

FIXTURES_AUTH = Path(__file__).parent / "fixtures" / "auth"
FIXTURES_PR = Path(__file__).parent / "fixtures" / "purchase"
FIXTURES_PAYMENT = Path(__file__).parent / "fixtures" / "payment"
FIXTURES_BIZCASE = Path(__file__).parent / "fixtures" / "bizcase"
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


def _account_correlated_search_html() -> str:
    return (
        _search_html()
        .replace("20001", "10002")
        .replace("XQ-REDACTED-101", "XQ-REDACTED-002")
        .replace("PRJ-REDACTED-X", "PRJ-REDACTED-B")
        .replace("REDACTED PROJECT X", "REDACTED PROJECT BETA")
        .replace("USER_REQUESTER", "USER_B")
        .replace("2026-07-01", "2026-06-15")
        .replace("20002", "10001")
        .replace("XQ-REDACTED-102", "XQ-REDACTED-001")
        .replace("PRJ-REDACTED-Y", "PRJ-REDACTED-A")
        .replace("REDACTED PROJECT Y", "REDACTED PROJECT ALPHA")
        .replace("USER_DONE", "USER_B")
        .replace("2026-06-20", "2026-07-01")
    )


def _approval_html() -> str:
    return (FIXTURES_PR / "list_approval_empty.html").read_text(encoding="utf-8")


def _payment_html() -> str:
    return (FIXTURES_PAYMENT / "list.html").read_text(encoding="utf-8")


def _bizcase_html(page: int) -> str:
    return (FIXTURES_BIZCASE / f"page{page}.html").read_text(encoding="utf-8")


def _portal_html() -> str:
    return (
        '<div id="AccountGreetings"><div id="Greeting">'
        "<p>Hi, USER_B</p>"
        "</div></div>"
    )


def _procurement_search_html(workflow: WorkflowKind) -> str:
    spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
    status = {
        WorkflowKind.PROCUREMENT_CONTRACT: "审批通过",
        WorkflowKind.PROCUREMENT_ORDER: "审批通过",
        WorkflowKind.COST_CONFIRMATION: "审批拒绝",
        WorkflowKind.CHECK_ACCEPTANCE: "审批中",
    }[workflow]
    fields = {
        header: f"REDACTED-{index}"
        for index, header in enumerate(spec.headers[1:], start=1)
    }
    fields[spec.reference_field] = f"REF-{workflow.value}"
    fields[spec.title_field] = f"REDACTED {workflow.label}"
    fields[spec.project_no_field] = "PROJECT-REDACTED"
    fields[spec.status_field] = status
    fields[spec.next_approver_field] = "USER_APPROVER" if status == "审批中" else ""
    if spec.applicant_field:
        fields[spec.applicant_field] = (
            "USER_B" if workflow is WorkflowKind.CHECK_ACCEPTANCE else "OTHER_USER"
        )
    if spec.submitted_at_field:
        fields[spec.submitted_at_field] = "2026-07-01"
    headers = "".join(f"<th>{header}</th>" for header in spec.headers)
    cells = [
        f'<td><a class="View" ajax-data="{workflow.value}-1">查看</a></td>'
    ]
    cells.extend(f"<td>{fields[header]}</td>" for header in spec.headers[1:])
    return (
        '<table class="data-grid"><thead><tr>'
        + headers
        + "</tr></thead><tbody><tr>"
        + "".join(cells)
        + "</tr></tbody></table><span>总共1条记录</span>"
    )


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
    if host == "ipsapro.isstech.com" and path.rstrip("/") == "/Portal":
        return httpx.Response(200, text=_portal_html(), request=request)
    if host == "ipsapro.isstech.com" and path == "/WebPMS/Payment/index":
        return httpx.Response(200, text=_payment_html(), request=request)
    if host == "ipsapro.isstech.com" and path == "/WebPMP/Main.aspx":
        page = 1
        if request.method == "POST":
            body = httpx.QueryParams(request.content.decode())
            page = int(body.get("__EVENTARGUMENT", "0"))
        return httpx.Response(200, text=_bizcase_html(page), request=request)
    if host == "ipsapro.isstech.com" and path.startswith(
        "/WebTP/PurchaseRequisition/Download/"
    ):
        return httpx.Response(
            200,
            content=b"FILEBYTES",
            headers={"content-type": "application/pdf"},
            request=request,
        )
    for workflow in (
        WorkflowKind.PROCUREMENT_CONTRACT,
        WorkflowKind.PROCUREMENT_ORDER,
        WorkflowKind.COST_CONFIRMATION,
        WorkflowKind.CHECK_ACCEPTANCE,
    ):
        spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
        if host == "ipsapro.isstech.com" and path.startswith(spec.search_path):
            return httpx.Response(
                200,
                text=_procurement_search_html(workflow),
                request=request,
            )
    if host == "ipsapro.isstech.com" and "/WebTP/PurchaseRequisition/" in path:
        if "/Detail/" in path:
            return httpx.Response(200, text=_edit_html(), request=request)
        if "/ApprovalIndex" in path:
            return httpx.Response(200, text=_approval_html(), request=request)
        if "/SearchIndex" in path:
            return httpx.Response(
                200,
                text=_account_correlated_search_html(),
                request=request,
            )
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


def _add_authed_session(client: TestClient, username: str) -> str:
    upstream = IsstechClient(
        settings=Settings(base_url=BUSINESS, passport_url=PASSPORT),
        transport=httpx.MockTransport(_upstream_handler),
    )
    result = login(upstream, username, "TEST_PASSWORD")
    assert result.success
    record = client.app.state.session_store.create(upstream, username=username)
    return record.token


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
            ("GET", "/v1/extractions"),
            ("GET", "/v1/extractions/missing"),
            ("GET", "/v1/drafts"),
            ("GET", "/v1/drafts/missing"),
            ("GET", "/v1/work-items/current"),
            ("GET", "/v1/work-items/missing/detail"),
            ("GET", "/v1/sync/runs"),
            ("POST", "/v1/readonly-modules/sync"),
            ("GET", "/v1/readonly-modules/payment"),
            ("GET", "/v1/readonly-modules/bizcases"),
            ("GET", "/v1/readonly-modules/runs"),
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


def test_unified_work_items_returns_all_account_visible_workflows() -> None:
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        r = client.get("/v1/work-items", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_count"] == 6
    assert body["follow_up_count"] == 2
    assert body["approved_count"] == 3
    assert body["other_count"] == 1
    by_workflow = {item["workflow"]: item for item in body["items"]}
    follow_up = next(
        item
        for item in body["items"]
        if item["workflow"] == "purchase_requisition" and item["category"] == "follow_up"
    )
    approved = next(
        item
        for item in body["items"]
        if item["workflow"] == "purchase_requisition" and item["category"] == "approved"
    )
    assert follow_up["workflow"] == "purchase_requisition"
    assert follow_up["external_id"] == "10002"
    assert follow_up["current_approver"] == "USER_APPROVER"
    assert follow_up["status"] == "审批中"
    assert follow_up["source_url"].endswith(
        "/WebTP/PurchaseRequisition/Detail/10002"
    )
    assert isinstance(follow_up["waiting_days"], int)
    assert approved["external_id"] == "10001"
    assert approved["status"] == "审批通过"
    assert approved["current_approver"] == ""
    assert approved["waiting_days"] is None
    assert set(by_workflow) == {workflow.value for workflow in WorkflowKind}
    assert follow_up["relations"] == ["applicant"]
    assert approved["relations"] == ["applicant"]
    assert by_workflow["check_acceptance"]["relations"] == ["applicant"]
    assert by_workflow["check_acceptance"]["workflow_label"] == "采购验收"


def test_work_items_reports_incomplete_pagination_as_upstream_error() -> None:
    client, token = _authed_client()
    record = client.app.state.session_store.get(token)
    assert record is not None

    def fail_sync(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise PaginationIncompleteError("pagination incomplete")

    record.client.list_all_procurement_documents = fail_sync  # type: ignore[method-assign]
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
        current = client.get("/v1/work-items/current", headers=headers)
        runs = client.get("/v1/sync/runs", headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["observed_count"] == 6
    assert first.json()["actionable_count"] == 2
    assert first.json()["event_count"] == 6
    assert len(first.json()["streams"]) == 5
    assert second.status_code == 200
    assert second.json()["event_count"] == 0
    assert current.status_code == 200
    assert current.json()["source"] == "sqlite_current"
    assert current.json()["total_count"] == 3
    assert current.json()["follow_up_count"] == 2
    assert current.json()["approved_count"] == 1
    assert current.json()["other_count"] == 0
    assert current.json()["ownership_scope"] == "personal_projects_and_submissions"
    assert current.json()["source_total_count"] == 6
    assert current.json()["matched_count"] == 3
    assert current.json()["my_project_count"] == 0
    assert current.json()["submitted_by_me_count"] == 3
    assert current.json()["workflow_counts"] == {
        WorkflowKind.PURCHASE_REQUISITION.value: 2,
        WorkflowKind.PROCUREMENT_CONTRACT.value: 0,
        WorkflowKind.PROCUREMENT_ORDER.value: 0,
        WorkflowKind.COST_CONFIRMATION.value: 0,
        WorkflowKind.CHECK_ACCEPTANCE.value: 1,
    }
    assert {item["category"] for item in current.json()["items"]} == {
        "follow_up",
        "approved",
    }
    assert all(item["scope_reasons"] == ["submitted_by_me"] for item in current.json()["items"])
    assert sum(bool(item["relations"]) for item in current.json()["items"]) == 3
    assert runs.status_code == 200
    assert len(runs.json()) == 10
    assert runs.json()[0]["status"] == "succeeded"
    storage = WorkflowStorage(
        account_database_path("alice", base_database_path=database)
    )
    assert storage.table_count("sync_runs") == 10
    assert storage.table_count("workflow_current") == 6
    assert storage.table_count("workflow_events") == 6


def test_readonly_module_api_syncs_and_replays_cached_lists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        work_items_sync = client.post("/v1/sync/work-items", headers=headers)
        before = client.get("/v1/work-items/current", headers=headers)
        first = client.post("/v1/readonly-modules/sync", headers=headers)
        second = client.post("/v1/readonly-modules/sync", headers=headers)
        payment = client.get("/v1/readonly-modules/payment", headers=headers)
        bizcases = client.get("/v1/readonly-modules/bizcases", headers=headers)
        runs = client.get("/v1/readonly-modules/runs", headers=headers)
        after = client.get("/v1/work-items/current", headers=headers)

    assert work_items_sync.status_code == 200
    assert before.status_code == 200
    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert first.json()["observed_count"] == 5
    assert first.json()["changed_count"] == 5
    assert [stream["module"] for stream in first.json()["streams"]] == [
        "payment",
        "bizcase",
    ]
    assert second.status_code == 200
    assert second.json()["changed_count"] == 0
    assert payment.status_code == 200
    assert payment.json()["ownership_scope"] == "account_visible"
    assert payment.json()["total_count"] == 2
    assert payment.json()["items"][0]["payment_no"] == "PAYMENT-REDACTED-1"
    assert bizcases.status_code == 200
    assert bizcases.json()["total_count"] == 3
    assert [item["ordinal"] for item in bizcases.json()["items"]] == [1, 2, 3]
    assert runs.status_code == 200
    assert len(runs.json()) == 4
    assert after.status_code == 200
    assert after.json()["total_count"] == before.json()["total_count"] == 3

    storage = WorkflowStorage(
        account_database_path("alice", base_database_path=database)
    )
    assert storage.table_count("readonly_module_runs") == 4
    assert storage.table_count("readonly_module_current") == 5


def test_work_item_detail_is_limited_to_visible_current_account_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    client, token = _authed_client()
    headers = {"Authorization": f"Bearer {token}"}
    record = client.app.state.session_store.get(token)
    assert record is not None

    with client:
        sync = client.post("/v1/sync/work-items", headers=headers)
        assert sync.status_code == 200

        detail_calls: list[str] = []
        original_detail = record.client.get_purchase_requisition

        def tracked_detail(external_id: str):
            detail_calls.append(external_id)
            return original_detail(external_id)

        record.client.get_purchase_requisition = tracked_detail  # type: ignore[method-assign]
        owned = client.get("/v1/work-items/10001/detail", headers=headers)
        assert detail_calls == []

        legacy_payload = json.dumps(
            {"payload_version": 1},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        legacy_hash = hashlib.sha256(legacy_payload.encode()).hexdigest()
        scoped_database = account_database_path(
            "alice",
            base_database_path=database,
        )
        with sqlite3.connect(scoped_database) as connection:
            connection.execute(
                "UPDATE workflow_current SET payload_json = ?, payload_hash = ? "
                "WHERE external_id = '10001'",
                (legacy_payload, legacy_hash),
            )
            connection.commit()
        legacy = client.get("/v1/work-items/10001/detail", headers=headers)
        missing = client.get("/v1/work-items/not-owned/detail", headers=headers)
        blank = client.get("/v1/work-items/%20/detail", headers=headers)
        outside_scope = client.get(
            "/v1/work-items/procurement_contract/procurement_contract-1/detail",
            headers=headers,
        )

        with sqlite3.connect(scoped_database) as connection:
            connection.execute(
                "UPDATE workflow_current SET status = '已保存', actionable = 0 "
                "WHERE external_id = '10002'"
            )
            connection.commit()
        hidden = client.get("/v1/work-items/10002/detail", headers=headers)

    assert owned.status_code == 200
    assert owned.json()["item"]["external_id"] == "10001"
    assert owned.json()["item"]["category"] == "approved"
    assert owned.json()["fields"]["PR_RequisitionNo"] == "XQ-REDACTED-101"
    assert len(owned.json()["approval_steps"]) == 2
    assert owned.json()["approval_status"] == "available"
    assert legacy.status_code == 404
    assert missing.status_code == 404
    assert blank.status_code == 404
    assert outside_scope.status_code == 404
    assert hidden.status_code == 200
    assert hidden.json()["item"]["category"] == "other"
    assert missing.json()["detail"]["code"] == "NOT_FOUND"
    assert detail_calls == []


def test_current_work_items_keeps_freshness_when_successful_sync_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    storage = WorkflowStorage(
        account_database_path("alice", base_database_path=database)
    )
    observed_at = "2026-07-15T01:00:00+00:00"
    storage.start_run(
        run_id="empty-run",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=observed_at,
        max_pages=20,
    )
    storage.complete_run(
        run_id="empty-run",
        observed_at=observed_at,
        finished_at=observed_at,
        source_total_count=0,
        snapshots=(),
        actionable_count=0,
    )
    client, token = _authed_client()

    with client:
        response = client.get(
            "/v1/work-items/current",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total_count"] == 0
    assert response.json()["synced_at"] == observed_at


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
    assert not (tmp_path / "accounts").exists()


def test_persisted_work_items_and_runs_are_isolated_by_login_account(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "workflow.sqlite3"
    monkeypatch.setenv("ISSTECH_DATABASE_PATH", str(database))
    legacy = WorkflowStorage(database)
    observed_at = "2026-07-15T01:00:00+00:00"
    legacy.start_run(
        run_id="legacy-unscoped-run",
        adapter=WorkflowKind.PURCHASE_REQUISITION,
        started_at=observed_at,
        max_pages=20,
    )
    legacy.complete_run(
        run_id="legacy-unscoped-run",
        observed_at=observed_at,
        finished_at=observed_at,
        source_total_count=0,
        snapshots=(),
        actionable_count=0,
    )
    legacy_bytes = database.read_bytes()

    client, alice_token = _authed_client()
    bob_token = _add_authed_session(client, "bob")
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    with client:
        assert client.get("/v1/sync/runs", headers=alice_headers).json() == []
        alice_sync = client.post("/v1/sync/work-items", headers=alice_headers)
        bob_before = client.get("/v1/work-items/current", headers=bob_headers)
        bob_runs_before = client.get("/v1/sync/runs", headers=bob_headers)
        bob_sync = client.post("/v1/sync/work-items", headers=bob_headers)
        alice_after = client.get("/v1/work-items/current", headers=alice_headers)
        alice_runs_after = client.get("/v1/sync/runs", headers=alice_headers)

    assert alice_sync.status_code == 200
    assert bob_before.status_code == 200
    assert bob_before.json()["items"] == []
    assert bob_before.json()["synced_at"] is None
    assert bob_runs_before.json() == []
    assert bob_sync.status_code == 200
    assert alice_after.json()["total_count"] == 3
    assert alice_after.json()["follow_up_count"] == 2
    assert alice_after.json()["approved_count"] == 1
    assert len(alice_runs_after.json()) == 5
    assert account_database_path(
        "alice", base_database_path=database
    ).is_file()
    assert account_database_path("bob", base_database_path=database).is_file()
    assert database.read_bytes() == legacy_bytes


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
        extraction_list = client.get(
            f"/v1/extractions?material_id={material_id}",
            headers=headers,
        )
        draft_list = client.get("/v1/drafts", headers=headers)

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
    assert extraction_list.status_code == 200
    assert [item["extraction_id"] for item in extraction_list.json()] == [extraction_id]
    assert draft_list.status_code == 200
    assert draft_list.json()[0]["draft_id"] == draft_id
    assert draft_list.json()[0]["state"] == "ready"
    assert draft_list.json()[0]["title"] == "REDACTED PROJECT"


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
    assert "/v1/work-items/current" in paths
    assert "/v1/work-items/{external_id}/detail" in paths
    assert "/v1/sync/work-items" in paths
    assert "/v1/sync/runs" in paths
    assert "/v1/materials" in paths
    assert "/v1/materials/{material_id}/content" in paths
    assert "/v1/materials/{material_id}/extractions" in paths
    assert "/v1/extractions" in paths
    assert "/v1/extractions/{extraction_id}" in paths
    assert "/v1/extractions/{extraction_id}/drafts" in paths
    assert "/v1/drafts/{draft_id}" in paths
    assert "/v1/drafts" in paths
    assert "/v1/drafts/{draft_id}/fields/{field_name}" in paths
    assert "/v1/drafts/{draft_id}/validate" in paths
    assert "/v1/drafts/{draft_id}/ready" in paths
    assert "/v1/previews/purchase-requisitions/{requisition_id}/delete" in paths


def test_committed_openapi_matches_runtime() -> None:
    app = create_app(session_store=SessionStore())
    committed = json.loads((Path(__file__).parents[1] / "docs" / "openapi.json").read_text())
    assert committed == app.openapi()
