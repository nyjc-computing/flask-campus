"""Unit tests for the circles-backed view gates.

OAuthLoginManager.circle_member_required / circle_admin_required gate
views on membership in a named circle, resolved through the user's
session toward campus.api. The fake client mirrors the SDK surface the
check consumes (with_user_session → api.v1.circles); answers, not
upstream shapes, are the contract here.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace

import flask
import werkzeug.exceptions

from campus.flask_campus.circles import (
    CirclesUnavailableError,
    MembersCache,
    is_member,
)
from campus.flask_campus.login_manager import OAuthLoginManager

ROLE_CIRCLE_ID = "circle_role001"
MEMBERS = {
    "admin@campus.test": 15,
    "teacher@campus.test": 10,
}


class FakeCirclesCollection:
    """The slice of client.api.v1.circles the membership check uses."""

    def __init__(self, circles, calls):
        self._circles = circles
        self._calls = calls
        self.members_list_calls = 0

    def list(self):
        self._calls.append(("circles.list",))
        return self._circles

    def __getitem__(self, circle_id):
        collection = self

        class FakeMembers:
            def list(self):
                collection.members_list_calls += 1
                collection._calls.append(("members.list", circle_id))
                return dict(MEMBERS)

        return SimpleNamespace(id=circle_id, members=FakeMembers())


class FakeCampus:
    """A Campus stand-in whose with_user_session yields canned circles.

    The attribute chain mirrors the real SDK: Campus.api is ApiRoot
    (url_prefix /api/v1) and exposes .circles directly.
    """

    def __init__(self, circles=None, error=None):
        self.calls = []
        self._circles = circles if circles is not None else []
        self._error = error
        self.api = SimpleNamespace(
            circles=FakeCirclesCollection(self._circles, self.calls)
        )

    @contextmanager
    def with_user_session(self):
        if self._error is not None:
            raise self._error
        yield self

    @property
    def api_calls(self):
        return self.api.circles.members_list_calls


def make_manager(campus):
    """A manager wired like an app's, plus its Flask app for contexts."""
    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    # The signed-out branch redirects to auth.login; provide the
    # endpoint without pulling in the full OAuth blueprint.
    app.add_url_rule("/login", "auth.login", lambda: "")
    return OAuthLoginManager(campus_client=campus), app


def signed_in(app, user_id="admin@campus.test", with_user=True):
    ctx = app.test_request_context("/protected")
    ctx.push()
    if with_user:
        flask.g.user = SimpleNamespace(id=user_id)
    return ctx


