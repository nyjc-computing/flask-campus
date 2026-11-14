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
from campus.flask_campus import circles, context


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
        default_endpoint: str,
        login_scopes: "list[str] | None" = None,
) -> flask.Blueprint:
    """Create authentication blueprint for Flask app.

    Args:
        push_hook: App before_request hook that pushes the auth context.
        campus: Campus client instance
        cache: User cache backing the context push
        default_endpoint: Default endpoint to redirect to after login/logout
        login_scopes: OAuth scopes the /login flow requests for the
            minted user credential; None keeps context's
            DEFAULT_LOGIN_SCOPES. Must be covered by the minting
            client's allowed_scopes cap (validated fail-closed
            upstream).
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
        return context.start_login(
            campus, target=callback_url, scopes=login_scopes
        )


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
            default_endpoint: str = "index",
            login_scopes: "list[str] | None" = None,
    ):
        """Initialize the OAuthLoginManager.

        Args:
            campus_client: Optional Campus client instance. If None, a
                new instance will be created.
            default_endpoint: Default endpoint to redirect to after login/logout
            login_scopes: OAuth scopes the /login flow requests for the
                minted user credential (rides start_login's scopes
                parameter). None keeps ["campus.profile"]. Apps whose
                features need management scopes on the user's token
                (#865/#883 two-leg model) pass the full list — and
                must extend their client's allowed_scopes cap BEFORE
                deploying, since campus.auth validates the request
                against that cap fail-closed.
        """
        self.campus = campus_client or campus_python.Campus()
        self.default_endpoint = default_endpoint
        self.login_scopes = login_scopes
        self._cache = context.UserCache()
        self._circle_cache = circles.MembersCache()

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
            self.default_endpoint, login_scopes=self.login_scopes,
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

    def circle_member_required(self, circle: str) -> Callable:
        """Decorator: the view requires membership in a named circle.

        View-level gating over campus.api circles (the domain access
        control): the signed-in user's ID — their email, which is also
        their user circle's name — must be a member of `circle`
        (e.g. "campus-admin" or a role circle "timetable.admin"). Use
        circle_admin_required for the per-resource admin roles.

        Enforcement stays in campus.api: this gate renders the right
        UI before the API's 403 wall and must never be the only
        protection for data.

        Signed-out users redirect to sign-in (the login_required
        behaviour, folded in so decorator stacking order never
        matters). Signed-in non-members get 403 — they are
        authenticated, not unknown. If the circles backend cannot
        answer (network, API error), the view fails closed with 503
        rather than a misleading 403; answers are cached briefly
        (circles.MembersCache) so warm views make no upstream calls.
        """
        def decorator(view: Callable) -> Callable:
            @wraps(view)
            def wrapped_view(*args, **kwargs):
                user = getattr(flask.g, "user", None)
                if user is None:
                    return flask.redirect(
                        flask.url_for('auth.login', next=flask.request.path)
                    )
                user_id = getattr(user, "id", None)
                if not user_id:
                    flask.abort(403, description=(
                        "Signed-in identity has no user ID; membership "
                        f"in '{circle}' cannot be verified."
                    ))
                try:
                    member = circles.is_member(
                        self.campus, self._circle_cache, circle, user_id
                    )
                except circles.CirclesUnavailableError as err:
                    flask.current_app.logger.error(
                        "Circle membership unavailable: %s", err
                    )
                    flask.abort(503, description=(
                        "Authorization backend unavailable; "
                        "try again shortly."
                    ))
                if not member:
                    flask.abort(403, description=(
                        f"Membership in the '{circle}' circle is required."
                    ))
                return view(*args, **kwargs)
            return wrapped_view
        return decorator

    def circle_admin_required(self, resource: str) -> Callable:
        """Decorator: the view requires the <resource>.admin role.

        Convenience over circle_member_required for the circles
        bootstrap's per-resource role circles: circle_admin_required(
        "timetable") gates on membership in the "timetable.admin"
        circle — the same role campus.api's mutation routes enforce
        server-side (campus/api/authz.py require_resource_admin).
        """
        return self.circle_member_required(f"{resource}.admin")
