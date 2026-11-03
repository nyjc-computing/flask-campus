"""campus.audit.middleware.tracing

Tracing middleware implementation for capturing HTTP request-response spans.
"""

import concurrent.futures
import json
import logging
import time
import typing

import flask
import requests

from campus.audit.client import AuditClient
from campus.common import protocol, schema
from campus.common.protocol import PARENT_SPAN_ID_HEADER, TRACE_ID_HEADER
from campus.common.utils import uid

logger = logging.getLogger(__name__)

class ExecutorManager:
    """Manages executor lifecycle with clear ownership and state tracking.

    This class provides:
    - Explicit state tracking (created, running, shut down)
    - Idempotent shutdown operations
    - Safe executor recreation for tests
    - Clear error messages for lifecycle violations

    Lifecycle States:
    - Created: Manager exists but executor not yet initialized
    - Running: Executor is accepting tasks
    - Shut down: Executor has been shut down and won't accept new tasks

    Example:
        manager = ExecutorManager()
        executor = manager.get_executor()
        manager.shutdown(wait=True)
        manager.recreate()  # For tests that need a fresh executor
    """

    def __init__(self, max_workers: int = 2, thread_name_prefix: str = "audit_ingest"):
        """Initialize the executor manager.

        Args:
            max_workers: Maximum number of worker threads
            thread_name_prefix: Prefix for thread names
        """
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._shutdown = False
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix

    def get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create the thread pool executor.

        Returns:
            ThreadPoolExecutor instance for submitting tasks

        Raises:
            RuntimeError: If executor has been shut down
        """
        if self._shutdown:
            raise RuntimeError(
                "Executor has been shut down. Call recreate() to create a new executor."
            )

        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix=self._thread_name_prefix
            )

        return self._executor

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor idempotently.

        This method is safe to call multiple times. After the first call,
        subsequent calls are no-ops.

        Args:
            wait: If True, wait for pending tasks to complete
        """
        if self._executor is not None and not self._shutdown:
            self._executor.shutdown(wait=wait)
            self._shutdown = True

    def recreate(self) -> None:
        """Recreate the executor after shutdown.

        This is primarily useful for tests that need a fresh executor.
        If the executor is currently running, this will shut it down first.

        Raises:
            RuntimeError: If shutdown fails during recreation
        """
        # Shutdown existing executor if it's running
        if self._executor is not None and not self._shutdown:
            self.shutdown(wait=True)

        # Create new executor and reset shutdown state
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix=self._thread_name_prefix
        )
        self._shutdown = False

    @property
    def is_shutdown(self) -> bool:
        """Check if the executor has been shut down.

        Returns:
            True if executor has been shut down, False otherwise
        """
        return self._shutdown

    @property
    def is_initialized(self) -> bool:
        """Check if the executor has been initialized.

        Returns:
            True if executor has been created, False otherwise
        """
        return self._executor is not None


# Ingestion executor (async, avoids blocking requests).
# Single worker so span ingestions are processed serially (FIFO), per the
# #557 resolution: multiple ingestion workers contended for the storage
# layer. This alone does not make the shared test-mode SQLite connection
# safe (the serving thread also uses it) - the storage backend's
# per-connection statement locking handles that - but it keeps ingestion
# ordered and halves the concurrency.
_ingestion_executor_manager = ExecutorManager(
    max_workers=1,
    thread_name_prefix="audit_ingest"
)

# Backward compatibility: expose the executor directly
# This is deprecated - use _ingestion_executor_manager instead
_ingestion_executor: concurrent.futures.ThreadPoolExecutor | None = None

# Client singleton (lazy initialized)
_audit_client: AuditClient | None = None
# Track credentials used to create the client, for detecting when they change
# Includes (client_id, client_secret, access_token, api_key) to detect when
# any credential source changes
_client_credentials: tuple[str | None, str | None, str | None, str | None] | None = None


