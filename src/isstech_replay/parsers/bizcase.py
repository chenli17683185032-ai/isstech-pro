"""Parse fixed-schema BizCase WebForms list pages."""

from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from html.parser import HTMLParser
import re

from isstech_replay.models.bizcase import (
    BIZCASE_APPLICATION_HEADERS,
    BIZCASE_HEADERS,
    BizCaseListResult,
    BizCasePage,
    BizCaseRecord,
)


_WS_RE = re.compile(r"\s+")
_DETAIL_POSTBACK_RE = re.compile(
    r"^javascript:__doPostBack\('ctl05\$dgr\$ctl\d+\$lbtnVersionNo',''\)$"
)
_APPLICATION_SCOPE_RE = re.compile(
    rb"BC_CreatorEmpID\s*=\s*(?P<identity>\d+)\s+or\s+"
    rb"BC_BUID\s+in\s*\(\s*select\s+Role_DeptID\s+from\s+"
    rb"View_ProjectQueryList\s+where\s+RBE_EmpID\s*=\s*(?P=identity)\s+"
    rb"and\s+RE_EntityID\s*=\s*\d+\s*\)\s+or\s+"
    rb"BC_BGID\s+in\s*\(\s*select\s+Role_DeptID\s+from\s+"
    rb"View_ProjectQueryList\s+where\s+RBE_EmpID\s*=\s*(?P=identity)\s+"
    rb"and\s+RE_EntityID\s*=\s*\d+\s*\)\s+or\s+"
    rb"BC_DeliveryDeptID\s+in\s*\(\s*select\s+Role_DeptID\s+from\s+"
    rb"View_ProjectQueryList\s+where\s+RBE_EmpID\s*=\s*(?P=identity)\s+"
    rb"and\s+RE_EntityID\s*=\s*\d+\s*\)",
    re.IGNORECASE,
)
_POSTBACK_FIELD_NAMES = frozenset(
    {
        "__EVENTTARGET",
        "__EVENTARGUMENT",
        "__VIEWSTATE",
        "__VIEWSTATEGENERATOR",
        "ctl03$CheckDashboard",
        "ctl03$IsShowScorecard",
        "ctl05$txtNo$NewCustTextBox",
        "ctl05$txtClientName$NewCustTextBox",
        "ctl05$txtBGName$NewCustTextBox",
        "ctl05$txtBUName$NewCustTextBox",
        "ctl05$ddlRevRecognitionType",
        "ctl05$ddlStatus",
        "ctl05$txtPrjName$TextBox1",
        "ctl05$GridPager1ddlPager",
    }
)


