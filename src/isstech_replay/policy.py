"""Endpoint safety policy: host + method + path template + business action.

Callers must not self-declare READ_ONLY. Classification is derived only from
matching rules. Unknown endpoints are denied. Some GET paths are mutating
(notably PurchaseRequisition Delete).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlparse
import re


class SideEffect(StrEnum):
    NONE = "none"
    SESSION = "session"
    MUTATING = "mutating"
    UNKNOWN = "unknown"


class RequestClass(StrEnum):
    ALLOW_LIVE = "allow-live"
    BUILD_ONLY = "build-only"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    request_class: RequestClass
    side_effect: SideEffect
    action: str
    rule_id: str
    reason: str

    @property
    def allows_transport(self) -> bool:
        return self.request_class is RequestClass.ALLOW_LIVE


@dataclass(frozen=True, slots=True)
class EndpointRule:
    rule_id: str
    action: str
    methods: frozenset[str]
    host_suffixes: tuple[str, ...]
    path_pattern: re.Pattern[str]
    side_effect: SideEffect
    request_class: RequestClass
    description: str = ""


def _compile(path_regex: str) -> re.Pattern[str]:
    return re.compile(path_regex)


def _unsafe_path_reason(path: str) -> str | None:
    """Reject path forms that an upstream server may normalize differently."""
    candidate = path
    for _ in range(3):
        if "\\" in candidate or "\x00" in candidate:
            return "Path contains a backslash or NUL"
        lower_candidate = candidate.lower()
        if "%2f" in lower_candidate or "%5c" in lower_candidate:
            return "Path contains an encoded separator"
        segments = candidate.split("/")
        if any(segment in {".", ".."} for segment in segments):
            return "Path contains a dot segment"
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    else:
        return "Path contains excessive nested encoding"

    if "//" in candidate:
        return "Path contains an empty segment"
    return None


def _default_rules() -> tuple[EndpointRule, ...]:
    business = ("ipsapro.isstech.com",)
    passport = ("passport.isstech.com",)
    both = business + passport

    return (
        # --- authentication / session (live allowed) ---
        EndpointRule(
            rule_id="auth.passport.get",
            action="auth.passport_page",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=passport,
            path_pattern=_compile(r"^/$|^/\?.*$|^/Login(?:/.*)?$"),
            side_effect=SideEffect.SESSION,
            request_class=RequestClass.ALLOW_LIVE,
            description="Passport login page and related GET assets under Login",
        ),
        EndpointRule(
            rule_id="auth.passport.post",
            action="auth.login",
            methods=frozenset({"POST"}),
            host_suffixes=passport,
            path_pattern=_compile(r"^/$|^/\?.*$"),
            side_effect=SideEffect.SESSION,
            request_class=RequestClass.ALLOW_LIVE,
            description="Credential POST; session-only side effect",
        ),
        EndpointRule(
            rule_id="auth.passport.static",
            action="auth.static",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=passport,
            path_pattern=_compile(r"^/(?:Content|Scripts|Login)/.+$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Passport static assets",
        ),
        # --- mutating / write-preparation paths (must precede read rules) ---
        EndpointRule(
            rule_id="pr.delete",
            action="pr.delete",
            methods=frozenset({"GET", "POST", "DELETE"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/PurchaseRequisition/Delete(?:/.*)?$"),
            side_effect=SideEffect.MUTATING,
            request_class=RequestClass.BUILD_ONLY,
            description="Delete is mutating even when issued as GET via $.ajax",
        ),
        EndpointRule(
            rule_id="pr.edit_page",
            action="pr.edit_page",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/"
                r"(?:Edit/[A-Za-z0-9_-]+|ProjectSelection)/?$"
            ),
            side_effect=SideEffect.UNKNOWN,
            request_class=RequestClass.BUILD_ONLY,
            description="Write-preparation UI is outside the CTF_SAFE read-only flow",
        ),
        EndpointRule(
            rule_id="pr.write_post",
            action="pr.write",
            methods=frozenset({"POST", "PUT", "PATCH", "DELETE"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/"
                r"(?:Create|Edit|Save|Submit|Approve|Adjust|Revoke|Revocation|"
                r"Import|New)(?:/.*)?$"
            ),
            side_effect=SideEffect.MUTATING,
            request_class=RequestClass.BUILD_ONLY,
        ),
        EndpointRule(
            rule_id="attachment.upload",
            action="attachment.upload",
            methods=frozenset({"POST", "PUT"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/Attachment/Upload(?:/.*)?$"),
            side_effect=SideEffect.MUTATING,
            request_class=RequestClass.BUILD_ONLY,
        ),
        EndpointRule(
            rule_id="attachment.delete",
            action="attachment.delete",
            methods=frozenset({"GET", "POST", "DELETE"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/Attachment/Delete(?:/.*)?$"),
            side_effect=SideEffect.MUTATING,
            request_class=RequestClass.BUILD_ONLY,
        ),
        EndpointRule(
            rule_id="procurement.write",
            action="procurement.write",
            methods=frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/(?:ProcurementContract|ProcurementOrder|CostConfirmation|"
                r"CheckAcceptance)/(?:Create|Edit|Save|Submit|Approve|Adjust|Delete|"
                r"Revoke|Revocation|Import|New|RollBack)(?:/.*)?$"
            ),
            side_effect=SideEffect.MUTATING,
            request_class=RequestClass.BUILD_ONLY,
            description="Write and write-preparation paths for procurement workflows",
        ),
        # --- purchase requisition reads ---
        EndpointRule(
            rule_id="pr.entry",
            action="pr.entry",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/PurchaseRequisition/?$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
        ),
        EndpointRule(
            rule_id="pr.list_views",
            action="pr.list",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/"
                r"(?:Index|ApprovalIndex|AdjustIndex|RevocationIndex|SearchIndex)"
                r"(?:/0/[1-9]\d*/(?:True|False)"
                r"(?:/[1-9]\d*(?:/(?:10|15|20|30|50|100))?"
                r"(?:/lastOrderField/(?:PR_RequisitionNo|PR_PrjNo|PR_PrjName|"
                r"PR_CreaterName|PR_CreateDate))?"
                r")?"
                r")?/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="List/filter/pager/sort for five PR views",
        ),
        EndpointRule(
            rule_id="pr.filter_post",
            action="pr.filter",
            methods=frozenset({"POST"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/?$"
                r"|^/WebTP/PurchaseRequisition/"
                r"(?:Index|ApprovalIndex|AdjustIndex|RevocationIndex|SearchIndex)"
                r"(?:/0/[1-9]\d*/(?:True|False)"
                r"(?:/[1-9]\d*(?:/(?:10|15|20|30|50|100))?"
                r"(?:/lastOrderField/(?:PR_RequisitionNo|PR_PrjNo|PR_PrjName|"
                r"PR_CreaterName|PR_CreateDate))?"
                r")?"
                r")?/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="AJAX filter form posts that only replace list HTML",
        ),
        EndpointRule(
            rule_id="pr.detail_get",
            action="pr.detail",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/Detail/[A-Za-z0-9_-]+/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Observed read-only Detail page",
        ),
        EndpointRule(
            rule_id="pr.js",
            action="pr.script",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/PurchaseRequisition/(?:JS|js)/.+$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
        ),
        EndpointRule(
            rule_id="procurement.search_views",
            action="procurement.search",
            methods=frozenset({"GET", "HEAD", "POST"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/(?:ProcurementContract|ProcurementOrder|CostConfirmation|"
                r"CheckAcceptance)/SearchIndex"
                r"(?:/0/1/False/[1-9]\d*(?:/(?:10|15|20|30|50|100))?)?/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Observed read-only SearchIndex and pagination for procurement flows",
        ),
        EndpointRule(
            rule_id="procurement.detail_get",
            action="procurement.detail",
            methods=frozenset({"GET"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/(?:(?:ProcurementContract|ProcurementOrder)/SearchDetail|"
                r"(?:CostConfirmation|CheckAcceptance)/Detail)/"
                r"[A-Za-z0-9_-]+/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Observed read-only detail pages for procurement flows",
        ),
        EndpointRule(
            rule_id="webtp.static",
            action="webtp.static",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/(?:Content|Scripts|fonts)/.+$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
        ),
        EndpointRule(
            rule_id="portal.entry",
            action="portal.entry",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/Portal/?$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Portal shell entry used for SSO verification",
        ),
        # Attachments — download is read; upload/delete are writes
        EndpointRule(
            rule_id="pr.download",
            action="attachment.download",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(
                r"^/WebTP/PurchaseRequisition/Download/[A-Za-z0-9_-]+/?$"
            ),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
            description="Observed read-only attachment download route from Detail",
        ),
        EndpointRule(
            rule_id="attachment.download",
            action="attachment.download",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/Attachment/Download/[A-Za-z0-9_-]+/?$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
        ),
        EndpointRule(
            rule_id="attachment.script",
            action="attachment.script",
            methods=frozenset({"GET", "HEAD"}),
            host_suffixes=business,
            path_pattern=_compile(r"^/WebTP/Attachment/js/.+$"),
            side_effect=SideEffect.NONE,
            request_class=RequestClass.ALLOW_LIVE,
        ),
        # catch-all for other hosts in the isstech family still defaults deny
        EndpointRule(
            rule_id="deny.other_isstech",
            action="unknown",
            methods=frozenset(
                {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
            ),
            host_suffixes=both + (".isstech.com",),
            path_pattern=_compile(r"^.*$"),
            side_effect=SideEffect.UNKNOWN,
            request_class=RequestClass.DENY,
            description="Final fall-through for matched hosts; still deny",
        ),
    )


class EndpointPolicy:
    """Match method+host+path to a safety decision. First matching rule wins."""

    def __init__(self, rules: tuple[EndpointRule, ...] | None = None) -> None:
        self.rules = rules if rules is not None else _default_rules()

    @staticmethod
    def _host_matches(host: str, suffix: str) -> bool:
        host = host.lower()
        suffix = suffix.lower()
        if suffix.startswith("."):
            return host == suffix[1:] or host.endswith(suffix)
        return host == suffix

    def decide(self, method: str, url: str) -> PolicyDecision:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        method_u = method.upper()

        if not host:
            return PolicyDecision(
                request_class=RequestClass.DENY,
                side_effect=SideEffect.UNKNOWN,
                action="unknown",
                rule_id="deny.no_host",
                reason="URL has no host",
            )

        if parsed.username is not None or parsed.password is not None:
            return PolicyDecision(
                request_class=RequestClass.DENY,
                side_effect=SideEffect.UNKNOWN,
                action="unknown",
                rule_id="deny.userinfo",
                reason="URL user information is forbidden",
            )

        expected_origin = {
            "ipsapro.isstech.com": ("http", 80),
            "passport.isstech.com": ("https", 443),
        }.get(host)
        if expected_origin is not None:
            expected_scheme, expected_port = expected_origin
            try:
                port = parsed.port
            except ValueError:
                port = -1
            if parsed.scheme.lower() != expected_scheme or port not in {None, expected_port}:
                return PolicyDecision(
                    request_class=RequestClass.DENY,
                    side_effect=SideEffect.UNKNOWN,
                    action="unknown",
                    rule_id="deny.origin",
                    reason="Scheme or port does not match the configured target origin",
                )

        unsafe_reason = _unsafe_path_reason(path)
        if unsafe_reason is not None:
            return PolicyDecision(
                request_class=RequestClass.DENY,
                side_effect=SideEffect.UNKNOWN,
                action="unknown",
                rule_id="deny.unsafe_path",
                reason=unsafe_reason,
            )

        for rule in self.rules:
            if method_u not in rule.methods:
                continue
            if not any(self._host_matches(host, s) for s in rule.host_suffixes):
                continue
            if not rule.path_pattern.search(path):
                continue
            return PolicyDecision(
                request_class=rule.request_class,
                side_effect=rule.side_effect,
                action=rule.action,
                rule_id=rule.rule_id,
                reason=rule.description or rule.rule_id,
            )

        return PolicyDecision(
            request_class=RequestClass.DENY,
            side_effect=SideEffect.UNKNOWN,
            action="unknown",
            rule_id="deny.default",
            reason="No matching allow rule; deny by default",
        )

    def assert_live_allowed(self, method: str, url: str) -> PolicyDecision:
        decision = self.decide(method, url)
        if not decision.allows_transport:
            raise PolicyViolation(
                method,
                url,
                decision,
            )
        return decision


class PolicyViolation(RuntimeError):
    """Raised when a request is not allowed to reach the real transport."""

    def __init__(self, method: str, url: str, decision: PolicyDecision) -> None:
        self.method = method
        self.url = url
        self.decision = decision
        super().__init__(
            f"Refusing {method.upper()} {url}: "
            f"class={decision.request_class.value} "
            f"side_effect={decision.side_effect.value} "
            f"rule={decision.rule_id} ({decision.reason})"
        )


# Back-compat alias used by older tests/docs wording
UnsafeRequestError = PolicyViolation
