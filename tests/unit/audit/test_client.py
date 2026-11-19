"""Unit tests for the tracing middleware's audit-client credential
handling.

Verifies that the middleware's client singleton accepts AUDIT_API_KEY
alone without requiring CLIENT_ID/CLIENT_SECRET, and is recreated when
the key rotates.

The client's own auth-priority tests moved with the client into the
SDK (campus_python.audit; flask-campus#31).

File: tests/unit/audit/test_client.py
Issue: #699, flask-campus#31
"""

import unittest
from unittest import mock

from campus_python.audit import AuditClient


def _del_env(test: unittest.TestCase, name: str) -> None:
    """Unset an env var for the duration of a test."""
    saved = __import__("os").environ.pop(name, None)

    def restore():
        import os
        if saved is not None:
            os.environ[name] = saved

    test.addCleanup(restore)


def _set_env(test: unittest.TestCase, name: str, value: str) -> None:
    """Set an env var for the duration of a test."""
    import os
    os.environ[name] = value
    test.addCleanup(__import__("os").environ.pop, name, None)


class TestGetAuditClientCredentials(unittest.TestCase):
    """Verify the tracing middleware client credential rules (#699)."""

    def setUp(self):
        from campus.flask_campus.audit import tracing

        # Save/restore the module-level client singleton so tests don't
        # leak credentials into other suites
        self._saved_client = tracing._audit_client
        self._saved_credentials = tracing._client_credentials
        self.addCleanup(setattr, tracing, "_audit_client", self._saved_client)
        self.addCleanup(setattr, tracing, "_client_credentials", self._saved_credentials)
        tracing._audit_client = None
        tracing._client_credentials = None
        # _get_base_url resolves PUBLIC_URL under ENV=testing; pin it
        patcher = mock.patch("campus_python.audit.client._get_base_url", return_value="https://audit.test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_api_key_alone_is_sufficient(self):
        """AUDIT_API_KEY alone creates the client without OAuth creds."""
        from campus.flask_campus.audit import tracing

        _del_env(self, "CLIENT_ID")
        _del_env(self, "CLIENT_SECRET")
        _del_env(self, "ACCESS_TOKEN")
        _set_env(self, "AUDIT_API_KEY", "audit_v1_abcdefghijklmnopqrstuvwxyz")
        client = tracing._get_audit_client()
        self.assertIsInstance(client, AuditClient)

    def test_missing_credentials_raises(self):
        """No AUDIT_API_KEY and no CLIENT_ID/CLIENT_SECRET raises."""
        from campus.flask_campus.audit import tracing

        _del_env(self, "CLIENT_ID")
        _del_env(self, "CLIENT_SECRET")
        _del_env(self, "AUDIT_API_KEY")
        with self.assertRaises(ValueError):
            tracing._get_audit_client()

    def test_client_recreated_when_api_key_changes(self):
        """The singleton is recreated when AUDIT_API_KEY is rotated."""
        from campus.flask_campus.audit import tracing

        _del_env(self, "CLIENT_ID")
        _del_env(self, "CLIENT_SECRET")
        _del_env(self, "ACCESS_TOKEN")
        _set_env(self, "AUDIT_API_KEY", "audit_v1_aaaaaaaaaaaaaaaaaaaaaa")
        first = tracing._get_audit_client()
        _set_env(self, "AUDIT_API_KEY", "audit_v1_bbbbbbbbbbbbbbbbbbbbbb")
        second = tracing._get_audit_client()
        self.assertIsNot(second, first)


if __name__ == "__main__":
    unittest.main()
