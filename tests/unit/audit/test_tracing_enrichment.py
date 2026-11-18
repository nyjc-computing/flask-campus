"""Tests for the #26 trace enrichment: static-asset capture (env-gated,
tagged) and WARNING+ log records attached to the request's span.
"""

import logging
import os
import tempfile
import unittest
from unittest import mock

import flask

from campus.audit.middleware import init_app, tracing


class SpanCaptureTestCase(unittest.TestCase):
    """Base: a Flask app with the tracing hooks and a capture sink."""

    def setUp(self):
        self.static_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.static_dir.cleanup)
        with open(os.path.join(self.static_dir.name, "app.js"), "w") as fh:
            fh.write("console.log(1);")

        self.app = flask.Flask(
            __name__, static_folder=self.static_dir.name, static_url_path="/static"
        )

        @self.app.route("/page")
        def page():
            # JSON: _extract_response_body parses JSON bodies only
            # (matching campus.api/auth responses).
            return flask.jsonify({"ok": True})

        self.captured = []
        # Patch before init_app: init_app itself doesn't ingest, but keep
        # the sink in place for whatever fires.
        patcher = mock.patch.object(tracing, "_ingest_span_async",
                                     side_effect=self.captured.append)
        patcher.start()
        self.addCleanup(patcher.stop)

        init_app(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        # The warning collector is a root-logger handler; remove it so
        # other test modules don't inherit it.
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, tracing.RequestWarningCollector):
                root.removeHandler(handler)

    def last_span(self):
        return self.captured[-1] if self.captured else None


class TestStaticCapture(SpanCaptureTestCase):
    """AUDIT_TRACING_INCLUDE_STATIC gates static-asset spans (#818 -> #26)."""

    def test_static_skipped_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDIT_TRACING_INCLUDE_STATIC", None)
            self.client.get("/static/app.js")
        self.assertIsNone(self.last_span())

    def test_favicon_skipped_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDIT_TRACING_INCLUDE_STATIC", None)
            self.client.get("/favicon.ico")
        self.assertIsNone(self.last_span())

    def test_static_captured_and_tagged_when_enabled(self):
        with mock.patch.dict(os.environ, {"AUDIT_TRACING_INCLUDE_STATIC": "1"}):
            response = self.client.get("/static/app.js")
        self.assertEqual(response.status_code, 200)
        span = self.last_span()
        self.assertIsNotNone(span)
        self.assertEqual(span["tags"].get("request_kind"), "static")
        # Binary noise: bodies are not captured for static assets.
        self.assertIsNone(span["response_body"])
        self.assertIsNone(span["request_body"])
        # Headers/status/timing still captured.
        self.assertEqual(span["status_code"], 200)
        self.assertIn("Content-Type", span["response_headers"])

    def test_page_requests_unaffected_by_static_flag(self):
        with mock.patch.dict(os.environ, {"AUDIT_TRACING_INCLUDE_STATIC": "1"}):
            self.client.get("/page")
        span = self.last_span()
        self.assertIsNotNone(span)
        self.assertNotIn("request_kind", span["tags"])
        self.assertIsNotNone(span["response_body"])


class TestWarningCapture(SpanCaptureTestCase):
    """WARNING+ records from the request ride along as tags.warnings (#26)."""

    def test_warnings_attached_to_span(self):
        logger = logging.getLogger("test.enrichment")
        with self.app.test_request_context("/page"):
            tracing.start_span()
            logger.warning("something odd happened")
            span = tracing.build_span_from_context(
                flask.g.trace_id, flask.g.span_id,
                flask.make_response("ok"), 1.0,
            )
        self.assertEqual(
            span["tags"]["warnings"],
            [{"level": "WARNING", "logger": "test.enrichment",
              "message": "something odd happened"}],
        )

    def test_no_warnings_no_tag(self):
        with self.app.test_request_context("/page"):
            tracing.start_span()
            span = tracing.build_span_from_context(
                flask.g.trace_id, flask.g.span_id,
                flask.make_response("ok"), 1.0,
            )
        self.assertNotIn("warnings", span["tags"])

    def test_collector_capped(self):
        logger = logging.getLogger("test.enrichment.cap")
        with self.app.test_request_context("/page"):
            tracing.start_span()
            for i in range(tracing._MAX_COLLECTED_WARNINGS + 5):
                logger.warning("warn %d", i)
            span = tracing.build_span_from_context(
                flask.g.trace_id, flask.g.span_id,
                flask.make_response("ok"), 1.0,
            )
        warnings = span["tags"]["warnings"]
        self.assertEqual(len(warnings), tracing._MAX_COLLECTED_WARNINGS)
        self.assertEqual(warnings[0]["message"], "warn 0")

    def test_message_strings_pass_through(self):
        # Keyed redaction applies to dict values only; formatted log
        # messages are free text and must survive verbatim.
        with self.app.test_request_context("/page"):
            tracing.start_span()
            logging.getLogger("test.enrichment.text").warning(
                "failed for user %s", "u1"
            )
            span = tracing.build_span_from_context(
                flask.g.trace_id, flask.g.span_id,
                flask.make_response("ok"), 1.0,
            )
        self.assertEqual(
            span["tags"]["warnings"][0]["message"], "failed for user u1"
        )

    def test_install_warning_collector_idempotent(self):
        root = logging.getLogger()
        # setUp's init_app already installed one; re-installing must not add more.
        before = len([h for h in root.handlers
                      if isinstance(h, tracing.RequestWarningCollector)])
        self.assertGreaterEqual(before, 1)
        tracing.install_warning_collector()
        tracing.install_warning_collector()
        after = len([h for h in root.handlers
                     if isinstance(h, tracing.RequestWarningCollector)])
        self.assertEqual(after, before)


class TestCollectorOutsideRequest(unittest.TestCase):

    def test_records_outside_request_context_ignored(self):
        collector = tracing.RequestWarningCollector()
        record = logging.LogRecord(
            "test.offctx", logging.WARNING, __file__, 1,
            "no request here", None, None,
        )
        # No request context: emit must not raise and must not buffer.
        collector.emit(record)


if __name__ == "__main__":
    unittest.main()