class TestCircleMemberRequired(unittest.TestCase):
    """The decorator's gate decisions across the fail-closed matrix."""

    def setUp(self):
        self.campus = FakeCampus(circles=[
            SimpleNamespace(id=ROLE_CIRCLE_ID, name="timetable.admin"),
        ])
        self.manager, self.app = make_manager(self.campus)

        @self.manager.circle_member_required("timetable.admin")
        def view(slug, extra=None):
            return "ran", slug, extra

        self.view = view

    def test_member_passes_and_args_forward(self):
        ctx = signed_in(self.app)
        self.addCleanup(ctx.pop)
        self.assertEqual(self.view("classroom"), ("ran", "classroom", None))
        self.assertEqual(
            self.view("classroom", extra=1), ("ran", "classroom", 1)
        )

    def test_redirects_when_signed_out(self):
        ctx = signed_in(self.app, with_user=False)
        self.addCleanup(ctx.pop)
        response = self.view("classroom")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_non_member_gets_403(self):
        ctx = signed_in(self.app, user_id="outsider@campus.test")
        self.addCleanup(ctx.pop)
        with self.assertRaises(werkzeug.exceptions.HTTPException) as caught:
            self.view("classroom")
        self.assertEqual(caught.exception.code, 403)

    def test_user_without_id_fails_closed(self):
        ctx = signed_in(self.app)
        self.addCleanup(ctx.pop)
        flask.g.user = SimpleNamespace()
        with self.assertRaises(werkzeug.exceptions.HTTPException) as caught:
            self.view("classroom")
        self.assertEqual(caught.exception.code, 403)

    def test_missing_circle_denies_fail_closed(self):
        # In-place clear: the fake collection holds the same list.
        self.campus._circles.clear()
        ctx = signed_in(self.app)
        self.addCleanup(ctx.pop)
        with self.assertRaises(werkzeug.exceptions.HTTPException) as caught:
            self.view("classroom")
        self.assertEqual(caught.exception.code, 403)

    def test_backend_failure_gets_503(self):
        self.campus._error = RuntimeError("connection reset")
        ctx = signed_in(self.app)
        self.addCleanup(ctx.pop)
        with self.assertRaises(werkzeug.exceptions.HTTPException) as caught:
            self.view("classroom")
        self.assertEqual(caught.exception.code, 503)

    def test_second_request_hits_cache(self):
        ctx = signed_in(self.app)
        self.addCleanup(ctx.pop)
        self.view("classroom")
        calls_after_first = len(self.campus.calls)
        self.view("classroom")
        self.assertEqual(len(self.campus.calls), calls_after_first)

    def test_stacking_order_does_not_matter(self):
        """The gate folds in the signed-out redirect, so either stacking
        order with login_required protects the view."""
        @self.manager.login_required
        @self.manager.circle_member_required("timetable.admin")
        def view():
            return "ran"

        ctx = signed_in(self.app, with_user=False)
        self.addCleanup(ctx.pop)
        # Signed out: the innermost gate redirects instead of crashing
        # on the missing g.user the outer gate would have set up.
        response = view()
        self.assertEqual(response.status_code, 302)


class TestCircleAdminRequired(unittest.TestCase):
    """circle_admin_required is sugar for the <resource>.admin role."""

    def test_resolves_resource_to_role_circle(self):
        campus = FakeCampus(circles=[
            SimpleNamespace(id=ROLE_CIRCLE_ID, name="timetable.admin"),
        ])
        manager, app = make_manager(campus)

        @manager.circle_admin_required("timetable")
        def view():
            return "ran"

        ctx = signed_in(app, user_id="teacher@campus.test")
        self.addCleanup(ctx.pop)
        # Passing IS the resolution check: only the circle named
        # "timetable.admin" holds teacher@campus.test in the fake.
        self.assertEqual(view(), "ran")
        members_lookup = campus.api.circles.members_list_calls
        self.assertEqual(members_lookup, 1)


class TestIsMember(unittest.TestCase):
    """circles.is_member answers and cache semantics."""

    def setUp(self):
        self.campus = FakeCampus(circles=[
            SimpleNamespace(id=ROLE_CIRCLE_ID, name="timetable.admin"),
        ])
        self.cache = MembersCache()

    def test_member_and_non_member_answers(self):
        self.assertTrue(is_member(
            self.campus, self.cache, "timetable.admin", "admin@campus.test"
        ))
        self.assertFalse(is_member(
            self.campus, self.cache, "timetable.admin", "ghost@campus.test"
        ))

    def test_cache_hit_skips_upstream(self):
        is_member(self.campus, self.cache, "timetable.admin", "admin@campus.test")
        calls_after_first = len(self.campus.calls)
        self.assertTrue(is_member(
            self.campus, self.cache, "timetable.admin", "admin@campus.test"
        ))
        self.assertEqual(len(self.campus.calls), calls_after_first)

    def test_missing_circle_denies_and_does_not_raise(self):
        campus = FakeCampus(circles=[])
        self.assertFalse(is_member(
            campus, self.cache, "no-such.admin", "admin@campus.test"
        ))

    def test_backend_failure_raises_unavailable(self):
        campus = FakeCampus(error=ConnectionError("refused"))
        with self.assertRaises(CirclesUnavailableError):
            is_member(campus, self.cache, "timetable.admin", "admin@campus.test")


if __name__ == "__main__":
    unittest.main()
