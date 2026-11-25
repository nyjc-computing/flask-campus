"""Unit tests for the login blueprint's mount prefix (flask-campus#47).

A deployment behind a path-dispatching gateway sets its namespace so the
auth surface mounts at /<namespace>/login instead of the root-level
/login four consumer apps share — the gateway cannot dispatch root-level
auth paths. The default stays the standalone root mount, so every
existing consumer keeps its exact behavior.
"""

import os
import unittest
from types import SimpleNamespace

import flask
from campus_python.auth.v1 import set_session_id_provider
from campus_python.auth.v1.logins import LOGIN_STORAGE_KEY

from campus.flask_campus.login_manager import OAuthLoginManager


class _FakeAuth:
    """Minimal stand-in for the campus_python auth resource surface
    that flask_campus.context orchestrates (login + finalize flows)."""

    base_url = "https://campusauth.test"

    def __init__(self):
        self.pushed = False
        self.session_new_kwargs = None
        self.finalized = False

        def _has_session():
            self.pushed = True
            return False

        def _new(**kwargs):
            self.session_new_kwargs = kwargs
            return SimpleNamespace(id="state-1")

        self.sessions = SimpleNamespace(has_session=_has_session, new=_new)
        self.logins = SimpleNamespace(has_session=lambda: False)

    def make_path(self, endpoint):
        return f"/auth/v1/{endpoint}"


class _FakeCampus:
    def __init__(self):
        self.auth = _FakeAuth()


class _FinalizeSessions:
    """sessions surface for the finalize flow: from_code accepts any
    code and the session finalize hands back a fixed redirect."""

    def __init__(self, auth: "_FinalizeAuth"):
        self._auth = auth

    def has_session(self):
        self._auth.pushed = True
        return False

    def from_code(self, code):
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


class _FinalizeLogins:
    def has_session(self):
        return False

    def new(self, **kwargs):
        return SimpleNamespace(id="login-1")


class _FinalizeAuth:
    def __init__(self):
        self.pushed = False
        self.finalized = False
        self.sessions = _FinalizeSessions(self)
        self.logins = _FinalizeLogins()

    def exchange_code_for_token(self, code, redirect_uri):
        return SimpleNamespace(access_token="tok")


class _FinalizeCampus:
    def __init__(self):
        self.auth = _FinalizeAuth()


class _PrefixCase(unittest.TestCase):
    """Shared env pinning: the login flow resolves PUBLIC_URL and
    CLIENT_ID at request time; finalize needs ENV for the push."""

    def setUp(self):
        self.saved = {
            name: os.environ.get(name)
            for name in ("ENV", "PUBLIC_URL", "CLIENT_ID")
        }
        os.environ["ENV"] = "testing"
        os.environ["PUBLIC_URL"] = "https://campus.test"
        os.environ["CLIENT_ID"] = "uid-client-test"

    def tearDown(self):
        set_session_id_provider(None)
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    @staticmethod
    def _prefixed_login_routes(app) -> set[str]:
        return {
            str(rule)
            for rule in app.url_map.iter_rules()
            if "login" in str(rule) or "logout" in str(rule)
        }


