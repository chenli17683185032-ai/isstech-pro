"""Portal identity parsing is exact and fails closed on layout drift."""

from __future__ import annotations

import pytest

from isstech_replay.parsers.portal import (
    display_name_matches,
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


@pytest.mark.parametrize(
    ("value", "identity"),
    [
        ("CURRENT USER", " current   user "),
        ("current-user (ACCOUNT_1)", "account_1"),
        ("ACCOUNT_1 / Current User", "account_1"),
        ("ＡＣＣＯＵＮＴ＿１（Current User）", "account_1"),
    ],
)
def test_display_name_matches_exact_or_decorated_identity_tokens(
    value: str,
    identity: str,
) -> None:
    assert display_name_matches(value, identity)


@pytest.mark.parametrize(
    ("value", "identity"),
    [
        ("ACCOUNT_10", "ACCOUNT_1"),
        ("XACCOUNT_1", "ACCOUNT_1"),
        ("ACCOUNT_1_SUFFIX", "ACCOUNT_1"),
        ("OTHER USER", "ACCOUNT_1"),
        ("", "ACCOUNT_1"),
    ],
)
def test_display_name_match_rejects_unbounded_substrings(
    value: str,
    identity: str,
) -> None:
    assert not display_name_matches(value, identity)