def _clean(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


def _has_identity_bound_application_scope(viewstate: str) -> bool:
    try:
        raw = b64decode(viewstate, validate=True)
    except (BinasciiError, ValueError):
        return False
    return _APPLICATION_SCOPE_RE.search(raw) is not None


class _BizCaseParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.form_depth = 0
        self.form_fields: dict[str, str] = {}
        self._select_name: str | None = None
        self._select_options: list[tuple[str, bool]] = []
        self.pager_options: list[int] = []
        self.pager_selected: int | None = None
        self.has_pager_control = False

        self.in_grid = False
        self.grid_depth = 0
        self.found_grid = False
        self.headers: list[str] = []
        self.rows: list[list[dict[str, object]]] = []
        self._row: list[dict[str, object]] | None = None
        self._row_class = ""
        self._cell: dict[str, object] | None = None
        self._parts: list[str] = []

    def _store_field(self, name: str, value: str) -> None:
        if name not in _POSTBACK_FIELD_NAMES:
            return
        if name in self.form_fields:
            raise ValueError(f"duplicate BizCase form field: {name}")
        self.form_fields[name] = value

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        has_named_pager = "gridpager1" in (
            attributes.get("name", "") + attributes.get("id", "")
        ).lower()
        has_pager_link = "ctl05$gridpager1" in attributes.get("href", "").lower()
        if (tag in {"input", "select", "button"} and has_named_pager) or (
            tag == "a" and has_pager_link
        ):
            self.has_pager_control = True
        if tag == "form" and attributes.get("id") == "Form1":
            self.in_form = True
            self.form_depth = 1
        elif self.in_form and tag == "form":
            self.form_depth += 1

        if self.in_form:
            if tag == "input":
                name = attributes.get("name", "")
                input_type = attributes.get("type", "text").lower()
                if (
                    name
                    and "disabled" not in attributes
                    and input_type not in {"submit", "button", "reset", "file"}
                    and not (
                        input_type in {"checkbox", "radio"}
                        and "checked" not in attributes
                    )
                ):
                    self._store_field(name, attributes.get("value", ""))
            elif tag == "select" and "disabled" not in attributes:
                name = attributes.get("name", "")
                if name in _POSTBACK_FIELD_NAMES:
                    self._select_name = name
                    self._select_options = []
            elif tag == "option" and self._select_name is not None:
                value = attributes.get("value", "")
                self._select_options.append((value, "selected" in attributes))

        if tag == "table" and attributes.get("id") == "ctl05_dgr" and not self.in_grid:
            self.in_grid = True
            self.grid_depth = 1
            self.found_grid = True
            return
        if not self.in_grid:
            return
        if tag == "table":
            self.grid_depth += 1
        elif tag == "tr":
            self._row = []
            self._row_class = attributes.get("class", "")
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"text": "", "hrefs": []}
            self._parts = []
        elif tag == "a" and self._cell is not None:
            hrefs = self._cell["hrefs"]
            assert isinstance(hrefs, list)
            hrefs.append(attributes.get("href", ""))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "option" and self._select_name is not None:
            return
        if tag == "select" and self._select_name is not None:
            selected = [value for value, is_selected in self._select_options if is_selected]
            value = selected[0] if selected else (self._select_options[0][0] if self._select_options else "")
            if len(selected) > 1:
                raise ValueError(f"multiple selected values for BizCase field: {self._select_name}")
            self._store_field(self._select_name, value)
            if self._select_name == "ctl05$GridPager1ddlPager":
                try:
                    self.pager_options = [int(option) for option, _ in self._select_options]
                    self.pager_selected = int(value)
                except ValueError as exc:
                    raise ValueError("BizCase pager contains a non-numeric value") from exc
            self._select_name = None
            self._select_options = []

        if self.in_grid:
            if tag in {"td", "th"} and self._cell is not None and self._row is not None:
                self._cell["text"] = _clean("".join(self._parts))
                self._row.append(self._cell)
                self._cell = None
                self._parts = []
            elif tag == "tr" and self._row is not None:
                if "Grid_Header" in self._row_class:
                    self.headers = [str(cell["text"]) for cell in self._row]
                elif self._row and any(
                    row_class in self._row_class
                    for row_class in ("Grid_Item", "Grid_AlternatingItem")
                ):
                    self.rows.append(self._row)
                self._row = None
                self._row_class = ""
            elif tag == "table":
                self.grid_depth -= 1
                if self.grid_depth == 0:
                    self.in_grid = False

        if tag == "form" and self.in_form:
            self.form_depth -= 1
            if self.form_depth == 0:
                self.in_form = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._parts.append(data)


