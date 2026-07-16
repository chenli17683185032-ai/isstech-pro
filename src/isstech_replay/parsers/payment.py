"""Parse the fixed Payment application list without browser dependencies."""

from __future__ import annotations

from html.parser import HTMLParser
import re

from isstech_replay.models.payment import (
    PAYMENT_HEADERS,
    PAYMENT_QUERY_HEADERS,
    PaymentListResult,
    PaymentRecord,
)


_WS_RE = re.compile(r"\s+")
_PAGER_RE = re.compile(
    r"总共\s*(\d+)\s*条记录\s*，\s*共\s*(\d+)\s*页\s*，\s*当前第\s*(\d+)\s*页"
)


def _clean(value: str) -> str:
    return _WS_RE.sub(" ", value).strip()


class _PaymentGridParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "table" and not self.in_grid and {"table", "table-bordered"} <= classes:
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
            self._cell = {"text": "", "ajax_ids": []}
            self._parts = []
        elif tag == "a" and self._cell is not None and attributes.get("ajax-data"):
            ajax_ids = self._cell["ajax_ids"]
            assert isinstance(ajax_ids, list)
            ajax_ids.append(attributes["ajax-data"])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.in_grid:
            return
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

    def handle_data(self, data: str) -> None:
        if self._header_parts is not None and self._header_child_depth == 0:
            self._header_parts.append(data)
        elif self._cell is not None:
            self._parts.append(data)


def _parse_payment_list(
    html: str,
    *,
    expected_headers: tuple[str, ...],
    source_url: str,
) -> PaymentListResult:
    parser = _PaymentGridParser()
    parser.feed(html)
    if not parser.found_grid:
        raise ValueError("payment list grid not found")
    rows = parser.rows
    if (
        expected_headers == PAYMENT_QUERY_HEADERS
        and tuple(parser.headers) == ("", *expected_headers)
    ):
        rows = [row[1:] for row in rows]
    elif tuple(parser.headers) != expected_headers:
        raise ValueError("payment list schema changed")

    items: list[PaymentRecord] = []
    seen_ids: set[str] = set()
    field_names = expected_headers[1:]
    for row in rows:
        if len(row) != len(expected_headers):
            raise ValueError("payment row does not match list schema")
        raw_ids = row[0]["ajax_ids"]
        assert isinstance(raw_ids, list)
        identities = {str(value) for value in raw_ids if value}
        if len(identities) != 1:
            raise ValueError("payment row has no unique stable identity")
        identity = identities.pop()
        if identity in seen_ids:
            raise ValueError("payment page contains a duplicate stable identity")
        seen_ids.add(identity)
        values = [str(cell["text"]) for cell in row[1:]]
        fields = tuple(zip(field_names, values, strict=True))
        field_map = dict(fields)
        items.append(
            PaymentRecord(
                id=identity,
                payment_no=field_map["付款单编号"],
                payment_type=field_map["付款类别"],
                applicant=field_map["申请人"],
                project_no=field_map["项目编号"],
                project_name=field_map["项目名称"],
                cost_center=field_map["核算成本中心名称"],
                payee_company=field_map["收款公司"],
                payer_company=field_map["付款公司"],
                amount=field_map["金额"],
                currency=field_map["币种"],
                status=field_map["状态"],
                fields=fields,
            )
        )

    pager_match = _PAGER_RE.search(html)
    if pager_match is None:
        raise ValueError("payment pager metadata not found")
    total_count, page_count, current_page = map(int, pager_match.groups())
    if page_count < 1 or current_page < 1 or current_page > page_count:
        raise ValueError("payment pager metadata is invalid")
    if total_count < len(items):
        raise ValueError("payment row count exceeds declared total")
    return PaymentListResult(
        items=tuple(items),
        total_count=total_count,
        page_count=page_count,
        current_page=current_page,
        source_url=source_url,
    )


def parse_payment_list(html: str, *, source_url: str = "") -> PaymentListResult:
    return _parse_payment_list(
        html,
        expected_headers=PAYMENT_HEADERS,
        source_url=source_url,
    )


def parse_payment_query_list(html: str, *, source_url: str = "") -> PaymentListResult:
    return _parse_payment_list(
        html,
        expected_headers=PAYMENT_QUERY_HEADERS,
        source_url=source_url,
    )
