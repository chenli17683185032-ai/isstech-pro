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
from .models.bizcase import BizCaseListResult, BizCasePage, BizCaseRecord
from .models.daily_expense import DailyExpenseListResult
from .models.fee_application import (
    FEE_APPLICATION_PAGE_SIZE,
    TRAVEL_REIMBURSEMENT_SPEC,
    TRAVEL_SUBSIDY_SPEC,
    FeeApplicationListResult,
    FeeApplicationPage,
    FeeApplicationRecord,
    FeeApplicationSpec,
)
from .models.payment import (
    PaymentListResult,
    PaymentRecord,
    payment_query_form,
)
from .models.travel_application import (
    TRAVEL_APPLICATION_PAGE_SIZE,
    TravelApplicationListResult,
    TravelApplicationPage,
    TravelApplicationRecord,
)
from .models.procurement import (
    PROCUREMENT_STREAM_BY_WORKFLOW,
    ProcurementListResult,
)
from .models.purchase import (
    PurchaseListQuery,
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseView,
)
from .parsers.attachment import parse_attachment_list
from .parsers.bizcase import parse_bizcase_application_page, parse_bizcase_page
from .parsers.daily_expense import parse_daily_expense_page
from .parsers.fee_application import parse_fee_application_page
from .parsers.login import is_login_page
from .parsers.payment import parse_payment_query_list
from .parsers.portal import display_name_matches, parse_portal_display_name
from .parsers.procurement import parse_procurement_detail, parse_procurement_list
from .parsers.purchase import parse_purchase_detail, parse_purchase_list
from .parsers.travel_application import parse_travel_application_page
from .policy import (
    BIZCASE_APPLICATION_URL,
    BIZCASE_QUERY_URL,
    DAILY_EXPENSE_URL,
    PAYMENT_QUERY_PATH,
    TRAVEL_APPLICATION_URL,
    EndpointPolicy,
    PolicyViolation,
    UnsafeRequestError,
)
from .transport import GuardedTransport
from .validation import require_path_segment
from .models.work_items import WorkflowKind

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


