"""Unit tests for the audit client's v1 resource mappings.

Verifies that the client resource classes hit the right URL paths with
the right methods and query parameters, without any network I/O:

- traces.search: filters, cursor pagination, and the events toggle
  (#941) that lets a caller ask for the request-only list
- journeys [id].get: the journey view endpoint (#803)
- apikeys: the key-management surface (new/list/get/update/revoke/
  regenerate) used by the audit web UI's keys page

File: tests/unit/audit/test_client_resources.py
"""

import unittest

from campus.audit.client import AuditClient


class StubResponse:
    """Minimal JsonResponse double: json() returns the canned body."""

    def __init__(self, body):
        self._body = body
        self.status_code = 200

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class RecordingClient:
    """JsonClient double that records calls and returns a canned body."""

    def __init__(self, body=None):
        self.calls: list[tuple[str, str, object]] = []
        self._response = StubResponse(body if body is not None else {})

    def _record(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        return self._response

    def get(self, path, params=None):
        return self._record("GET", path, params)

    def post(self, path, json=None):
        return self._record("POST", path, None, json)

    def put(self, path, json=None):
        return self._record("PUT", path, None, json)

    def delete(self, path, json=None):
        return self._record("DELETE", path)

    def patch(self, path, json=None):
        return self._record("PATCH", path, None, json)


def _client_with_recorder(body=None):
    """An AuditClient backed by a RecordingClient; returns both."""
    recorder = RecordingClient(body)
    return AuditClient(http_client=recorder), recorder


class TestTracesSearchClient(unittest.TestCase):
    """traces.search maps filters and toggles to query params."""

    def test_search_defaults(self):
        client, recorder = _client_with_recorder()
        client.traces.search()
        method, path, params, _ = recorder.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/audit/v1/traces/search")
        self.assertEqual(params, {"limit": 50})

    def test_search_forwards_filters_and_cursor(self):
        client, recorder = _client_with_recorder()
        client.traces.search(
            path="/auth/v1/token",
            status=500,
            api_key_id="key-1",
            client_id="client-1",
            user_id="user-1",
            journey_id="j-1",
            since="2026-01-01T00:00:00+00:00",
            until="2026-01-02T00:00:00+00:00",
            limit=10,
            cursor="opaque",
        )
        _, _, params, _ = recorder.calls[0]
        self.assertEqual(params, {
            "limit": 10,
            "path": "/auth/v1/token",
            "status": 500,
            "api_key_id": "key-1",
            "client_id": "client-1",
            "user_id": "user-1",
            "journey_id": "j-1",
            "since": "2026-01-01T00:00:00+00:00",
            "until": "2026-01-02T00:00:00+00:00",
            "cursor": "opaque",
        })

    def test_search_events_toggle_wire_format(self):
        """events rides as 1/0; absent (None) sends nothing."""
        client, recorder = _client_with_recorder()
        client.traces.search(events=False)
        self.assertEqual(recorder.calls[0][2]["events"], "0")
        client.traces.search(events=True)
        self.assertEqual(recorder.calls[1][2]["events"], "1")
        client.traces.search()
        self.assertNotIn("events", recorder.calls[2][2])


class TestJourneysClient(unittest.TestCase):
    """journeys [id].get hits the journey endpoint."""

    def test_journey_get_path(self):
        client, recorder = _client_with_recorder()
        client.journeys["j-abc"].get()
        method, path, params, _ = recorder.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/audit/v1/journeys/j-abc/")
        self.assertIsNone(params)


class TestApiKeysClient(unittest.TestCase):
    """apikeys resources map the key-management surface."""

    def test_new_minimal(self):
        client, recorder = _client_with_recorder()
        client.apikeys.new(name="k", owner_id="o")
        method, path, _, body = recorder.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/audit/v1/apikeys/")
        self.assertEqual(body, {"name": "k", "owner_id": "o"})

    def test_new_full(self):
        client, recorder = _client_with_recorder()
        client.apikeys.new(
            name="k",
            owner_id="o",
            scopes=["traces:read"],
            role="traces-viewer",
            rate_limit=100,
            expires_at="2027-01-01T00:00:00+00:00",
        )
        _, _, _, body = recorder.calls[0]
        self.assertEqual(body, {
            "name": "k",
            "owner_id": "o",
            "scopes": ["traces:read"],
            "role": "traces-viewer",
            "rate_limit": 100,
            "expires_at": "2027-01-01T00:00:00+00:00",
        })

    def test_list_params(self):
        client, recorder = _client_with_recorder()
        client.apikeys.list()
        self.assertEqual(recorder.calls[0][2], {})
        client.apikeys.list(owner_id="o", active_only=False, limit=200)
        self.assertEqual(recorder.calls[1][2], {
            "owner_id": "o",
            "active_only": "false",
            "limit": 200,
        })

    def test_apikey_get(self):
        client, recorder = _client_with_recorder()
        client.apikeys["key-1"].get()
        method, path, _, _ = recorder.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/audit/v1/apikeys/key-1/")

    def test_apikey_update(self):
        client, recorder = _client_with_recorder()
        client.apikeys["key-1"].update(scopes=["traces:read"])
        method, path, _, body = recorder.calls[0]
        self.assertEqual(method, "PATCH")
        self.assertEqual(path, "/audit/v1/apikeys/key-1/")
        self.assertEqual(body, {"scopes": ["traces:read"]})

    def test_apikey_revoke(self):
        client, recorder = _client_with_recorder()
        client.apikeys["key-1"].revoke()
        method, path, _, _ = recorder.calls[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(path, "/audit/v1/apikeys/key-1/")

    def test_apikey_regenerate(self):
        client, recorder = _client_with_recorder()
        client.apikeys["key-1"].regenerate()
        method, path, _, _ = recorder.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/audit/v1/apikeys/key-1/regenerate")


if __name__ == "__main__":
    unittest.main()
