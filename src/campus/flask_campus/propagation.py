"""campus.flask_campus.propagation

Request-context trace propagation for Flask hosts.

Supplies the request-context half of campus_python.tracing's
propagation through its injectable provider seam: outbound SDK calls
made while this app handles a request carry the span/journey/device
headers stashed on flask.g by the audit middleware, so the receiving
campus service records them as child spans of the caller's span
(#794, campus#816).

campus.audit.middleware.init_app / init_journeys install the provider
(one process-global seam, idempotent); without it the SDK falls back
to its own built-in flask reader. The provider returns None outside
any request context — the SDK then applies the process boot journey
(#114), so startup/background SDK calls stay correlated.

Header names mirror campus_python.tracing and
campus.audit.middleware.tracing — keep all three in lockstep (W3C
traceparent interop would extend all of them); their long-term home
is the campus.common protocol-constant set (#980).
"""

__all__ = [
    "DEVICE_ID_HEADER",
    "JOURNEY_ID_HEADER",
    "JOURNEY_NAME_HEADER",
    "PARENT_SPAN_ID_HEADER",
    "TRACE_ID_HEADER",
    "install",
    "request_context_headers",
]

import flask
from campus_python import tracing

from campus.common.protocol import (
    DEVICE_ID_HEADER,
    JOURNEY_ID_HEADER,
    JOURNEY_NAME_HEADER,
    PARENT_SPAN_ID_HEADER,
    TRACE_ID_HEADER,
)

# The five wire-protocol header names are re-exported from
# campus.common.protocol (campus#1041) — the single source of truth
# since the former per-side definitions and their lockstep comments.
# They are forwarded on outbound SDK calls so a child service's span
# joins the caller's trace (#794), action journey (#828/#952), and
# device identity (#837).


def request_context_headers() -> dict[str, str] | None:
    """Provider callback for campus_python.tracing.

    The propagation headers for the active request (possibly empty —
    a journey-less request stays unjourneyed), or None outside any
    request context. The process device fills the device gap only
    when the request stashed none (#837 semantics unchanged).
    """
    if not flask.has_request_context():
        return None
    headers: dict[str, str] = {}
    trace_id = getattr(flask.g, "trace_id", None)
    span_id = getattr(flask.g, "span_id", None)
    if trace_id and span_id:
        headers[TRACE_ID_HEADER] = trace_id
        headers[PARENT_SPAN_ID_HEADER] = span_id
    journey_id = getattr(flask.g, "journey_id", None)
    if journey_id:
        headers[JOURNEY_ID_HEADER] = journey_id
    journey_name = getattr(flask.g, "journey_name", None)
    if journey_name:
        headers[JOURNEY_NAME_HEADER] = journey_name
    device_id = getattr(flask.g, "device", None)
    if not device_id:
        # Gap-fill only: a request with its own device keeps it.
        device_id = tracing.process_device()
    if device_id:
        headers[DEVICE_ID_HEADER] = str(device_id)
    return headers


def install() -> None:
    """Install the provider into campus_python.tracing (idempotent).

    Called by campus.audit.middleware.init_app / init_journeys; hosts
    without the audit middleware keep the SDK's built-in reader.
    """
    tracing.set_request_context_provider(request_context_headers)
