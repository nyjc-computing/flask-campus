"""Unit tests for flask_campus.context — the auth session-context
mechanics owned Flask-side (ported from campus_python's AuthRoot).

Covers: the SDK session-id provider installation, the user cache
(#57), push_context resolution incl. the embedded-user fallback
(#879) and stale-session eviction, finalize_login's storage handoff
(#825 device ride-along), and logout's best-effort revocation.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import campus_python
import flask
from campus_python.auth.v1 import sessionstore as sdk_sessionstore
from campus_python.auth.v1 import set_session_id_provider
from campus_python.auth.v1.logins import LOGIN_STORAGE_KEY
from campus_python.auth.v1.sessions import SESSION_STORAGE_KEY

import campus.model
from campus.flask_campus import context


def make_auth(**overrides) -> SimpleNamespace:
    """A duck-typed campus.auth resource surface for context tests.

    sessions/logins are MagicMocks so the subscript lanes
    (sessions[id] / logins[id]) work like the real resources.
    """
    auth = SimpleNamespace(
        sessions=MagicMock(),
        logins=MagicMock(),
        users={},
        exchange_code_for_token=Mock(),
        base_url="https://campusauth.test",
        make_path=lambda endpoint: f"/auth/v1/{endpoint}",
    )
    # MagicMock's __bool__ defaults truthy; an unconfigured lane must
    # not claim a session exists.
    auth.sessions.has_session.return_value = False
    auth.logins.has_session.return_value = False
    for name, value in overrides.items():
        setattr(auth, name, value)
    return auth


def make_campus(auth) -> SimpleNamespace:
    return SimpleNamespace(auth=auth)


def request_context():
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    return app.test_request_context()


class TestStorageKeys(unittest.TestCase):
    """The context-owned keys are the SDK's legacy flask.session keys,
    so both sides read/write the same values during the rollout."""

    def test_key_values(self):
        self.assertEqual(SESSION_STORAGE_KEY, "campus_session_id")
        self.assertEqual(LOGIN_STORAGE_KEY, "logins_login_id")

    def test_provider_resolves_from_flask_session(self):
        set_session_id_provider(None)
        context.install_session_provider()
        self.addCleanup(set_session_id_provider, None)
        with request_context():
            flask.session[LOGIN_STORAGE_KEY] = "login-9"
            flask.session[SESSION_STORAGE_KEY] = "sess-9"
            self.assertEqual(
                sdk_sessionstore.resolve_session_id(
                    sdk_sessionstore.LANE_LOGIN
                ),
                "login-9",
            )
            self.assertEqual(
                sdk_sessionstore.resolve_session_id(
                    sdk_sessionstore.LANE_SESSION
                ),
                "sess-9",
            )
            self.assertIsNone(
                sdk_sessionstore.resolve_session_id("bogus-lane")
            )


class TestPushContext(unittest.TestCase):
    """Request-entry context push: g.user/g.device resolution."""

    def setUp(self):
        self.cache = context.UserCache()
        self.auth = make_auth()
        self.campus = make_campus(self.auth)

    def test_auth_session_lane_sets_g_user(self):
        user = SimpleNamespace(id="user@nyjc.edu.sg")
        self.auth.sessions.has_session.return_value = True
        self.auth.sessions.from_session.return_value = SimpleNamespace(
            user_id="user@nyjc.edu.sg", user=user
        )
        with request_context():
            context.push_context(self.campus, self.cache)
            self.assertEqual(flask.g.user, user)
            # Embedded user (#879): the users route was never hit.
            self.assertEqual(self.auth.users, {})
        # Warm push is served entirely from the cache (#57).
        self.auth.sessions.from_session.assert_called_once()

    def test_users_route_fallback_without_embedded_user(self):
        users_get = Mock(return_value=SimpleNamespace(id="user-1"))
        self.auth.users = {"user-1": SimpleNamespace(get=users_get)}
        self.auth.sessions.has_session.return_value = True
        self.auth.sessions.from_session.return_value = SimpleNamespace(
            user_id="user-1", user=None
        )
        with request_context():
            context.push_context(self.campus, self.cache)
            self.assertEqual(flask.g.user.id, "user-1")
        users_get.assert_called_once()

    def test_login_lane_sets_g_user_and_device(self):
        users_get = Mock(return_value=SimpleNamespace(id="user-1"))
        self.auth.users = {"user-1": SimpleNamespace(get=users_get)}
        self.auth.logins.has_session.return_value = True
        self.auth.logins.from_session.return_value = SimpleNamespace(
            user_id="user-1", user=None, device_id="device-1"
        )
        with request_context():
            context.push_context(self.campus, self.cache)
            self.assertEqual(flask.g.user.id, "user-1")
            self.assertEqual(flask.g.device, "device-1")

    def test_stale_auth_session_evicts_storage(self):
        self.auth.sessions.has_session.return_value = True
        self.auth.sessions.from_session.side_effect = (
            campus_python.errors.NotFoundError(
                error_description="gone"
            )
        )
        with request_context() as _ctx:
            flask.session[SESSION_STORAGE_KEY] = "sess-stale"
            context.push_context(self.campus, self.cache)
            self.assertIsNone(flask.g.user)
            self.assertNotIn(SESSION_STORAGE_KEY, flask.session)

    def test_cache_expiry_refetches(self):
        user = SimpleNamespace(id="user@nyjc.edu.sg")
        self.auth.sessions.has_session.return_value = True
        self.auth.sessions.from_session.return_value = SimpleNamespace(
            user_id="user@nyjc.edu.sg", user=user
        )
        cache = context.UserCache(ttl_seconds=0.0)
        with request_context():
            context.push_context(self.campus, cache)
            context.push_context(self.campus, cache)
        self.assertEqual(self.auth.sessions.from_session.call_count, 2)


class TestUserCache(unittest.TestCase):
    """Cache snapshots and rehydration."""

    def test_put_get_roundtrip_plain_object(self):
        cache = context.UserCache()
        user = SimpleNamespace(id="user-1")
        cache.put("login", "login-1", user, "device-1")
        self.assertEqual(cache.get("login", "login-1"), (user, "device-1"))

    def test_snapshot_rehydrated_via_from_resource(self):
        """Dict snapshots (real campus.model.User) are rehydrated per
        hit so callers cannot poison the cache via flask.g.user."""
        rehydrated = SimpleNamespace(id="user-1")
        cache = context.UserCache()
        original = SimpleNamespace(
            id="user-1", to_resource=lambda: {"id": "user-1"}
        )
        cache.put("login", "login-1", original, None)
        with patch.object(
                campus.model.User, "from_resource", return_value=rehydrated
        ) as mock_from_resource:
            user, _device = cache.get("login", "login-1")
            self.assertIs(user, rehydrated)
            mock_from_resource.assert_called_once_with({"id": "user-1"})

    def test_pop_and_none_session_id(self):
        cache = context.UserCache()
        cache.put("login", "login-1", SimpleNamespace(id="u"), None)
        cache.pop("login", "login-1")
        self.assertIsNone(cache.get("login", "login-1"))
        self.assertIsNone(cache.get("login", None))


class TestFinalizeLogin(unittest.TestCase):
    """finalize_login: storage handoff and the #825 device ride-along."""

    def setUp(self):
        self.cache = context.UserCache()
        self.auth = make_auth()
        self.campus = make_campus(self.auth)
        self.auth.sessions.from_code.return_value = SimpleNamespace(
            id="state-1",
            user_id="user-1",
            redirect_uri="https://app.test/cb",
            device_id="device-1",
        )
        self.auth.sessions.__getitem__.return_value = SimpleNamespace(
            finalize=Mock(
                return_value=("https://app.test/after", SimpleNamespace(id="user-1"))
            )
        )
        self.auth.logins.new = Mock(
            return_value=SimpleNamespace(id="login-1")
        )

    def test_storage_handoff(self):
        with request_context() as _ctx:
            flask.session[SESSION_STORAGE_KEY] = "state-1"
            context.finalize_login(
                self.campus, state="state-1", code="code-1", scope="s"
            )
            # The consumed auth session key is cleared; the login
            # session key carries the new login id.
            self.assertNotIn(SESSION_STORAGE_KEY, flask.session)
            self.assertEqual(flask.session[LOGIN_STORAGE_KEY], "login-1")
        self.auth.exchange_code_for_token.assert_called_once_with(
            code="code-1", redirect_uri="https://app.test/cb"
        )
        self.auth.sessions.__getitem__.assert_called_once_with("state-1")

    def test_device_minted_when_auth_session_has_none(self):
        self.auth.sessions.from_code.return_value = SimpleNamespace(
            id="state-1",
            user_id="user-1",
            redirect_uri="https://app.test/cb",
            device_id=None,
        )
        with request_context():
            context.finalize_login(
                self.campus, state="state-1", code="code-1", scope="s"
            )
        device_id = self.auth.logins.new.call_args.kwargs["device_id"]
        self.assertTrue(device_id.startswith("uid-device"))

    def test_state_mismatch_raises(self):
        with request_context(), self.assertRaises(
                campus_python.errors.AuthenticationError
        ):
            context.finalize_login(
                self.campus, state="other", code="code-1", scope="s"
            )