class TestLoginPrefixMounting(_PrefixCase):
    """The blueprint mounts at the configured prefix; default is root."""

    def _make_app(self, **kwargs) -> flask.Flask:
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        manager = OAuthLoginManager(
            campus_client=_FakeCampus(),  # type: ignore[arg-type]
            **kwargs,
        )
        manager.init_app(app)
        return app

    def test_default_mounts_at_root(self):
        app = self._make_app()
        routes = self._prefixed_login_routes(app)
        self.assertIn("/login", routes)
        self.assertIn("/finalize_login", routes)
        self.assertIn("/logout", routes)
        self.assertNotIn("/timetable/login", routes)

    def test_prefix_mounts_namespaced(self):
        app = self._make_app(login_prefix="timetable")
        routes = self._prefixed_login_routes(app)
        self.assertIn("/timetable/login", routes)
        self.assertIn("/timetable/finalize_login", routes)
        self.assertIn("/timetable/logout", routes)
        self.assertNotIn("/login", routes)

    def test_prefix_normalization_variants_are_equivalent(self):
        routes = [
            self._prefixed_login_routes(self._make_app(login_prefix=variant))
            for variant in ("timetable", "/timetable", "/timetable/", " timetable ")
        ]
        self.assertTrue(all(r == routes[0] for r in routes))
        self.assertIn("/timetable/login", routes[0])

    def test_env_prefix_honored(self):
        os.environ["LOGIN_URL_PREFIX"] = "profile"
        try:
            app = self._make_app()
        finally:
            os.environ.pop("LOGIN_URL_PREFIX", None)
        self.assertIn("/profile/login", self._prefixed_login_routes(app))

    def test_explicit_arg_overrides_env(self):
        os.environ["LOGIN_URL_PREFIX"] = "fromenv"
        try:
            app = self._make_app(login_prefix="/fromarg")
        finally:
            os.environ.pop("LOGIN_URL_PREFIX", None)
        routes = self._prefixed_login_routes(app)
        self.assertIn("/fromarg/login", routes)
        self.assertNotIn("/fromenv/login", routes)

    def test_explicit_empty_prefix_disables_env(self):
        os.environ["LOGIN_URL_PREFIX"] = "fromenv"
        try:
            app = self._make_app(login_prefix="")
        finally:
            os.environ.pop("LOGIN_URL_PREFIX", None)
        routes = self._prefixed_login_routes(app)
        self.assertIn("/login", routes)
        self.assertNotIn("/fromenv/login", routes)


class TestPrefixedLoginFlow(_PrefixCase):
    """The login flow works under the prefix: redirects authorize
    through the fake auth host and honor safe next destinations."""

    def _make_client(self, login_prefix: str | None = None):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"

        @app.get("/")
        def index():
            return ""

        manager = OAuthLoginManager(
            campus_client=_FakeCampus(),  # type: ignore[arg-type]
            login_prefix=login_prefix,
        )
        manager.init_app(app)
        return manager, app.test_client()

    def test_login_under_prefix_starts_authorize_flow(self):
        _, client = self._make_client(login_prefix="timetable")
        response = client.get("/timetable/login?next=/timetable/dash")
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("https://campusauth.test/auth/v1/authorize", location)
        with client.session_transaction() as session:
            self.assertEqual(session.get("login_next"), "/timetable/dash")

    def test_root_login_is_404_under_prefix(self):
        _, client = self._make_client(login_prefix="timetable")
        self.assertEqual(client.get("/login").status_code, 404)

    def test_finalize_under_prefix_defaults_to_namespace_root(self):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        manager = OAuthLoginManager(
            campus_client=_FinalizeCampus(),  # type: ignore[arg-type]
            login_prefix="timetable",
        )
        manager.init_app(app)
        client = app.test_client()
        response = client.get(
            "/timetable/finalize_login?code=x&state=y&scope=z"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/timetable/")
        with client.session_transaction() as session:
            self.assertEqual(session[LOGIN_STORAGE_KEY], "login-1")

    def test_standalone_finalize_default_stays_root(self):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        manager = OAuthLoginManager(
            campus_client=_FinalizeCampus(),  # type: ignore[arg-type]
        )
        manager.init_app(app)
        response = app.test_client().get(
            "/finalize_login?code=x&state=y&scope=z"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_required_redirects_to_prefixed_login(self):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        manager = OAuthLoginManager(
            campus_client=_FakeCampus(),  # type: ignore[arg-type]
            login_prefix="timetable",
        )
        manager.init_app(app)

        @manager.login_required
        def view():
            return ""

        ctx = app.test_request_context("/timetable/protected")
        ctx.push()
        self.addCleanup(ctx.pop)
        response = view()
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "/timetable/login?next=/timetable/protected",
            response.headers["Location"],
        )


if __name__ == "__main__":
    unittest.main()
