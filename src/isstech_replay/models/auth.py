"""Authentication models. Never store raw cookie or password values in logs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LoginForm:
    """Parsed passport login form (field names and non-secret values only)."""

    action_url: str
    domain_url: str
    return_url: str
    domain_url_field_value: str = ""
    return_url_field_value: str = ""
    remember_me_field: str = "RemeberMe"  # target typo preserved
    username_field: str = "emp_DomainName"
    password_field: str = "emp_Password"

    def post_body(
        self,
        username: str,
        password: str,
        *,
        remember_me: bool = False,
    ) -> dict[str, str]:
        """Build application/x-www-form-urlencoded field map for POST."""
        body = {
            self.username_field: username,
            self.password_field: password,
            "DomainUrl": self.domain_url_field_value,
            "ReturnUrl": self.return_url_field_value,
        }
        if remember_me:
            body[self.remember_me_field] = "true"
        return body


@dataclass(frozen=True, slots=True)
class CookieMeta:
    name: str
    domain: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None


@dataclass(slots=True)
class AuthSession:
    """In-process view of an authenticated upstream session.

    Cookie *values* live only inside the httpx cookie jar referenced by
    ``cookie_names_present``. This object itself never holds secret values.
    """

    authenticated: bool
    username: str | None = None
    cookie_names_present: tuple[str, ...] = ()
    business_host: str = "ipsapro.isstech.com"
    notes: list[str] = field(default_factory=list)

    @property
    def has_ipsa_cookie(self) -> bool:
        return ".iPSA" in self.cookie_names_present


@dataclass(frozen=True, slots=True)
class LoginResult:
    success: bool
    session: AuthSession
    final_url: str
    status_code: int
    error_message: str | None = None