def _get_audit_client() -> AuditClient:
    """Get or create the audit client singleton.

    The client is recreated if credentials have changed since last creation,
    ensuring each test class gets a client with its own credentials.

    Credentials are resolved in priority order:
    1. AUDIT_API_KEY: explicit audit API key, sent as Bearer auth (#699)
    2. CLIENT_ID/CLIENT_SECRET: legacy Basic auth (rejected by the audit
       service front door, but kept for test harnesses that inject a
       custom json_client_class reading those variables)

    Returns:
        AuditClient instance for sending spans to audit service.

    Raises:
        ValueError: If neither AUDIT_API_KEY nor CLIENT_ID/CLIENT_SECRET
            are set in environment.
    """
    global _audit_client, _client_credentials

    # Get current credentials from environment
    from campus.common import env

    # Check if credentials are available (they may be deleted during test cleanup)
    api_key = env.get("AUDIT_API_KEY")
    client_id = env.get("CLIENT_ID")
    client_secret = env.get("CLIENT_SECRET")

    if not api_key and not (client_id and client_secret):
        raise ValueError(
            "AUDIT_API_KEY (or CLIENT_ID and CLIENT_SECRET) must be set "
            "in environment to create audit client"
        )

    # Track all credential sources so the client is recreated when any of
    # them change, e.g. when clear_test_data() deletes and recreates the
    # audit API key
    access_token = env.get("ACCESS_TOKEN")
    current_credentials = (client_id, client_secret, access_token, api_key)

    # Recreate client if credentials have changed or client doesn't exist
    if _audit_client is None or _client_credentials != current_credentials:
        _audit_client = AuditClient()
        _client_credentials = current_credentials

    return _audit_client


def is_static_request() -> bool:
    """True for static-asset requests that are never worth a span (#818).

    Static requests can never gain child spans — flask_campus's
    push_context already skips static endpoints (#689), so no SDK calls
    fire from them — they would only flood the traces list with 1-span
    rows. ``/favicon.ico`` has no route (endpoint None), so it needs a
    path check. HTML page loads are NOT static: they stay traced as the
    waterfall roots that give page-fired SDK calls parentage (#816).
    """
    endpoint = flask.request.endpoint or ""
    if endpoint == "static" or endpoint.endswith(".static"):
        return True
    return flask.request.path == "/favicon.ico"


def check_rate_gate() -> flask.Response | None:
    """Fail-closed gate for the audit ingest rate limit (#831).

    When AUDIT_TRACING_FAIL_CLOSED=1 (opt-in per deployment), requests
    whose identity bucket is tripped in the SDK's ingest circuit
    breaker (campus_python.audit.ratelimit — fed by this client's 429s)
    are short-circuited with 503 + Retry-After before any view runs.
    Off by default: producers run fail-open until flipped, per the
    ratified rollout (design comment on campus#538).

    The gate is per-identity when identity is already resolvable at
    request entry (app-level hooks run before blueprint-level ones, so
    e.g. campus.auth's session resolution has run); it degrades to the
    producer's learned apikey= fallback bucket (coarse) otherwise.

    Registered BEFORE start_span so a gated request never opens a span
    (end_span no-ops without span state — no wasted ingest against a
    bucket that audit is currently rejecting anyway).
    """
    from campus.common import env

    if env.get("AUDIT_TRACING_FAIL_CLOSED") != "1":
        return None

    # importlib with a string name: the module is optional (a stale
    # campus_python predating #831 lacks it) and opaque resolution
    # keeps type checkers happy against older SDK installs.
    import importlib

    try:
        ratelimit = importlib.import_module("campus_python.audit.ratelimit")
    except ImportError:
        logger.error(
            "AUDIT_TRACING_FAIL_CLOSED=1 but campus_python lacks "
            "audit.ratelimit (stale SDK); rate gate disabled"
        )
        return None

    retry_after = ratelimit.breaker.check_request(
        client_id=(
            _identity_id(getattr(flask.g, "current_client", None))
            or getattr(flask.g, "client_id", None)
        ),
        user_id=(
            _identity_id(getattr(flask.g, "current_user", None))
            or getattr(flask.g, "user_id", None)
        ),
    )
    if retry_after is None:
        return None

    logger.warning(
        "audit ingest breaker tripped; failing request closed "
        "(retry_after=%ds)",
        retry_after,
    )
    response = flask.jsonify({
        "error": {
            "code": "RATE_LIMITED",
            "message": f"Rate limit exceeded; retry after {retry_after}s",
            "details": {},
            "request_id": None,
        }
    })
    response.status_code = 503
    response.headers["Retry-After"] = str(max(1, int(retry_after)))
    return response


