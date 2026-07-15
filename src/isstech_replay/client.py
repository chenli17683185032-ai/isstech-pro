"""Upstream business client.

Safety is enforced by EndpointPolicy via GuardedTransport. Callers cannot
self-declare READ_ONLY to bypass classification.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx

from .config import Settings
from .models.attachment import AttachmentContent, AttachmentMeta
from .models.purchase import (
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseView,
)
from .parsers.attachment import parse_attachment_list
from .parsers.login import is_login_page
from .parsers.portal import parse_portal_display_name
from .parsers.purchase import parse_purchase_detail, parse_purchase_list
from .policy import EndpointPolicy, PolicyViolation, UnsafeRequestError
from .transport import GuardedTransport
from .validation import require_path_segment

# Re-export for existing imports
__all__ = [
    "EvidenceGapError",
    "IsstechClient",
    "PaginationIncompleteError",
    "PolicyViolation",
    "UnsafeRequestError",
]


class EvidenceGapError(RuntimeError):
    """Raised when runtime evidence is insufficient to expose a live operation."""


class PaginationIncompleteError(RuntimeError):
    """Raised instead of returning a list that cannot be proven complete."""


class IsstechClient:
    """Direct HTTP client with policy-gated transport."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        policy: EndpointPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.policy = policy or EndpointPolicy()
        self._transport: httpx.BaseTransport = GuardedTransport(
            policy=self.policy,
            inner=transport,
        )

        self._client = httpx.Client(
            follow_redirects=True,
            timeout=self.settings.timeout_seconds,
            transport=self._transport,
            headers={"User-Agent": "isstech-replay/0.1"},
        )

    def close(self) -> None:
        self._client.close()
        if isinstance(self._transport, GuardedTransport):
            self._transport.close()

    def __enter__(self) -> "IsstechClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.settings.base_url.rstrip("/") + "/", path.lstrip("/"))

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if "safety" in kwargs:
            raise TypeError(
                "safety= is no longer accepted; EndpointPolicy classifies requests"
            )
        return self._client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def classify(self, method: str, url: str):
        return self.policy.decide(method, url)

    def get_purchase_requisition_page(self) -> httpx.Response:
        return self.get(self._url("/WebTP/PurchaseRequisition"))

    def get_portal_display_name(self) -> str:
        response = self.get(self._url("/Portal"))
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_portal_display_name(response.text)

    def _ensure_not_login(self, response: httpx.Response) -> None:
        if is_login_page(response.text):
            raise PermissionError(
                "Upstream returned passport login page; session is not authenticated"
            )

    def list_purchase_requisitions(
        self,
        query: PurchaseListQuery | None = None,
    ) -> PurchaseListResult:
        query = query or PurchaseListQuery()
        path = query.path()
        url = self._url(path)
        if query.view is PurchaseView.APPLICATION and query.has_filters:
            # Application Index posts filters to the module root.
            response = self.post(
                self._url("/WebTP/PurchaseRequisition"),
                data=query.filter_form(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        elif query.view is not PurchaseView.APPLICATION and (
            query.has_filters or query.has_navigation_state
        ):
            # Captured views use ajaxSubmit POST for filters, sort, and pagination.
            response = self.post(
                url,
                data=query.filter_form(navigation=query.has_navigation_state),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        else:
            response = self.get(url)
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_purchase_list(
            response.text,
            view=query.view,
            source_url=str(response.url),
            page=query.page,
            page_size=query.page_size,
        )

    def list_all_purchase_requisitions(
        self,
        query: PurchaseListQuery | None = None,
        *,
        max_pages: int = 100,
    ) -> PurchaseListResult:
        """Read every page with duplicate and page-count guards."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        query = query or PurchaseListQuery()
        items = []
        seen: set[tuple[str, ...]] = set()
        first_result: PurchaseListResult | None = None
        expected_total: int | None = None

        for page in range(1, max_pages + 1):
            result = self.list_purchase_requisitions(replace(query, page=page))
            if first_result is None:
                first_result = result
            if result.total_count is not None:
                if expected_total is None:
                    expected_total = result.total_count
                elif result.total_count != expected_total:
                    raise PaginationIncompleteError(
                        "upstream total changed during pagination: "
                        f"{expected_total} -> {result.total_count}"
                    )

            added = 0
            for item in result.items:
                if item.id:
                    key = ("id", item.id)
                elif item.requisition_no:
                    key = ("reference", item.requisition_no, item.project_no)
                else:
                    raise PaginationIncompleteError(
                        f"page {page} contains a record without a stable identity"
                    )
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
                added += 1

            if expected_total is not None and len(items) > expected_total:
                raise PaginationIncompleteError(
                    f"collected {len(items)} records but upstream reported {expected_total}"
                )
            if expected_total is not None and len(items) == expected_total:
                break
            if not result.items:
                if expected_total is None:
                    break
                raise PaginationIncompleteError(
                    f"empty page {page} before reported total {expected_total}; "
                    f"collected {len(items)}"
                )
            if added == 0:
                raise PaginationIncompleteError(
                    f"page {page} repeated without progress; collected {len(items)}"
                )
            if len(result.items) < query.page_size:
                if expected_total is not None:
                    raise PaginationIncompleteError(
                        f"short page {page} before reported total {expected_total}; "
                        f"collected {len(items)}"
                    )
                break
        else:
            raise PaginationIncompleteError(
                f"pagination reached max_pages={max_pages} before completion; "
                f"collected {len(items)}"
            )

        if first_result is None:
            return PurchaseListResult(
                view=query.view,
                items=(),
                page=1,
                page_size=query.page_size,
            )
        return PurchaseListResult(
            view=query.view,
            items=tuple(items),
            total_text=first_result.total_text,
            total_count=expected_total,
            page=1,
            page_size=query.page_size,
            source_url=first_result.source_url,
        )

    def list_view(self, view: PurchaseView, **kwargs: Any) -> PurchaseListResult:
        return self.list_purchase_requisitions(PurchaseListQuery(view=view, **kwargs))

    def get_purchase_requisition(self, requisition_id: str) -> PurchaseRequisitionDetail:
        """Load the captured read-only Detail page."""
        requisition_id = require_path_segment(requisition_id, "requisition_id")
        response = self.get(self._url(f"/WebTP/PurchaseRequisition/Detail/{requisition_id}"))
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_purchase_detail(response.text, requisition_id=requisition_id)

    def list_attachments(self, html: str, *, doc_id: str = "") -> tuple[AttachmentMeta, ...]:
        """Parse attachment rows from already-fetched HTML (edit/detail page)."""
        return parse_attachment_list(html, doc_id=doc_id)

    def list_attachments_for(self, requisition_id: str) -> tuple[AttachmentMeta, ...]:
        requisition_id = require_path_segment(requisition_id, "requisition_id")
        response = self.get(self._url(f"/WebTP/PurchaseRequisition/Detail/{requisition_id}"))
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_attachment_list(response.text, doc_id=requisition_id)

    def download_attachment(self, attachment_id: str, *, keep_bytes: bool = False) -> AttachmentContent:
        attachment_id = require_path_segment(attachment_id, "attachment_id")
        url = self._url(f"/WebTP/PurchaseRequisition/Download/{attachment_id}")
        digest = hashlib.sha256()
        data = bytearray() if keep_bytes else None
        total = 0
        content_type: str | None = None
        with self._client.stream("GET", url) as response:
            final_host = (urlparse(str(response.url)).hostname or "").lower()
            if final_host != "ipsapro.isstech.com":
                raise PermissionError("Attachment request left the authenticated business host")
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    if int(declared_length) > self.settings.max_attachment_bytes:
                        raise ValueError("Attachment exceeds configured size limit")
                except ValueError as exc:
                    if "exceeds" in str(exc):
                        raise
            html_probe = bytearray() if content_type and "text/html" in content_type.lower() else None
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self.settings.max_attachment_bytes:
                    raise ValueError("Attachment exceeds configured size limit")
                digest.update(chunk)
                if data is not None:
                    data.extend(chunk)
                if html_probe is not None:
                    html_probe.extend(chunk)
            if html_probe is not None and is_login_page(
                html_probe.decode(response.encoding or "utf-8", errors="replace")
            ):
                raise PermissionError("Upstream returned passport login page")
            if html_probe is not None:
                raise ValueError("Attachment download returned HTML instead of a file")
        return AttachmentContent(
            id=attachment_id,
            content_type=content_type,
            content_length=total,
            sha256=digest.hexdigest(),
            data=bytes(data) if data is not None else None,
        )
