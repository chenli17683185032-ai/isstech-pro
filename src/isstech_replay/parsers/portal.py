"""Parse the authenticated Portal's current-user greeting."""

from __future__ import annotations

from html.parser import HTMLParser
import re
import unicodedata


_GREETING_RE = re.compile(r"^Hi\s*,\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_display_name(value: str) -> str:
    normalized = _WHITESPACE_RE.sub(
        " ",
        unicodedata.normalize("NFKC", value),
    ).strip()
    if (
        not normalized
        or len(normalized) > 200
        or any(character in normalized for character in "\r\n\x00")
    ):
        raise ValueError("Portal display name is invalid")
    return normalized.casefold()


class _PortalGreetingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._stack: list[tuple[str, str]] = []
        self._capturing = False
        self._parts: list[str] = []
        self.greetings: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        self._stack.append((tag.lower(), attributes.get("id", "")))
        account_index = next(
            (
                index
                for index, (_, element_id) in enumerate(self._stack)
                if element_id == "AccountGreetings"
            ),
            None,
        )
        greeting_index = next(
            (
                index
                for index, (_, element_id) in enumerate(self._stack)
                if element_id == "Greeting"
            ),
            None,
        )
        if (
            tag.lower() == "p"
            and account_index is not None
            and greeting_index is not None
            and account_index < greeting_index
        ):
            self._capturing = True
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "p" and self._capturing:
            self.greetings.append(_WHITESPACE_RE.sub(" ", "".join(self._parts)).strip())
            self._capturing = False
            self._parts = []
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)


def parse_portal_display_name(html: str) -> str:
    parser = _PortalGreetingParser()
    parser.feed(html)
    matches = []
    for greeting in parser.greetings:
        match = _GREETING_RE.fullmatch(greeting)
        if match:
            name = match.group("name").strip()
            normalize_display_name(name)
            matches.append(name)
    if len(matches) != 1:
        raise ValueError("Portal current-user greeting is missing or ambiguous")
    return matches[0]