class TestLogout(unittest.TestCase):
    """logout: best-effort revocation, local state always cleared."""

    def setUp(self):
        self.cache = context.UserCache()
        self.cache.put("login", "login-1", SimpleNamespace(id="u"), None)
        self.auth = make_auth()
        self.campus = make_campus(self.auth)
        self.auth.logins.has_session.return_value = True
        self.auth.logins.from_session.return_value = SimpleNamespace(
            id="login-1"
        )
        self.revoke = Mock()
        self.auth.logins.__getitem__.return_value = SimpleNamespace(
            revoke=self.revoke
        )

    def test_success_revokes_and_clears(self):
        with request_context() as _ctx:
            flask.session[LOGIN_STORAGE_KEY] = "login-1"
            context.logout(self.campus, self.cache)
            self.assertNotIn(LOGIN_STORAGE_KEY, flask.session)
            self.assertIsNone(flask.g.get("user"))
            self.assertIsNone(flask.g.get("device"))
        self.revoke.assert_called_once()
        self.assertIsNone(self.cache.get("login", "login-1"))

    def test_no_session_is_noop(self):
        self.auth.logins.has_session.return_value = False
        with request_context():
            context.logout(self.campus, self.cache)
        self.revoke.assert_not_called()

    def test_revocation_failure_still_clears_local_state(self):
        self.revoke.side_effect = RuntimeError("network down")
        with request_context() as _ctx:
            flask.session[LOGIN_STORAGE_KEY] = "login-1"
            context.logout(self.campus, self.cache)
            self.assertNotIn(LOGIN_STORAGE_KEY, flask.session)


if __name__ == "__main__":
    unittest.main()
