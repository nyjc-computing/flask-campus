"""campus.flask_campus.login_manager

OAuth Login Manager for Flask integration.

The policy half of the login flow (routes, redirects, session-next
handling); the mechanics — session storage, context push, token
exchange — live in flask_campus.context, which orchestrates the
campus_python client's pure API calls.
"""

from functools import wraps
from typing import Callable

import campus_python
import flask
import werkzeug

from campus import flask_campus
from campus.common import env
from campus.common.services import get_base_url
from campus.common.utils import url
from campus.flask_campus import context


def _is_safe_redirect(redirect_url: str) -> bool:
    """Ensure URL is safe for redirect (prevents open redirect attacks)."""
    # Only allow relative URLs starting with /
    # Reject protocol-relative URLs (//), including the backslash
    # spelling (/\): browsers normalize it to // before resolving
    # (#788; the auth-service logout endpoint applies the same rule).
    return redirect_url.startswith('/') and not redirect_url.startswith(
        ('//', '/\\')
    )

def _create_bp(
        push_hook: Callable[[], None],
        campus: campus_python.Campus,
        cache: context.UserCache,
        default_endpoint: str
) -> flask.Blueprint:
    """Create authentication blueprint for Flask app.

    Args:
        push_hook: App before_request hook that pushes the auth context.
        campus: Campus client instance
        cache: User cache backing the context push
        default_endpoint: Default endpoint to redirect to after login/logout
    """
    bp = flask.Blueprint("auth", __name__, url_prefix="/")
    bp.before_request(push_hook)

    @bp.get("/login")
    @flask_campus.unpack_request
    def login(next: str | None = None) -> werkzeug.Response:
        """Initiate OAuth login flow.

        Args:
            next: Destination URL to redirect to after successful login

        Returns:
            Redirect to Campus OAuth authorization endpoint
        """
        # Validate and store destination to prevent open redirect attacks
        if next and not _is_safe_redirect(next):
            next = url.full_url_for(default_endpoint)
        flask.session['login_next'] = next or url.full_url_for(default_endpoint)

        # Use /finalize_login as the OAuth callback
        # Use url.full_url_for to get proper public URL (not localhost/internal host)
        callback_url = url.full_url_for('auth.finalize_login')
        return context.start_login(campus, target=callback_url)


    @bp.get("/finalize_login")
    @flask_campus.unpack_request
    def finalize_login(
            code: str,
            state: str,
            scope: str
    ) -> werkzeug.Response:
        """OAuth callback endpoint - completes authentication.

        Steps:
        1. Validates the auth session
        2. Exchanges authorization code for access token
        3. Stores credentials automatically via token endpoint
        4. Creates login session (30-day expiry)
        5. Redirects to the user's intended destination

        Args:
            code: The authorization code from OAuth callback
            state: Session state for CSRF protection
            scope: Granted OAuth scopes

        Returns:
            Redirect to the destination stored in login_next session variable
        """
        # Complete the OAuth flow (creates login session). A rejected
        # code (replayed single-use code, expired code) is routine in a
        # browser flow — send the user back to sign-in instead of an
        # unhandled 500 (#690).
        try:
            context.finalize_login(
                campus, state=state, code=code, scope=scope
            )
        except campus_python.errors.BadRequestError as err:
            flask.current_app.logger.warning(
                "finalize_login rejected: %s", err
            )
            flask.flash(
                "Your sign-in session expired or was already used."
                " Please sign in again.",
                "warning"
            )
            return flask.redirect(flask.url_for('auth.login'))
        context.push_context(campus, cache)

        # Redirect to the original destination
        next_url = flask.session.pop('login_next', '/')
        return flask.redirect(next_url)

    @bp.get("/logout")
    def logout() -> werkzeug.Response:
        """Sign the browser out of the app and campusauth.

        Revokes the login session record through the API (#776), then
        redirects the browser through the auth service's browser-session
        logout so the campusauth SSO cookie is cleared too (#785). This
        app's post-logout target rides along as post_logout_redirect_uri:
        the auth service honors it when its origin matches a registered
        client redirect_uri (#788), otherwise falls back to its own
        landing ("/").
        """
        context.logout(campus, cache)
        post_logout_target = url.full_url_for(default_endpoint)
        auth_logout_url = url.add_query(
            f"{get_base_url('campus.auth')}/auth/v1/logout",
            post_logout_redirect_uri=post_logout_target,
        )
        return flask.redirect(auth_logout_url)

    return bp


class OAuthLoginManager:
    """OAuth Login Manager for Flask integration."""

    def __init__(
            self,
            campus_client: campus_python.Campus | None = None,
            default_endpoint: str = "index"
    ):
        """Initialize the OAuthLoginManager.

        Args:
            campus_client: Optional Campus client instance. If None, a
                new instance will be created.
            default_endpoint: Default endpoint to redirect to after login/logout
        """
        self.campus = campus_client or campus_python.Campus()
        self.default_endpoint = default_endpoint
        self._cache = context.UserCache()

    def _push_context_hook(self) -> None:
        """App-wide before_request hook that pushes the auth context.

        Static asset requests never read g.user, so they skip the
        upstream auth lookup (#689): a session-carrying browser
        otherwise pays push_context's auth API calls on every stylesheet
        and script, which can wedge a sync worker under load.
        """
        endpoint = flask.request.endpoint or ""
        if endpoint == "static" or endpoint.endswith(".static"):
            return
        context.push_context(self.campus, self._cache)

        # Span identity (#820): the audit tracing middleware reads
        # g.user_id / g.client_id when building this request's span.
        # Page loads are the waterfall roots, so stamping them here is
        # what lets the audit UI attribute a page load to the app's
        # client and the signed-in user.
        user = getattr(flask.g, "user", None)
        if user is not None and getattr(user, "id", None):
            flask.g.user_id = user.id
        client_id = env.get("CLIENT_ID")
        if client_id:
            flask.g.client_id = client_id

    def init_app(self, app: flask.Flask | flask.Blueprint):
        """Initialize the login manager with the Flask app."""
        # The SDK resources resolve their session ids through this
        # module's flask.session storage from here on (idempotent).
        context.install_session_provider()
        bp = _create_bp(
            self._push_context_hook, self.campus, self._cache,
            self.default_endpoint,
        )
        app.register_blueprint(bp)
        if isinstance(app, flask.Flask):
            app.before_request(self._push_context_hook)
        elif isinstance(app, flask.Blueprint):
            app.before_app_request(self._push_context_hook)

    def login_required(self, view: Callable) -> Callable:
        """Decorator to protect routes that require authentication."""
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not hasattr(flask.g, "user") or flask.g.user is None:
                return flask.redirect(
                    flask.url_for('auth.login', next=flask.request.path)
                )
            return view(*args, **kwargs)
        return wrapped_view
