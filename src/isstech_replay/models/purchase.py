"""Purchase requisition domain models.

Field values may contain business data when produced at runtime. Tests and
committed fixtures must use redacted placeholders only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PurchaseView(StrEnum):
    APPLICATION = "application"
    APPROVAL = "approval"
    ADJUSTMENT = "adjustment"
    REVOCATION = "revocation"
    SEARCH = "search"

    @property
    def path_segment(self) -> str:
        return {
            PurchaseView.APPLICATION: "Index",
            PurchaseView.APPROVAL: "ApprovalIndex",
            PurchaseView.ADJUSTMENT: "AdjustIndex",
            PurchaseView.REVOCATION: "RevocationIndex",
            PurchaseView.SEARCH: "SearchIndex",
        }[self]


@dataclass(frozen=True, slots=True)
class PurchaseListQuery:
    view: PurchaseView = PurchaseView.APPLICATION
    project_no: str = ""
    requisition_no: str = ""
    page: int = 1
    page_size: int = 10
    sort_field: str | None = None
    sort_desc: bool = False

    def path(self) -> str:
        segment = self.view.path_segment
        base = f"/WebTP/PurchaseRequisition/{segment}"
        # Observed Index path tails: /0/1/{sortBool}[/{page}/{size}[/lastOrderField/{field}]]
        sort_flag = "True" if self.sort_field else "False"
        if self.sort_field:
            return (
                f"{base}/0/1/{sort_flag}/{self.page}/{self.page_size}"
                f"/lastOrderField/{self.sort_field}"
            )
        if self.page != 1 or self.page_size != 10:
            return f"{base}/0/1/{sort_flag}/{self.page}/{self.page_size}"
        return base

    def filter_form(self) -> dict[str, str]:
        return {
            "PR_PrjNo": self.project_no,
            "PR_RequisitionNo": self.requisition_no,
            "btnSearch": "查询",
        }


@dataclass(frozen=True, slots=True)
class PurchaseRequisitionSummary:
    id: str
    requisition_no: str = ""
    project_no: str = ""
    project_name: str = ""
    creator_name: str = ""
    create_date: str = ""
    status: str = ""
    raw_cells: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PurchaseRequisitionDetail:
    id: str
    fields: dict[str, str] = field(default_factory=dict)
    html_title: str = ""


@dataclass(frozen=True, slots=True)
class PurchaseListResult:
    view: PurchaseView
    items: tuple[PurchaseRequisitionSummary, ...]
    total_text: str | None = None
    total_count: int | None = None
    page: int = 1
    page_size: int = 10
    source_url: str = ""