def start_span() -> None:
    """Start a span for the incoming request.

    A request that arrives with trace context headers (X-Request-ID +
    X-Parent-Span-ID, set by a calling campus service) becomes a child
    span of the caller; otherwise it starts a new root span.

    - Reuses trace_id from X-Request-ID header, or generates one
    - Records parent_span_id from X-Parent-Span-ID header, if present
    - Generates span_id for this request
    - Stores timing data in flask.g

    Static-asset requests (#818) are skipped entirely: no state is
    stored, so the paired end_span() call becomes a no-op.

    Stores in flask.g:
        - trace_id: 32-char hex trace identifier
        - span_id: 16-char hex span identifier
        - parent_span_id: caller's span id, or None for root spans
        - trace_start: perf_counter timestamp for duration calculation
        - trace_started_at: wall-clock DateTime for the span's started_at
    """
    if is_static_request():
        return

    # Get or generate trace_id from X-Request-ID header
    trace_id = flask.request.headers.get(TRACE_ID_HEADER) or uid.generate_trace_id()

    # Generate span_id for this request
    span_id = uid.generate_span_id()

    # Store in flask.g for use in after_request
    flask.g.trace_id = trace_id
    flask.g.span_id = span_id
    flask.g.parent_span_id = flask.request.headers.get(PARENT_SPAN_ID_HEADER) or None
    flask.g.trace_start = time.perf_counter()
    # Wall-clock start: recorded here so the span's started_at reflects
    # when the request arrived, not when the span was ingested (#794).
    flask.g.trace_started_at = schema.DateTime.utcnow()


def end_span(response: flask.Response) -> flask.Response:
    """Complete the span and ingest to audit service.

    - Builds TraceSpan from flask.request, flask.g, and response
    - Ingests asynchronously to avoid blocking
    - Echoes trace_id in response headers

    Args:
        response: The Flask response object

    Returns:
        Response with X-Request-ID header added
    """
    # Get trace data from flask.g (set by start_span)
    trace_id = getattr(flask.g, "trace_id", None)
    span_id = getattr(flask.g, "span_id", None)
    trace_start = getattr(flask.g, "trace_start", None)

    if not all([trace_id, span_id, trace_start]):
        # Tracing wasn't started properly, skip ingestion
        return response

    # Type narrowing: we know these are not None after the check
    trace_id = typing.cast(str, trace_id)
    span_id = typing.cast(str, span_id)
    trace_start = typing.cast(float, trace_start)

    # Calculate duration
    duration_ms = (time.perf_counter() - trace_start) * 1000

    # Build span from context
    span = build_span_from_context(trace_id, span_id, response, duration_ms)

    # Ingest asynchronously (don't block the response)
    _ingest_span_async(span)

    # Echo trace_id in response header
    response.headers[TRACE_ID_HEADER] = trace_id

    return response


