"""Tests for the producer-side redaction surface (flask-campus#20).

The policy sweep (campus#1131, environment-access-policy §3) extends
``redact_sensitive`` with flow secrets (authorization_code / otp /
user_code), person names, and value-aware email redaction (workspace
carve-out per the campus#1125 ruling); constrains header-supplied
journey names to a safe charset; and keeps audit-service error bodies
out of WARNING logs. These tests live in THIS repo because this dist
ships the redactor — a silent list regression must fail here, not only
downstream.
"""

import unittest
from unittest import mock

from campus.audit.middleware import redact_sensitive
from campus.audit.middleware.journeys import _clean_journey_name


class TestFlowSecretRedaction(unittest.TestCase):
    """Bearer-equivalent flow secrets must never reach storage."""

    def test_authorization_code_is_redacted(self):
        body = {"user_id": "u1", "authorization_code": "4/0Ab_5qn"}
        self.assertEqual(
            redact_sensitive(body),
            {"user_id": "u1", "authorization_code": "[REDACTED]"},
        )

    def test_otp_is_redacted(self):
        body = {"email": "student@nyjc.edu.sg", "otp": "123456"}
        self.assertEqual(
            redact_sensitive(body),
            {"email": "student@nyjc.edu.sg", "otp": "[REDACTED]"},
        )

    def test_user_code_is_redacted(self):
        self.assertEqual(
            redact_sensitive({"user_code": "ABCD-EFGH"}),
            {"user_code": "[REDACTED]"},
        )

    def test_code_suffix_variant_is_redacted(self):
        """authorization_code without underscores/dashes still matches."""
        self.assertEqual(
            redact_sensitive({"authorization-code": "x"}),
            {"authorization-code": "[REDACTED]"},
        )

    def test_non_secret_code_keys_survive(self):
        """Bare *_code keys stay deferred to #806; error_code is data."""
        body = {"error_code": "invalid_grant", "status_code": 200}
        self.assertEqual(redact_sensitive(body), body)

    def test_bearer_token_suffix_still_redacted(self):
        """Existing suffix behavior (google_refresh_token) unchanged."""
        self.assertEqual(
            redact_sensitive({"google_refresh_token": "t"}),
            {"google_refresh_token": "[REDACTED]"},
        )


class TestNameRedaction(unittest.TestCase):
    """Person names are PII (policy §3), not just secrets."""

    def test_name_keys_are_redacted(self):
        body = {"name": "Jun Siang", "full_name": "Jun Siang Ng",
                "given_name": "Jun", "family_name": "Ng"}
        redacted = redact_sensitive(body)
        self.assertEqual(set(redacted.values()), {"[REDACTED]"})

    def test_non_person_names_survive(self):
        """Non-person name-ish keys (journey_name, label) stay readable."""
        body = {"journey_name": "campus.test", "label": "venue-a"}
        self.assertEqual(redact_sensitive(body), body)


class TestEmailRedaction(unittest.TestCase):
    """Emails are PII except college-issued addresses (#1125 ruling)."""

    def test_workspace_email_survives(self):
        body = {"email": "student@nyjc.edu.sg"}
        self.assertEqual(redact_sensitive(body), body)

    def test_workspace_subdomain_email_survives(self):
        body = {"email": "student@moe.edu.sg.staff.nyjc.edu.sg"}
        self.assertEqual(redact_sensitive(body), body)

    def test_external_email_is_redacted(self):
        body = {"email": "personal@gmail.com"}
        self.assertEqual(
            redact_sensitive(body),
            {"email": "[REDACTED]"},
        )

    def test_lookalike_domain_is_redacted(self):
        body = {"email": "student@notnyjc.edu.sg"}
        self.assertEqual(
            redact_sensitive(body),
            {"email": "[REDACTED]"},
        )

    def test_email_suffix_key_is_redacted(self):
        body = {"user_email": "personal@gmail.com"}
        self.assertEqual(
            redact_sensitive(body),
            {"user_email": "[REDACTED]"},
        )

    def test_malformed_email_value_is_redacted(self):
        """Non-string/malformed values under email keys fail closed."""
        for value in (None, 42, "not-an-email", {"e": 1}):
            self.assertEqual(
                redact_sensitive({"email": value}),
                {"email": "[REDACTED]"},
                value,
            )

    def test_workspace_email_in_otp_body_survives(self):
        """The verify_otp body shape keeps its address, loses its otp."""
        body = {"email": "student@nyjc.edu.sg", "otp": "654321"}
        self.assertEqual(
            redact_sensitive(body),
            {"email": "student@nyjc.edu.sg", "otp": "[REDACTED]"},
        )


