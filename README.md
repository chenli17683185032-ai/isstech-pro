# iSStech Purchase Requisition protocol replay

Browser-independent protocol documentation and a **read-only-first** HTTP facade
for the authorized CTF target:

- Business: `http://ipsapro.isstech.com/WebTP/PurchaseRequisition`
- Passport: `https://passport.isstech.com/`
- Local API: `http://127.0.0.1:8000` (`/docs`, `/openapi.json`)

## What works today

| Capability | Status |
| --- | --- |
| Evidence inventory + endpoint matrix | Yes (`docs/`) |
| Endpoint policy (deny-by-default, Delete-as-GET = write) | Yes |
| Pure HTTP login (mocked tests; live needs credentials) | Code yes / live HAR pending |
| Application Index list + detail + bounded attachment download | Yes (mocked; live parity pending) |
| Approval / adjustment / revocation / search | `NOT_CAPTURED` until runtime evidence exists |
| Write request **previews** (never sent) | Inferred builders; intercepted bodies pending |
| FastAPI `/v1` sessions, lists, attachments, previews | Yes |
| Automated tests | Run `uv run pytest -q`; exact count is recorded in final verification |

## Safety boundary

- Browser / CDP / Playwright / mitmproxy are **analysis-only**, not runtime deps.
- Live verification is read-only: page loads, filters, details, attachment downloads.
- Create / edit / delete / submit / approve / adjust / revoke / upload are **not**
  sent to the target. Use intercept+abort for capture; use `/v1/previews/*` locally.
- Passwords, cookie values, `.iPSA`, employee names, project numbers, and attachment
  bodies must not enter git, test logs, or committed OpenAPI examples.
- Raw captures: `captures/raw/**` (mode `0600`, gitignored). Redacted: `captures/redacted/`.

See `docs/scope.md`, `docs/architecture.md`, `docs/endpoint-matrix.md`,
`docs/vulnerability-report.md`, and `docs/final-verification.md`.

## Quick start

```bash
cd /Users/ethan/Documents/isstech
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests
uv run isstech-api
```

### Local API (examples)

```bash
# health
curl -s http://127.0.0.1:8000/health

# login — credentials only at runtime; response is a local Bearer token
curl -s http://127.0.0.1:8000/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"username":"USER","password":"PASS"}'

# list (application view)
curl -s 'http://127.0.0.1:8000/v1/purchase-requisitions?view=application' \
  -H "Authorization: Bearer $TOKEN"

# delete is preview-only
curl -s -X POST http://127.0.0.1:8000/v1/previews/purchase-requisitions/ID/delete \
  -H "Authorization: Bearer $TOKEN"
```

Error codes: `AUTH_EXPIRED`, `UPSTREAM_ERROR`, `PARSE_ERROR`, `WRITE_BLOCKED`, `BAD_REQUEST`, `NOT_CAPTURED`.

### Layout

```text
src/isstech_replay/
  api.py policy.py transport.py client.py auth.py
  request_builders.py session_store.py
  models/ parsers/ routes/
tests/                 # unit + API tests (redacted fixtures only)
captures/raw/          # gitignored originals
captures/redacted/     # commit-safe fixtures
docs/                  # architecture, matrix, vulns, verification, openapi path list
tools/first-commit.sh  # baseline commit helper if .git is locked in a sandbox
```

## Delivery order (from the handoff brief)

Evidence baseline → success login capture → pure HTTP login → safety policy →
endpoint matrix → read-only client → write previews → FastAPI → vulnerability
report → clean-environment acceptance.

**Remaining evidence-dependent steps:** (1) successful-login HAR under
`captures/raw/` with redacted protocol JSON; (2) credentialed pure-HTTP smoke;
(3) deep captures for four blocked views and intercepted write bodies; (4)
optional second-role IDOR checks.

## License / authorization

Use only against the authorized CTF / lab target described in the project brief.
Do not point this client at production systems without explicit permission.
