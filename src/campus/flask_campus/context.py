"""campus.flask_campus.context

Auth session-context mechanics for Flask hosts.

This module owns the client-side half of the campus login flow: the
storage of the auth-session and login-session ids (flask.session, via
the SDK's SESSION_STORAGE_KEY / LOGIN_STORAGE_KEY), the request-entry
context push (g.user / g.device with a per-manager user cache), and
the login-flow steps (start / finalize / logout). It orchestrates the
SDK's pure API calls (campus.auth.sessions / logins / users /
exchange_code_for_token) around Flask request state, so campus_python
itself stays free of any web framework.

OAuthLoginManager wires this module in at init_app; apps never call it
directly. The SDK's session-id provider seam is installed here, so the
SDK resources resolve their ids from the same flask.session keys this
module writes — during the rollout window both sides write identical
values (idempotent), and after the SDK sheds its flask lane this
module is the sole owner.
"""

__all__ = [
    "LOGIN_STORAGE_KEY",
    "SESSION_STORAGE_KEY",
    "USER_CACHE_TTL_SECONDS",
    "UserCache",
    "finalize_login",
    "install_session_provider",
    "logout",
    "push_context",
    "start_login",
]

import logging
import time
import typing

import campus_python
import flask
import werkzeug
from campus_python.auth.v1 import sessionstore as sdk_sessionstore
from campus_python.auth.v1.logins import LOGIN_STORAGE_KEY
from campus_python.auth.v1.sessions import SESSION_STORAGE_KEY

import campus.model
from campus.common import env
from campus.common.utils import uid

logger = logging.getLogger(__name__)

# How long a resolved session user may be reused across requests (#57).
# The push runs as an app-wide before_request hook, so without the
# cache a session-carrying browser pays 1-2 synchronous auth API calls
# before every route. Warm requests within the window pay none.
USER_CACHE_TTL_SECONDS = 60.0

# User-cache lane names (match AuthRoot's namespacing).
LANE_SESSION = "session"
LANE_LOGIN = "login"


def _resolve_session_id(kind: str) -> str | None:
    """SDK session-id provider callback: ids live in flask.session."""
    if kind == sdk_sessionstore.LANE_SESSION:
        return flask.session.get(SESSION_STORAGE_KEY)
    if kind == sdk_sessionstore.LANE_LOGIN:
        return flask.session.get(LOGIN_STORAGE_KEY)
    return None


def install_session_provider() -> None:
    """Point the SDK's session-id seam at flask.session (idempotent).

    Called by OAuthLoginManager.init_app — apps that never initialize
    a login manager keep the SDK's legacy flask.session reads, which
    consult the identical keys.
    """
    sdk_sessionstore.set_session_id_provider(_resolve_session_id)


@typing.runtime_checkable
class _Snapshottable(typing.Protocol):
    """Anything model-shaped that can snapshot itself for caching."""

    def to_resource(self) -> dict:
        ...