def parse_bizcase_page(html: str, *, source_url: str = "") -> BizCasePage:
    parser = _BizCaseParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError("BizCase list grid not found")
    if tuple(parser.headers) != BIZCASE_HEADERS:
        raise ValueError("BizCase list schema changed")
    if not parser.pager_options or parser.pager_selected is None:
        raise ValueError("BizCase pager metadata not found")
    expected_pages = list(range(1, max(parser.pager_options) + 1))
    if parser.pager_options != expected_pages or parser.pager_selected not in expected_pages:
        raise ValueError("BizCase pager metadata is invalid")
    for required in ("__VIEWSTATE", "__VIEWSTATEGENERATOR"):
        if not parser.form_fields.get(required):
            raise ValueError(f"BizCase form field missing: {required}")

    items: list[BizCaseRecord] = []
    seen_ids: set[str] = set()
    field_names = BIZCASE_HEADERS[1:]
    for row in parser.rows:
        if len(row) != len(BIZCASE_HEADERS):
            raise ValueError("BizCase row does not match list schema")
        values = [str(cell["text"]) for cell in row]
        try:
            ordinal = int(values[0])
        except ValueError as exc:
            raise ValueError("BizCase row ordinal is not numeric") from exc
        detail_hrefs = [
            str(href)
            for href in row[1]["hrefs"]
            if _DETAIL_POSTBACK_RE.fullmatch(str(href))
        ]
        version_no = values[1]
        if len(detail_hrefs) != 1 or not version_no:
            raise ValueError("BizCase row has no unique stable identity")
        if version_no in seen_ids:
            raise ValueError("BizCase page contains a duplicate stable identity")
        seen_ids.add(version_no)
        fields = tuple(zip(field_names, values[1:], strict=True))
        field_map = dict(fields)
        items.append(
            BizCaseRecord(
                id=version_no,
                ordinal=ordinal,
                version_no=version_no,
                bizcase_no=field_map["BizCase编号"],
                client_name=field_map["客户名称"],
                profit_center_group=field_map["利润中心组"],
                profit_center=field_map["利润中心"],
                project_no=field_map["项目编号"],
                project_name=field_map["项目名称"],
                revenue_recognition_type=field_map["收入确认类型"],
                current_approver=field_map["当前审批人"],
                fields=fields,
            )
        )

    if items:
        expected_ordinals = list(range(items[0].ordinal, items[0].ordinal + len(items)))
        if [item.ordinal for item in items] != expected_ordinals:
            raise ValueError("BizCase page ordinals are not contiguous")
    postback_fields = tuple(
        (name, value)
        for name, value in parser.form_fields.items()
        if name not in {"__EVENTTARGET", "__EVENTARGUMENT"}
    )
    return BizCasePage(
        items=tuple(items),
        current_page=parser.pager_selected,
        page_count=max(parser.pager_options),
        source_url=source_url,
        postback_fields=postback_fields,
    )


def parse_bizcase_application_page(
    html: str,
    *,
    source_url: str = "",
) -> BizCaseListResult:
    parser = _BizCaseParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError("BizCase application list grid not found")
    if tuple(parser.headers) != BIZCASE_APPLICATION_HEADERS:
        raise ValueError("BizCase application schema changed")
    if parser.has_pager_control or parser.pager_options:
        raise ValueError("BizCase application must remain a complete single page")
    for required in ("__VIEWSTATE", "__VIEWSTATEGENERATOR"):
        if not parser.form_fields.get(required):
            raise ValueError(f"BizCase application form field missing: {required}")
    if not _has_identity_bound_application_scope(parser.form_fields["__VIEWSTATE"]):
        raise ValueError("BizCase application personal scope predicate changed")

    items: list[BizCaseRecord] = []
    seen_ids: set[str] = set()
    field_names = BIZCASE_APPLICATION_HEADERS[1:]
    for row in parser.rows:
        if len(row) != len(BIZCASE_APPLICATION_HEADERS):
            raise ValueError("BizCase application row does not match list schema")
        values = [str(cell["text"]) for cell in row]
        try:
            ordinal = int(values[0])
        except ValueError as exc:
            raise ValueError("BizCase application row ordinal is not numeric") from exc
        version_no = values[1]
        bizcase_no = values[2]
        if not version_no or not bizcase_no or not version_no.startswith(f"{bizcase_no}-V"):
            raise ValueError("BizCase application row has no stable identity")
        if version_no in seen_ids:
            raise ValueError("BizCase application contains a duplicate stable identity")
        seen_ids.add(version_no)
        fields = tuple(zip(field_names, values[1:], strict=True))
        field_map = dict(fields)
        items.append(
            BizCaseRecord(
                id=version_no,
                ordinal=ordinal,
                version_no=version_no,
                bizcase_no=bizcase_no,
                client_name=field_map["客户名称"],
                profit_center_group=field_map["利润中心组"],
                profit_center=field_map["利润中心"],
                project_no=field_map["项目编号"],
                project_name=field_map["项目名称"],
                revenue_recognition_type=field_map["收入确认类型"],
                fields=fields,
            )
        )

    if items and [item.ordinal for item in items] != list(range(1, len(items) + 1)):
        raise ValueError("BizCase application ordinals are not contiguous from one")
    return BizCaseListResult(
        items=tuple(items),
        total_count=len(items),
        page_count=1,
        source_url=source_url,
    )
