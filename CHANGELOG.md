# Changelog
## 0.2.7 — 2026-11-26

**Namespaced login blueprint (flask-campus#47)**: `OAuthLoginManager`
gains `login_prefix` (or the `LOGIN_URL_PREFIX` env), mounting
`/login`, `/finalize_login` and `/logout` under the app's namespace
instead of the host root. Gateway-routed consumer apps (campus#1274)
need this: four apps share the root-level `/login`, which a
path-dispatching gateway cannot route. Default unchanged — unset keeps
the standalone root mount, so existing consumers are unaffected until
they opt in. `login_required`/circle-gate redirects and the OAuth
callback URL follow the prefix automatically; `finalize_login`'s
no-`next` fallback lands on the namespace root instead of the shared
host's `/`.

## 0.2.6 — 2026-11-26

Pure repin release: campus-model v0.2.6 → **v0.2.7** (naive API-key
expiry normalization, campus#1273) and campus-api-python
v0.11.1 → **v0.11.2** (the matching repin). No code changes; the
wheel-metadata pin rule requires a rebuild here before consumers can
lock the new pins.

## 0.2.5 — 2026-11-25

Fix train for the prod sign-in loops (classroom#153):

- **Framework-owned Campus (#40, PR #41)**: `OAuthLoginManager` drives
  context push, the login flow, logout and circle-membership probes on
  its own Campus instance instead of the host's shared client — a host
  running `with_user_session()` blocks from gthread request threads can
  no longer displace the framework's ambient credential (the
  #854/#1055 session-flow denials behind the /login loops).
- **Shared re-auth classifier (#1096, PR #42)**:
  `context.is_reauth_required()` codifies the identity-vs-capability
  contract and the three re-auth error shapes; exported at package
  level, contract documented on `login_required`.
- Inline rationale at the framework-campus call sites so the isolation
  is not simplified away later.
- SDK pin → campus-api-python v0.11.1 (#189 context-local session
  credentials — the root-cause fix).
## 0.2.4 — 2026-11-23

Pure repin release: campus-model v0.2.5 → **v0.2.6** (VenueBooking.status
approval lifecycle) and campus-api-python v0.10.12 → **v0.10.13**
(the matching repin, campus#1217). No code changes; the
wheel-metadata pin rule requires a rebuild here before consumers can
lock the new pins.

## 0.2.3 — 2026-11-22

Pure repin release: campus-api-python v0.10.11 → **v0.10.12**
(sdk#183 — explicit `?resolve=` on every membership probe). No code
changes; the wheel-metadata pin rule requires a rebuild here before
consumers (the campus suite) can lock the new SDK.

## 0.2.2 — 2026-11-22

Transitive membership resolution (campus#1202, with campus#1203 and
campus-api-python v0.10.11):

- `circles.is_member` — the engine behind `circle_member_required` /
  `circle_admin_required` — now answers through the server's shared
  membership resolver (`members.resolve`, the gate-parity probe)
  instead of the direct-only `members.get`. Membership held through a
  seeded group now passes the view gate exactly as it passes
  campus.api's 403 wall; previously the gate could 403 a group-only
  member the API would admit. SDK pin repinned to
  campus-api-python v0.10.11 (requires the SDK release: an SDK bump
  rebuilds this wheel's metadata).

## 0.2.0 — 2026-11-19

**BREAKING** — audit namespace reorg (flask-campus#31, unfreezes the
campus#970/#993 import-path freeze):

- The dist no longer ships any `campus.audit.*` portion. The middleware
  moved to `campus.flask_campus.audit` and its HTTP client moved to the
  SDK as `campus_python.audit` (one owner per import prefix;
  `campus.audit` is now campus-suite's service alone).
- `init_app` renamed `init_tracing`. Consumer setup becomes:

  ```python
  from campus.flask_campus import audit
  audit.init_journeys(app)
  if env.get_flag("AUDIT_TRACING_ENABLED", False):
      audit.init_tracing(app)
  ```

- Roles catalogue (`campus.audit.roles`) → `campus_python.audit.roles`;
  `AuditClient`/`campus.audit.http` → `campus_python.audit` /
  `campus_python.audit.http` (campus-api-python v0.10.9).
- flask_campus README: fixed the stale "apps install campus-suite"
  line (post-#993 apps pin flask-campus, never the suite).

## 0.1.21 — 2026-11-19


`push_context` degrades ANY session/login resolution failure to
anonymous (flask-campus#28): a cookie whose session is gone (404),
denied (#854 owning-client checks), or unreachable (gateway 502 ->
MalformedResponseError) now pops the stale reference and continues as
logged-out, so the app's auth gate redirects to login. Previously only
404 was handled; any other error propagated out of the app-wide
before_request hook and 500'd every route — including /logout —
bricking the browser until the cookie was manually cleared (2026-11-19
dev incident: a gateway outage locked out every signed-in browser).

## 0.1.18 — 2026-11-17

`ApiKeys.new()` gains `trace_user_id`/`trace_client_id` bind params
(campus#944): opaque trace-read scoping values the audit service
enforces server-side; required server-side for read-scoped mints,
omitted = unscoped. The admin portal's key-mint UI passes these
through from its form (campus-admin). Binds are mint-time only.
## 0.1.17 — 2026-11-17

Pin rebuild per the collision rule: campus-model v0.2.2 → v0.2.3
(APIKey trace-read scoping binds, campus#944 / campus-model#13) +
campus-api-python v0.10.5 → v0.10.6 (trace-read bind mint params,
same wave). No code changes.

## 0.1.16 — 2026-11-17

Pin rebuild per the collision rule: campus-common v0.5.0
(from_exception static-message fix, campus#1128) + campus-api-python v0.10.5
+ campus-model v0.2.2 (rebuilds of the same bump). No code changes.

## 0.1.15 — 2026-11-17

Policy sweep for the producer-side redaction surface
(flask-campus#20; environment-access-policy 2026-11-17 §3 — campus.audit
records scrub all PII and sensitive secrets; deviations tracker
campus#1131).

- `redact_sensitive` extended: `authorization_code`, `otp`, and
  `user_code` (bearer-equivalent while their flow is live) join the
  sensitive-key set; person names (`name`, `full_name`, `given_name`,
  `family_name`) are redacted as PII; email keys (`email`, `*_email`)
  are value-aware — workspace-domain addresses pass through
  (college-issued identifiers are not PII, campus#1125 ruling) and
  everything else under them is masked, failing closed on malformed
  values. Bare `_code`/`_key` stays deferred to #806.
- Header-supplied journey names (`X-Journey-Name`) are character-filtered
  to a safe display set before landing in span tags — they are
  client-forgeable free text and could smuggle PII/markup into the
  stored trail.
- The audit HTTP client's ingestion-failure warning now logs status and
  content-length only; the response body (which can echo offending span
  content) moved to DEBUG, below the default level.
- `redact_sensitive` is re-exported as public interface of
  `campus.audit.middleware` (interface manifest regenerated): the audit
  service and the retroactive scrub script on campus reuse the same key
  list, so keyed redaction cannot drift between writer and scrubber.
- New redaction unit tests in-repo (`tests/unit/audit/test_redaction.py`)
  so a silent key-list regression fails here, not only downstream.
- Pin rebuild per the collision rule: campus-common v0.4.0 (error-surface
  scrubs, campus-common#8) + campus-api-python v0.10.4 + campus-model
  v0.2.1 (rebuilds of the same bump).

## 0.1.14 — 2026-11-16

Pin rebuild for the venues sweep (campus#1099, flask-campus#18):
campus-model v0.1.7 -> v0.2.0 (documents-backed `Venue` reshape,
campus-model#9) + campus-api-python v0.10.2 -> v0.10.3 (venues client,
campus-api-python#171). No code changes — the pins move so shared
lockfiles with the suite stay resolvable per the URL-pin collision
rule.

## 0.1.13 — 2026-11-14

chore(deps) + probe switch: campus-api-python pin v0.10.1 -> v0.10.2
(the membership probe client, campus-api-python#170 /
campus#1098's GET /circles/{id}/members/{member_id}/). \`circles.is_member\`
switches from the members.list() snapshot to the probe — the server's
answer to "is this principal in this circle", so client checks keep
resolving membership in exactly one place when campus.api adds access
tiers or transitive resolution. Missing-circle denial and the 60s
MembersCache are unchanged.

## 0.1.12 — 2026-11-14

feat: circles-backed view gates — `OAuthLoginManager.circle_member_required(circle)`
and `circle_admin_required(resource)` (sugar for the `<resource>.admin`
role circle). The gate answers "is the signed-in user in this circle"
the same way campus.api's own enforcement does (user ID = email, exact
match against the circle's members), resolved as the user via
`with_user_session`, cached process-local per (circle, user) for 60s.
Fail-closed: signed-out redirects to sign-in, non-members get 403, a
missing circle denies with a warning (bootstrap problem), and a
backend failure gets 503 — never a misleading 403, never fail-open.
View-level gating only: campus.api stays the enforcement point.
Mechanism note — membership currently resolves through
`members.list()` because the pinned campus-api-python (v0.10.1)
predates `CircleMembers.get`; switch the lookup to the probe
(campus-api-python#170) when the pin bumps past 0.10.2. Additive
(strict-0.x: patch bump); interface manifest unchanged — both gates
are methods on the already-exported `OAuthLoginManager`.

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
