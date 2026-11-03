# Changelog

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
