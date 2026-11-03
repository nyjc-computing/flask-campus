"""Unit tests for the audit client credential handling.

Verifies that AuditClient sends the AUDIT_API_KEY as explicit Bearer
auth (scoped to the audit client only) and that the tracing middleware
accepts AUDIT_API_KEY alone without requiring CLIENT_ID/CLIENT_SECRET.

File: tests/unit/audit/test_client.py
Issue: #699
"""

import os
import unittest
from unittest import mock

from campus.audit.client import AuditClient


def _set_env(test: unittest.TestCase, name: str, value: str) -> None:
    """Set an env var for the duration of a test."""
    os.environ[name] = value
    test.addCleanup(os.environ.pop, name, None)


def _del_env(test: unittest.TestCase, name: str) -> None:
    """Unset an env var for the duration of a test."""
    saved = os.environ.pop(name, None)

    def restore():
        if saved is not None:
            os.environ[name] = saved

    test.addCleanup(restore)


class TestCreateHttpClientAuth(unittest.TestCase):
    """Verify _create_http_client auth priority (#699)."""

    def setUp(self):
        # Save/restore test-injection attribute in case another suite set it
        self._saved_client_class = AuditClient.json_client_class
        self.addCleanup(setattr, AuditClient, "json_client_class", self._saved_client_class)
        AuditClient.json_client_class = None

    def test_api_key_sends_explicit_bearer(self):
        """AUDIT_API_KEY produces a Bearer Authorization header."""
        from campus.audit.client import _create_http_client
        from campus.audit.http import DefaultClient

        _set_env(self, "AUDIT_API_KEY", "audit_v1_abcdefghijklmnopqrstuvwxyz")
        client = _create_http_client("https://audit.test")
        self.assertIsInstance(client, DefaultClient)
        self.assertEqual(
            client.headers["Authorization"],
            "Bearer audit_v1_abcdefghijklmnopqrstuvwxyz"
        )

    def test_api_key_ignores_ambient_credentials(self):
        """AUDIT_API_KEY wins over ambient ACCESS_TOKEN/CLIENT_ID env.

        The explicit auth path must not inherit the shared ambient
        credentials used by other DefaultClient consumers (#699).
        """
        from campus.audit.client import _create_http_client

        _set_env(self, "AUDIT_API_KEY", "audit_v1_abcdefghijklmnopqrstuvwxyz")
        _set_env(self, "ACCESS_TOKEN", "ambient-access-token")
        _set_env(self, "CLIENT_ID", "uid-client-ambient")
        _set_env(self, "CLIENT_SECRET", "ambient-secret")
        client = _create_http_client("https://audit.test")
        self.assertEqual(
            client.headers["Authorization"],
            "Bearer audit_v1_abcdefghijklmnopqrstuvwxyz"
        )

    def test_without_api_key_falls_back_to_ambient_basic(self):
        """Without AUDIT_API_KEY, ambient CLIENT_ID/CLIENT_SECRET apply."""
        from campus.audit.client import _create_http_client
        from campus.audit.http import DefaultClient

        _del_env(self, "AUDIT_API_KEY")
        _del_env(self, "ACCESS_TOKEN")
        _set_env(self, "CLIENT_ID", "uid-client-legacy")
        _set_env(self, "CLIENT_SECRET", "legacy-secret")
        client = _create_http_client("https://audit.test")
        self.assertIsInstance(client, DefaultClient)
        self.assertTrue(client.headers["Authorization"].startswith("Basic "))

    def test_json_client_class_takes_priority(self):
        """Test-injection json_client_class wins over AUDIT_API_KEY."""

        class StubClient:
            def __init__(self, base_url: str):
                self.base_url = base_url

        AuditClient.json_client_class = StubClient  # type: ignore[assignment]
        _set_env(self, "AUDIT_API_KEY", "audit_v1_abcdefghijklmnopqrstuvwxyz")
        client = AuditClient(base_url="https://audit.test")
        self.assertIsInstance(client._root._client, StubClient)


class TestGetAuditClientCredentials(unittest.TestCase):
    """Verify the tracing middleware client credential rules (#699)."""

    def setUp(self):
        from campus.audit.middleware import tracing

        # Save/restore the module-level client singleton so tests don't
        # leak credentials into other suites
        self._saved_client = tracing._audit_client
        self._saved_credentials = tracing._client_credentials
        self.addCleanup(setattr, tracing, "_audit_client", self._saved_client)
        self.addCleanup(setattr, tracing, "_client_credentials", self._saved_credentials)
        tracing._audit_client = None
        tracing._client_credentials = None
        # _get_base_url resolves PUBLIC_URL under ENV=testing; pin it
        patcher = mock.patch("campus.audit.client._get_base_url", return_value="https://audit.test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_api_key_alone_is_sufficient(self):
        """AUDIT_API_KEY alone creates the client without OAuth creds."""
        from campus.audit.middleware import tracing

        _del_env(self, "CLIENT_ID")
        _del_env(self, "CLIENT_SECRET")
        _del_env(self, "ACCESS_TOKEN")
        _set_env(self, "AUDIT_API_KEY", "audit_v1_abcdefghijklmnopqrstuvwxyz")
        client = tracing._get_audit_client()
        self.assertIsInstance(client, AuditClient)

    def test_missing_credentials_raises(self):
        """No AUDIT_API_KEY and no CLIENT_ID/CLIENT_SECRET raises."""
        from campus.audit.middleware import tracing

        _del_env(self, "CLIENT_ID")
        _del_env(self, "CLIENT_SECRET")
        _del_env(self, "AUDIT_API_KEY")
        with self.assertRaises(ValueError):
            tracing._get_audit_client()

    def test_client_recreated_when_api_key_changes(self):
        """The singleton is recreated when AUDIT_API_KEY is rotated."""
        from campus.audit.middleware import tracing

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
