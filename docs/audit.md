# Audit middleware

`campus.flask_campus.audit` gives any Flask Campus app an audit trail:
request-response spans for the audit service, and **action journeys**
(#828) that group related requests — a page load, its XHRs and form
posts, and the server-to-server calls they spawn — into one
user-initiated episode.

The middleware moved here from `campus.audit.middleware` in
flask-campus#31: it is request-scoped Flask glue, so it lives in the
`campus.flask_campus` layer, while its HTTP client (span ingest, trace
queries, API keys) ships with the SDK as `campus_python.audit`.

```python
from campus.common import env
from campus.flask_campus import audit

audit.init_journeys(app)                     # always on — cheap plumbing
if env.get_flag("AUDIT_TRACING_ENABLED", False):
    audit.init_tracing(app)                  # opt-in per deployment
```

## Span capture: `init_tracing`

Registers before/after request hooks that open a span per request and
ingest it asynchronously through `campus_python.audit`:

- **Trace identity**: reuses `X-Request-ID` as trace id; the response
  echoes it back for correlation.
- **Rate gate**: a fail-closed gate (opt-in with
  `AUDIT_TRACING_FAIL_CLOSED=1`) rejects requests rather than dropping
  spans when ingest is rate-limited (#831).
- **Warnings**: WARNING+ records logged during a request ride on the
  span as redacted, capped `tags.warnings`, so incident triage doesn't
  need Railway stdout (#26). `redact_sensitive` is the shared
  producer-side scrub primitive — the same key list the audit service
  and retroactive scrub scripts use (#20).
- **Don't instrument the audit service itself** — spans ingesting
  spans is infinite recursion.

Static files are skipped unless `AUDIT_TRACING_INCLUDE_STATIC` is set.

## Action journeys: `init_journeys` and `@journey`

`init_journeys` adopts or mints a journey id per request and keeps the
`campus_action_journey` cookie alive, so a user episode stays one
journey in the audit UI. Pass `mint_on_navigation=False` for services
whose browser surface is the login flow itself (campus.auth) or that
serve no browser pages (campus.api) — they adopt forwarded
`X-Journey-ID` headers only.

The [`@journey`][campus.flask_campus.journeys.journey] decorator marks
a view as a named action inside the journey:

```python
@app.post("/assignments/<assignment_id>/send")
@journey("campus.submissions.submit")     # label convention: campus.<subject>.<action>
def send_to_classroom(assignment_id: str): ...
```

It works standalone too (without the journeys middleware): adopt the
action cookie, else mint. `fresh=True` forces a new journey — a hard
action boundary mid-episode.

## Propagation

Either initializer installs the propagation seam: outbound SDK calls
made while a request is being handled carry the span/journey/device
headers this middleware stashed, keeping the audit waterfall intact
across services. To instrument **your own** non-SDK `requests.Session`,
use the SDK's
[`instrument_requests_session`](https://nyjc-computing.github.io/campus-api-python/tracing/).

## Reference

- [Audit middleware reference](reference/audit.md) — `init_tracing`,
  `init_journeys`, `redact_sensitive`
