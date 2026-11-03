# campus.flask_campus

**The middleware layer for Flask-based Campus apps.** flask_campus owns
everything request-scoped: the OAuth login flow, client-side session
storage, the auth context push, trace/journey propagation, and request
parsing/validation utilities. It orchestrates the `campus_python`
client's pure API calls — the SDK itself is framework-free and holds no
session state (see [Session management and the campus_python
boundary](#session-management-and-the-campus_python-boundary)).

Apps install `campus-suite` (this package) plus `campus-api-python`;
the CLI and other non-Flask clients use `campus_api_python` alone.

## Installation

```python
from campus.flask_campus import (
    HtmlResponse,
    JsonResponse,
    OAuthLoginManager,
    get_user_agent,
    get_request_headers,
    get_request_payload,
    unpack_into,
    unpack_request,
    validate_request_and_extract_json,
    validate_request_and_extract_urlparams,
    validate_json_response,
)
```

## OAuth Login Manager

The `OAuthLoginManager` provides seamless OAuth authentication integration with Flask using the Campus Admin Portal.

### Basic Setup

```python
from flask import Flask
from campus.flask_campus import OAuthLoginManager

app = Flask(__name__)
app.secret_key = "your-secret-key"

# Initialize the login manager
login_manager = OAuthLoginManager(
    campus_client=None,  # Uses default Campus client
    default_endpoint="index"  # Redirect here after login/logout
)
login_manager.init_app(app)

@app.route("/")
def index():
    return "Welcome!"
```

### Using a Custom Campus Client

```python
import campus_python

# No timeout needed: the client defaults to 60s (override per-call or
# via CAMPUS_CLIENT_TIMEOUT).
campus = campus_python.Campus()
login_manager = OAuthLoginManager(
    campus_client=campus,
    default_endpoint="dashboard"
)
login_manager.init_app(app)
```

### Protecting Routes

Use the `@login_manager.login_required` decorator to protect routes that require authentication:

```python
@app.route("/dashboard")
@login_manager.login_required
def dashboard():
    # Access the authenticated user via flask.g.user
    return f"Hello, {flask.g.user}"
```

### Authentication Endpoints

`init_app` registers the `/login`, `/finalize_login`, and `/logout`
blueprint, the app-wide context push, and the SDK session-id provider
(see [Session management](#session-management-and-the-campus_python-boundary)).

Once initialized, the following endpoints are automatically available:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET | Initiates OAuth login flow. Accepts optional `?next=/path` parameter for post-login redirect |
| `/finalize_login` | GET | OAuth callback endpoint (internal use) |
| `/logout` | GET | Signs out from Campus Admin Portal |

### Authentication Flow

1. User visits a protected route
2. Redirected to `/login` (with original path saved in `next` parameter)
3. Redirected to Campus OAuth authorization page
4. After successful authorization, redirected to `/finalize_login`
5. Access token is stored and login session is created (30-day expiry)
6. Redirected to the original destination

### Session management and the `campus_python` boundary

flask_campus owns the client-side session state; `campus_python` owns
transport. Concretely:

- **Storage.** The auth-session and login-session ids live in
  `flask.session` under `campus_session_id` / `logins_login_id`
  (`campus_python.auth.v1.sessions.SESSION_STORAGE_KEY` /
  `logins.LOGIN_STORAGE_KEY`). `OAuthLoginManager.init_app` installs
  these keys as the SDK's session-id provider
  (`campus_python.auth.v1.set_session_id_provider`), so SDK resource
  methods resolve ids without ever touching a web-framework session
  store themselves.
- **Context push.** `context.push_context` (an app-wide
  `before_request` hook) resolves `flask.g.user` / `flask.g.device`,
  with a 60-second per-session user cache (#57). Static endpoints skip
  it (#689).
- **Login flow.** `context.start_login` / `finalize_login` /
  `logout` build the authorize redirect, exchange the code, create the
  login session (device id rides the auth session, #825), and revoke
  on sign-out — all via the SDK's pure API calls
  (`sessions.new`, `exchange_code_for_token`, `logins.new`, …).
- **The user-session token lane stays in the SDK.**
  `campus.with_user_session()` turns the current login session into an
  authenticated API client (bearer setup + 401 auto-refresh). That is
  transport orchestration, not middleware: the session id reaches it
  through the provider above, and the SDK holds no state. Classroom is
  currently the only consumer (it is the only app making
  user-principal campus API calls; admin/profile/nyxchange operate
  app-scope).

Hosts that need the ids outside a Flask request context read the same
keys themselves — the SDK's resource methods also accept explicit
`session_id` arguments.

### Trace propagation

`campus.audit.middleware.init_app` / `init_journeys` install
`campus.flask_campus.propagation` as the SDK's request-context
provider (`campus_python.tracing.set_request_context_provider`):
outbound SDK calls made while your app handles a request then carry
the span/journey/device headers the audit middleware stashed on
`flask.g` (`X-Request-ID`, `X-Parent-Span-ID`, `X-Journey-ID`,
`X-Journey-Name`, `X-Campus-Device`), so the receiving campus service
records them as child spans of the caller (the audit waterfall). The
header names mirror `campus_python.tracing` — keep the two in
lockstep. Without a provider the SDK sends only the process boot
journey; audit.middleware installs one, so traced apps get full
waterfalls by default.

### Action journeys

`@flask_campus.journey("campus.submissions.submit")` names a
user-action episode for the audit UI (campus#828/#840); the journeys
middleware keeps the `campus_action_journey` cookie alive across the
episode's requests and XHRs. See `journeys.py` and
`docs/audit-tracing.md`.

### Configuring the OAuth Callback URL

`OAuthLoginManager` builds the `/finalize_login` callback URL from the app's
canonical public origin, resolved by `campus.common.utils.url.canonical_origin()`:

1. `PUBLIC_URL` env var, if set — a full origin (`scheme://host[:port]`), e.g.
   `PUBLIC_URL=https://classroom.example.com`
2. `https://{HOSTNAME}` — legacy fallback for deployments that only set
   `HOSTNAME` (deprecated; see issue #652)

For local development over plain HTTP, set the origin explicitly:

```dotenv
PUBLIC_URL=http://localhost:5000
```

and register `http://localhost:5000/finalize_login` in the Campus client's
`redirect_uris`. The explicit origin is also what keeps the OAuth `redirect_uri`
stable — it is never derived from the incoming request (see RFC 6749 §3.1.2.2).

GitHub Codespaces needs no configuration: `configure_for_codespace()` sets
`PUBLIC_URL` from the forwarded domain automatically.

## Request/Response Utilities

### `unpack_request`

Decorator that automatically unpacks Flask request data into function arguments based on type annotations.

- **GET requests**: Uses URL parameters
- **POST/PUT requests**: Uses JSON body

```python
from campus.flask_campus import unpack_request

@app.get("/search")
@unpack_request
def search(query: str, limit: int = 10):
    # query and limit are automatically extracted from URL params
    return {"results": [], "limit": limit}

@app.post("/users")
@unpack_request
def create_user(email: str, name: str, is_active: bool = False):
    # email, name, is_active are extracted from JSON body
    return {"email": email, "name": name, "is_active": is_active}
```

**Requirements:**
- All parameters must have type annotations
- Parameters must be keyword-argument compatible (no positional-only parameters)
- Missing required parameters raise `ValidationError`

### `unpack_into`

Manually unpack request arguments into a function's arguments.

```python
from campus.flask_campus import unpack_into

def process_data(name: str, count: int = 1):
    return {"name": name, "count": count}

# Unpack request data into the function
result = unpack_into(process_data, **request_args)
```

### `get_request_payload`

Extract JSON payload or URL parameters from the current Flask request.

```python
from campus.flask_campus import get_request_payload

@app.post("/process")
def process():
    data = get_request_payload()  # Returns dict
    # For GET requests: returns URL params
    # For POST requests: returns JSON body
```

### `get_request_headers`

Get request headers as a `campus.model.HttpHeader` dictionary.

```python
from campus.flask_campus import get_request_headers

@app.before_request
def log_headers():
    headers = get_request_headers()
    # Returns campus.model.HttpHeader (dict-like)
```

### `get_user_agent`

Get the User-Agent header from the current request.

```python
from campus.flask_campus import get_user_agent

@app.route("/info")
def info():
    ua = get_user_agent()
    return f"User-Agent: {ua}"
```

## Validation Utilities

### `validate_request_and_extract_json`

Validate request JSON body against a schema.

```python
from campus.flask_campus import validate_request_and_extract_json

def on_error(status, message):
    from flask import jsonify
    return jsonify({"error": message}), status

@app.post("/submit")
def submit():
    schema = {"title": str, "count": int}
    data = validate_request_and_extract_json(schema, on_error=on_error)
    # data is validated
    return {"status": "ok"}
```

### `validate_request_and_extract_urlparams`

Validate URL parameters against a schema.

```python
from campus.flask_campus import validate_request_and_extract_urlparams

@app.get("/search")
def search():
    schema = {"q": str, "page": int}
    params = validate_request_and_extract_urlparams(
        schema,
        on_error=on_error,
        strict=True  # All schema keys required
    )
    return params
```

### `validate_json_response`

Validate response JSON against a schema.

```python
from campus.flask_campus import validate_json_response
import requests

resp = requests.get("https://api.example.com/data")
schema = {"id": int, "name": str}

validate_json_response(
    schema,
    resp.json(),
    on_error=on_error,
    ignore_extra=True,
    error_status_code=502,
    error_message="External API error"
)
```

## Types

Type definitions for type hinting:

| Type | Definition |
|------|------------|
| `HtmlResponse` | `tuple[str, int]` |
| `JsonResponse` | `tuple[dict[str, Any], int]` |
| `JsonObject` | `dict[str, Any]` |
| `StatusCode` | `int` |

## Error Handling

The utilities raise standard errors:

- `ValidationError` - Raised when validation fails with structured field errors
- `InvalidRequestError` - Raised for malformed JSON payloads
- `RuntimeError` - Raised when functions are called outside a Flask request context

```python
from campus.common.errors import ValidationError, FieldError

try:
    data = get_request_payload()
except ValidationError as e:
    # e.errors contains list of FieldError objects
    for error in e.errors:
        print(f"{error.field}: {error.message} ({error.code})")
```
