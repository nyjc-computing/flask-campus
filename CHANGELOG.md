# Changelog
## 0.1.8 — 2026-11-07

Dependency rebuild: campus-api-python pin v0.8.1 -> v0.8.2 (files[id]
management surface, campus-api-python#125/#164). No code changes -
direct-URL deps bake the pinned wheel URLs into this distribution's
metadata, so the bump requires a rebuild here per the pin-rebuild
contract.


## 0.1.7 — 2026-11-07

feat: login flows can request scopes beyond `campus.profile` (#8).
`context.start_login` gains a `scopes` parameter and
`OAuthLoginManager` a `login_scopes` parameter (threaded through the
auth blueprint's /login view); None keeps the `["campus.profile"]`
default. Apps whose features need management scopes on the signed-in
user's token — the #865/#883 two-leg capability leg, e.g.
nyxchange-timetable-v2's `timetable:admin` — no longer monkeypatch
`start_login`. campus.auth validates the requested scopes against the
minting client's `allowed_scopes` cap fail-closed, so extend the
client cap BEFORE deploying a wider request. Additive (strict-0.x:
patch bump); interface manifest unchanged in shape.


## 0.1.6 — 2026-11-07

Dependency rebuild: campus-api-python pin v0.8.0 -> v0.8.1 and
campus-model pin v0.1.4 -> v0.1.5 (`TimetableEntry` label persistence,
campus-model#5 / campus#1080). No code changes - direct-URL deps bake
the pinned wheel URLs into this distribution's metadata, so the bump
requires a rebuild here per the pin-rebuild contract.


## 0.1.4 — 2026-11-05

Dependency rebuild: campus-model pin v0.1.3 -> v0.1.4 (new
`HostedApp` model, campus-classroom#81 / campus#1066). No code
changes - direct-URL deps bake the pinned wheel URL into this
distribution's metadata, so the bump requires a rebuild here per
the pin-rebuild contract.


## 0.1.0 — 2026-11-04

Initial release: the Flask consumer layer carved from the campus
monorepo (campus#993, part of the #970 distribution split). Four PEP 420
namespace portions:

- `campus/flask_campus` — request/response glue, OAuthLoginManager +
  login/session context (`context`, `propagation`), `@journey`, the
  pure OpenAPI generator core (`openapi`), parameter/utils/types.
- `campus/audit/middleware` — tracing + journeys + identity middleware
  (`init_app`, `init_journeys`).
- `campus/audit/client` — `AuditClient`, the caller-side audit API.
- `campus/audit/http` — the HTTP client seam (`DefaultClient`,
  `JsonClient` protocol).

First split of the audit package: the audit *service* (routes,
resources, web UI, its `init_app`) stays in campus-suite; apps install
flask-campus and never a campus service. Import paths are unchanged —
`from campus.flask_campus import OAuthLoginManager` and
`from campus.audit.middleware import init_app` keep working via the
portion merge.

One carve adaptation: `flask_campus.login_manager` resolves the auth
base URL via `campus.common.services.get_base_url` (the
`campus.config` compatibility shim no longer exists in this dist).

Interface kit from day one: closed-world `__all__` per package,
checked-in interface manifest, boundary guard (consumer layer never
imports services or the storage/webauth core), strict-0.x versioning
(README/CHANGELOG).

## 0.1.1 — 2026-11-04

- New `campus.audit.roles` module (moved from the campus-suite audit
  service): the audit key-role/scope catalogue (`list_roles`,
  `resolve_role`, `resolve_scopes`). Additive (patch) — a downstream
  consumer (campus-admin's key management) needs the same vocabulary
  the service mints against, and the suite's copy is removed in its
  next minor release (no-double-ship rule: never install
  campus-suite + flask-campus ≥0.1.1 together until campus-suite
  ≥0.8.0, which stops shipping the module).

## 0.1.2 — 2026-11-04

- `campus.audit.roles` becomes a package (`campus/audit/roles/__init__.py`)
  instead of a bare module. Import paths are unchanged; the wheel portion
  for the shared `campus/audit/` subnamespace stays subpackage-only, which
  keeps pyright's pkgutil-style portion resolution working for consumers
  (a loose `.py` file in the portion made pyright drop the whole merged
  portion, breaking `campus.audit.middleware` imports downstream too).
