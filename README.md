# flask-campus

The Flask consumer layer for Campus apps, shipped as a PEP 420 namespace
portion: `campus/flask_campus` — request/response glue, login/session
context (`OAuthLoginManager`), trace/journey propagation, the audit
middleware (`campus.flask_campus.audit`: tracing + journeys), `@journey`,
and the OpenAPI generator core.

User-facing documentation lives at
<https://nyjc-computing.github.io/flask-campus/> (built from `docs/` —
see AGENTS.md for the docs workflow and the version-bump docs rule).

Part of the Campus distribution split
([nyjc-computing/campus#970](https://github.com/nyjc-computing/campus/issues/970)):
apps install campus-common + campus-model + flask-campus (+ the SDK) and
never a campus service or the storage/webauth core. The audit *service*
(`campus.audit` — routes, resources, helpers) lives in campus-suite, and
since flask-campus#31 the caller-side audit *client* ships with the SDK
as `campus_python.audit`: one owner per import prefix.

## Install

Release wheels are consumed as version-pinned URLs (Poetry):

```toml
flask-campus = {url = "https://github.com/nyjc-computing/flask-campus/releases/download/v0.2.8/flask_campus-0.2.8-py3-none-any.whl"}
```

Dependencies: `flask`, `werkzeug`, `requests`, the
[campus-common](https://github.com/nyjc-computing/campus-common) and
[campus-model](https://github.com/nyjc-computing/campus-model) wheels,
and [campus-api-python](https://github.com/nyjc-computing/campus-api-python)
(the SDK: framework-free session/tracing seams **and** the audit client
`campus_python.audit` the middleware ingests through).

## Usage

```python
from campus.common import env
from campus.flask_campus import OAuthLoginManager, journey, audit

app = flask.Flask(__name__)
login_manager = OAuthLoginManager(default_endpoint="routes.landing")
login_manager.init_app(app)
audit.init_journeys(app)                     # episode plumbing: always on
if env.get_flag("AUDIT_TRACING_ENABLED", False):
    audit.init_tracing(app)                  # span capture: opt-in

@app.post("/assignments/<assignment_id>/send")
@journey("campus.submissions.submit")
def send_to_classroom(assignment_id: str): ...
```

Span ingest, trace queries, and audit API-key management go through the
SDK:

```python
from campus_python.audit import AuditClient

client = AuditClient()
client.traces.new(span_data)
```

## Boundary rules (enforced in this repo's tests)

- The consumer layer never imports campus services (`campus.auth`,
  `campus.api`, `campus.audit`, `campus.yapper`, `campus.deploy`,
  `campus.config`) — services import the consumer layer, never the
  reverse (#980).
- It never imports the storage/webauth core (apps never touch them,
  #992).
- The dist ships no `campus/audit` portion at all (#31): the audit
  middleware lives under `campus.flask_campus.audit`; tests guard the
  one-owner-per-prefix invariant.

## Versioning

Strict 0.x: **patch** releases (`0.x.Y`) are additive or fix-only —
downstream flows them in at the next relock; **minor** releases
(`0.Y.0`) are the breaking line. Every interface change ships with a
CHANGELOG entry, a manifest regen
(`scripts/generate_interface_manifest.py`), and the version bump the
diff dictates. The interface manifest is enforced in CI.

## Development

```bash
poetry install
poetry run python -m unittest discover -s tests -t . -v
poetry run ruff check .
poetry run python scripts/generate_interface_manifest.py --check
```

Releases: merge to `main` (ruleset-protected PRs), tag `vX.Y.Z`,
`poetry build`, attach `dist/flask_campus-X.Y.Z-py3-none-any.whl` to
the GitHub release.
