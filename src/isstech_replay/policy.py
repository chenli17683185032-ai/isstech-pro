"""Endpoint safety policy: host + method + path template + business action.

Callers must not self-declare READ_ONLY. Classification is derived only from
matching rules. Unknown endpoints are denied. Some GET paths are mutating
(notably PurchaseRequisition Delete).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qsl, unquote, urlparse
import re


PAYMENT_INDEX_PATH = "/WebPMS/Payment/index"
BIZCASE_QUERY_PATH = "/WebPMP/Main.aspx"
BIZCASE_QUERY_THURL = (
    "28^mcontrol^iss.psa.webui.bizcasemanage.bizcasequery.list^"
    "PMP/BuiltItemM/Bizcase_title.gif^0"
)
BIZCASE_QUERY_URL = (
    "/WebPMP/Main.aspx?"
    "thUrl=28%5emcontrol%5eiss.psa.webui.bizcasemanage.bizcasequery.list%5e"
    "PMP%2fBuiltItemM%2fBizcase_title.gif%5e0"
)
_BIZCASE_POSTBACK_FIELDS = frozenset(
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
_BIZCASE_REQUIRED_POSTBACK_FIELDS = frozenset(
    {
        "__EVENTTARGET",
        "__EVENTARGUMENT",
        "__VIEWSTATE",
        "__VIEWSTATEGENERATOR",
        "ctl05$GridPager1ddlPager",
    }
)
_BIZCASE_MAX_BODY_BYTES = 1024 * 1024


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


def _decision(
    request_class: RequestClass,
    side_effect: SideEffect,
    action: str,
    rule_id: str,
    reason: str,
) -> PolicyDecision:
    return PolicyDecision(
        request_class=request_class,
        side_effect=side_effect,
        action=action,
        rule_id=rule_id,
        reason=reason,
    )


def _exact_bizcase_query(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.path != BIZCASE_QUERY_PATH:
        return False
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except ValueError:
        return False
    return pairs == [("thUrl", BIZCASE_QUERY_THURL)]


def _blocked_bizcase_postback(reason: str) -> PolicyDecision:
    return _decision(
        RequestClass.BUILD_ONLY,
        SideEffect.UNKNOWN,
        "bizcase.postback",
        "bizcase.postback.blocked",
        reason,
    )


def _classify_bizcase_pagination(
    *,
    headers: Mapping[str, str] | None,
    body: bytes | None,
) -> PolicyDecision:
    if body is None:
        return _blocked_bizcase_postback("BizCase POST requires an in-memory form body")
    if not body or len(body) > _BIZCASE_MAX_BODY_BYTES:
        return _blocked_bizcase_postback("BizCase form body is empty or exceeds the size limit")
    content_type_header = next(
        (
            value
            for name, value in (headers or {}).items()
            if name.lower() == "content-type"
        ),
        "",
    )
    content_type = content_type_header.split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return _blocked_bizcase_postback("BizCase POST must be form URL encoded")
    try:
        encoded = body.decode("utf-8")
        pairs = parse_qsl(
            encoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=32,
        )
    except (UnicodeDecodeError, ValueError):
        return _blocked_bizcase_postback("BizCase form body is malformed")
    counts = Counter(name for name, _ in pairs)
    names = frozenset(counts)
    if any(count != 1 for count in counts.values()):
        return _blocked_bizcase_postback("BizCase form contains duplicate fields")
    if not _BIZCASE_REQUIRED_POSTBACK_FIELDS <= names:
        return _blocked_bizcase_postback("BizCase pagination fields are incomplete")
    if names - _BIZCASE_POSTBACK_FIELDS:
        return _blocked_bizcase_postback("BizCase form contains an unapproved control")
    values = dict(pairs)
    if values["__EVENTTARGET"] != "ctl05$GridPager1":
        return _blocked_bizcase_postback("BizCase event target is not the proven pager")
    page = values["__EVENTARGUMENT"]
    selected_page = values["ctl05$GridPager1ddlPager"]
    if not re.fullmatch(r"[1-9]\d{0,2}", page) or not re.fullmatch(
        r"[1-9]\d{0,2}", selected_page
    ):
        return _blocked_bizcase_postback("BizCase page values are not bounded positive integers")
    if not values["__VIEWSTATE"] or not re.fullmatch(
        r"[A-Fa-f0-9]{8}", values["__VIEWSTATEGENERATOR"]
    ):
        return _blocked_bizcase_postback("BizCase opaque state is missing or malformed")
    return _decision(
        RequestClass.ALLOW_LIVE,
        SideEffect.NONE,
        "bizcase.paginate",
        "bizcase.pagination_post",
        "Body-validated BizCase GridPager postback",
    )


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


def _module_url_decision(method: str, url: str) -> PolicyDecision | None:
    parsed = urlparse(url)
    path = parsed.path or "/"

    if path == PAYMENT_INDEX_PATH and not parsed.query:
        if method == "GET":
            return _decision(
                RequestClass.ALLOW_LIVE,
                SideEffect.NONE,
                "payment.list",
                "payment.index_get",
                "Observed GET-only Payment application list",
            )
        return _decision(
            RequestClass.BUILD_ONLY,
            SideEffect.UNKNOWN,
            "payment.write_or_filter",
            "payment.index_non_get",
            "Payment index writes and filters are not enabled",
        )

    if re.fullmatch(r"/WebPMS/Payment/Edit/[A-Za-z0-9_-]+/?", path):
        return _decision(
            RequestClass.BUILD_ONLY,
            SideEffect.UNKNOWN,
            "payment.edit",
            "payment.edit_page",
            "Payment Edit is write preparation",
        )
    if path.rstrip("/") == "/WebPMS/Payment/DelMain":
        return _decision(
            RequestClass.BUILD_ONLY,
            SideEffect.MUTATING,
            "payment.delete",
            "payment.delete",
            "Payment DelMain is mutating",
        )
    if path.rstrip("/") == "/WebPMS/selector/selecttype":
        return _decision(
            RequestClass.BUILD_ONLY,
            SideEffect.UNKNOWN,
            "payment.create_prepare",
            "payment.create_prepare",
            "Payment selector starts a write flow",
        )
    if path.rstrip("/") == "/WebPMS" and method == "POST":
        return _decision(
            RequestClass.DENY,
            SideEffect.NONE,
            "payment.filter_unavailable",
            "payment.broken_filter",
            "The served Payment filter action deterministically returns HTTP 500",
        )

    if path == BIZCASE_QUERY_PATH:
        if _exact_bizcase_query(url):
            if method == "GET":
                return _decision(
                    RequestClass.ALLOW_LIVE,
                    SideEffect.NONE,
                    "bizcase.list",
                    "bizcase.query_get",
                    "Observed exact BizCase query entry",
                )
            if method == "POST":
                return _blocked_bizcase_postback(
                    "BizCase POST is blocked unless its form body proves pagination"
                )
        return _decision(
            RequestClass.DENY,
            SideEffect.UNKNOWN,
            "bizcase.unapproved",
            "bizcase.query_mismatch",
            "BizCase path or query does not match the exact read-only entry",
        )
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

        module_decision = _module_url_decision(method_u, url)
        if module_decision is not None:
            return module_decision

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

    def decide_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> PolicyDecision:
        decision = self.decide(method, url)
        if (
            method.upper() == "POST"
            and decision.rule_id == "bizcase.postback.blocked"
            and _exact_bizcase_query(url)
        ):
            return _classify_bizcase_pagination(headers=headers, body=body)
        return decision

    def assert_live_allowed(self, method: str, url: str) -> PolicyDecision:
        decision = self.decide(method, url)
        if not decision.allows_transport:
            raise PolicyViolation(
                method,
                url,
                decision,
            )
        return decision

    def assert_request_live_allowed(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> PolicyDecision:
        decision = self.decide_request(method, url, headers=headers, body=body)
        if not decision.allows_transport:
            raise PolicyViolation(method, url, decision)
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
