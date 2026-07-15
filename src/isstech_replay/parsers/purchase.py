"""Parse purchase requisition list/detail HTML (stdlib only)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from isstech_replay.models.purchase import (
    PurchaseApprovalStep,
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseRequisitionSummary,
    PurchaseView,
)


_TOTAL_RE = re.compile(r"(?:总共|共)\s*(\d+)\s*条(?:记录)?")
_WS_RE = re.compile(r"\s+")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_grid = False
        self.found_grid = False
        self.grid_depth = 0
        self.capture = False
        self.rows: list[list[dict[str, str]]] = []
        self._row: list[dict[str, str]] | None = None
        self._cell: dict[str, str] | None = None
        self._parts: list[str] = []
        self.headers: list[str] = []
        self._in_th = False
        self._th_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        classes = ad.get("class", "")
        if tag == "table" and "data-grid" in classes:
            self.in_grid = True
            self.found_grid = True
            self.grid_depth = 1
            return
        if self.in_grid and tag == "table":
            self.grid_depth += 1
        if not self.in_grid:
            return
        if tag == "tr":
            self._row = []
            return
        if tag == "th":
            self._in_th = True
            self._th_parts = []
            return
        if tag == "td" and self._row is not None:
            self._cell = {
                "text": "",
                "title": "",
                "ajax_data": "",
                "links": "",
            }
            self._parts = []
            return
        if self._cell is not None:
            if tag == "a":
                if ad.get("ajax-data"):
                    self._cell["ajax_data"] = ad["ajax-data"]
                cls = ad.get("class", "")
                if cls:
                    self._cell["links"] += cls + " "
            if tag == "span" and ad.get("title"):
                if not self._cell["title"]:
                    self._cell["title"] = ad["title"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_grid:
            self.grid_depth -= 1
            if self.grid_depth <= 0:
                self.in_grid = False
            return
        if not self.in_grid and not self._in_th:
            return
        if tag == "th" and self._in_th:
            text = _WS_RE.sub(" ", "".join(self._th_parts)).strip()
            self.headers.append(text)
            self._in_th = False
            self._th_parts = []
            return
        if tag == "td" and self._cell is not None and self._row is not None:
            text = _WS_RE.sub(" ", "".join(self._parts)).strip()
            self._cell["text"] = text
            if not self._cell["title"]:
                self._cell["title"] = text
            self._row.append(self._cell)
            self._cell = None
            self._parts = []
            return
        if tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._in_th:
            self._th_parts.append(data)
        elif self._cell is not None:
            self._parts.append(data)


def _cell_value(cell: dict[str, str]) -> str:
    return (cell.get("title") or cell.get("text") or "").strip()


def parse_purchase_list(
    html: str,
    *,
    view: PurchaseView = PurchaseView.APPLICATION,
    source_url: str = "",
    page: int = 1,
    page_size: int = 10,
) -> PurchaseListResult:
    parser = _TableParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError("Purchase list grid not found")

    items: list[PurchaseRequisitionSummary] = []
    for row in parser.rows:
        # Header-like rows have no ajax-data and look like column labels
        ajax_ids = [c.get("ajax_data", "") for c in row if c.get("ajax_data")]
        if not ajax_ids and any("申请单" in _cell_value(c) for c in row):
            continue
        rid = ajax_ids[0] if ajax_ids else ""
        # Observed application Index column order after ops:
        # requisition_no, project_no, project_name, creator, date, status
        values = [_cell_value(c) for c in row]
        # All captured grids put an operation column first; its labels vary by view.
        has_operation_column = bool(parser.headers and parser.headers[0] == "操作")
        has_operation_text = bool(
            values
            and any(action in values[0] for action in ("编辑", "删除", "查看", "调整", "审批"))
        )
        data_cells = values[1:] if has_operation_column or has_operation_text else values
        while len(data_cells) < 7:
            data_cells.append("")
        items.append(
            PurchaseRequisitionSummary(
                id=rid,
                requisition_no=data_cells[0],
                project_no=data_cells[1],
                project_name=data_cells[2],
                creator_name=data_cells[3],
                create_date=data_cells[4],
                status=data_cells[5],
                next_approver=data_cells[6],
                raw_cells=tuple(values),
            )
        )

    total_count = None
    total_text = None
    m = _TOTAL_RE.search(html)
    if m:
        total_count = int(m.group(1))
        total_text = m.group(0)

    return PurchaseListResult(
        view=view,
        items=tuple(items),
        total_text=total_text,
        total_count=total_count,
        page=page,
        page_size=page_size,
        source_url=source_url,
    )


class _DetailFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}
        self.form_depth = 0
        self.title = ""
        self._in_title = False
        self._title_parts: list[str] = []
        self._textarea_name: str | None = None
        self._textarea_parts: list[str] = []
        self._select_name: str | None = None
        self._select_value: str | None = None
        self._option_value = ""
        self._option_selected = False
        self._option_parts: list[str] = []

    @staticmethod
    def _keep_name(name: str) -> bool:
        return bool(name) and not name.startswith("__") and name not in {
            "btnSearch",
            "btnNew",
            "btnSave",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
            self._title_parts = []
            return
        if tag == "form":
            self.form_depth += 1
            return
        if self.form_depth <= 0:
            return
        if tag == "input":
            name = ad.get("name", "")
            input_type = ad.get("type", "text").lower()
            if not self._keep_name(name) or input_type in {"submit", "button", "reset", "file"}:
                return
            if input_type in {"checkbox", "radio"} and "checked" not in ad:
                return
            self.fields[name] = ad.get("value", "on" if input_type in {"checkbox", "radio"} else "")
            return
        if tag == "textarea":
            name = ad.get("name", "")
            if self._keep_name(name):
                self._textarea_name = name
                self._textarea_parts = []
            return
        if tag == "select":
            name = ad.get("name", "")
            if self._keep_name(name):
                self._select_name = name
                self._select_value = None
            return
        if tag == "option" and self._select_name is not None:
            self._option_value = ad.get("value", "")
            self._option_selected = "selected" in ad
            self._option_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self._in_title:
            self.title = _WS_RE.sub(" ", "".join(self._title_parts)).strip()
            self._in_title = False
            return
        if tag == "form" and self.form_depth > 0:
            self.form_depth -= 1
            return
        if tag == "textarea" and self._textarea_name is not None:
            self.fields[self._textarea_name] = "".join(self._textarea_parts).strip()
            self._textarea_name = None
            self._textarea_parts = []
            return
        if tag == "option" and self._select_name is not None:
            text = _WS_RE.sub(" ", "".join(self._option_parts)).strip()
            value = self._option_value or text
            if self._option_selected or self._select_value is None:
                self._select_value = value
            self._option_parts = []
            return
        if tag == "select" and self._select_name is not None:
            self.fields[self._select_name] = self._select_value or ""
            self._select_name = None
            self._select_value = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._textarea_name is not None:
            self._textarea_parts.append(data)
        if self._select_name is not None:
            self._option_parts.append(data)


class _DetailTableParser(HTMLParser):
    """Capture top-level table rows without interpreting business values."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[tuple[str, str]]]] = []
        self._depth = 0
        self._table: list[list[tuple[str, str]]] | None = None
        self._row: list[tuple[str, str]] | None = None
        self._cell_tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "table":
            if self._depth == 0:
                self._table = []
            self._depth += 1
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell_tag = tag
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._depth == 1 and self._cell_tag == tag:
            text = _WS_RE.sub(" ", "".join(self._parts)).strip()
            if self._row is not None:
                self._row.append((tag, text))
            self._cell_tag = None
            self._parts = []
            return
        if tag == "tr" and self._depth == 1 and self._row is not None:
            if self._row and self._table is not None:
                self._table.append(self._row)
            self._row = None
            return
        if tag == "table" and self._depth > 0:
            self._depth -= 1
            if self._depth == 0 and self._table is not None:
                self.tables.append(self._table)
                self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._parts.append(data)