def build_span_from_context(
    trace_id: str,
    span_id: str,
    response: flask.Response,
    duration_ms: float,
) -> dict:
    """Build a span dict from request/response context.

    Args:
        trace_id: The trace identifier
        span_id: The span identifier
        response: The Flask response object
        duration_ms: Request duration in milliseconds

    Returns:
        Dictionary representation of the span for ingestion.
    """
    request = flask.request

    # Extract headers (strip Authorization)
    headers = dict(request.headers)
    headers.pop("Authorization", None)
    headers.pop("authorization", None)  # Case-insensitive

    # Get request body (only for supported content types)
    request_body = _extract_request_body(request)

    # Get response body (truncated to 64KB)
    response_body = _extract_response_body(response)

    # Span start: wall-clock time captured in start_span (before_request),
    # not the ingestion time — waterfall offsets depend on it (#794).
    started_at = getattr(flask.g, "trace_started_at", None) or schema.DateTime.utcnow()

    # Login-journey correlation (#803): routes may stash the journey id
    # in flask.g (the /token hop, which never sees the browser cookie),
    # otherwise it comes from the campus_journey cookie set by
    # campus.auth at the first login-flow touch.
    journey_id = (
        getattr(flask.g, "journey_id", None)
        or request.cookies.get(protocol.JOURNEY_COOKIE)
    )

    # Action-journey name (#828): an optional human label stashed by
    # flask_campus's @journey decorator ("submit-assignment") for the
    # journey view's cards.
    journey_name = getattr(flask.g, "journey_name", None)

    # Stable device attribution (#825): flask_campus's push_context
    # stashes g.device from the request's login session, and campus.auth's
    # login hops stash it from the auth session (/token) — the cookie
    # fallback covers browser hops whose handlers didn't stash. The
    # device-verification page intentionally tags spans with the
    # *authorizing browser's* device id; the CLI's own device lives only
    # on its login session (Lane 2, client-asserted). Non-browser
    # clients present their device id on API calls as X-Campus-Device
    # (#837), which wins only when nothing better is known.
    device_id = (
        getattr(flask.g, "device", None)
        or request.headers.get(protocol.DEVICE_ID_HEADER)
        or request.cookies.get(protocol.DEVICE_COOKIE)
    )

    # Build span dict matching TraceSpan schema
    span = {
        "trace_id": trace_id,
        "span_id": span_id,
        # Set by start_span from the X-Parent-Span-ID header when the
        # request was spawned by another traced campus service (#794)
        "parent_span_id": getattr(flask.g, "parent_span_id", None),
        "started_at": started_at,
        "duration_ms": round(duration_ms, 3),
        "status_code": response.status_code,
        "method": request.method,
        "path": request.path,
        "query_params": redact_sensitive(dict(request.args)),
        "request_headers": redact_sensitive(headers),
        "request_body": redact_sensitive(request_body),
        "response_headers": redact_sensitive(dict(response.headers)),
        "response_body": redact_sensitive(response_body),
        "client_ip": request.remote_addr,
        # werkzeug 3's UserAgent object is falsy even when a UA header is
        # present (#826) — read .string unconditionally and normalize the
        # empty-string no-header case to None.
        "user_agent": request.user_agent.string or None,
        "error_message": None,  # No error for successful requests
        # Journey/device tags when the request belongs to a login
        # journey (#803), an identified device (#825), or a named
        # action journey (#828)
        "tags": {
            key: value
            for key, value in (
                ("journey_id", journey_id),
                ("journey_name", journey_name),
                ("device_id", device_id),
            )
            if value
        },
        # Optional: populated by auth middleware if available
        "api_key_id": getattr(flask.g, "api_key_id", None),
        # The shared Authenticator glue stashes the authenticated caller's
        # full client/user under current_client/current_user (#802) —
        # resource dicts via the SDK (campus.api), model objects
        # in-process (campus.auth). Direct id attributes stay supported
        # for deployment glue that stashes ids instead.
        "client_id": (
            _identity_id(getattr(flask.g, "current_client", None))
            or getattr(flask.g, "client_id", None)
        ),
        "user_id": (
            _identity_id(getattr(flask.g, "current_user", None))
            or getattr(flask.g, "user_id", None)
        ),
    }

    return span