class TestRedactionInvariants(unittest.TestCase):
    """Structural invariants carried over from #805."""

    def test_nested_structures_are_redacted(self):
        body = {"data": [{"authorization_code": "x"}, {"ok": True}]}
        self.assertEqual(
            redact_sensitive(body),
            {"data": [{"authorization_code": "[REDACTED]"}, {"ok": True}]},
        )

    def test_input_is_not_mutated(self):
        body = {"authorization_code": "x", "nested": {"otp": "y"}}
        redact_sensitive(body)
        self.assertEqual(body, {"authorization_code": "x",
                                "nested": {"otp": "y"}})

    def test_scalars_pass_through(self):
        self.assertEqual(redact_sensitive("authorization_code"), "authorization_code")
        self.assertIsNone(redact_sensitive(None))

    def test_reexport_from_package(self):
        """The scrub primitive is the shared cross-dist API (#20)."""
        from campus.audit import middleware

        self.assertIs(middleware.redact_sensitive, redact_sensitive)


class TestJourneyNameSanitization(unittest.TestCase):
    """Header-supplied journey names carry no free text (#20)."""

    def test_safe_name_survives(self):
        self.assertEqual(_clean_journey_name("submit-assignment v2"),
                         "submit-assignment v2")

    def test_email_in_name_is_stripped(self):
        self.assertEqual(
            _clean_journey_name("me@somewhere.example"),
            "mesomewhere.example",
        )

    def test_markup_is_stripped(self):
        self.assertEqual(
            _clean_journey_name("<script>x</script>"),
            "scriptxscript",
        )

    def test_unicode_is_stripped(self):
        self.assertEqual(_clean_journey_name("héllo"), "hllo")

    def test_length_cap_still_applies(self):
        self.assertEqual(len(_clean_journey_name("a" * 500)), 120)

    def test_empty_and_none(self):
        self.assertIsNone(_clean_journey_name(None))
        self.assertIsNone(_clean_journey_name(""))
        self.assertIsNone(_clean_journey_name("   "))


class TestIngestionWarningScrub(unittest.TestCase):
    """4xx ingestion failures log status/size, not the response body."""

    def _make_client(self, status, body):
        from campus.audit.http.default import DefaultClient

        response = mock.Mock()
        response.status_code = status
        response.text = body
        response.headers = {"Content-Length": str(len(body))}
        session = mock.Mock()
        session.request.return_value = response
        with mock.patch.object(DefaultClient, "__init__", lambda self: None):
            client = DefaultClient()
            client._session = session
            client.base_url = "http://audit.internal"
        return client

    def test_warning_has_no_body(self):
        client = self._make_client(
            422, '{"error":{"message":"invalid span: otp=123456"}}'
        )
        with self.assertLogs("campus.audit.http.default",
                             level="WARNING") as captured:
            client._make_request("POST", "/traces/", json={"x": 1})
        warnings = [line for line in captured.output
                    if "WARNING" in line]
        self.assertTrue(warnings)
        self.assertNotIn("otp=123456", "".join(warnings))
        self.assertIn("422", "".join(warnings))

    def test_body_only_at_debug(self):
        """The error body appears only in the DEBUG record, never WARNING."""
        client = self._make_client(
            422, '{"error":{"message":"invalid span: otp=123456"}}'
        )
        with self.assertLogs("campus.audit.http.default",
                             level="DEBUG") as captured:
            client._make_request("POST", "/traces/", json={"x": 1})
        joined = "\n".join(captured.output)
        self.assertIn("otp=123456", joined)
        warning_lines = [line for line in captured.output
                         if "WARNING" in line]
        self.assertTrue(warning_lines)
        self.assertTrue(
            all("otp=123456" not in line for line in warning_lines)
        )


if __name__ == "__main__":
    unittest.main()
