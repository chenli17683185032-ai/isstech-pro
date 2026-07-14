# Endpoint matrix

Verification states:

- `observed`: seen in live browser traffic
- `replayed`: reproduced through direct HTTP from a clean client session
- `static-only`: derived from served HTML/JavaScript without transmission
- `blocked-write`: request identified as mutating and intentionally not sent
- `pending`: known gap; not yet captured or classified
- `superseded`: historical evidence replaced by a better capture

## Authentication / session

| Area | UI action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Authentication | Open purchase requisition while anonymous | GET | `http://ipsapro.isstech.com/WebTP/PurchaseRequisition` | 302 to passport | observed | `captures/playwright/unauth.har` |
| Authentication | Load passport login page | GET | `https://passport.isstech.com/?DomainUrl=http://ipsapro.isstech.com&ReturnUrl=%2fWebTP%2fPurchaseRequisition` | Sets `ASP.NET_SessionId` (HttpOnly, SameSite=Lax) | observed | `captures/playwright/unauth.har` |
| Authentication | Login form shape | POST | `https://passport.isstech.com/?DomainUrl=…&ReturnUrl=…` | Session only | observed (form) / pending (success body) | `portal_login.html`, `captures/Login_JS_Index`, unauth HAR HTML |
| Authentication | Failed credential POST | POST | `https://passport.isstech.com/` (form action `/` after fail) | 302 to `/`, re-render with `flag=0` | observed / superseded for success path | `captures/login_fail_response.html`, `captures/login_fail_followed.html` |
| Authentication | Successful login | POST + 30x chain | Passport → business host, obtain `.iPSA` | Session only | pending | Needs `captures/raw/YYYYMMDD-login-success.har` |
| Authentication | Authenticated purchase page | GET | `/WebTP/PurchaseRequisition` | None | observed | `captures/raw/auth_purchase_requisition.html`, `captures/redacted/purchase_requisition_initial_network.json` |
| Authentication | Reuse observed browser ticket in ordinary HTTP client | GET | `/WebTP/PurchaseRequisition` | None | replayed with imported ticket | task runtime probe, `captures/redacted/auth-cookie-probe.json` |
| Authentication | Obtain ticket from clean pure-HTTP credential login | POST + redirects | Passport → business host | Session only | pending | Requires successful-login HAR + credentialed smoke |

### Login form fields (passport)

| Field | Role | Notes |
| --- | --- | --- |
| `emp_DomainName` | Username | Text input |
| `emp_Password` | Password | Never store values |
| `RemeberMe` | Remember me | Typo preserved; submitted only when checked |
| `DomainUrl` | Target host | e.g. `http://ipsapro.isstech.com` |
| `ReturnUrl` | Post-login path | e.g. `/WebTP/PurchaseRequisition` |

`flag`, `uname`, `ctip`, `etip`, and `bgstr` are page-level inputs after
`</form>`. They affect UI/error state but are not successful browser form
controls and must not be included in the credential POST.

Failed login shows: `用户名或密码错误，请重新登陆！` and redirects with `Object moved to /`.

### Session cookies (names/attrs only)

See `captures/redacted/auth-cookie-probe.json` and `captures/redacted/auth_cookie_metadata.json`.
Business auth cookie of interest: `.iPSA` on `.isstech.com` (HttpOnly, SameSite=Lax, session).

## Purchase requisition — application view (`Index`)

