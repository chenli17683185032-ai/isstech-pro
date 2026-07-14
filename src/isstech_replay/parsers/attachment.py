"""Parse attachment list fragments from HTML."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from isstech_replay.models.attachment import AttachmentMeta


class _AttachmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[AttachmentMeta] = []
        self._in_row = False
        self._row_id = ""
        self._is_attachment = False
        self._cells: list[str] = []
        self._parts: list[str] = []
        self._in_td = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "tr":
            classes = ad.get("class", "")
            self._in_row = True
            self._is_attachment = "attachment" in classes
            self._row_id = ad.get("id", "")
            self._cells = []
            return
        if self._in_row and tag == "td":
            self._in_td = True
            self._parts = []
            return
        if self._in_row and tag == "a":
            onclick = ad.get("onclick", "")
            m = re.search(r"download\((\d+)", onclick)
            if m and not self._row_id:
                self._row_id = m.group(1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_td:
            text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
            self._cells.append(text)
            self._in_td = False
            self._parts = []
            return
        if tag == "tr" and self._in_row:
            if self._is_attachment or self._row_id:
                # Expected: ops | file_name | uploader | date
                file_name = self._cells[1] if len(self._cells) > 1 else ""
                uploader = self._cells[2] if len(self._cells) > 2 else ""
                date = self._cells[3] if len(self._cells) > 3 else ""
                if self._row_id:
                    self.items.append(
                        AttachmentMeta(
                            id=self._row_id,
                            file_name=file_name,
                            uploader_name=uploader,
                            upload_date=date,
                        )
                    )
            self._in_row = False
            self._row_id = ""
            self._cells = []

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._parts.append(data)


def parse_attachment_list(html: str, *, doc_id: str = "") -> tuple[AttachmentMeta, ...]:
    parser = _AttachmentParser()
    parser.feed(html)
    items = parser.items
    if doc_id:
        items = [
            AttachmentMeta(
                id=i.id,
                file_name=i.file_name,
                uploader_name=i.uploader_name,
                upload_date=i.upload_date,
                doc_id=doc_id,
            )
            for i in items
        ]
    return tuple(items)


_DOWNLOAD_PATH = re.compile(r"/WebTP/Attachment/Download/([^\"'?\s]+)")


def extract_download_ids(html: str) -> tuple[str, ...]:
    return tuple(_DOWNLOAD_PATH.findall(html))
