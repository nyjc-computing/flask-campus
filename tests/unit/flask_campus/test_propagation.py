"""Integration tests: flask_campus.propagation as the SDK's
request-context provider.

Once installed (audit.middleware.init_app / init_journeys), outbound
SDK calls compute their propagation headers from the flask.g state
this middleware stashes — the same contract the SDK's built-in flask
reader mirrors.
"""

import os
import unittest

import flask
from campus_python import tracing

from campus.flask_campus import propagation

TRACE_ID = "a" * 32
SPAN_ID = "b" * 16


class TestPropagationProvider(unittest.TestCase):
    """install() feeds campus_python.tracing.propagation_headers."""

    def tearDown(self):
        tracing.set_request_context_provider(None)
        tracing._reset_process_journey()

    def test_installed_provider_supplies_request_headers(self):
        propagation.install()
        app = flask.Flask(__name__)
        with app.test_request_context("/"):
            flask.g.trace_id = TRACE_ID
            flask.g.span_id = SPAN_ID
            flask.g.journey_id = "uid-journey-1"
            flask.g.journey_name = "campus.test"
            flask.g.device = "device-9"
            headers = tracing.propagation_headers()
        self.assertEqual(
            headers,
            {
                "X-Request-ID": TRACE_ID,
                "X-Parent-Span-ID": SPAN_ID,
                "X-Journey-ID": "uid-journey-1",
                "X-Journey-Name": "campus.test",
                "X-Campus-Device": "device-9",
            },
        )

    def test_device_gap_fill_from_process_device(self):
        """A request that stashed no device presents the process device
        (#837 gap-fill semantics unchanged)."""
        propagation.install()
        saved = os.environ.get("RAILWAY_DEPLOYMENT_ID")
        os.environ["RAILWAY_DEPLOYMENT_ID"] = "deploy-1"
        try:
            app = flask.Flask(__name__)
            with app.test_request_context("/"):
                headers = tracing.propagation_headers()
            self.assertEqual(headers.get("X-Campus-Device"), "deploy-1")
            self.assertNotIn("X-Request-ID", headers)
        finally:
            if saved is None:
                os.environ.pop("RAILWAY_DEPLOYMENT_ID", None)
            else:
                os.environ["RAILWAY_DEPLOYMENT_ID"] = saved

    def test_out_of_request_stays_unparented(self):
        """None contract: the SDK applies only the boot journey outside
        requests — no trace/span headers."""
        propagation.install()
        headers = tracing.propagation_headers()
        self.assertNotIn("X-Request-ID", headers)
        self.assertNotIn("X-Parent-Span-ID", headers)

    def test_uninstall_falls_back_to_boot_journey_only(self):
        """Without a provider the SDK has no request-context half
        (the built-in flask reader was removed in campus-api-python
        0.5.0) — even stashed flask.g state yields no trace headers,
        which is why audit.middleware keeps the provider installed."""
        propagation.install()
        tracing.set_request_context_provider(None)
        app = flask.Flask(__name__)
        with app.test_request_context("/"):
            flask.g.trace_id = TRACE_ID
            flask.g.span_id = SPAN_ID
            headers = tracing.propagation_headers()
        self.assertNotIn("X-Request-ID", headers)
        self.assertNotIn("X-Parent-Span-ID", headers)

    def test_header_names_match_sdk_mirrors(self):
        """Lockstep contract: same names as campus_python.tracing."""
        self.assertEqual(propagation.TRACE_ID_HEADER, tracing.TRACE_ID_HEADER)
        self.assertEqual(
            propagation.PARENT_SPAN_ID_HEADER, tracing.PARENT_SPAN_ID_HEADER
        )
        self.assertEqual(
            propagation.JOURNEY_ID_HEADER, tracing.JOURNEY_ID_HEADER
        )
        self.assertEqual(
            propagation.JOURNEY_NAME_HEADER, tracing.JOURNEY_NAME_HEADER
        )
        self.assertEqual(
            propagation.DEVICE_ID_HEADER, tracing.DEVICE_ID_HEADER
        )


if __name__ == "__main__":
    unittest.main()