| Area | UI action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| PR Application | Shell / default entry | GET | `/WebTP/PurchaseRequisition` | None | observed | auth HTML, initial network JSON |
| PR Application | Index list | GET | `/WebTP/PurchaseRequisition/Index` | None | static-only / partial observed | auth HTML nav + form action |
| PR Application | Filter / search (AJAX replace) | POST | `/WebTP/PurchaseRequisition` (`formPurchaseRequisitionIndex`, `data-ajax-method=Post`) | None (read filter) | static-only | auth HTML form attrs |
| PR Application | Pagination / page size | GET (via `ajaxSubmit` url) | `/WebTP/PurchaseRequisition/Index/{a}/{b}/{c}[/{page}/{size}]` | None | static-only | pager links in auth HTML |
| PR Application | Sort | GET | `/WebTP/PurchaseRequisition/Index/…/True/…/lastOrderField/{field}` | None | static-only | fields: `PR_RequisitionNo`, `PR_PrjNo`, `PR_PrjName`, `PR_CreaterName`, `PR_CreateDate` |
| PR Application | Page script | GET | `/WebTP/PurchaseRequisition/JS/Index` | None | observed | `captures/raw/purchase_requisition_index.js` |
| PR Application | New → project selection | navigation | `/WebTP/PurchaseRequisition/ProjectSelection` (via `iPSA.GoToUrl`) | write-prep UI | static-only / blocked-write if POST later | index JS |
| PR Application | Edit row | navigation | `/WebTP/PurchaseRequisition/Edit/{id}` | write-prep UI | static-only / blocked-write if POST later | index JS `ajax-data` |
| PR Application | Delete row | AJAX (default GET) | `/WebTP/PurchaseRequisition/Delete/{id}` | **Mutating** | blocked-write / static-only | index JS `$.ajax('/WebTP/PurchaseRequisition/Delete/'+id)` |

Filter fields on Index form: `PR_PrjNo`, `PR_RequisitionNo`, `btnSearch`.

Grid columns observed (labels only): 项目, 申请单编号, 操作, 单据状态.

## Purchase requisition — other views (nav only so far)

| View | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- |
| Approval | GET | `/WebTP/PurchaseRequisition/ApprovalIndex` | Unknown until captured | pending / live-blocked | auth HTML nav |
| Adjustment | GET | `/WebTP/PurchaseRequisition/AdjustIndex` | Unknown until captured | pending / live-blocked | auth HTML nav |
| Revocation | GET | `/WebTP/PurchaseRequisition/RevocationIndex` | Unknown until captured | pending / live-blocked | auth HTML nav |
| Search | GET | `/WebTP/PurchaseRequisition/SearchIndex` | Unknown until captured | pending / live-blocked | auth HTML nav |

## Attachments

| Area | UI action | Method | Endpoint | Side effect | State | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Attachment | Upload script | GET | `/WebTP/Attachment/js/Upload` | None | observed (script load) | auth HTML, `captures/raw/attachment_upload.js` |
| Attachment | Upload | POST | `/WebTP/Attachment/Upload/` | **Mutating** | blocked-write / static-only | attachment_upload.js |
| Attachment | Delete | (from script) | `/WebTP/Attachment/Delete/` | **Mutating** | blocked-write / static-only | attachment_upload.js |
| Attachment | Download | GET | `/WebTP/Attachment/Download/` | None (read content) | static-only | attachment_upload.js |

## Shared client libraries

| Asset | Role | State | Evidence |
| --- | --- | --- | --- |
| `/WebTP/Scripts/iPSA_elle.min.js` | `iPSA.GoToUrl`, dialogs, appPath=`/WebTP` | observed | `captures/raw/ipsa_elle.min.js` |
| `/WebTP/Scripts/jquery.form.min.js` | `ajaxSubmit` for filters/pager | observed | `captures/Scripts_jquery.form.min.js` |
| `/WebTP/Scripts/MicrosoftAjax.js` | ASP.NET AJAX | observed | `captures/Scripts_MicrosoftAjax.js` |

## Safety notes for implementers

1. `GET /WebTP/PurchaseRequisition/Delete/{id}` is a **write**. Policy must block it from live transport.
2. Unknown endpoints default to deny until classified.
3. Successful login capture is the next required evidence gap; failed-login artifacts are marked `superseded` for the success path only (still useful for error UX).
4. Do not put cookie values, passwords, employee names, project numbers, or attachment bodies in this matrix.
5. Only the application `Index` view is live-enabled. The other four views return `NOT_CAPTURED` until runtime evidence exists.
