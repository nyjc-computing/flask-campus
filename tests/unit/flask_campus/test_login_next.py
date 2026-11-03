"""Unit tests for the login `next` open-redirect validation.

flask_campus honors a caller-supplied `next` destination only when it
is a safe relative path. Protocol-relative URLs (//) were always
rejected; the backslash spelling (/\\) normalizes to // in browsers
and must be rejected too (#788) — matching the hardening the auth
service's logout endpoint already applies.
"""

import os
import unittest
from types import SimpleNamespace

import flask
from campus_python.auth.v1 import set_session_id_provider

from campus.flask_campus.login_manager import OAuthLoginManager


class _FakeAuth:
    """Minimal stand-in for the campus_python auth resource surface
    that flask_campus.context orchestrates."""

    base_url = "https://campusauth.test"

    def __init__(self):
        self.pushed = False

        def _has_session():
            self.pushed = True
            return False

        def _new(**kwargs):
            return SimpleNamespace(id="state-1")

        self.sessions = SimpleNamespace(has_session=_has_session, new=_new)
        self.logins = SimpleNamespace(has_session=lambda: False)

    def make_path(self, endpoint):
        return f"/auth/v1/{endpoint}"


class _FakeCampus:
    def __init__(self):
        self.auth = _FakeAuth()


class TestLoginNextValidation(unittest.TestCase):
    """GET /login?next=... stores the destination only when safe."""

    def setUp(self):
        # login stores the fallback via full_url_for, which resolves
        # PUBLIC_URL at call time; pin and restore around each test.
        # start_login reads CLIENT_ID for the authorize URL.
        self.saved = {
            name: os.environ.get(name)
            for name in ("ENV", "PUBLIC_URL", "CLIENT_ID")
        }
        os.environ["ENV"] = "testing"
        os.environ["PUBLIC_URL"] = "https://campus.test"
        os.environ["CLIENT_ID"] = "uid-client-test"
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

    def _stored_next(self) -> str:
        with self.client.session_transaction() as session:
            return session.get("login_next", "")

    def test_safe_next_is_preserved(self):
        response = self.client.get("/login?next=/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._stored_next(), "/dashboard")

    def test_protocol_relative_next_falls_back_to_default(self):
        self.client.get("/login?next=//evil.example.com")

        self.assertEqual(self._stored_next(), "https://campus.test/")

    def test_backslash_next_falls_back_to_default(self):
        r"""/\evil.example.com normalizes to //evil... in browsers."""
        self.client.get("/login?next=/\\evil.example.com")

        self.assertEqual(self._stored_next(), "https://campus.test/")

    def test_login_redirects_to_auth_service_authorize(self):
        """The login redirect targets the auth service's authorize
        endpoint with the flow parameters."""
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(
            location.startswith(
                "https://campusauth.test/auth/v1/authorize?"
            ),
            f"unexpected authorize redirect: {location}",
        )
        self.assertIn("state=state-1", location)
        self.assertIn("response_type=code", location)


if __name__ == "__main__":
    unittest.main()
