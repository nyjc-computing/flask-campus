"""campus.flask_campus.journeys

Action-journey decorator for client apps (#828).

One decorator is the entire API surface app authors touch:

    from campus import flask_campus

    @app.post("/assignments/submit")
    @flask_campus.journey("campus.submissions.submit")
    def submit_assignment(): ...

``@journey`` names the user action the view serves. Names follow the
yapper emission convention (``campus.<subject>[.<sub>].<action>``) and
reuse the same labels (#840): an emission is the event an episode
culminates in, so a journey named ``campus.submissions.submit`` reads
as "the episode that ended in that emission". Mint a new label (same
format) only when no emission exists for the action. The journey id
itself is adopted from the active request when one exists (the journeys
middleware's cookie/header adoption, or a previous hop of the same
episode) and minted fresh only when there is none — so decorating a
view is always safe, never fragments an ongoing episode, and requires
no knowledge of ids, cookies, or headers. ``fresh=True`` opts out of
adoption for a hard action boundary mid-episode.

Journeys are observability correlation, not application state: if audit
tracing is disabled the decorator still runs but nothing is recorded.
"""

__all__ = ["journey"]

import typing
from functools import wraps

import flask

from campus.common.protocol import (
    ACTION_JOURNEY_COOKIE,
    ACTION_JOURNEY_COOKIE_MAX_AGE,
)
from campus.common.utils import uid


def _set_journey_cookie(response: flask.Response) -> flask.Response:
    """Mirror the journeys middleware's cookie refresh for standalone use."""
    journey_id = getattr(flask.g, "journey_id", None)
    if journey_id:
        response.set_cookie(
            ACTION_JOURNEY_COOKIE,
            journey_id,
            max_age=ACTION_JOURNEY_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            path="/",
        )
    return response


def journey(
        name: str | None = None,
        *,
        fresh: bool = False,
) -> typing.Callable:
    """Mark a view as part of a (named) user-action journey.

    Args:
        name: Label for the journey, following the yapper emission
            convention (``campus.<subject>[.<sub>].<action>``) and
            reusing the emission label where one exists (#840); lands
            in the span's tags.journey_name for the journey view cards.
        fresh: Start a NEW journey for this request even if one is
            active — a hard action boundary mid-episode. Default False:
            adopt the active journey, minting only when there is none.

    Returns:
        The decorated view.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            journey_id = getattr(flask.g, "journey_id", None)
            if not journey_id:
                # Standalone use (no journeys middleware): adopt the
                # action cookie, else mint.
                journey_id = (
                    flask.request.cookies.get(
                        ACTION_JOURNEY_COOKIE
                    )
                    or uid.generate_category_uid("journey")
                )
            elif fresh:
                journey_id = uid.generate_category_uid("journey")
            flask.g.journey_id = journey_id
            if name:
                flask.g.journey_name = name
            # Continue the episode after this response (redirects,
            # same-page XHRs) even without the journeys middleware.
            flask.after_this_request(_set_journey_cookie)
            return view(*args, **kwargs)
        return wrapped
    return decorator