def _identity_id(value: typing.Any) -> str | None:
    """Extract an id from an Authenticator identity object or dict (#802).

    Returns None when the identity is missing or carries no id.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("id")
    return getattr(value, "id", None)


# Keys whose values must never reach audit storage (#805). Matched
# case-insensitively with dashes normalized to underscores; the suffixes
# catch variants (google_refresh_token, db_password, ...) without widening
# to bare "_key"/"_code", which carry usable non-secret data.
SENSITIVE_KEYS = frozenset({
    "access_token", "api_key", "authorization", "client_secret",
    "cookie", "device_code", "id_token", "passwd", "password",
    "refresh_token", "secret", "set_cookie", "token",
})
_SENSITIVE_SUFFIXES = ("_secret", "_password", "_api_key", "_token")

_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: typing.Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return (
        normalized in SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_SUFFIXES)
    )


def redact_sensitive(value: typing.Any) -> typing.Any:
    """Mask values stored under sensitive keys, recursively (#805).

    Keyed redaction only: scalars and free-text bodies pass through
    unchanged — text scrubbing needs sensitive-data marking (#806).
    Returns a new structure; the input is not mutated.
    """
    if isinstance(value, dict):
        return {
            key: _REDACTED if _is_sensitive_key(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def _extract_request_body(request: flask.Request) -> dict | str | None:
    """Extract request body safely.

    Only extracts for supported content types (JSON, form data).
    Returns None for unsupported types (files, binary, etc).

    Args:
        request: The Flask request object.

    Returns:
        Request body as dict, str, or None.
    """
    # Don't extract body for file uploads or unsupported content types
    if request.files:
        return None
    if request.content_length and request.content_length > 1_000_000:  # 1MB
        return None

    content_type = request.content_type or ""

    if "application/json" in content_type:
        try:
            return request.json
        except Exception:
            return None
    elif "application/x-www-form-urlencoded" in content_type:
        return dict(request.form)
    elif "text/" in content_type:
        try:
            return request.data.decode("utf-8")
        except Exception:
            return None

    return None


def _extract_response_body(response: flask.Response) -> dict | None:
    """Extract response body as JSON with 64KB truncation.

    Campus API and auth endpoints only return JSON dict responses,
    so we expect JSON-parseable bodies.

    Truncated responses have a special "_truncated" key appended with
    the original size, e.g. {"_truncated": "[128000 ...]"}

    Args:
        response: The Flask response object.

    Returns:
        Response body as dict, or None if not applicable (streaming,
        no data, or invalid JSON).
    """
    # Don't try to extract from streaming responses
    if response.is_streamed:
        return None

    # Don't extract if no response data
    if not response.response:
        return None

    try:
        # Get response data
        if isinstance(response.response, list):
            # Join bytes from list
            parts = typing.cast(list, response.response)
            if all(isinstance(p, bytes) for p in parts):
                data = bytes(b"".join(typing.cast(list[bytes], parts)))
            else:
                # Mixed types, convert to string
                data = "".join(str(p) for p in parts)
        elif isinstance(response.response, str):
            data = response.response.encode("utf-8")
        else:
            data = typing.cast(bytes, response.response)

        # Decode bytes to string
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError:
                return None

        # Truncate to 64KB before parsing
        max_size = 64 * 1024
        original_size = len(data) if isinstance(data, str) else 0
        if isinstance(data, str) and len(data) > max_size:
            data = data[:max_size]

        # Parse as JSON (campus.api and campus.auth always return dicts)
        if isinstance(data, str):
            body = json.loads(data)
            # Add truncation marker if we truncated
            if original_size > max_size and isinstance(body, dict):
                body["_truncated"] = f"[{original_size} ...]"
            return body

        return None
    except (json.JSONDecodeError, UnicodeDecodeError, Exception):
        return None


def _ingest_span_async(span: dict) -> None:
    """Ingest span asynchronously using thread pool.

    Args:
        span: Span data to ingest.
    """
    def _do_ingest():
        try:
            client = _get_audit_client()
            client.traces.new(span)
        except ValueError as e:
            # Credentials not available (e.g., during test cleanup)
            # This is expected during shutdown, so log at debug level
            logger.debug(f"Skipping trace ingestion: {e}")
        except Exception as e:
            # Don't let tracing errors break the application
            logger.warning(f"Failed to ingest trace span: {e}")

    try:
        executor = _ingestion_executor_manager.get_executor()
        executor.submit(_do_ingest)
    except RuntimeError as e:
        # Executor has been shut down (e.g., during test cleanup)
        # Log and continue - don't break the application
        logger.debug(f"Cannot submit span to executor: {e}")


def ingest_span(span: dict) -> None:
    """Send span to audit service via HTTP.

    This is a synchronous wrapper for backward compatibility.
    The actual ingestion happens asynchronously to avoid blocking.

    Args:
        span: Span data dictionary to ingest
    """
    _ingest_span_async(span)


def shutdown_executor(wait: bool = True) -> None:
    """Shutdown the trace ingestion executor.

    This is primarily useful for tests that need to wait for async operations
    to complete before making assertions. The shutdown is idempotent - safe
    to call multiple times.

    Args:
        wait: If True, wait for pending tasks to complete

    Note:
        This is typically called automatically during test cleanup.
        Manual calls are only needed when you need to synchronize with async operations.
    """
    _ingestion_executor_manager.shutdown(wait=wait)


def recreate_executor() -> None:
    """Recreate the trace ingestion executor after shutdown.

    This is primarily useful for tests that need a fresh executor.
    If the executor is currently running, it will be shut down first.

    Example:
        # In test setup
        recreate_executor()

        # Run test that uses tracing
        # ...

        # In test teardown
        shutdown_executor(wait=True)

    Warning:
        This should only be used in tests, not in production code.
    """
    _ingestion_executor_manager.recreate()


def get_executor_state() -> dict:
    """Get the current state of the executor for debugging/testing.

    Returns:
        Dictionary with keys:
        - 'initialized': True if executor has been created
        - 'shutdown': True if executor has been shut down
    """
    return {
        'initialized': _ingestion_executor_manager.is_initialized,
        'shutdown': _ingestion_executor_manager.is_shutdown,
    }


def current_context() -> tuple[str, str] | None:
    """Return the (trace_id, span_id) of the active traced request.

    Returns None outside a request context, or when the request has no
    active span (tracing disabled or middleware not yet run). Span
    ingestion runs on a separate executor thread, so it never sees a
    context here and stays unparented by design.
    """
    if not flask.has_request_context():
        return None
    trace_id = getattr(flask.g, "trace_id", None)
    span_id = getattr(flask.g, "span_id", None)
    if trace_id and span_id:
        return trace_id, span_id
    return None


def propagation_headers() -> dict[str, str]:
    """Headers to attach to an outbound request spawned by the current one.

    Empty outside a traced request context. The receiving service's
    tracing middleware turns these into a child span of the caller's
    span (#794).
    """
    context = current_context()
    if context is None:
        return {}
    return {
        TRACE_ID_HEADER: context[0],
        PARENT_SPAN_ID_HEADER: context[1],
    }


_INSTRUMENTED_ATTR = "_campus_trace_instrumented"


def instrument_requests_session(session: requests.Session) -> bool:
    """Wrap a requests.Session so its calls carry trace context headers.

    Headers are computed at call time from the active request context, so
    a shared session stays correct under concurrent requests, and calls
    made outside a traced request (e.g. the span-ingestion executor
    thread, startup code) are left untouched.

    Idempotent: re-instrumenting a session is a no-op.

    Args:
        session: The requests.Session to instrument.

    Returns:
        True if the session was instrumented now, False if already done.
    """
    if getattr(session, _INSTRUMENTED_ATTR, False):
        return False

    original_request = session.request

    def request(method, url, **kwargs):
        extra = propagation_headers()
        if extra:
            headers = dict(kwargs.get("headers") or {})
            for name, value in extra.items():
                headers.setdefault(name, value)
            kwargs["headers"] = headers
        return original_request(method, url, **kwargs)

    # Instance-level override of the bound method: every requests verb
    # funnels through Session.request, so one wrap covers all calls.
    typing.cast(typing.Any, session).request = request
    setattr(session, _INSTRUMENTED_ATTR, True)
    return True
