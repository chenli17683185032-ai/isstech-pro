"""Parse purchase requisition list/detail HTML (stdlib only)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from isstech_replay.models.purchase import (
    PurchaseListResult,
    PurchaseRequisitionDetail,
    PurchaseRequisitionSummary,
    PurchaseView,
)


_TOTAL_RE = re.compile(r"共\s*(\d+)\s*条")
_WS_RE = re.compile(r"\s+")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_grid = False
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
        # Drop the ops cell text (编辑 删除)
        data_cells = values[1:] if values and ("编辑" in values[0] or "删除" in values[0]) else values
        while len(data_cells) < 6:
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


def parse_purchase_detail(html: str, *, requisition_id: str) -> PurchaseRequisitionDetail:
    """Extract successful form controls from an edit/detail HTML response."""
    parser = _DetailFormParser()
    parser.feed(html)
    return PurchaseRequisitionDetail(
        id=requisition_id,
        fields=parser.fields,
        html_title=parser.title,
    )


VIEW_BY_SEGMENT = {
    "Index": PurchaseView.APPLICATION,
    "ApprovalIndex": PurchaseView.APPROVAL,
    "AdjustIndex": PurchaseView.ADJUSTMENT,
    "RevocationIndex": PurchaseView.REVOCATION,
    "SearchIndex": PurchaseView.SEARCH,
}
