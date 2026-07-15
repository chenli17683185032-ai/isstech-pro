"""Portal identity parsing is exact and fails closed on layout drift."""

from __future__ import annotations

import pytest

from isstech_replay.parsers.portal import (
    normalize_display_name,
    parse_portal_display_name,
)


def _greeting(name: str) -> str:
    return (
        '<div id="AccountGreetings"><div id="Greeting">'
        f"<p>Hi, <strong>{name}</strong></p>"
        "</div></div>"
    )


def test_portal_display_name_comes_only_from_the_account_greeting() -> None:
    html = '<p>Hi, UNRELATED</p>' + _greeting("CURRENT USER")

    assert parse_portal_display_name(html) == "CURRENT USER"
    assert normalize_display_name("  ＣＵＲＲＥＮＴ   User ") == "current user"


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>No greeting</body></html>",
        '<div id="Greeting"><p>Welcome CURRENT USER</p></div>',
        '<div id="Greeting"><p>Hi, DECOY USER</p></div>',
        _greeting("USER A") + _greeting("USER B"),
    ],
)
def test_portal_display_name_rejects_missing_or_ambiguous_identity(html: str) -> None:
    with pytest.raises(ValueError, match="missing or ambiguous"):
        parse_portal_display_name(html)
