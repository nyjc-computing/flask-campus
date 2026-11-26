# Login & sessions

`OAuthLoginManager` is the one object that wires Campus identity into a
Flask app. It bundles three things:

1. an **auth blueprint** with `/auth/login`, `/auth/finalize`, and
   `/auth/logout` views (Google federated login against Campus Auth);
2. a **before-request context hook** that resolves the session cookie
   to a user object and pushes it onto `flask.g.user` (via
   `campus.flask_campus.context.push_context`, with a process-local
   `UserCache` so warm requests skip upstream calls);
3. **view decorators** for gating: `login_required`,
   `circle_member_required`, and `circle_admin_required`.

```python
login_manager = OAuthLoginManager(
    default_endpoint="routes.landing",   # redirect target after login/logout
    login_scopes=None,                   # default ["campus.profile"]
)
login_manager.init_app(app)              # Flask app or blueprint
```

`login_scopes` requests management scopes for the minted user token
(#865/#883 two-leg model). Apps that pass anything beyond the default
must extend their OAuth client's `allowed_scopes` allowlist **before**
deploying — campus.auth validates against that cap fail-closed.

## How a request gets its user

- Signed-in: `flask.g.user` is a Campus user object; `g.user.email` is
  also the name of the user's own circle (their identity in the access
  model). Snapshots in `UserCache` are rehydrated per hit, so callers
  cannot poison the cache by mutating `g.user`.
- Signed-out: `flask.g.user` is absent (`getattr(flask.g, "user",
  None)` is the idiom). Gated views redirect to sign-in with
  `?next=` the original path; ungated views run anonymously.
- The SDK's legacy `flask.session` reads stay compatible:
  `install_session_provider()` points the SDK's sessionstore at the
  same session keys (the login manager installs this for you).

## Gating reference

| Decorator | Effect |
|---|---|
| `login_required` | redirect signed-out users to sign-in |
| `circle_member_required(circle)` | + 403 for signed-in non-members, 503 if the circles backend is unreachable (fail closed) |
| `circle_admin_required(resource)` | sugar for `circle_member_required(f"{resource}.admin")` |

All three fold the sign-in redirect in, so decorator stacking order
never matters. Enforcement stays in campus.api: a circle gate renders
the right UI before the API's 403 wall — it must never be the only
protection for data.

## Gateway namespaces (X-Forwarded-Prefix)

Since 0.2.8, `init_app` installs **PrefixFix**, a small WSGI middleware
that moves the campus gateway's `X-Forwarded-Prefix` header into
`SCRIPT_NAME` (stripped from `PATH_INFO`; the header is consumed, so
nested forwarding middleware cannot double-strip). Apps are therefore
**root-first** — routes defined without any namespace — and the campus
gateway mounts them under its namespaces (`/timetable/`, `/apps/`,
...) with zero per-app configuration: behind the gateway every
`url_for` emits namespace-aware URLs (`/<ns>/…`), while on the origin
domain `SCRIPT_NAME` is empty and everything stays root-relative.

`login_prefix` (flask-campus#47) is **superseded** for gateway
deployments — do not combine the two. PrefixFix strips the namespace
the static mount baked into the route rules, so the prefixed routes
never match and emitted URLs double (`SCRIPT_NAME` + mount).
`login_prefix` remains valid only for header-less deployments: a
foreign path-dispatching proxy that does not set
`X-Forwarded-Prefix`, or a standalone deployment that wants a
namespaced auth surface on its own origin.

Normative spec: [URL namespacing](https://github.com/nyjc-computing/campus/blob/weekly/docs/url-namespacing.md).

## Journeys on login flows

Services whose browser surface **is** the login flow (campus.auth) or
that serve no browser pages (campus.api) initialize journeys with
`mint_on_navigation=False` so navigation events don't mint journeys;
they adopt forwarded `X-Journey-ID` headers only. See
[Audit middleware](audit.md).
