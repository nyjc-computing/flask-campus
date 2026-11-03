"""campus.audit.middleware

Request tracing middleware for Campus audit API.

This module provides automatic span capture for HTTP requests flowing
through campus.auth and campus.api deployments, and the action-journey
lifecycle (#828) that groups related requests into user-initiated
episodes.
"""

__all__ = ["init_app", "init_journeys"]

import flask


def init_app(app: flask.Flask) -> None:
    """Initialize tracing middleware for the Flask app.

    Registers before_request and after_request hooks to capture
    request-response data as spans.

    Note: Should only be called for campus.auth and campus.api deployments,
    not for campus.audit (to avoid infinite recursion).

    Args:
        app: The Flask application to instrument.
    """
    from . import tracing

    _install_propagation()

    @app.before_request
    def audit_rate_gate():
        """Fail-closed gate for audit ingest rate limiting (#831).

        Registered before start_span so a gated (503) request never
        opens a span. No-op unless AUDIT_TRACING_FAIL_CLOSED=1.
        """
        return tracing.check_rate_gate()

    @app.before_request
    def start_span():
        """Start a root span for each incoming request.

        Generates or reuses trace_id from X-Request-ID header.
        Stores timing and identifier data in flask.g for use in after_request.
        """
        tracing.start_span()

    @app.after_request
    def end_span(response):
        """Complete the span and send to audit service.

        Builds TraceSpan from request-response data and ingests asynchronously.
        Echoes trace_id in response headers for correlation.

        Args:
            response: The Flask response object.

        Returns:
            The response with X-Request-ID header added.
        """
        return tracing.end_span(response)


def init_journeys(
        app: flask.Flask | flask.Blueprint,
        *,
        mint_on_navigation: bool = True,
) -> None:
    """Initialize the action-journey lifecycle (#828).

    Adopts or mints a journey id per request (see .journeys for the
    lifecycle) and keeps the campus_action_journey cookie alive, so a
    user-initiated action episode — page load, its XHRs and form posts,
    and the server-to-server calls they spawn — groups as one journey
    in the audit UI.

    Args:
        app: The Flask app, or a blueprint for scoped opt-in.
        mint_on_navigation: Mint on page navigations with no active
            journey. Pass False for services whose browser surface is
            the login flow itself (campus.auth, #803) or that serve no
            browser pages (campus.api) — they adopt forwarded
            X-Journey-ID headers only.
    """
    from . import journeys

    _install_propagation()

    journeys.init_journeys(app, mint_on_navigation=mint_on_navigation)


def _install_propagation() -> None:
    """Point campus_python's propagation seam at this process's flask.g
    (idempotent).

    Outbound SDK calls made while a request is being handled then carry
    the span/journey/device headers stashed by this middleware, keeping
    the audit waterfall intact regardless of which implementation
    computes the headers. Safe with or without tracing enabled — the
    provider simply finds no g state when nothing was stashed.
    """
    from campus.flask_campus import propagation

    propagation.install()
