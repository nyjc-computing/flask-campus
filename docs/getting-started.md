# Getting started

## Install

Release wheels are consumed as version-pinned URLs (Poetry):

```toml
flask-campus = {url = "https://github.com/nyjc-computing/flask-campus/releases/download/v0.2.0/flask_campus-0.2.0-py3-none-any.whl"}
```

Dependencies: `flask`, `werkzeug`, `requests`, the
[campus-common](https://github.com/nyjc-computing/campus-common) and
[campus-model](https://github.com/nyjc-computing/campus-model) wheels,
and [campus-api-python](https://github.com/nyjc-computing/campus-api-python)
(the SDK: framework-free session/tracing seams **and** the audit client
`campus_python.audit` the middleware ingests through) — pip resolves
all of them from the URLs baked into the wheel metadata.

## Minimal app

```python
import flask
from campus.flask_campus import OAuthLoginManager

app = flask.Flask(__name__)
app.secret_key = "..."                 # sessions need a secret
login_manager = OAuthLoginManager(default_endpoint="routes.landing")
login_manager.init_app(app)

@app.route("/")
@login_manager.login_required
def index():
    return f"Hello, {flask.g.user.email}"
```

`init_app` registers an `auth` blueprint (`/auth/login`,
`/auth/finalize`, `/auth/logout`) and a before-request hook that
resolves the session cookie to `flask.g.user`. The default endpoint is
addressed as `blueprint.endpoint` (`routes.landing` = the `landing`
view in the `routes` blueprint).

## Configuration

The default `Campus` client resolves from the environment (see the
SDK's [configuration
guide](https://nyjc-computing.github.io/campus-api-python/getting-started/)):

| Variable | Used for |
|---|---|
| `CLIENT_ID` / `CLIENT_SECRET` | server-mode Campus client (login flows, API calls as the client) |
| `ACCESS_TOKEN` | bearer alternative for the audit client |
| `AUDIT_API_KEY` | audit span ingest (with tracing enabled) |
| `AUDIT_TRACING_ENABLED` | gate: call `audit.init_tracing` only when set |
| `AUDIT_TRACING_FAIL_CLOSED` | fail-closed audit rate gate (opt-in) |
| `AUDIT_TRACING_INCLUDE_STATIC` | capture spans for static-file requests (default off) |

Pass `campus_client=` to `OAuthLoginManager` to share an existing
`campus_python.Campus` instance instead.

## Adding gated views

```python
@app.route("/admin")
@login_manager.circle_member_required("campus-admin")
def admin_panel(): ...

@app.route("/timetable/edit")
@login_manager.circle_admin_required("timetable")   # gates on timetable.admin
def edit_timetable(): ...
```

Circle gates check membership through campus.api circles (cached 60s),
redirect signed-out users to sign-in, 403 non-members, and fail closed
with 503 if the circles backend cannot answer. Enforcement lives in
campus.api — these gates render the right UI, they are never the only
protection for data.

## Next steps

- [Login & sessions](login.md) — the full login lifecycle
- [Audit middleware](audit.md) — spans and journeys
- [Reference](reference/login-manager.md) — every public name
