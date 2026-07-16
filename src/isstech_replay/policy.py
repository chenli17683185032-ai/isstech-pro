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

from .models.payment import (
    PAYMENT_QUERY_FORM_FIELDS,
    PAYMENT_QUERY_PAGER_FORM_FIELDS,
)


PAYMENT_INDEX_PATH = "/WebPMS/Payment/index"
PAYMENT_LIST_PATHS = {
    "application": PAYMENT_INDEX_PATH,
    "approval": "/WebPMS/Payment/ApprovalList",
    "replenish_invoice": "/WebPMS/Payment/ReplenishInvoiceList",
    "replenish_invoice_approval": "/WebPMS/Payment/ReplenishInvoiceApprovalList",
    "query": "/WebPMS/Payment/QueryList",
}
PAYMENT_QUERY_PATH = "/WebPMS/payment/QueryListBySearch"
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
BIZCASE_VIEW_PARAMS = {
    "application": (
        "iss.psa.webui.bizcasemanage.bizcaseapply.list",
        "280101",
    ),
    "adjustment": (
        "iss.psa.webui.bizcasemanage.bizcaseadjust.list",
        "280102",
    ),
    "approval": (
        "iss.psa.webui.bizcasemanage.bizcaseexamine.list",
        "280103",
    ),
    "query": (
        "iss.psa.webui.bizcasemanage.bizcasequery.list",
        "280104",
    ),
}
BIZCASE_APPLICATION_URL = (
    f"{BIZCASE_QUERY_URL}"
    f"&url={BIZCASE_VIEW_PARAMS['application'][0]}"
    "&urltype=mcontrol"
    f"&helpmenucode={BIZCASE_VIEW_PARAMS['application'][1]}"
)
TRAVEL_APPLICATION_PATH = "/WebPSAOA/Fee/FeeApply/EvectionLoan/List.aspx"
TRAVEL_APPLICATION_URL = f"{TRAVEL_APPLICATION_PATH}?helpmenucode=92"
TRAVEL_APPLICATION_PAGER_TARGET = "ctl00$ContentPlaceHolder1$gp"
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
_PAYMENT_QUERY_MAX_BODY_BYTES = 16 * 1024
_TRAVEL_APPLICATION_MAX_BODY_BYTES = 128 * 1024
_TRAVEL_APPLICATION_FIXED_FIELDS = frozenset(
    {
        "__EVENTTARGET",
        "__EVENTARGUMENT",
        "__VIEWSTATE",
        "__VIEWSTATEGENERATOR",
        "__VIEWSTATEENCRYPTED",
        "__EVENTVALIDATION",
        "ctl00$ContentPlaceHolder1$txtApplyNo",
        "ctl00$ContentPlaceHolder1$DDListFeeFormStatus1",
        "ctl00$ContentPlaceHolder1$ApplyStartDate",
        "ctl00$ContentPlaceHolder1$ApplyEndDate",
        "ctl00$ContentPlaceHolder1$ddlOrderBy",
        "ctl00$ContentPlaceHolder1$chkOrderBy",
        "ctl00$ContentPlaceHolder1$gp_input",
    }
)
_TRAVEL_APPLICATION_ROW_FIELD_RE = re.compile(
    r"^ctl00\$ContentPlaceHolder1\$MyGridView\$(ctl\d{2})\$"
    r"(workflowownerid|applyno)$"
)
_PAYMENT_QUERY_PAGER_PATH_RE = re.compile(
    r"^/WebPMS/payment/QueryListBySearch/0/1/False/([1-9]\d*)$"
)


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


def _exact_travel_application_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.path != TRAVEL_APPLICATION_PATH:
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
    return pairs == [("helpmenucode", "92")]


def _bizcase_get_view(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.path != BIZCASE_QUERY_PATH:
        return None
    try:
        pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=5,
        )
    except ValueError:
        return None
    if pairs == [("thUrl", BIZCASE_QUERY_THURL)]:
        return "query"
    for view, (control, help_menu_code) in BIZCASE_VIEW_PARAMS.items():
        if pairs == [
            ("thUrl", BIZCASE_QUERY_THURL),
            ("url", control),
            ("urltype", "mcontrol"),
            ("helpmenucode", help_menu_code),
        ]:
            return view
    return None


def _blocked_bizcase_postback(reason: str) -> PolicyDecision:
    return _decision(
        RequestClass.BUILD_ONLY,
        SideEffect.UNKNOWN,
        "bizcase.postback",
        "bizcase.postback.blocked",
        reason,
    )


def _blocked_payment_query(reason: str) -> PolicyDecision:
    return _decision(
        RequestClass.BUILD_ONLY,
        SideEffect.NONE,
        "payment.query",
        "payment.query_post.blocked",
        reason,
    )


