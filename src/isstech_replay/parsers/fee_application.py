"""Parse fixed WebForms fee application lists and their opaque pager state."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlparse

from isstech_replay.models.fee_application import (
    FEE_APPLICATION_GRID_ID,
    FEE_APPLICATION_HEADERS,
    FEE_APPLICATION_PAGE_SIZE,
    FeeApplicationPage,
    FeeApplicationRecord,
    FeeApplicationSpec,
)


_WS_RE = re.compile(r"\s+")


def _clean(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


class _FeeApplicationParser(HTMLParser):
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

        self.pager_links: list[tuple[str, str, str]] = []
        self.submit_controls: dict[str, bool] = {}

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

        if tag == "a":
            self.pager_links.append(
                (
                    attributes.get("href", ""),
                    attributes.get("onclick", ""),
                    attributes.get("title", ""),
                )
            )

        if (
            tag == "table"
            and not self.in_grid
            and attributes.get("id") == FEE_APPLICATION_GRID_ID
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
            links.append(attributes.get("href", ""))

    def _capture_input(self, attributes: dict[str, str]) -> None:
        name = attributes.get("name", "")
        input_type = attributes.get("type", "text").lower()
        if name and input_type == "submit":
            self.submit_controls[name] = "disabled" in attributes
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


def _pager_pages(parser: _FeeApplicationParser, spec: FeeApplicationSpec) -> set[int]:
    pages: set[int] = set()
    escaped_target = re.escape(spec.pager_target)
    postback = re.compile(
        rf"__doPostBack\('{escaped_target}','([1-9]\d{{0,2}})'\)"
    )
    titled_page = re.compile(r"转到第([1-9]\d{0,2})页")
    for href, onclick, title in parser.pager_links:
        combined = href + onclick
        match = postback.search(combined)
        if match is not None:
            pages.add(int(match.group(1)))
            continue
        title_match = titled_page.fullmatch(title)
        if spec.pager_target in combined and title_match is not None:
            pages.add(int(title_match.group(1)))
    return pages


def parse_fee_application_page(
    html: str,
    *,
    spec: FeeApplicationSpec,
    source_url: str = "",
) -> FeeApplicationPage:
    parser = _FeeApplicationParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError(f"{spec.key} list grid not found")
    if tuple(parser.headers) != FEE_APPLICATION_HEADERS:
        raise ValueError(f"{spec.key} schema changed")
    if parser.form_method != "POST" or parser.form_action != spec.form_action:
        raise ValueError(f"{spec.key} form action changed")

    required_fields = {
        "ctl00$ContentPlaceHolder1$txtApplyNo",
        "ctl00$ContentPlaceHolder1$DDListFeeFormStatus1",
        "ctl00$ContentPlaceHolder1$ApplyStartDate",
        "ctl00$ContentPlaceHolder1$ApplyEndDate",
        "ctl00$ContentPlaceHolder1$ddlOrderBy",
        "ctl00$ContentPlaceHolder1$chkOrderBy",
        spec.pager_input,
        *spec.required_empty_fields,
    }
    if spec.pagination_enabled:
        required_fields.update(
            {
                "__VIEWSTATE",
                "__VIEWSTATEGENERATOR",
                "__VIEWSTATEENCRYPTED",
                "__EVENTVALIDATION",
            }
        )
    if required_fields - parser.form_fields.keys():
        raise ValueError(f"{spec.key} list controls are incomplete")
    for name in (
        "ctl00$ContentPlaceHolder1$txtApplyNo",
        "ctl00$ContentPlaceHolder1$DDListFeeFormStatus1",
        "ctl00$ContentPlaceHolder1$ApplyStartDate",
        "ctl00$ContentPlaceHolder1$ApplyEndDate",
        *spec.required_empty_fields,
    ):
        if parser.form_fields[name]:
            raise ValueError(f"{spec.key} list contains an unapproved filter")
    if (
        parser.form_fields["ctl00$ContentPlaceHolder1$ddlOrderBy"] != "AI_ApplyNo"
        or parser.form_fields["ctl00$ContentPlaceHolder1$chkOrderBy"] != "on"
    ):
        raise ValueError(f"{spec.key} ordering changed")
    try:
        current_page = int(parser.form_fields[spec.pager_input])
    except ValueError as exc:
        raise ValueError(f"{spec.key} current page is not numeric") from exc
    pages = _pager_pages(parser, spec)
    page_count = max({current_page, *pages})
    if current_page < 1 or current_page > page_count:
        raise ValueError(f"{spec.key} pager metadata is invalid")
    if spec.pagination_enabled:
        if not parser.form_fields["__VIEWSTATE"] or not parser.form_fields[
            "__EVENTVALIDATION"
        ]:
            raise ValueError(f"{spec.key} opaque state is missing")
        if parser.submit_controls.get(spec.pager_target) is True:
            raise ValueError(f"{spec.key} live pager is unexpectedly disabled")
    elif (
        current_page != 1
        or page_count != 1
        or parser.submit_controls.get(spec.pager_target) is not True
    ):
        raise ValueError(f"{spec.key} pagination is not proven read-only")

    items: list[FeeApplicationRecord] = []
    seen_ids: set[str] = set()
    identity_re = re.compile(
        rf"^{re.escape(spec.application_no_prefix)}[0-9A-Z-]+$"
    )
    field_names = FEE_APPLICATION_HEADERS[1:-1]
    for row in parser.rows:
        if len(row) != len(FEE_APPLICATION_HEADERS):
            raise ValueError(f"{spec.key} row does not match list schema")
        values = [str(cell["text"]) for cell in row]
        try:
            ordinal = int(values[0])
        except ValueError as exc:
            raise ValueError(f"{spec.key} row ordinal is not numeric") from exc
        application_no = values[1]
        if not identity_re.fullmatch(application_no):
            raise ValueError(f"{spec.key} row has no stable identity")
        if application_no in seen_ids:
            raise ValueError(f"{spec.key} page contains a duplicate identity")
        seen_ids.add(application_no)
        links = row[1]["links"]
        assert isinstance(links, list)
        matching_links = [href for href in links if href]
        if len(matching_links) != 1:
            raise ValueError(f"{spec.key} row has no unique detail reference")
        detail = urlparse(str(matching_links[0]))
        query = parse_qs(detail.query, keep_blank_values=True)
        query_names = frozenset(query)
        core_names = {"id", "oper", "page"}
        extended_names = core_names | {"ControlString", "SqlString"}
        if (
            detail.path != "Add.aspx"
            or query_names not in {frozenset(core_names), frozenset(extended_names)}
            or query.get("oper") != ["edit"]
            or query.get("page") != [str(current_page)]
            or len(query.get("id", [])) != 1
            or not query["id"][0].isdigit()
        ):
            raise ValueError(f"{spec.key} detail reference changed")
        fields = tuple(zip(field_names, values[1:-1], strict=True))
        field_map = dict(fields)
        items.append(
            FeeApplicationRecord(
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

    expected_start = (current_page - 1) * FEE_APPLICATION_PAGE_SIZE + 1
    expected_ordinals = list(range(expected_start, expected_start + len(items)))
    if [item.ordinal for item in items] != expected_ordinals:
        raise ValueError(f"{spec.key} ordinals are not globally contiguous")
    return FeeApplicationPage(
        items=tuple(items),
        current_page=current_page,
        page_count=page_count,
        source_url=source_url,
        pager_target=(spec.pager_target if spec.pagination_enabled else ""),
        pager_input=(spec.pager_input if spec.pagination_enabled else ""),
        postback_fields=(
            tuple(parser.form_fields.items()) if spec.pagination_enabled else ()
        ),
    )
