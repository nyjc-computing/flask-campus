"""campus.flask_campus.circles

Circles-backed membership checks for Flask views.

This module answers "is the signed-in user in this circle" for view
gates (OAuthLoginManager.circle_member_required), resolving membership
the same way campus.api's own enforcement does: the caller's user ID
(their email, which is also their user circle's name) against the
target circle's members. It is view-level gating only — campus.api
remains the enforcement point for the resources themselves; this gate
exists so apps can render the right UI before the API's 403 wall.

Checks run as the signed-in user (campus.with_user_session), not with
the app's client credentials: client principals hold no domain
authority, and circle reads are only open "for now".

Membership answers are cached process-local per (circle, user) for a
short TTL — warm admin views make zero upstream calls. Definitive
non-membership (absent member, missing circle) fails closed with
False; infrastructure failures raise CirclesUnavailableError so the
caller can distinguish a 403 from a degraded backend.
"""

__all__ = [
    "CIRCLE_MEMBERS_TTL_SECONDS",
    "CirclesUnavailableError",
    "MembersCache",
    "is_member",
]

import logging
import time
import typing

logger = logging.getLogger(__name__)


@typing.runtime_checkable
class _WithUserSession(typing.Protocol):
    """The slice of the Campus client the membership check consumes.

    Narrow on purpose: checks run as the signed-in user, which only
    needs the user-session context manager (the yielded client's api
    surface stays loosely typed here — test doubles and SDK point
    releases both move under this seam).
    """

    def with_user_session(self) -> typing.ContextManager[typing.Any]:
        ...


# How long a membership answer may be reused across requests. Mirrors
# context.USER_CACHE_TTL_SECONDS: warm requests within the window pay
# no upstream calls; a membership revoked mid-window lapses with the
# entry (the API's own gate still protects the resource).
CIRCLE_MEMBERS_TTL_SECONDS = 60.0


class CirclesUnavailableError(Exception):
    """The circles backend could not answer a membership probe.

    Raised for infrastructure failures (network errors, non-403/404
    API errors, user-token refresh failures) — callers fail closed
    with a degraded-response status, never with a silent denial-as-403.
    """


class MembersCache:
    """Process-local cache of membership answers.

    Keys are (circle_name, user_id) pairs; values are
    (expires_at, is_member). Expired entries are dropped lazily on
    read. Answers only — never the members dict itself: transitive
    resolution (a stated campus.api refinement) changes which users
    count as members, so caching raw membership data would drift.
    """

    def __init__(self, ttl_seconds: float = CIRCLE_MEMBERS_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[
            tuple[str, str],
            tuple[float, bool],
        ] = {}

    def get(self, circle: str, user_id: str) -> bool | None:
        """The cached answer for (circle, user), or None on miss/expiry."""
        entry = self._entries.get((circle, user_id))
        if entry is None:
            return None
        expires_at, is_member = entry
        if time.monotonic() >= expires_at:
            self._entries.pop((circle, user_id), None)
            return None
        return is_member

    def put(self, circle: str, user_id: str, is_member: bool) -> None:
        """Cache the membership answer for ttl_seconds."""
        self._entries[(circle, user_id)] = (
            time.monotonic() + self.ttl_seconds,
            is_member,
        )


def is_member(
        campus: _WithUserSession,
        cache: MembersCache,
        circle: str,
        user_id: str,
) -> bool:
    """True if `user_id` is a member of the named circle.

    The circle is resolved by name (names are unique best-effort and
    stable — role circles use dotted names like "timetable.admin";
    user circles are named after the email account, and user IDs are
    emails, so the lookup key is the caller's ID verbatim). A missing
    circle denies fail-closed with a warning — that is a bootstrap
    problem, not a per-user denial.

    Raises:
        CirclesUnavailableError: If the circles backend (or the user
            token flow backing the check) fails to answer.
    """
    cached = cache.get(circle, user_id)
    if cached is not None:
        return cached
    try:
        with campus.with_user_session() as client:
            matches = [
                c for c in client.api.circles.list()
                if c.name == circle
            ]
            if not matches:
                logger.warning(
                    "No circle named '%s' is configured: denying "
                    "membership fail-closed — run the circles "
                    "bootstrap (docs/circles-access-control.md)",
                    circle,
                )
                answer = False
            else:
                # TODO(authz): switch to the single-membership probe
                # (client.api.circles[matches[0].id].members.get(
                #     user_id)) once the campus-api-python pin carries
                # CircleMembers.get (0.10.2) — the probe keeps client
                # checks honest when campus.api adds access tiers or
                # transitive resolution; members.list() is the
                # direct-membership snapshot meanwhile.
                members = (
                    client.api.circles[matches[0].id]
                    .members.list()
                )
                answer = user_id in members
    except CirclesUnavailableError:
        raise
    except Exception as err:
        logger.error("Circle membership check failed: %s", err)
        raise CirclesUnavailableError(
            f"Could not answer membership in '{circle}': {err}"
        ) from err
    cache.put(circle, user_id, answer)
    return answer
