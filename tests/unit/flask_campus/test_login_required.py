"""Unit tests for OAuthLoginManager.login_required argument forwarding.

Flask calls views with keyword arguments, so routing was never broken —
but the wrapper rejected positional arguments, which breaks direct view
invocation in tests (#772).
"""

import unittest
from types import SimpleNamespace

import flask

from campus.flask_campus.login_manager import OAuthLoginManager


class TestLoginRequiredForwarding(unittest.TestCase):
    """The wrapper passes both positional and keyword args to the view."""

    def setUp(self):
        self.app = flask.Flask(__name__)
        self.app.secret_key = "test-secret"
        # The signed-out branch redirects to auth.login; provide the
        # endpoint without pulling in the full OAuth blueprint.
        self.app.add_url_rule("/login", "auth.login", lambda: "")
        self.manager = OAuthLoginManager(campus_client=object())

    def _signed_in(self, path="/"):
        ctx = self.app.test_request_context(path)
        ctx.push()
        self.addCleanup(ctx.pop)
        flask.g.user = SimpleNamespace(id="user@example.com")

    def test_forwards_positional_and_keyword_args(self):
        @self.manager.login_required
        def view(slug, extra=None):
            return slug, extra

        self._signed_in()
        self.assertEqual(view("classroom"), ("classroom", None))
        self.assertEqual(view("classroom", extra=1), ("classroom", 1))

    def test_redirects_when_signed_out(self):
        @self.manager.login_required
        def view(slug):
            return slug

        ctx = self.app.test_request_context("/protected")
        ctx.push()
        self.addCleanup(ctx.pop)
        response = view("classroom")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
