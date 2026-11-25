"""flask_campus drives its own Campus, never the host's (flask-campus#40).

The host's client is shared with its route handlers, which run
with_user_session()/with_app_session() blocks from gthread request
threads — those blocks leave the current block's credential on the
instance. If the framework's ambient calls (push_context resolution,
the login flow) rode the same instance, they could present a USER
bearer; campus.auth denies user principals on session-flow routes
(#854/#1055) and the request degrades to anonymous, looping the browser
into /login (classroom#153). Every framework call must therefore go
through the framework-owned instance.
"""

import os
import unittest
from unittest.mock import patch

import campus_python
import flask
from campus_python.auth.v1 import set_session_id_provider

from campus.flask_campus.login_manager import OAuthLoginManager

# The framework instance is constructed unconditionally at manager init.
os.environ.setdefault("CLIENT_ID", "test-client-id")
os.environ.setdefault("CLIENT_SECRET", "test-client-secret")
# The /login and /logout routes build absolute URLs (campus#652).
os.environ.setdefault("PUBLIC_URL", "https://classroom.test")
os.environ.setdefault("CAMPUS_AUTH_URL", "https://campusauth.test/auth/v1")

_PUSH_TARGET = "campus.flask_campus.login_manager.context.push_context"


class TestFrameworkOwnedCampus(unittest.TestCase):
    """Framework calls never touch the host's Campus instance."""

    def setUp(self):
        self.app = flask.Flask(__name__)
        self.app.secret_key = "test-secret"
        self.pushed: list[object] = []
        patcher = patch(
            _PUSH_TARGET,
            side_effect=lambda campus, _cache: self.pushed.append(campus),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(set_session_id_provider, None)
        self.host_client = campus_python.Campus()
        self.manager = OAuthLoginManager(campus_client=self.host_client)
        self.manager.init_app(self.app)

        @self.app.get("/")
        def index():
            return "ok"

    def test_framework_campus_is_distinct(self):
        self.assertIsNot(self.manager._framework_campus, self.host_client)
        self.assertIsInstance(self.manager._framework_campus, campus_python.Campus)

    def test_push_uses_framework_campus(self):
        self.app.test_client().get("/")
        self.assertEqual(len(self.pushed), 1)
        self.assertIs(self.pushed[0], self.manager._framework_campus)
        self.assertIsNot(self.pushed[0], self.host_client)

    def test_login_flow_uses_framework_campus(self):
        with patch(
            "campus.flask_campus.login_manager.context.start_login"
        ) as start_login:
            self.app.test_client().get("/login")
        (campus,), _kwargs = start_login.call_args
        self.assertIs(campus, self.manager._framework_campus)
        self.assertIsNot(campus, self.host_client)

    def test_finalize_uses_framework_campus(self):
        finalize = patch(
            "campus.flask_campus.login_manager.context.finalize_login"
        )
        push = patch(
            _PUSH_TARGET, side_effect=lambda _campus, _cache: None
        )
        with finalize as finalize_mock, push:
            resp = self.app.test_client().get(
                "/finalize_login?code=abc&state=def&scope=campus.profile"
            )
        self.assertEqual(resp.status_code, 302)
        (campus,), _kwargs = finalize_mock.call_args
        self.assertIs(campus, self.manager._framework_campus)
        self.assertIsNot(campus, self.host_client)

    def test_logout_uses_framework_campus(self):
        with patch(
            "campus.flask_campus.login_manager.context.logout"
        ) as logout_mock:
            self.app.test_client().get("/logout")
        (campus, _cache), _kwargs = logout_mock.call_args
        self.assertIs(campus, self.manager._framework_campus)
        self.assertIsNot(campus, self.host_client)


if __name__ == "__main__":
    unittest.main()
