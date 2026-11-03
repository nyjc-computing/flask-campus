"""Unit tests for the push_context hook's span-identity stash (#820).

The OAuthLoginManager's app-wide hook stamps g.user_id/g.client_id so
the audit tracing middleware can attribute page-load root spans to the
app's client and the signed-in user.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import flask

from campus.flask_campus.login_manager import OAuthLoginManager

_PUSH_TARGET = "campus.flask_campus.login_manager.context.push_context"


class TestPushContextIdentity(unittest.TestCase):
    """_push_context_hook() stashes span identity after push_context."""

    def setUp(self):
        self.saved_client_id = os.environ.get("CLIENT_ID")
        self.addCleanup(self._restore_env)
        self.app = flask.Flask(__name__)
        self.pushed = []

    def _restore_env(self):
        if self.saved_client_id is None:
            os.environ.pop("CLIENT_ID", None)
        else:
            os.environ["CLIENT_ID"] = self.saved_client_id

    def _manager(self, user=None):
        """Login manager whose context push stashes the given user."""
        def push_context(_campus, _cache):
            self.pushed.append(True)
            if user is not None:
                flask.g.user = user

        patcher = patch(_PUSH_TARGET, side_effect=push_context)
        patcher.start()
        self.addCleanup(patcher.stop)
        return OAuthLoginManager(
            campus_client=SimpleNamespace()  # type: ignore[arg-type]
        )

    def test_stamps_user_and_client(self):
        os.environ["CLIENT_ID"] = "uid-client-test"
        manager = self._manager(user=SimpleNamespace(id="user@nyjc.edu.sg"))

        with self.app.test_request_context("/dashboard"):
            manager._push_context_hook()

            self.assertEqual(flask.g.user_id, "user@nyjc.edu.sg")
            self.assertEqual(flask.g.client_id, "uid-client-test")

    def test_stamps_client_without_user(self):
        """Anonymous page loads still carry the app's client."""
        os.environ["CLIENT_ID"] = "uid-client-test"
        manager = self._manager(user=None)

        with self.app.test_request_context("/"):
            manager._push_context_hook()

            self.assertFalse(hasattr(flask.g, "user_id"))
            self.assertEqual(flask.g.client_id, "uid-client-test")

    def test_no_client_id_env_no_stash(self):
        os.environ.pop("CLIENT_ID", None)
        manager = self._manager(user=None)

        with self.app.test_request_context("/"):
            manager._push_context_hook()

            self.assertFalse(hasattr(flask.g, "client_id"))

    def test_static_requests_skip_entirely(self):
        """Static endpoints stay unpushed and unstamped (#689)."""
        os.environ["CLIENT_ID"] = "uid-client-test"
        manager = self._manager(user=SimpleNamespace(id="user@nyjc.edu.sg"))

        with self.app.test_request_context("/static/css/style.css"):
            manager._push_context_hook()

            self.assertEqual(self.pushed, [])
            self.assertFalse(hasattr(flask.g, "user_id"))
            self.assertFalse(hasattr(flask.g, "client_id"))


if __name__ == "__main__":
    unittest.main()