_DETAIL_LABELS = {
    "申请单编号": "PR_RequisitionNo",
    "项目编号": "PR_PrjNo",
    "项目名称": "PR_PrjName",
    "项目经理": "PR_ProjectManagerName",
    "销售合同": "PR_SalesContractNo",
    "签署主体": "PR_SigningEntity",
    "第三方软硬件剩余成本": "PR_RemainingHardwareCost",
    "第三方服务剩余成本": "PR_RemainingServiceCost",
    "采购方式": "PR_ProcurementMethod",
    "采购经理": "PR_ProcurementManagerName",
    "备注": "PR_Remark",
}


def _clean_label(value: str) -> str:
    return _WS_RE.sub(" ", value.replace("*", " ")).strip()


def _parse_readonly_fields(tables: list[list[list[tuple[str, str]]]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for table in tables:
        for row in table:
            for index, (tag, label) in enumerate(row[:-1]):
                if tag != "th" or row[index + 1][0] != "td":
                    continue
                field_name = _DETAIL_LABELS.get(_clean_label(label))
                if field_name and field_name not in fields:
                    fields[field_name] = row[index + 1][1]
    return fields


def _parse_approval_steps(
    tables: list[list[list[tuple[str, str]]]],
) -> tuple[PurchaseApprovalStep, ...]:
    required = ("序号", "时间", "审批人", "职位", "操作", "批注")
    for table in tables:
        header_index = None
        column_indexes: dict[str, int] = {}
        for index, row in enumerate(table):
            labels = [_clean_label(text) for _, text in row]
            if all(label in labels for label in required):
                header_index = index
                column_indexes = {label: labels.index(label) for label in required}
                break
        if header_index is None:
            continue

        steps: list[PurchaseApprovalStep] = []
        for row in table[header_index + 1 :]:
            values = [text for tag, text in row if tag == "td"]
            if not values:
                continue

            def value(label: str) -> str:
                index = column_indexes[label]
                return values[index] if index < len(values) else ""

            step = PurchaseApprovalStep(
                sequence=value("序号"),
                timestamp=value("时间"),
                approver_name=value("审批人"),
                role=value("职位"),
                action=value("操作"),
                comment=value("批注"),
            )
            if any((step.sequence, step.timestamp, step.approver_name, step.action)):
                steps.append(step)
        return tuple(steps)
    return ()


def parse_purchase_detail(html: str, *, requisition_id: str) -> PurchaseRequisitionDetail:
    """Extract edit controls or captured read-only Detail fields and approval steps."""
    form_parser = _DetailFormParser()
    form_parser.feed(html)
    table_parser = _DetailTableParser()
    table_parser.feed(html)
    fields = _parse_readonly_fields(table_parser.tables)
    fields.update(form_parser.fields)
    if not fields:
        raise ValueError("Purchase detail fields not found")
    return PurchaseRequisitionDetail(
        id=requisition_id,
        fields=fields,
        html_title=form_parser.title,
        approval_steps=_parse_approval_steps(table_parser.tables),
    )


VIEW_BY_SEGMENT = {
    "Index": PurchaseView.APPLICATION,
    "ApprovalIndex": PurchaseView.APPROVAL,
    "AdjustIndex": PurchaseView.ADJUSTMENT,
    "RevocationIndex": PurchaseView.REVOCATION,
    "SearchIndex": PurchaseView.SEARCH,
}
