"""Unit tests for flask_campus /logout routing through the auth service.

App-side sign-out must end the campusauth SSO session, not just
revoke the login_sessions record: the auth service holds its own
Flask session behind its cookie (#785). /logout therefore redirects
the browser through the auth service's browser-session logout
endpoint, carrying this app's post-logout target along as
post_logout_redirect_uri (#788): the auth service honors it when the
origin is a registered client redirect_uri origin, otherwise lands
the browser on its own "/".
"""

import os
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import flask
from campus_python.auth.v1 import set_session_id_provider

from campus.flask_campus.login_manager import OAuthLoginManager

_AUTH_BASE = "https://campus.test"
_POST_LOGOUT_TARGET = "https://campus.test/"


class _FakeLogins:
    """logins resource stand-in: revocation via the resource surface."""

    def __init__(self, auth: "_FakeAuth"):
        self._auth = auth

    def has_session(self):
        return True

    def from_session(self):
        # Coherent with the context push, which resolves this session
        # for the /logout request's own hooks.
        return SimpleNamespace(
            id="login-1",
            user_id="user-1",
            user=None,
            device_id="device-1",
        )

    def __getitem__(self, login_id):
        return SimpleNamespace(revoke=self._revoke)

    def _revoke(self):
        self._auth.logged_out = True


class _FakeAuth:
    """Minimal stand-in for the campus_python auth resource surface
    that flask_campus.context orchestrates."""

    def __init__(self):
        self.logged_out = False
        self.pushed = False

        def _has_session():
            self.pushed = True
            return False

        self.sessions = SimpleNamespace(has_session=_has_session)
        self.logins = _FakeLogins(self)
        self.users = {
            "user-1": SimpleNamespace(
                get=lambda: SimpleNamespace(id="user-1")
            )
        }


class _FakeCampus:
    def __init__(self):
        self.auth = _FakeAuth()


class TestLogoutRoutesThroughAuthService(unittest.TestCase):
    """GET /logout revokes, then redirects to the auth service logout."""

    def setUp(self):
        # get_base_url and full_url_for resolve through ENV/PUBLIC_URL
        # at call time; pin both and restore around each test.
        self.saved = {
            name: os.environ.get(name)
            for name in ("ENV", "PUBLIC_URL")
        }
        os.environ["ENV"] = "testing"
        os.environ["PUBLIC_URL"] = _AUTH_BASE
        self.app = flask.Flask(__name__)
        self.app.secret_key = "test-secret"

        @self.app.get("/")
        def index():
            return ""

        self.manager = OAuthLoginManager(
            campus_client=_FakeCampus()  # type: ignore[arg-type]
        )
        self.manager.init_app(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        set_session_id_provider(None)
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_logout_revokes_then_redirects_to_auth_service(self):
        response = self.client.get("/logout")

        self.assertTrue(self.manager.campus.auth.logged_out)
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers["Location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}", _AUTH_BASE)
        self.assertEqual(location.path, "/auth/v1/logout")

    def test_logout_carries_post_logout_redirect_target(self):
        """The app's own post-logout target rides along (#788)."""
        response = self.client.get("/logout")

        query = parse_qs(urlparse(response.headers["Location"]).query)
        self.assertEqual(
            query["post_logout_redirect_uri"],
            [_POST_LOGOUT_TARGET]
        )


if __name__ == "__main__":
    unittest.main()