_PAYMENT_QUERY_TIMEOUT_SECONDS = 90.0


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

    def list_procurement_documents(
        self,
        workflow: WorkflowKind,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> ProcurementListResult:
        """Read one observed SearchIndex page with no business filters."""
        try:
            spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
        except KeyError as exc:
            raise ValueError(f"unsupported procurement workflow: {workflow}") from exc
        path = spec.page_path(page, page_size)
        data: dict[str, str] = {}
        if workflow is WorkflowKind.PURCHASE_REQUISITION:
            data = PurchaseListQuery(
                view=PurchaseView.SEARCH,
                page=page,
                page_size=page_size,
            ).filter_form(navigation=True)
        response = self.post(
            self._url(path),
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_procurement_list(
            response.text,
            spec=spec,
            source_url=str(response.url),
            page=page,
            page_size=page_size,
        )

    def list_all_procurement_documents(
        self,
        workflow: WorkflowKind,
        *,
        max_pages: int = 100,
        page_size: int = 50,
    ) -> ProcurementListResult:
        """Read one complete procurement stream or fail without returning a partial list."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if workflow not in PROCUREMENT_STREAM_BY_WORKFLOW:
            raise ValueError(f"unsupported procurement workflow: {workflow}")

        items = []
        seen: set[str] = set()
        first_result: ProcurementListResult | None = None
        expected_total: int | None = None
        for page in range(1, max_pages + 1):
            result = self.list_procurement_documents(
                workflow,
                page=page,
                page_size=page_size,
            )
            if first_result is None:
                first_result = result
            if result.total_count is None:
                raise PaginationIncompleteError(
                    f"{workflow.value} page {page} did not declare a total"
                )
            if expected_total is None:
                expected_total = result.total_count
            elif result.total_count != expected_total:
                raise PaginationIncompleteError(
                    f"{workflow.value} total changed during pagination: "
                    f"{expected_total} -> {result.total_count}"
                )

            added = 0
            for item in result.items:
                if not item.id:
                    raise PaginationIncompleteError(
                        f"{workflow.value} page {page} contains a record without an identity"
                    )
                if item.id in seen:
                    continue
                seen.add(item.id)
                items.append(item)
                added += 1

            if len(items) > expected_total:
                raise PaginationIncompleteError(
                    f"{workflow.value} collected {len(items)} records but reported "
                    f"{expected_total}"
                )
            if len(items) == expected_total:
                break
            if not result.items:
                raise PaginationIncompleteError(
                    f"{workflow.value} page {page} was empty at "
                    f"{len(items)}/{expected_total}"
                )
            if added == 0:
                raise PaginationIncompleteError(
                    f"{workflow.value} page {page} repeated without progress"
                )
            if len(result.items) < page_size:
                raise PaginationIncompleteError(
                    f"{workflow.value} page {page} was short at "
                    f"{len(items)}/{expected_total}"
                )
        else:
            raise PaginationIncompleteError(
                f"{workflow.value} reached max_pages={max_pages} at "
                f"{len(items)}/{expected_total}"
            )

        assert first_result is not None
        return ProcurementListResult(
            workflow=workflow,
            items=tuple(items),
            total_count=expected_total,
            page=1,
            page_size=page_size,
            source_url=first_result.source_url,
        )

    def _read_payment_query_page(
        self,
        page: int,
        *,
        applicant: str = "",
        project_no: str = "",
    ) -> PaymentListResult:
        path = (
            PAYMENT_QUERY_PATH
            if page == 1
            else f"{PAYMENT_QUERY_PATH}/0/1/False/{page}"
        )
        response = self.post(
            self._url(path),
            data=payment_query_form(
                applicant=applicant,
                project_no=project_no,
                pager=page > 1,
            ),
            headers={"Accept-Language": "zh-CN"},
            timeout=max(self.settings.timeout_seconds, _PAYMENT_QUERY_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_payment_query_list(response.text, source_url=str(response.url))

    def _list_payment_query(
        self,
        *,
        max_pages: int,
        applicant: str = "",
        project_no: str = "",
    ) -> PaymentListResult:
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        current = self._read_payment_query_page(
            1,
            applicant=applicant,
            project_no=project_no,
        )
        if current.current_page != 1:
            raise PaginationIncompleteError("Payment initial query response is not page 1")
        if current.page_count > max_pages:
            raise PaginationIncompleteError(
                f"Payment page count {current.page_count} exceeds max_pages={max_pages}"
            )
        expected_total = current.total_count
        expected_pages = current.page_count
        expected_page_size = len(current.items)
        if expected_total and expected_page_size == 0:
            raise PaginationIncompleteError("Payment first page is empty before later records")

        items: list[PaymentRecord] = []
        seen: set[str] = set()
        for page_number in range(1, expected_pages + 1):
            if page_number > 1:
                current = self._read_payment_query_page(
                    page_number,
                    applicant=applicant,
                    project_no=project_no,
                )
            if current.current_page != page_number:
                raise PaginationIncompleteError(
                    f"Payment requested page {page_number} but received {current.current_page}"
                )
            if current.page_count != expected_pages or current.total_count != expected_total:
                raise PaginationIncompleteError("Payment totals changed during pagination")
            if page_number < expected_pages and len(current.items) != expected_page_size:
                raise PaginationIncompleteError(
                    f"Payment page {page_number} was short before the last page"
                )
            if page_number == expected_pages and len(current.items) > expected_page_size:
                raise PaginationIncompleteError("Payment last page exceeded the first page size")
            for item in current.items:
                if item.id in seen:
                    raise PaginationIncompleteError(
                        f"Payment repeated stable identity on page {page_number}"
                    )
                seen.add(item.id)
                items.append(item)

        if len(items) != expected_total:
            raise PaginationIncompleteError(
                f"Payment collected {len(items)}/{expected_total} declared records"
            )
        return PaymentListResult(
            items=tuple(items),
            total_count=expected_total,
            page_count=expected_pages,
            current_page=1,
            source_url=self._url(PAYMENT_QUERY_PATH),
        )

    def list_payment_records(self, *, max_pages: int = 100) -> PaymentListResult:
        """Low-frequency source audit across every Payment query page."""
        return self._list_payment_query(max_pages=max_pages)

    def list_personal_payment_records(
        self,
        *,
        display_name: str,
        project_numbers: tuple[str, ...],
        max_pages: int = 20,
    ) -> PaymentListResult:
        """Union complete applicant and exact personal-project query streams."""
        identity = display_name.strip()
        if not identity:
            raise ValueError("Payment personal scope requires a display name")
        projects = tuple(sorted({value.strip() for value in project_numbers if value.strip()}))
        streams = [
            (
                "applicant",
                identity,
                self._list_payment_query(max_pages=max_pages, applicant=identity),
            )
        ]
        streams.extend(
            (
                "project",
                project_no,
                self._list_payment_query(max_pages=max_pages, project_no=project_no),
            )
            for project_no in projects
        )

        records: dict[str, PaymentRecord] = {}
        page_count = 0
        for scope, value, result in streams:
            page_count += result.page_count
            for record in result.items:
                matches = (
                    display_name_matches(record.applicant, value)
                    if scope == "applicant"
                    else record.project_no.strip() == value
                )
                if not matches:
                    continue
                previous = records.get(record.id)
                if previous is not None and previous != record:
                    raise PaginationIncompleteError(
                        "Payment personal query returned conflicting duplicate records"
                    )
                records[record.id] = record
        items = tuple(records.values())
        return PaymentListResult(
            items=items,
            total_count=len(items),
            page_count=page_count,
            current_page=1,
            source_url=self._url(PAYMENT_QUERY_PATH),
        )

    def _read_bizcase_page(
        self,
        *,
        previous: BizCasePage | None = None,
        page: int = 1,
    ) -> BizCasePage:
        url = self._url(BIZCASE_QUERY_URL)
        if previous is None:
            response = self.get(url)
        else:
            response = self.post(
                url,
                data=previous.pagination_form(page),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_bizcase_page(response.text, source_url=str(response.url))

    def list_all_bizcases(self, *, max_pages: int = 20) -> BizCaseListResult:
        """Sequentially replay the proven WebForms pager with completeness guards."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        current = self._read_bizcase_page()
        if current.current_page != 1:
            raise PaginationIncompleteError("BizCase initial response is not page 1")
        if current.page_count > max_pages:
            raise PaginationIncompleteError(
                f"BizCase page count {current.page_count} exceeds max_pages={max_pages}"
            )

        items: list[BizCaseRecord] = []
        seen: set[str] = set()
        expected_page_count = current.page_count
        expected_page_size = len(current.items)
        if current.page_count > 1 and expected_page_size == 0:
            raise PaginationIncompleteError("BizCase first page is empty before later pages")

        for page_number in range(1, expected_page_count + 1):
            if page_number > 1:
                current = self._read_bizcase_page(previous=current, page=page_number)
            if current.current_page != page_number:
                raise PaginationIncompleteError(
                    f"BizCase requested page {page_number} but received {current.current_page}"
                )
            if current.page_count != expected_page_count:
                raise PaginationIncompleteError(
                    "BizCase page count changed during pagination: "
                    f"{expected_page_count} -> {current.page_count}"
                )
            if page_number < expected_page_count and len(current.items) != expected_page_size:
                raise PaginationIncompleteError(
                    f"BizCase page {page_number} was short before the last page"
                )
            if page_number == expected_page_count and expected_page_count > 1:
                if not current.items or len(current.items) > expected_page_size:
                    raise PaginationIncompleteError("BizCase last-page size is invalid")
            for item in current.items:
                expected_ordinal = len(items) + 1
                if item.ordinal != expected_ordinal:
                    raise PaginationIncompleteError(
                        f"BizCase ordinal changed at {expected_ordinal}: {item.ordinal}"
                    )
                if item.id in seen:
                    raise PaginationIncompleteError(
                        f"BizCase page {page_number} repeated identity {item.id}"
                    )
                seen.add(item.id)
                items.append(item)

        return BizCaseListResult(
            items=tuple(items),
            total_count=len(items),
            page_count=expected_page_count,
            source_url=current.source_url,
        )

    def list_bizcase_applications(self) -> BizCaseListResult:
        """Read the identity-bound, single-page BizCase application view."""
        url = self._url(BIZCASE_APPLICATION_URL)
        response = self.get(url)
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_bizcase_application_page(
            response.text,
            source_url=str(response.url),
        )

    def list_bizcases_with_application_visibility(
        self,
        *,
        max_pages: int = 20,
    ) -> BizCaseListResult:
        """Join application-view visibility to the complete query checkpoint."""
        applications = self.list_bizcase_applications()
        source = self.list_all_bizcases(max_pages=max_pages)
        source_by_id = {record.id: record for record in source.items}
        shared_fields = (
            "bizcase_no",
            "client_name",
            "profit_center_group",
            "profit_center",
            "project_no",
            "project_name",
            "revenue_recognition_type",
        )
        for application in applications.items:
            matched = source_by_id.get(application.id)
            if matched is None:
                raise PaginationIncompleteError(
                    "BizCase application identity is absent from the query source"
                )
            if any(
                getattr(application, field) != getattr(matched, field)
                for field in shared_fields
            ):
                raise PaginationIncompleteError(
                    "BizCase application identity conflicts with the query source"
                )
        return replace(
            source,
            application_visible_ids=tuple(
                record.id for record in applications.items
            ),
        )

    def _read_travel_application_page(
        self,
        *,
        previous: TravelApplicationPage | None = None,
        page: int = 1,
    ) -> TravelApplicationPage:
        url = self._url(TRAVEL_APPLICATION_URL)
        if previous is None:
            response = self.get(url)
        else:
            response = self.post(
                url,
                data=previous.pagination_form(page),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_travel_application_page(
            response.text,
            source_url=str(response.url),
        )

    def list_personal_travel_applications(
        self,
        *,
        display_name: str,
        max_pages: int = 20,
    ) -> TravelApplicationListResult:
        """Replay the identity-bound travel list and fail on incomplete paging."""
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("travel application display_name is required")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        current = self._read_travel_application_page()
        if current.current_page != 1:
            raise PaginationIncompleteError(
                "Travel application initial response is not page 1"
            )
        if current.page_count > max_pages:
            raise PaginationIncompleteError(
                "Travel application page count "
                f"{current.page_count} exceeds max_pages={max_pages}"
            )

        items: list[TravelApplicationRecord] = []
        seen: set[str] = set()
        expected_page_count = current.page_count
        for page_number in range(1, expected_page_count + 1):
            if page_number > 1:
                current = self._read_travel_application_page(
                    previous=current,
                    page=page_number,
                )
            if current.current_page != page_number:
                raise PaginationIncompleteError(
                    "Travel application requested page "
                    f"{page_number} but received {current.current_page}"
                )
            if current.page_count != expected_page_count:
                raise PaginationIncompleteError(
                    "Travel application page count changed during pagination: "
                    f"{expected_page_count} -> {current.page_count}"
                )
            if page_number < expected_page_count and len(current.items) != (
                TRAVEL_APPLICATION_PAGE_SIZE
            ):
                raise PaginationIncompleteError(
                    f"Travel application page {page_number} was short before the last page"
                )
            if page_number == expected_page_count and expected_page_count > 1:
                if not current.items or len(current.items) > TRAVEL_APPLICATION_PAGE_SIZE:
                    raise PaginationIncompleteError(
                        "Travel application last-page size is invalid"
                    )
            for item in current.items:
                if not display_name_matches(item.applicant, display_name):
                    raise PaginationIncompleteError(
                        "Travel application applicant does not match the current identity"
                    )
                if item.id in seen:
                    raise PaginationIncompleteError(
                        f"Travel application page {page_number} repeated identity {item.id}"
                    )
                seen.add(item.id)
                items.append(item)

        return TravelApplicationListResult(
            items=tuple(items),
            total_count=len(items),
            page_count=expected_page_count,
            source_url=current.source_url,
        )

    def list_personal_daily_expenses(
        self,
        *,
        display_name: str,
        max_pages: int = 20,
    ) -> DailyExpenseListResult:
        """Read the proven single-page, identity-bound daily expense list."""
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("daily expense display_name is required")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        response = self.get(self._url(DAILY_EXPENSE_URL))
        response.raise_for_status()
        self._ensure_not_login(response)
        page = parse_daily_expense_page(
            response.text,
            source_url=str(response.url),
        )
        if page.current_page != 1 or page.page_count != 1:
            raise PaginationIncompleteError(
                "Daily expense response exceeds the proven single page"
            )
        for item in page.items:
            if not display_name_matches(item.applicant, display_name):
                raise PaginationIncompleteError(
                    "Daily expense applicant does not match the current identity"
                )
        return DailyExpenseListResult(
            items=page.items,
            total_count=len(page.items),
            page_count=page.page_count,
            source_url=page.source_url,
        )

    def _read_fee_application_page(
        self,
        *,
        spec: FeeApplicationSpec,
        previous: FeeApplicationPage | None = None,
        page: int = 1,
    ) -> FeeApplicationPage:
        url = self._url(spec.list_url)
        if previous is None:
            response = self.get(url)
        else:
            response = self.post(
                url,
                data=previous.pagination_form(page),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_fee_application_page(
            response.text,
            spec=spec,
            source_url=str(response.url),
        )

    def _list_personal_fee_applications(
        self,
        *,
        spec: FeeApplicationSpec,
        display_name: str,
        max_pages: int,
    ) -> FeeApplicationListResult:
        display_name = display_name.strip()
        if not display_name:
            raise ValueError(f"{spec.key} display_name is required")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        current = self._read_fee_application_page(spec=spec)
        if current.current_page != 1:
            raise PaginationIncompleteError(
                f"{spec.label} initial response is not page 1"
            )
        if current.page_count > max_pages:
            raise PaginationIncompleteError(
                f"{spec.label} page count {current.page_count} "
                f"exceeds max_pages={max_pages}"
            )
        if not spec.pagination_enabled and current.page_count != 1:
            raise PaginationIncompleteError(f"{spec.label} exceeds the proven single page")

        items: list[FeeApplicationRecord] = []
        seen: set[str] = set()
        expected_page_count = current.page_count
        for page_number in range(1, expected_page_count + 1):
            if page_number > 1:
                current = self._read_fee_application_page(
                    spec=spec,
                    previous=current,
                    page=page_number,
                )
            if current.current_page != page_number:
                raise PaginationIncompleteError(
                    f"{spec.label} requested page {page_number} "
                    f"but received {current.current_page}"
                )
            if current.page_count != expected_page_count:
                raise PaginationIncompleteError(
                    f"{spec.label} page count changed during pagination: "
                    f"{expected_page_count} -> {current.page_count}"
                )
            if page_number < expected_page_count and len(current.items) != (
                FEE_APPLICATION_PAGE_SIZE
            ):
                raise PaginationIncompleteError(
                    f"{spec.label} page {page_number} was short before the last page"
                )
            if page_number == expected_page_count and expected_page_count > 1:
                if not current.items or len(current.items) > FEE_APPLICATION_PAGE_SIZE:
                    raise PaginationIncompleteError(
                        f"{spec.label} last-page size is invalid"
                    )
            for item in current.items:
                if not display_name_matches(item.applicant, display_name):
                    raise PaginationIncompleteError(
                        f"{spec.label} applicant does not match the current identity"
                    )
                if item.id in seen:
                    raise PaginationIncompleteError(
                        f"{spec.label} page {page_number} repeated an identity"
                    )
                seen.add(item.id)
                items.append(item)

        return FeeApplicationListResult(
            items=tuple(items),
            total_count=len(items),
            page_count=expected_page_count,
            source_url=current.source_url,
        )

    def list_personal_travel_reimbursements(
        self,
        *,
        display_name: str,
        max_pages: int = 20,
    ) -> FeeApplicationListResult:
        return self._list_personal_fee_applications(
            spec=TRAVEL_REIMBURSEMENT_SPEC,
            display_name=display_name,
            max_pages=max_pages,
        )

    def list_personal_travel_subsidies(
        self,
        *,
        display_name: str,
        max_pages: int = 20,
    ) -> FeeApplicationListResult:
        return self._list_personal_fee_applications(
            spec=TRAVEL_SUBSIDY_SPEC,
            display_name=display_name,
            max_pages=max_pages,
        )

    def get_purchase_requisition(self, requisition_id: str) -> PurchaseRequisitionDetail:
        """Load the captured read-only Detail page."""
        requisition_id = require_path_segment(requisition_id, "requisition_id")
        response = self.get(self._url(f"/WebTP/PurchaseRequisition/Detail/{requisition_id}"))
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_purchase_detail(response.text, requisition_id=requisition_id)

    def get_procurement_document_detail(
        self,
        workflow: WorkflowKind,
        external_id: str,
    ) -> PurchaseRequisitionDetail:
        """Load one runtime-proven read-only procurement detail page."""
        try:
            spec = PROCUREMENT_STREAM_BY_WORKFLOW[workflow]
        except KeyError as exc:
            raise ValueError(f"unsupported procurement workflow: {workflow}") from exc
        external_id = require_path_segment(external_id, f"{workflow.value} id")
        if workflow is WorkflowKind.PURCHASE_REQUISITION:
            return self.get_purchase_requisition(external_id)
        response = self.get(self._url(spec.detail_path(external_id)))
        response.raise_for_status()
        self._ensure_not_login(response)
        return parse_procurement_detail(
            response.text,
            workflow=workflow,
            external_id=external_id,
        )

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
