"""The shared re-auth classifier (#1096 contract).

Hosts that mint user credentials must translate mint failures into a
re-auth flow, and every host must make the same call on the same error
shapes — campus-classroom's /api/ 401 + browser-redirect degrade (#98)
is the reference implementation, now shared here so the audit-service
hosts and friends stop re-rolling it.
"""

import os
import unittest

from campus_python import errors

from campus.flask_campus import is_reauth_required

os.environ.setdefault("CLIENT_ID", "test-client-id")
os.environ.setdefault("CLIENT_SECRET", "test-client-secret")


def _grant_error() -> errors.APIError:
    # oauth_error rides the error envelope's details dict.
    return errors.BadRequestError(
        error_description="No campus grant … re-authentication is required",
        details={"oauth_error": "invalid_grant"},
    )


class TestIsReauthRequired(unittest.TestCase):
    """Three shapes mean re-auth; everything else means retry/report."""

    def test_oauth_invalid_grant_is_reauth(self):
        self.assertTrue(is_reauth_required(_grant_error()))

    def test_missing_login_session_id_is_reauth(self):
        # The flask session lost its stored ids entirely: the SDK raises
        # AuthenticationError before any HTTP call.
        self.assertTrue(is_reauth_required(
            errors.AuthenticationError(
                error_description="No login session id in storage"
            )
        ))

    def test_sessions_read_denial_is_reauth(self):
        # A login-session row that died server-side: the auth store
        # answers the mint's session lookup with a denial.
        self.assertTrue(is_reauth_required(
            errors.AccessDeniedError(
                error_description=(
                    "No access grant designates this user for "
                    "'sessions:read' (#883)"
                )
            )
        ))

    def test_plain_access_denied_is_not_reauth(self):
        # A grant denial the user could fix by other means (missing
        # circle) is NOT a sign-in problem.
        self.assertFalse(is_reauth_required(
            errors.AccessDeniedError(
                error_description="missing submissions.admin circle"
            )
        ))

    def test_network_and_server_errors_are_not_reauth(self):
        self.assertFalse(is_reauth_required(
            errors.ServerError(error_description="gateway 502")
        ))
        self.assertFalse(is_reauth_required(
            errors.RateLimitExceededError(error_description="slow down")
        ))
        self.assertFalse(is_reauth_required(ConnectionError("network down")))

    def test_invalid_grant_without_oauth_error_shape_is_not_reauth(self):
        err = errors.BadRequestError(error_description="malformed request")
        self.assertFalse(is_reauth_required(err))


if __name__ == "__main__":
    unittest.main()
