"""Workflow-specific records normalize into a stable follow-up list."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from isstech_replay.models.purchase import (
    PurchaseListResult,
    PurchaseRequisitionSummary,
    PurchaseView,
)
from isstech_replay.models.work_items import (
    WorkItemCategory,
    WorkItemRelation,
    WorkItemScopeReason,
    WorkflowKind,
    WorkflowSnapshot,
)
from isstech_replay.parsers.purchase import parse_purchase_list
from isstech_replay.work_items import (
    is_purchase_approved,
    personal_work_item_scope,
    purchase_center_items,
    purchase_follow_up_items,
)


FIXTURES = Path(__file__).parent / "fixtures" / "purchase"


def _scope_snapshot(
    external_id: str,
    *,
    workflow: WorkflowKind = WorkflowKind.PURCHASE_REQUISITION,
    project_no: str = "",
    relations: tuple[WorkItemRelation, ...] = (),
) -> WorkflowSnapshot:
    return WorkflowSnapshot(
        adapter=workflow,
        external_id=external_id,
        observed_at="2026-07-16T00:00:00+00:00",
        project_no=project_no,
        relations=relations,
    )


def test_personal_scope_unions_project_records_and_submissions_once() -> None:
    snapshots = (
        _scope_snapshot(
            "project-seed",
            project_no=" PROJECT-A ",
            relations=(WorkItemRelation.PROJECT_MANAGER,),
        ),
        _scope_snapshot(
            "same-project-contract",
            workflow=WorkflowKind.PROCUREMENT_CONTRACT,
            project_no="PROJECT-A",
        ),
        _scope_snapshot(
            "submission-only",
            workflow=WorkflowKind.COST_CONFIRMATION,
            project_no="PROJECT-B",
            relations=(WorkItemRelation.SUBMITTER,),
        ),
        _scope_snapshot(
            "overlap",
            workflow=WorkflowKind.CHECK_ACCEPTANCE,
            project_no="PROJECT-A",
            relations=(WorkItemRelation.APPLICANT,),
        ),
    )

    scoped = personal_work_item_scope(snapshots)

    assert [record.snapshot.external_id for record in scoped] == [
        "project-seed",
        "same-project-contract",
        "submission-only",
        "overlap",
    ]
    assert scoped[1].scope_reasons == (WorkItemScopeReason.MY_PROJECT,)
    assert scoped[2].scope_reasons == (WorkItemScopeReason.SUBMITTED_BY_ME,)
    assert scoped[3].scope_reasons == (
        WorkItemScopeReason.MY_PROJECT,
        WorkItemScopeReason.SUBMITTED_BY_ME,
    )


def test_personal_scope_excludes_approval_and_procurement_roles_alone() -> None:
    scoped = personal_work_item_scope(
        (
            _scope_snapshot(
                "approver",
                relations=(WorkItemRelation.APPROVER,),
            ),
            _scope_snapshot(
                "procurement-manager",
                relations=(WorkItemRelation.PROCUREMENT_MANAGER,),
            ),
            _scope_snapshot("unrelated"),
        )
    )

    assert scoped == ()


def test_personal_scope_never_joins_empty_project_numbers() -> None:
    scoped = personal_work_item_scope(
        (
            _scope_snapshot(
                "empty-manager",
                project_no="   ",
                relations=(WorkItemRelation.PROJECT_MANAGER,),
            ),
            _scope_snapshot("empty-other", project_no=""),
        )
    )

    assert scoped == ()


def test_purchase_follow_up_items_only_include_active_named_node() -> None:
    html = (FIXTURES / "list_search.html").read_text(encoding="utf-8")
    result = parse_purchase_list(html, view=PurchaseView.SEARCH)
    items = purchase_follow_up_items(
        result,
        base_url="http://ipsapro.isstech.com",
        today=date(2026, 7, 15),
    )

    assert len(items) == 1
    item = items[0]
    assert item.key == "purchase_requisition:20001"
    assert item.reference_no == "XQ-REDACTED-101"
    assert item.current_approver == "USER_APPROVER"
    assert item.waiting_days == 14
    assert item.source_url.endswith("/PurchaseRequisition/Detail/20001")


def test_waiting_days_do_not_guess_invalid_or_future_dates() -> None:
    result = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=(
            PurchaseRequisitionSummary(
                id="1",
                requisition_no="REF-1",
                create_date="not-a-date",
                status="审批中",
                next_approver="USER_A",
            ),
            PurchaseRequisitionSummary(
                id="2",
                requisition_no="REF-2",
                create_date="2026-07-16",
                status="审批中",
                next_approver="USER_B",
            ),
        ),
    )
    items = purchase_follow_up_items(
        result,
        base_url="http://ipsapro.isstech.com",
        today=date(2026, 7, 15),
    )
    assert [item.waiting_days for item in items] == [None, None]


def test_purchase_center_separates_follow_up_approved_and_unproven_states() -> None:
    result = PurchaseListResult(
        view=PurchaseView.APPLICATION,
        items=(
            PurchaseRequisitionSummary(
                id="pending",
                status="审批中",
                next_approver="USER_APPROVER",
                create_date="2026-07-01",
            ),
            PurchaseRequisitionSummary(id="approved", status="审批通过"),
            PurchaseRequisitionSummary(id="completed", status="已完成"),
            PurchaseRequisitionSummary(id="saved", status="已保存"),
            PurchaseRequisitionSummary(id="rejected", status="已驳回"),
            PurchaseRequisitionSummary(id="unknown", status="未知终态"),
        ),
    )

    items = purchase_center_items(
        result,
        base_url="http://ipsapro.isstech.com",
        today=date(2026, 7, 15),
    )

    assert [(item.external_id, item.category) for item in items] == [
        ("pending", WorkItemCategory.FOLLOW_UP),
        ("approved", WorkItemCategory.APPROVED),
        ("completed", WorkItemCategory.APPROVED),
    ]
    assert items[0].waiting_days == 14
    assert all(item.waiting_days is None for item in items[1:])
    assert is_purchase_approved("审批通过") is True
    assert is_purchase_approved("已驳回") is False


def test_actionable_record_without_internal_id_fails_normalization() -> None:
    result = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=(
            PurchaseRequisitionSummary(
                id="",
                requisition_no="REF-1",
                create_date="2026-07-01",
                status="审批中",
                next_approver="USER_A",
            ),
        ),
    )
    with pytest.raises(ValueError, match="purchase requisition id is required"):
        purchase_follow_up_items(
            result,
            base_url="http://ipsapro.isstech.com",
            today=date(2026, 7, 15),
        )


def test_work_items_sort_known_waiting_days_descending_and_unknown_last() -> None:
    result = PurchaseListResult(
        view=PurchaseView.SEARCH,
        items=tuple(
            PurchaseRequisitionSummary(
                id=item_id,
                requisition_no=f"REF-{item_id}",
                create_date=create_date,
                status="审批中",
                next_approver="USER_APPROVER",
            )
            for item_id, create_date in (
                ("1", "2026-07-10"),
                ("2", "not-a-date"),
                ("3", "2026-07-01"),
            )
        ),
    )
    items = purchase_follow_up_items(
        result,
        base_url="http://ipsapro.isstech.com",
        today=date(2026, 7, 15),
    )
    assert [item.external_id for item in items] == ["3", "1", "2"]
    assert [item.waiting_days for item in items] == [14, 5, None]
