# Capture handling

Files already present at this directory's root predate project setup and are
preserved as original observations. New sensitive captures go under `raw/` and
must not be committed. Only manually reviewed and redacted material goes under
`redacted/`.

Redaction must remove or replace:

- usernames and passwords
- cookie, session, authentication ticket, and CSRF token values
- employee names, identifiers, contact details, and free-text business data
- attachment contents unless they are explicit challenge fixtures

Keep method, URL path, parameter names, status code, content type, cookie
attributes, and response schema whenever they are needed for replay.

