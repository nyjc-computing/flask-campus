# flask-campus

The Flask consumer layer for Campus apps, shipped as PEP 420 namespace
portions: `campus/flask_campus` (request/response glue, login/session
context, `@journey`, OpenAPI generator core) and the caller-side audit
modules `campus/audit/{middleware,client,http}` (tracing + journeys
middleware, `AuditClient`, the HTTP client seam).

Part of the Campus distribution split
([nyjc-computing/campus#970](https://github.com/nyjc-computing/campus/issues/970)):
apps install campus-common + campus-model + flask-campus and never a
campus service or the storage/webauth core. The audit *service*
(routes, resources, web UI) stays in campus-suite — this is the first
split of the audit package, and the app-facing halves live here.

## Install

Release wheels are consumed as version-pinned URLs (Poetry):

```toml
flask-campus = {url = "https://github.com/nyjc-computing/flask-campus/releases/download/v0.1.0/flask_campus-0.1.0-py3-none-any.whl"}
```

Dependencies: `flask`, `werkzeug`, `requests`, the
[campus-common](https://github.com/nyjc-computing/campus-common) and
[campus-model](https://github.com/nyjc-computing/campus-model) wheels,
and [campus-python](https://github.com/nyjc-computing/campus-api-python)
(git dep — the SDK's framework-free halves provide the session/tracing
seams).

## Usage

```python
from campus.flask_campus import OAuthLoginManager, journey
from campus.audit.middleware import init_app, init_journeys

app = flask.Flask(__name__)
login_manager = OAuthLoginManager(default_endpoint="routes.landing")
login_manager.init_app(app)
init_app(app)          # tracing + rate gate + identity stamping
init_journeys(app)     # action-journey cookies
```

Import paths are identical to the pre-split monorepo — the portions
merge at import time.

## Boundary rules (enforced in this repo's tests)

- The consumer layer never imports campus services (`campus.auth`,
  `campus.api`, the audit service half, `campus.yapper`,
  `campus.deploy`, `campus.config`) — services import the consumer
  layer, never the reverse (#980).
- It never imports the storage/webauth core (apps never touch them,
  #992).
- `campus.flask_campus` must not import `campus.audit.*` at module
  scope (lazy at call time only, to avoid the package-init cycle).

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