def _blocked_travel_application(reason: str) -> PolicyDecision:
    return _decision(
        RequestClass.BUILD_ONLY,
        SideEffect.UNKNOWN,
        "travel_application.postback",
        "travel_application.postback.blocked",
        reason,
    )


def _classify_payment_query(
    *,
    url: str,
    headers: Mapping[str, str] | None,
    body: bytes | None,
) -> PolicyDecision:
    if body is None:
        return _blocked_payment_query("Payment query requires an in-memory form body")
    if not body or len(body) > _PAYMENT_QUERY_MAX_BODY_BYTES:
        return _blocked_payment_query("Payment query form body is empty or too large")
    content_type = (headers or {}).get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return _blocked_payment_query("Payment query must be form URL encoded")
    try:
        pairs = parse_qsl(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(PAYMENT_QUERY_FORM_FIELDS) + 2,
        )
    except (UnicodeDecodeError, ValueError):
        return _blocked_payment_query("Payment query form body is malformed")
    names = [name for name, _ in pairs]
    if any(count != 1 for count in Counter(names).values()):
        return _blocked_payment_query("Payment query form contains duplicate fields")
    path = urlparse(url).path
    pager_match = _PAYMENT_QUERY_PAGER_PATH_RE.fullmatch(path)
    if pager_match and int(pager_match.group(1)) > 100:
        return _blocked_payment_query("Payment query page exceeds the safety limit")
    expected_names = {
        "ajax",
        *(
            PAYMENT_QUERY_PAGER_FORM_FIELDS
            if pager_match
            else PAYMENT_QUERY_FORM_FIELDS
        ),
    }
    if set(names) != expected_names:
        return _blocked_payment_query("Payment query form field set does not match evidence")
    fields = dict(pairs)
    if fields["ajax"] != "1":
        return _blocked_payment_query("Payment query ajax marker is invalid")
    nonempty = {name: value.strip() for name, value in fields.items() if name != "ajax" and value.strip()}
    allowed_pairs = (
        frozenset({"PM_EmpNo", "PM_EmpName"}),
        frozenset({"PM_ProjectNo", "PM_ProjectName"}),
    )
    if nonempty and frozenset(nonempty) not in allowed_pairs:
        return _blocked_payment_query("Payment query contains an unapproved filter shape")
    if nonempty:
        values = set(nonempty.values())
        if len(values) != 1:
            return _blocked_payment_query("Payment personal filter fields must match")
        value = values.pop()
        if len(value) > 128 or any(character in value for character in "\r\n\x00"):
            return _blocked_payment_query("Payment personal filter value is invalid")
    return _decision(
        RequestClass.ALLOW_LIVE,
        SideEffect.NONE,
        "payment.query",
        "payment.query_post",
        "Body-validated Payment source or personal-scope query",
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


def _classify_travel_application_pagination(
    *,
    headers: Mapping[str, str] | None,
    body: bytes | None,
) -> PolicyDecision:
    if body is None:
        return _blocked_travel_application(
            "Travel application POST requires an in-memory form body"
        )
    if not body or len(body) > _TRAVEL_APPLICATION_MAX_BODY_BYTES:
        return _blocked_travel_application(
            "Travel application form body is empty or exceeds the size limit"
        )
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
        return _blocked_travel_application(
            "Travel application POST must be form URL encoded"
        )
    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
    except (UnicodeDecodeError, ValueError):
        return _blocked_travel_application("Travel application form body is malformed")
    counts = Counter(name for name, _ in pairs)
    if any(count != 1 for count in counts.values()):
        return _blocked_travel_application(
            "Travel application form contains duplicate fields"
        )
    names = frozenset(counts)
    if not _TRAVEL_APPLICATION_FIXED_FIELDS <= names:
        return _blocked_travel_application(
            "Travel application pagination fields are incomplete"
        )
    dynamic_names = names - _TRAVEL_APPLICATION_FIXED_FIELDS
    row_fields: dict[str, set[str]] = {}
    for name in dynamic_names:
        match = _TRAVEL_APPLICATION_ROW_FIELD_RE.fullmatch(name)
        if match is None:
            return _blocked_travel_application(
                "Travel application form contains an unapproved control"
            )
        row_fields.setdefault(match.group(1), set()).add(match.group(2))
    if not row_fields or len(row_fields) > 10 or any(
        fields != {"workflowownerid", "applyno"} for fields in row_fields.values()
    ):
        return _blocked_travel_application(
            "Travel application row state fields are incomplete"
        )

    values = dict(pairs)
    if values["__EVENTTARGET"] != TRAVEL_APPLICATION_PAGER_TARGET:
        return _blocked_travel_application(
            "Travel application event target is not the proven pager"
        )
    page = values["__EVENTARGUMENT"]
    current_page = values["ctl00$ContentPlaceHolder1$gp_input"]
    if not re.fullmatch(r"[1-9]\d{0,2}", page) or not re.fullmatch(
        r"[1-9]\d{0,2}", current_page
    ):
        return _blocked_travel_application(
            "Travel application page values are not bounded positive integers"
        )
    if not values["__VIEWSTATE"] or not values["__EVENTVALIDATION"] or not re.fullmatch(
        r"[A-Fa-f0-9]{8}", values["__VIEWSTATEGENERATOR"]
    ):
        return _blocked_travel_application(
            "Travel application opaque state is missing or malformed"
        )
    for name in (
        "ctl00$ContentPlaceHolder1$txtApplyNo",
        "ctl00$ContentPlaceHolder1$DDListFeeFormStatus1",
        "ctl00$ContentPlaceHolder1$ApplyStartDate",
        "ctl00$ContentPlaceHolder1$ApplyEndDate",
    ):
        if values[name]:
            return _blocked_travel_application(
                "Travel application pager contains an unapproved filter"
            )
    if values["ctl00$ContentPlaceHolder1$ddlOrderBy"] != "AI_ApplyNo" or values[
        "ctl00$ContentPlaceHolder1$chkOrderBy"
    ] != "on":
        return _blocked_travel_application(
            "Travel application ordering differs from the proven list"
        )
    for name in dynamic_names:
        match = _TRAVEL_APPLICATION_ROW_FIELD_RE.fullmatch(name)
        assert match is not None
        value = values[name]
        if match.group(2) == "applyno" and not re.fullmatch(
            r"ELA[0-9A-Z-]+", value
        ):
            return _blocked_travel_application(
                "Travel application row identity is malformed"
            )
        if match.group(2) == "workflowownerid" and not re.fullmatch(
            r"[A-Za-z0-9_-]{0,64}", value
        ):
            return _blocked_travel_application(
                "Travel application workflow owner state is malformed"
            )
    return _decision(
        RequestClass.ALLOW_LIVE,
        SideEffect.NONE,
        "travel_application.paginate",
        "travel_application.pagination_post",
        "Body-validated travel application pager postback",
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

    payment_view = next(
        (
            view
            for view, candidate_path in PAYMENT_LIST_PATHS.items()
            if path == candidate_path
        ),
        None,
    )
    if payment_view is not None and not parsed.query:
        if method == "GET":
            return _decision(
                RequestClass.ALLOW_LIVE,
                SideEffect.NONE,
                f"payment.{payment_view}.list",
                f"payment.{payment_view}_get",
                "Served GET-only Payment list view",
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
    if (
        path == PAYMENT_QUERY_PATH
        or _PAYMENT_QUERY_PAGER_PATH_RE.fullmatch(path) is not None
    ) and not parsed.query:
        if method == "POST":
            return _blocked_payment_query(
                "Payment POST is blocked unless its body proves an empty query"
            )
        return _decision(
            RequestClass.DENY,
            SideEffect.UNKNOWN,
            "payment.query",
            "payment.query_method",
            "Payment query supports only the served POST request",
        )

    if path == BIZCASE_QUERY_PATH:
        view = _bizcase_get_view(url)
        if method == "GET" and view is not None:
            return _decision(
                RequestClass.ALLOW_LIVE,
                SideEffect.NONE,
                f"bizcase.{view}.list",
                f"bizcase.{view}_get",
                "Served exact BizCase list view",
            )
        if method == "POST" and _exact_bizcase_query(url):
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
    if path == TRAVEL_APPLICATION_PATH:
        if _exact_travel_application_url(url):
            if method == "GET":
                return _decision(
                    RequestClass.ALLOW_LIVE,
                    SideEffect.NONE,
                    "travel_application.list",
                    "travel_application.list_get",
                    "Served exact travel application list",
                )
            if method == "POST":
                return _blocked_travel_application(
                    "Travel application POST is blocked unless its body proves pagination"
                )
        return _decision(
            RequestClass.DENY,
            SideEffect.UNKNOWN,
            "travel_application.unapproved",
            "travel_application.query_mismatch",
            "Travel application path or query does not match the exact list entry",
        )
    if path == "/WebPSAOA/Fee/FeeApply/EvectionLoan/Add.aspx":
        return _decision(
            RequestClass.BUILD_ONLY,
            SideEffect.UNKNOWN,
            "travel_application.edit",
            "travel_application.edit_page",
            "Travel application Add.aspx is an edit-capable form",
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
        if method.upper() == "POST" and decision.rule_id == "payment.query_post.blocked":
            return _classify_payment_query(url=url, headers=headers, body=body)
        if (
            method.upper() == "POST"
            and decision.rule_id == "bizcase.postback.blocked"
            and _exact_bizcase_query(url)
        ):
            return _classify_bizcase_pagination(headers=headers, body=body)
        if (
            method.upper() == "POST"
            and decision.rule_id == "travel_application.postback.blocked"
            and _exact_travel_application_url(url)
        ):
            return _classify_travel_application_pagination(
                headers=headers,
                body=body,
            )
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
