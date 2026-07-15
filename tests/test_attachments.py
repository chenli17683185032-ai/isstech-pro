"""Attachment list parsing and download digest."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from isstech_replay.client import IsstechClient
from isstech_replay.config import Settings
from isstech_replay.parsers.attachment import extract_download_ids, parse_attachment_list
from isstech_replay.policy import PolicyViolation

FIXTURES = Path(__file__).parent / "fixtures" / "purchase"
BUSINESS = "http://ipsapro.isstech.com"


def test_parse_attachment_rows() -> None:
    html = (FIXTURES / "detail_readonly.html").read_text(encoding="utf-8")
    items = parse_attachment_list(html, doc_id="10001")
    assert len(items) == 1
    assert items[0].id == "99001"
    assert items[0].file_name == "REDACTED CONTRACT.pdf"
    assert items[0].uploader_name == "USER_UPLOADER"
    assert items[0].upload_date == "2026-07-01"
    assert items[0].doc_id == "10001"
    assert extract_download_ids(html) == ("99001",)


def test_client_list_and_download() -> None:
    detail_html = (FIXTURES / "detail_readonly.html").read_text(encoding="utf-8")
    payload = b"%PDF-REDACTED-BYTES%"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/Detail/10001" in request.url.path:
            return httpx.Response(200, text=detail_html, request=request)
        if "/PurchaseRequisition/Download/9001" in request.url.path:
            return httpx.Response(
                200,
                content=payload,
                headers={"content-type": "application/pdf"},
                request=request,
            )
        return httpx.Response(404, request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        items = client.list_attachments_for("10001")
        assert items[0].id == "99001"
        content = client.download_attachment("9001", keep_bytes=True)
        assert content.content_length == len(payload)
        assert content.content_type == "application/pdf"
        assert content.data == payload
        assert len(content.sha256) == 64
        # default drops bytes
        meta_only = client.download_attachment("9001")
        assert meta_only.data is None
        assert meta_only.sha256 == content.sha256


def test_upload_and_delete_blocked() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(200, request=request)

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PolicyViolation):
            client.post(f"{BUSINESS}/WebTP/Attachment/Upload/10001")
        with pytest.raises(PolicyViolation):
            client.post(f"{BUSINESS}/WebTP/Attachment/Delete/9001")
    assert seen == []


def test_attachment_size_limit_stops_stream() -> None:
    payload = b"x" * 11

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/octet-stream"},
            request=request,
        )

    with IsstechClient(
        settings=Settings(base_url=BUSINESS, max_attachment_bytes=10),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="size limit"):
            client.download_attachment("9001", keep_bytes=True)


def test_attachment_redirect_to_login_is_rejected() -> None:
    login_html = '<input name="emp_DomainName"><input name="emp_Password">'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ipsapro.isstech.com":
            return httpx.Response(
                302,
                headers={"Location": "https://passport.isstech.com/"},
                request=request,
            )
        return httpx.Response(
            200,
            text=login_html,
            headers={"content-type": "text/html"},
            request=request,
        )

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PermissionError):
            client.download_attachment("9001")


def test_attachment_html_error_is_not_hashed_as_file() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html><body>upstream error</body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    with IsstechClient(
        settings=Settings(base_url=BUSINESS),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="returned HTML"):
            client.download_attachment("9001")
