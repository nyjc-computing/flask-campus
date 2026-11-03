"""Unit tests for the app-wide push_context hook's static-endpoint skip.

OAuthLoginManager.init_app registers the context push on the whole
app, so every request — stylesheets and scripts included — paid the
upstream auth lookup even though static assets never read g.user
(#689). Static endpoints (app-level and blueprint-level) now skip it.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import flask
from campus_python.auth.v1 import set_session_id_provider

from campus.flask_campus.login_manager import OAuthLoginManager

_PUSH_TARGET = "campus.flask_campus.login_manager.context.push_context"


class TestPushContextHook(unittest.TestCase):
    """Static asset requests stay out of the auth path."""

    def setUp(self):
        self.app = flask.Flask(__name__)
        self.app.secret_key = "test-secret"
        self.pushed: list[str] = []
        patcher = patch(
            _PUSH_TARGET,
            side_effect=lambda _campus, _cache: self.pushed.append(
                flask.request.path
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(set_session_id_provider, None)
        manager = OAuthLoginManager(
            campus_client=SimpleNamespace()  # type: ignore[arg-type]
        )
        manager.init_app(self.app)

        @self.app.get("/")
        def index():
            return "ok"

    def test_skips_app_static_assets(self):
        client = self.app.test_client()
        # The asset itself may 404 (harness has no static folder); the
        # point is that no auth lookup runs for the request.
        client.get("/static/style.css")
        self.assertEqual(self.pushed, [])

    def test_skips_blueprint_static_assets(self):
        admin_bp = flask.Blueprint("admin", __name__, static_folder="static")

        @admin_bp.get("/")
        def admin_home():
            return "ok"

        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        client = self.app.test_client()
        client.get("/admin/static/style.css")
        self.assertEqual(self.pushed, [])

    def test_pushes_for_regular_routes(self):
        client = self.app.test_client()
        client.get("/")
        self.assertEqual(self.pushed, ["/"])


if __name__ == "__main__":
    unittest.main()
