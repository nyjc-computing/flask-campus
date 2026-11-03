"""Unit tests for finalize_login's handling of rejected codes.

The auth service rejects authorization codes as invalid for routine
reasons in a browser flow — a replayed single-use code (refresh,
back-button), or a code that expired on the consent screen. The
callback must send the user back to sign-in rather than serve an
unhandled 500 (#690).
"""

import unittest
from types import SimpleNamespace

import campus_python
import flask
from campus_python.auth.v1 import set_session_id_provider
from campus_python.auth.v1.logins import LOGIN_STORAGE_KEY

from campus.flask_campus.login_manager import OAuthLoginManager


class _FakeSessions:
    """sessions resource stand-in: the surface context drives."""

    def __init__(self, auth: "_FakeAuth"):
        self._auth = auth

    def has_session(self):
        # The context push consults this first; recording here is how
        # the fake reports that a push ran.
        self._auth.pushed = True
        return False

    def from_code(self, code):
        if self._auth.finalize_error is not None:
            raise self._auth.finalize_error
        self._auth.finalized = True
        return SimpleNamespace(
            id="y",
            user_id="user-1",
            redirect_uri="https://campus.test/cb",
            device_id="device-1",
        )

    def __getitem__(self, session_id):
        return SimpleNamespace(
            finalize=lambda: (
                "https://campus.test/",
                SimpleNamespace(id="user-1"),
            )
        )


class _FakeLogins:
    def __init__(self):
        self.created = None

    def has_session(self):
        return False

    def new(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(id="login-1")


class _FakeAuth:
    """Minimal stand-in for the campus_python auth resource surface
    that flask_campus.context orchestrates."""

    def __init__(self, finalize_error: Exception | None = None):
        self.finalize_error = finalize_error
        self.finalized = False
        self.pushed = False
        self.sessions = _FakeSessions(self)
        self.logins = _FakeLogins()

    def exchange_code_for_token(self, code, redirect_uri):
        return SimpleNamespace(access_token="tok")


class _FakeCampus:
    def __init__(self, finalize_error: Exception | None = None):
        self.auth = _FakeAuth(finalize_error)


class TestFinalizeLoginRejection(unittest.TestCase):
    """A rejected authorization code redirects to sign-in, not a 500."""

    def tearDown(self):
        set_session_id_provider(None)

    def _make_app(self, finalize_error: Exception | None):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        manager = OAuthLoginManager(
            campus_client=_FakeCampus(finalize_error)  # type: ignore[arg-type]
        )
        manager.init_app(app)
        return app, manager

    def test_rejected_code_redirects_to_sign_in(self):
        err = campus_python.errors.BadRequestError("Invalid authorization code")
        app, manager = self._make_app(err)
        client = app.test_client()
        response = client.get("/finalize_login?code=x&state=y&scope=z")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        with client.session_transaction() as session:
            flashes = [msg for _, msg in session.get("_flashes", [])]
        self.assertTrue(
            any("sign in again" in msg for msg in flashes),
            f"expected a sign-in flash message, got {flashes}"
        )

    def test_valid_code_completes_and_redirects(self):
        app, manager = self._make_app(None)
        client = app.test_client()
        response = client.get("/finalize_login?code=x&state=y&scope=z")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(manager.campus.auth.finalized)
        self.assertTrue(manager.campus.auth.pushed)
        # The login session id lands in context-owned storage.
        with client.session_transaction() as session:
            self.assertEqual(session[LOGIN_STORAGE_KEY], "login-1")


if __name__ == "__main__":
    unittest.main()
