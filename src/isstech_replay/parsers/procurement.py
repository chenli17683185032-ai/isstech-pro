"""Parse fixed-schema procurement SearchIndex grids."""

from __future__ import annotations

from isstech_replay.models.procurement import (
    ProcurementDocumentSummary,
    ProcurementListResult,
    ProcurementStreamSpec,
)

from .purchase import _TOTAL_RE, _TableParser, _cell_value


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
