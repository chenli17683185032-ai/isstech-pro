from pathlib import Path

from tools.verify_no_secrets import scan


def test_secret_scanner_allows_explicit_test_placeholders(tmp_path: Path) -> None:
    (tmp_path / "fixture.txt").write_text(
        ".iPSA=TEST_TICKET\nemp_Password=TEST_PASSWORD\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == []


def test_secret_scanner_fails_on_likely_live_values(tmp_path: Path) -> None:
    api_key = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
    cookie = ".iPSA" + "=" + "opaque-ticket-value"
    (tmp_path / "leak.txt").write_text(
        f"{api_key}\n{cookie}\n",
        encoding="utf-8",
    )
    findings = scan(tmp_path)
    assert any("api-key" in finding for finding in findings)
    assert any("ipsa-cookie" in finding for finding in findings)