class UserCache:
    """Process-local cache of resolved session users (#57 semantics).

    Entries snapshot the user via to_resource() and are rehydrated per
    hit, so callers cannot poison the cache by mutating flask.g.user.
    """

    def __init__(self, ttl_seconds: float = USER_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        # (kind, session_id) -> (expires_at, user snapshot, device_id).
        self._entries: dict[
            tuple[str, str],
            tuple[float, object, str | None],
        ] = {}

    def get(
            self, kind: str, session_id: str | None
    ) -> tuple[object, str | None] | None:
        """The cached (user, device_id) for a session, or None.

        Expired entries are dropped lazily on read.
        """
        if session_id is None:
            return None
        entry = self._entries.get((kind, session_id))
        if entry is None:
            return None
        expires_at, snapshot, device_id = entry
        if time.monotonic() >= expires_at:
            self._entries.pop((kind, session_id), None)
            return None
        user = (
            campus.model.User.from_resource(snapshot)
            if isinstance(snapshot, dict)
            else snapshot
        )
        return user, device_id

    def put(
            self,
            kind: str,
            session_id: str | None,
            user: object,
            device_id: str | None,
    ) -> None:
        """Cache a session's resolved user for ttl_seconds."""
        if session_id is None:
            return
        snapshot: object = (
            user.to_resource()
            if isinstance(user, _Snapshottable)
            else user
        )
        self._entries[(kind, session_id)] = (
            time.monotonic() + self.ttl_seconds,
            snapshot,
            device_id,
        )

    def pop(self, kind: str, session_id: str | None) -> None:
        """Drop a session's cached user (logout, stale session)."""
        if session_id is not None:
            self._entries.pop((kind, session_id), None)


def push_context(campus: campus_python.Campus, cache: UserCache) -> None:
    """Push auth/login context to flask.g (the SDK push_context port).

    Resolved users are cached per session for UserCache's ttl (#57):
    warm requests make zero upstream calls. Trade-off: a session
    revoked server-side — not through logout(), which evicts
    immediately — keeps resolving for up to the TTL window.
    """
    flask.g.user = None
    flask.g.device = None

    # Try to load auth session if one exists
    if campus.auth.sessions.has_session():
        session_id = flask.session.get(SESSION_STORAGE_KEY)
        cached = cache.get(LANE_SESSION, session_id)
        if cached is not None:
            flask.g.user, flask.g.device = cached
            return
        try:
            auth_session = campus.auth.sessions.from_session()
            if auth_session.user_id:
                # The auth service embeds the session user for the
                # owning client (#879); the operator-gated users route
                # remains the fallback for older auth services.
                user = (
                    getattr(auth_session, "user", None)
                    or campus.auth.users[auth_session.user_id].get()
                )
                flask.g.user = user
                cache.put(LANE_SESSION, session_id, user, None)
        except campus_python.errors.NotFoundError:
            # Session no longer exists on server (expired, restart, etc.)
            cache.pop(LANE_SESSION, session_id)
            # Clear the stale session reference from the browser.
            flask.session.pop(SESSION_STORAGE_KEY, None)

    # Try to load login session if one exists
    elif campus.auth.logins.has_session():
        login_id = flask.session.get(LOGIN_STORAGE_KEY)
        cached = cache.get(LANE_LOGIN, login_id)
        if cached is not None:
            flask.g.user, flask.g.device = cached
            return
        try:
            login_session = campus.auth.logins.from_session()
            login_user_id = login_session.user_id
            # Embedded login user (#879) with the same fallback.
            user = getattr(login_session, "user", None)
            if user is None and login_user_id is not None:
                user = campus.auth.users[login_user_id].get()
            flask.g.user = user
            flask.g.device = login_session.device_id
            if user is not None:
                cache.put(
                    LANE_LOGIN, login_id, user, login_session.device_id
                )
        except campus_python.errors.NotFoundError:
            # Login session no longer exists on server
            cache.pop(LANE_LOGIN, login_id)
            flask.session.pop(LOGIN_STORAGE_KEY, None)


def start_login(campus: campus_python.Campus, target: str) -> werkzeug.Response:
    """Begin an authorization flow (the SDK authorize port).

    Returns a redirect response to the Campus OAuth authorize endpoint.

    IMPORTANT: This returns a redirect response directly (never fetches
    via requests) to preserve the browser's user agent and cookies. If
    we proxied through requests, Google detects a server user agent and
    switches to OAuth Lite flow which breaks in browsers (500 error).
    """
    from urllib.parse import urlencode

    auth_session = campus.auth.sessions.new(
        redirect_uri=target,
        scopes=["campus.profile"],
        target=target,
    )
    # Context owns the storage key; the SDK's new() writes the same key
    # with the same value during the compat window (idempotent).
    flask.session[SESSION_STORAGE_KEY] = auth_session.id
    authorize_url = campus.auth.base_url + campus.auth.make_path("authorize")
    params = {
        "client_id": env.CLIENT_ID,
        "response_type": "code",  # OAuth2 authorization code flow
        "redirect_uri": target,  # Callback URL after auth
        "state": auth_session.id,
    }
    return flask.redirect(f"{authorize_url}?{urlencode(params)}")


def finalize_login(
        campus: campus_python.Campus,
        *,
        state: str,
        code: str,
        scope: str,
) -> None:
    """Complete an authorization flow (the SDK finalize port).

    Steps: validate the auth session, exchange the code (credentials
    are stored server-side by the token endpoint), finalize the
    session, provision the user, create the login session — the
    device id rides the auth session (#825: campus.auth observed the
    campus_device cookie at /authorize; the minted fallback keeps
    non-cookie flows working).

    Raises:
        campus_python.errors.BadRequestError / AuthenticationError:
            rejected codes (replayed, expired) or a state mismatch —
            routine in a browser flow; callers send the user back to
            sign-in (#690).

    Args:
        state: The OAuth state parameter (the auth session id).
        code: The authorization code from the OAuth callback.
        scope: Granted OAuth scopes (accepted for flow compatibility;
            the grant itself is recorded server-side).
    """
    # 1. Validate auth session
    auth_session = campus.auth.sessions.from_code(code=code)
    if auth_session.id != state:
        raise campus_python.errors.AuthenticationError(
            error_description="State parameter does not match session ID."
        )
    if auth_session.user_id is None:
        raise campus_python.errors.AuthenticationError(
            error_description="Unidentified user from auth session"
        )

    # 2. Exchange authorization code for access token
    campus.auth.exchange_code_for_token(
        code=code,
        redirect_uri=auth_session.redirect_uri,
    )

    # 3. Finalize session; the response embeds the session user for
    # the owning client (#879). Pre-#879 services return no user.
    _target, session_user = campus.auth.sessions[auth_session.id].finalize()

    # 4. Ensure user exists (the server provisions the user record
    # during verify_login; a missing user is a hard error here).
    if session_user is None:
        campus.auth.users[auth_session.user_id].get()

    # The auth session is consumed — clear its storage key (the SDK's
    # finalize() pops the same key during the compat window).
    flask.session.pop(SESSION_STORAGE_KEY, None)

    # 5. Create the login session
    login_session = campus.auth.logins.new(
        user_id=auth_session.user_id,
        device_id=(
            auth_session.device_id
            or uid.generate_category_uid("device", length=16)
        ),
        agent_string=flask.request.headers.get("User-Agent", ""),
    )
    flask.session[LOGIN_STORAGE_KEY] = login_session.id


def logout(
        campus: campus_python.Campus,
        cache: UserCache | None = None,
) -> None:
    """Logout the current user (the SDK logout port).

    Revokes the login session through the API, then clears local
    state. Remote revocation is best-effort: the login session may
    already be gone server-side (expired, swept, or the client's auth
    service session cookie lost across app workers), but the local
    session state is always cleared so the user is signed out locally
    regardless of the revocation outcome.
    """
    flask.g.pop("user", None)
    flask.g.pop("device", None)
    if not campus.auth.logins.has_session():
        return
    try:
        login_session = campus.auth.logins.from_session()
        campus.auth.logins[login_session.id].revoke()
        if cache is not None:
            cache.pop(LANE_LOGIN, login_session.id)
    except Exception as err:
        logger.warning(
            "Login session revocation failed (continuing with local "
            "logout): %s", err
        )
        flask.session.pop(LOGIN_STORAGE_KEY, None)
    # The SDK's revoke() pops the same key during the compat window;
    # this module is the owner, so clear it unconditionally.
    flask.session.pop(LOGIN_STORAGE_KEY, None)
