"""Parse passport login HTML and detect authenticated business pages.

Uses stdlib html.parser only — no browser runtime dependency.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

from isstech_replay.models.auth import LoginForm


LOGIN_ERROR_MARKERS = (
    "用户名或密码错误，请重新登陆！",
    "用户名或密码错误",
    "请重新登陆",
)

LOGIN_PAGE_MARKERS = (
    "emp_DomainName",
    "emp_Password",
    "g_loginform",
    "BtnLogin",
)

AUTH_PAGE_MARKERS = (
    "formPurchaseRequisitionIndex",
    "PurchaseRequisition",
    "main_content",
)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form":
            self._current = {
                "action": ad.get("action", ""),
                "method": ad.get("method", "get").lower(),
                "id": ad.get("id", ""),
                "fields": {},
            }
            self.forms.append(self._current)
            return
        if self._current is None:
            return
        if tag.lower() in {"input", "select", "textarea"}:
            name = ad.get("name") or ""
            if not name:
                return
            fields = self._current["fields"]  # type: ignore[assignment]
            assert isinstance(fields, dict)
            fields[name] = ad.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None


def _pick_login_form(forms: list[dict[str, object]]) -> dict[str, object] | None:
    for form in forms:
        fields = form.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        if "emp_DomainName" in fields and "emp_Password" in fields:
            return form
    for form in forms:
        if form.get("method") == "post":
            return form
    return forms[0] if forms else None


def parse_login_form(html: str, page_url: str) -> LoginForm:
    """Extract login form action and hidden fields from passport HTML."""
    parser = _FormParser()
    parser.feed(html)
    form = _pick_login_form(parser.forms)
    if form is None:
        raise ValueError("No login form found in HTML")

    fields = form.get("fields") or {}
    assert isinstance(fields, dict)
    action = str(form.get("action") or "/")
    action_url = urljoin(page_url, action)

    parsed_page = urlparse(page_url)
    qs = parse_qs(parsed_page.query)

    domain_url_field_value = str(fields.get("DomainUrl") or "")
    return_url_field_value = str(fields.get("ReturnUrl") or "")
    domain_url = domain_url_field_value
    return_url = return_url_field_value
    # Passport often leaves hidden DomainUrl/ReturnUrl empty and encodes them
    # only on the form action / query string.
    if not domain_url:
        domain_url = (qs.get("DomainUrl") or [""])[0]
    if not return_url:
        return_url = (qs.get("ReturnUrl") or [""])[0]

    action_qs = parse_qs(urlparse(action_url).query)
    if not domain_url:
        domain_url = (action_qs.get("DomainUrl") or [""])[0]
    if not return_url:
        return_url = (action_qs.get("ReturnUrl") or [""])[0]

    return LoginForm(
        action_url=action_url,
        domain_url=domain_url,
        return_url=return_url,
        domain_url_field_value=domain_url_field_value,
        return_url_field_value=return_url_field_value,
    )


def is_login_page(html: str) -> bool:
    hits = sum(1 for m in LOGIN_PAGE_MARKERS if m in html)
    return hits >= 2 or ("emp_Password" in html and "emp_DomainName" in html)


def is_authenticated_business_page(html: str) -> bool:
    if is_login_page(html):
        return False
    return any(m in html for m in AUTH_PAGE_MARKERS) and "软通智慧科技专业服务系统" in html


def extract_login_error(html: str) -> str | None:
    for marker in LOGIN_ERROR_MARKERS:
        if marker in html:
            return marker
    return None


def cookie_names(jar: object) -> tuple[str, ...]:
    """Return sorted unique cookie names from an httpx/RequestsCookieJar-like object."""
    names: set[str] = set()
    try:
        for cookie in jar:  # type: ignore[attr-defined]
            name = getattr(cookie, "name", None)
            if name:
                names.add(str(name))
    except TypeError:
        # mapping-like
        try:
            names.update(str(k) for k in jar.keys())  # type: ignore[attr-defined]
        except Exception:
            pass
    return tuple(sorted(names))
