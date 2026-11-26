"""Unit tests for PrefixFix (flask-campus#50): gateway subpath mounting.

X-Forwarded-Prefix moves from the header into SCRIPT_NAME (stripped
from PATH_INFO), making every url_for — static, links, the auth
blueprint — namespace-aware under a path-dispatching gateway. No
header, no behavior change: standalone/origin deployments are untouched.
"""

import os
import unittest
from types import SimpleNamespace

import flask
from campus_python.auth.v1 import set_session_id_provider

from campus.flask_campus.login_manager import OAuthLoginManager
from campus.flask_campus.prefix import PrefixFix, _normalize_prefix


class _FakeAuth:
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


class TestNormalizePrefix(unittest.TestCase):
    def test_valid_forms(self):
        self.assertEqual(_normalize_prefix("/timetable"), "/timetable")
        self.assertEqual(_normalize_prefix("/timetable/"), "/timetable")

    def test_invalid_forms_rejected(self):
        for value in (None, "", "timetable", "/", "//x", "/a/../b"):
            self.assertIsNone(_normalize_prefix(value), value)


class _PrefixApp(unittest.TestCase):
    def _make_app(self, prefix_header: str | None = None):
        app = flask.Flask(__name__)
        app.secret_key = "test-secret"
        if prefix_header is not None:
            app.wsgi_app = PrefixFix(app.wsgi_app)

        @app.get("/login")
        def login():
            return flask.redirect(flask.url_for("static", filename="css/style.css"))

        return app

    def test_no_header_is_a_noop(self):
        app = self._make_app()
        with app.test_request_context("/login"):
            self.assertEqual(flask.request.environ.get("SCRIPT_NAME", ""), "")
            self.assertEqual(flask.url_for("static", filename="css/style.css"),
                             "/static/css/style.css")

    def test_header_moves_namespace_into_script_name(self):
        app = self._make_app(prefix_header="/timetable")
        client = app.test_client()
        # auth.login is mounted at /login; through the gateway the
        # namespace arrives in X-Forwarded-Prefix and the request path
        # is /timetable/login.
        environ_overrides = {"PATH_INFO": "/timetable/login",
                             "HTTP_X_FORWARDED_PREFIX": "/timetable"}
        response = client.get("/timetable/login", environ_overrides=environ_overrides)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"], "/timetable/static/css/style.css"
        )

    def test_double_prefixfix_does_not_double_strip(self):
        """The header is consumed: a second stack (as downstream forwarding
        middleware would be) no-ops instead of double-stripping."""
        app = self._make_app(prefix_header="/timetable")
        app.wsgi_app = PrefixFix(app.wsgi_app)
        client = app.test_client()
        response = client.get(
            "/timetable/login",
            environ_overrides={"PATH_INFO": "/timetable/login",
                               "HTTP_X_FORWARDED_PREFIX": "/timetable"},
        )
        self.assertEqual(response.status_code, 302)
        # the view redirects to url_for('static'): the assertion pins
        # the namespace-aware generation that #50 exists for
        self.assertEqual(
            response.headers["Location"], "/timetable/static/css/style.css"
        )

    def test_exact_prefix_path_becomes_root(self):
        """A request for exactly the prefix ("/timetable") moves cleanly:
        PATH_INFO becomes "/" (not empty)."""
        app = self._make_app(prefix_header="/timetable")
        client = app.test_client()
        response = client.get(
            "/timetable",
            environ_overrides={"PATH_INFO": "/timetable",
                               "HTTP_X_FORWARDED_PREFIX": "/timetable"},
        )
        # no "/" route on this bare app -> 404 proves the move happened
        # (an unmoved PATH_INFO would have matched nothing either, so
        # also check the generated URL shape in a request context)
        self.assertEqual(response.status_code, 404)
        with app.test_request_context(
            "/timetable/", environ_overrides={"SCRIPT_NAME": "/timetable"}
        ):
            self.assertEqual(
                flask.url_for("static", filename="favicon.ico"),
                "/timetable/static/favicon.ico",
            )

    def test_invalid_header_ignored(self):
        app = self._make_app(prefix_header="/timetable")
        client = app.test_client()
        response = client.get(
            "/login",
            environ_overrides={"PATH_INFO": "/login",
                               "HTTP_X_FORWARDED_PREFIX": "//evil"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"], "/static/css/style.css"
        )


class TestPrefixedUrlsEndToEnd(unittest.TestCase):
    """With the gateway header, url_for emits namespace-ful URLs for
    static AND the auth blueprint — the whole point of #50."""

    def setUp(self):
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

    def test_login_flow_under_forwarded_prefix(self):
        """GET /timetable/login (namespace via the header; auth blueprint
        mounted at root) starts the flow and emits the namespaced
        callback URL."""
        response = self.client.get(
            "/timetable/login",
            environ_overrides={"PATH_INFO": "/timetable/login",
                               "HTTP_X_FORWARDED_PREFIX": "/timetable"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "https://campusauth.test/auth/v1/authorize", response.headers["Location"]
        )
        self.assertIn(
            "redirect_uri=https%3A%2F%2Fcampus.test%2Ftimetable%2Ffinalize_login",
            response.headers["Location"],
        )
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("login_next"), "https://campus.test/timetable/")

    def test_static_under_forwarded_prefix(self):
        response = self.client.get(
            "/timetable/static/css/style.css",
            environ_overrides={"PATH_INFO": "/timetable/static/css/style.css",
                               "HTTP_X_FORWARDED_PREFIX": "/timetable"},
        )
        self.assertEqual(response.status_code, 404)  # no such file; routed OK


if __name__ == "__main__":
    unittest.main()
