"""Parse the fixed WebForms travel application list and pager state."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlparse

from isstech_replay.models.travel_application import (
    TRAVEL_APPLICATION_GRID_ID,
    TRAVEL_APPLICATION_HEADERS,
    TravelApplicationPage,
    TravelApplicationRecord,
)


_WS_RE = re.compile(r"\s+")
_APPLICATION_NO_RE = re.compile(r"^ELA[0-9A-Z-]+$")
_PAGER_HREF_RE = re.compile(
    r"^javascript:__doPostBack\('ctl00\$ContentPlaceHolder1\$gp','([1-9]\d{0,2})'\)$"
)


def _clean(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


class _TravelApplicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.form_depth = 0
        self.form_action = ""
        self.form_method = ""
        self.form_fields: dict[str, str] = {}
        self._select_name: str | None = None
        self._select_options: list[tuple[str, bool]] = []

        self.in_grid = False
        self.grid_depth = 0
        self.found_grid = False
        self.headers: list[str] = []
        self.rows: list[list[dict[str, object]]] = []
        self._row: list[dict[str, object]] | None = None
        self._cell: dict[str, object] | None = None
        self._parts: list[str] = []
        self._header_parts: list[str] | None = None
        self._header_child_depth = 0
        self.pager_pages: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag == "form" and not self.in_form:
            self.in_form = True
            self.form_depth = 1
            self.form_action = attributes.get("action", "")
            self.form_method = attributes.get("method", "").upper()
        elif self.in_form and tag == "form":
            self.form_depth += 1

        if self.in_form and tag == "input":
            self._capture_input(attributes)
        elif self.in_form and tag == "select":
            self._select_name = attributes.get("name") or None
            self._select_options = []
        elif self._select_name is not None and tag == "option":
            self._select_options.append(
                (attributes.get("value", ""), "selected" in attributes)
            )

        href = attributes.get("href", "")
        pager_match = _PAGER_HREF_RE.fullmatch(href)
        if tag == "a" and pager_match:
            self.pager_pages.add(int(pager_match.group(1)))

        if (
            tag == "table"
            and not self.in_grid
            and attributes.get("id") == TRAVEL_APPLICATION_GRID_ID
        ):
            self.in_grid = True
            self.grid_depth = 1
            self.found_grid = True
            return
        if not self.in_grid:
            return
        if self._header_parts is not None and tag != "th":
            self._header_child_depth += 1
        if tag == "table":
            self.grid_depth += 1
        elif tag == "tr":
            self._row = []
        elif tag == "th":
            self._header_parts = []
            self._header_child_depth = 0
        elif tag == "td" and self._row is not None:
            self._cell = {"text": "", "links": []}
            self._parts = []
        elif tag == "a" and self._cell is not None:
            links = self._cell["links"]
            assert isinstance(links, list)
            links.append(href)

    def _capture_input(self, attributes: dict[str, str]) -> None:
        name = attributes.get("name", "")
        input_type = attributes.get("type", "text").lower()
        if not name or input_type in {"submit", "image", "button", "reset", "file"}:
            return
        if input_type in {"checkbox", "radio"} and "checked" not in attributes:
            return
        self.form_fields[name] = (
            attributes.get("value", "on")
            if input_type in {"checkbox", "radio"}
            else attributes.get("value", "")
        )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._select_name is not None and tag == "select":
            selected = next(
                (value for value, is_selected in self._select_options if is_selected),
                self._select_options[0][0] if self._select_options else "",
            )
            self.form_fields[self._select_name] = selected
            self._select_name = None
            self._select_options = []

        if self.in_grid:
            if self._header_parts is not None and tag != "th" and self._header_child_depth:
                self._header_child_depth -= 1
            if tag == "th" and self._header_parts is not None:
                self.headers.append(_clean("".join(self._header_parts)))
                self._header_parts = None
            elif tag == "td" and self._cell is not None and self._row is not None:
                self._cell["text"] = _clean("".join(self._parts))
                self._row.append(self._cell)
                self._cell = None
                self._parts = []
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None
            elif tag == "table":
                self.grid_depth -= 1
                if self.grid_depth == 0:
                    self.in_grid = False

        if self.in_form and tag == "form":
            self.form_depth -= 1
            if self.form_depth == 0:
                self.in_form = False

    def handle_data(self, data: str) -> None:
        if self._header_parts is not None and self._header_child_depth == 0:
            self._header_parts.append(data)
        elif self._cell is not None:
            self._parts.append(data)


def parse_travel_application_page(
    html: str,
    *,
    source_url: str = "",
) -> TravelApplicationPage:
    parser = _TravelApplicationParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError("travel application list grid not found")
    if tuple(parser.headers) != TRAVEL_APPLICATION_HEADERS:
        raise ValueError("travel application schema changed")
    if parser.form_method != "POST" or parser.form_action != "List.aspx?helpmenucode=92":
        raise ValueError("travel application form action changed")

    required_fields = {
        "__VIEWSTATE",
        "__VIEWSTATEGENERATOR",
        "__VIEWSTATEENCRYPTED",
        "__EVENTVALIDATION",
        "ctl00$ContentPlaceHolder1$txtApplyNo",
        "ctl00$ContentPlaceHolder1$DDListFeeFormStatus1",
        "ctl00$ContentPlaceHolder1$ApplyStartDate",
        "ctl00$ContentPlaceHolder1$ApplyEndDate",
        "ctl00$ContentPlaceHolder1$ddlOrderBy",
        "ctl00$ContentPlaceHolder1$chkOrderBy",
        "ctl00$ContentPlaceHolder1$gp_input",
    }
    if required_fields - parser.form_fields.keys():
        raise ValueError("travel application postback fields are incomplete")
    if not parser.form_fields["__VIEWSTATE"] or not parser.form_fields["__EVENTVALIDATION"]:
        raise ValueError("travel application opaque state is missing")
    try:
        current_page = int(parser.form_fields["ctl00$ContentPlaceHolder1$gp_input"])
    except ValueError as exc:
        raise ValueError("travel application current page is not numeric") from exc
    page_count = max({current_page, *parser.pager_pages})
    if current_page < 1 or current_page > page_count:
        raise ValueError("travel application pager metadata is invalid")

    items: list[TravelApplicationRecord] = []
    seen_ids: set[str] = set()
    field_names = TRAVEL_APPLICATION_HEADERS[1:-1]
    for row in parser.rows:
        if len(row) != len(TRAVEL_APPLICATION_HEADERS):
            raise ValueError("travel application row does not match list schema")
        values = [str(cell["text"]) for cell in row]
        try:
            ordinal = int(values[0])
        except ValueError as exc:
            raise ValueError("travel application row ordinal is not numeric") from exc
        application_no = values[1]
        if not _APPLICATION_NO_RE.fullmatch(application_no):
            raise ValueError("travel application row has no stable identity")
        if application_no in seen_ids:
            raise ValueError("travel application page contains a duplicate identity")
        seen_ids.add(application_no)
        links = row[1]["links"]
        assert isinstance(links, list)
        matching_links = [href for href in links if href]
        if len(matching_links) != 1:
            raise ValueError("travel application row has no unique detail reference")
        detail = urlparse(str(matching_links[0]))
        query = parse_qs(detail.query, keep_blank_values=True)
        if (
            detail.path != "Add.aspx"
            or query.get("oper") != ["edit"]
            or query.get("page") != [str(current_page)]
            or len(query.get("id", [])) != 1
            or not query["id"][0].isdigit()
        ):
            raise ValueError("travel application detail reference changed")
        fields = tuple(zip(field_names, values[1:-1], strict=True))
        field_map = dict(fields)
        items.append(
            TravelApplicationRecord(
                id=application_no,
                ordinal=ordinal,
                application_no=application_no,
                project_name=field_map["项目名称"],
                applicant=field_map["申请人"],
                application_date=field_map["申请日期"],
                status=field_map["单据状态"],
                amount=field_map["总金额"],
                current_approver=field_map["下一级审批人"],
                fields=fields,
            )
        )

    if [item.ordinal for item in items] != list(range(1, len(items) + 1)):
        raise ValueError("travel application ordinals are not contiguous from one")
    return TravelApplicationPage(
        items=tuple(items),
        current_page=current_page,
        page_count=page_count,
        source_url=source_url,
        postback_fields=tuple(parser.form_fields.items()),
    )
