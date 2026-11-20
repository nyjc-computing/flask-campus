# Flask Campus

The Flask consumer layer for Campus apps: login/session context, request
glue, the `@journey` decorator, and the audit middleware (request-span
tracing + action journeys). Part of the Campus distribution split
([campus#970](https://github.com/nyjc-computing/campus/issues/970)) —
apps install flask-campus and never a campus service or the
storage/webauth core.

--8<-- "snippets/version.md"

```python
import flask
from campus.common import env
from campus.flask_campus import OAuthLoginManager, journey, audit

app = flask.Flask(__name__)
login_manager = OAuthLoginManager(default_endpoint="routes.landing")
login_manager.init_app(app)
audit.init_journeys(app)                     # episode plumbing: always on
if env.get_flag("AUDIT_TRACING_ENABLED", False):
    audit.init_tracing(app)                  # span capture: opt-in

@app.post("/assignments/<assignment_id>/send")
@login_manager.circle_member_required("timetable")
@journey("campus.submissions.submit")
def send_to_classroom(assignment_id: str): ...
```

## What's in the box

- **[Login manager & session context](login.md)** — `OAuthLoginManager`
  wires Google federated login into any Flask app and pushes the
  signed-in user onto `flask.g` per request; view decorators gate on
  sign-in and circle membership.
- **[Audit middleware](audit.md)** — `audit.init_tracing` captures
  request-response spans for the audit trail; `audit.init_journeys`
  groups related requests into user-action journeys; `@journey` labels
  them.
- **Views & request glue** — typed view helpers
  (`unpack_request`, `validate_json_response`, ...), request/response
  types, and the OpenAPI generator core. See the
  [reference](reference/glue.md).

Import paths are identical to the pre-split monorepo — the portions
merge at import time. Span ingest, trace queries, and audit API-key
management ship with the
[SDK](https://nyjc-computing.github.io/campus-api-python/) as
`campus_python.audit`; this dist ships no `campus/audit` portion.

## Boundary rules (enforced in this repo's tests)

- The consumer layer never imports campus services (`campus.auth`,
  `campus.api`, `campus.audit`, `campus.yapper`, `campus.deploy`,
  `campus.config`) — services import the consumer layer, never the
  reverse (#980).
- It never imports the storage/webauth core (apps never touch them,
  #992).

## Where to go next

| If you want to... | Read |
|---|---|
| wire up login and gated views | [Getting started](getting-started.md) |
| understand sessions, `g.user`, circles | [Login & sessions](login.md) |
| capture audit spans and journeys | [Audit middleware](audit.md) |
| look up any function | [Reference](reference/login-manager.md) |
