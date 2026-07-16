"""Parse fixed-schema procurement SearchIndex grids."""

from __future__ import annotations

from isstech_replay.models.procurement import (
    ProcurementDocumentSummary,
    ProcurementListResult,
    ProcurementStreamSpec,
)
from isstech_replay.models.purchase import PurchaseRequisitionDetail
from isstech_replay.models.work_items import WorkflowKind

from .purchase import (
    _TOTAL_RE,
    _DetailFormParser,
    _DetailTableParser,
    _TableParser,
    _cell_value,
    _clean_label,
    _parse_approval_steps,
)


def parse_procurement_list(
    html: str,
    *,
    spec: ProcurementStreamSpec,
    source_url: str = "",
    page: int = 1,
    page_size: int = 50,
) -> ProcurementListResult:
    parser = _TableParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError(f"{spec.workflow.value} list grid not found")
    if tuple(parser.headers) != spec.headers:
        raise ValueError(f"{spec.workflow.value} list schema changed")

    items = []
    field_names = spec.headers[1:]
    for row in parser.rows:
        ajax_ids = [cell.get("ajax_data", "") for cell in row if cell.get("ajax_data")]
        if len(ajax_ids) != 1:
            raise ValueError(f"{spec.workflow.value} row has no unique stable identity")
        values = [_cell_value(cell) for cell in row]
        data_values = values[1:]
        if len(data_values) != len(field_names):
            raise ValueError(f"{spec.workflow.value} row does not match list schema")
        fields = tuple(zip(field_names, data_values, strict=True))
        field_map = dict(fields)
        items.append(
            ProcurementDocumentSummary(
                workflow=spec.workflow,
                id=ajax_ids[0],
                reference_no=field_map.get(spec.reference_field, ""),
                project_no=field_map.get(spec.project_no_field, ""),
                title=field_map.get(spec.title_field, ""),
                applicant=(
                    field_map.get(spec.applicant_field, "") if spec.applicant_field else ""
                ),
                submitted_at=(
                    field_map.get(spec.submitted_at_field, "")
                    if spec.submitted_at_field
                    else ""
                ),
                status=field_map.get(spec.status_field, ""),
                next_approver=field_map.get(spec.next_approver_field, ""),
                fields=fields,
            )
        )

    total_match = _TOTAL_RE.search(html)
    total_count = int(total_match.group(1)) if total_match else None
    if total_count is None and "当前没有任何记录" in html:
        total_count = 0
    return ProcurementListResult(
        workflow=spec.workflow,
        items=tuple(items),
        total_count=total_count,
        page=page,
        page_size=page_size,
        source_url=source_url,
    )


def parse_procurement_detail(
    html: str,
    *,
    workflow: WorkflowKind,
    external_id: str,
) -> PurchaseRequisitionDetail:
    """Parse the shared read-only field tables and approval trail."""
    form_parser = _DetailFormParser()
    form_parser.feed(html)
    table_parser = _DetailTableParser()
    table_parser.feed(html)

    fields: dict[str, str] = {}
    for table in table_parser.tables:
        for row in table:
            for index, (tag, label) in enumerate(row[:-1]):
                if tag != "th" or row[index + 1][0] != "td":
                    continue
                field_name = _clean_label(label)
                if field_name and field_name not in fields:
                    fields[field_name] = row[index + 1][1]
    fields.update(form_parser.fields)
    approval_steps = _parse_approval_steps(table_parser.tables)
    if not fields and not approval_steps:
        raise ValueError(f"{workflow.value} detail fields not found")
    return PurchaseRequisitionDetail(
        id=external_id,
        fields=fields,
        html_title=form_parser.title or workflow.label,
        approval_steps=approval_steps,
    )
