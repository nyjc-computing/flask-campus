"""campus.audit.client.v1.traces

Traces resource for audit v1 API client.

Provides methods to ingest and query trace spans.

URL path mapping:
    /audit/v1/traces/               → Traces (list, new)
    /audit/v1/traces/{trace_id}/    → Trace (get_tree, spans sub-resource)
    /audit/v1/traces/search         → Search traces
"""

import contextlib
from typing import Any, Optional

from campus.audit.http.interface import JsonClient, JsonResponse

from ..interface import SLASH, Resource, ResourceCollection, ResourceRoot


def _import_ratelimit():
    """Import the SDK breaker module lazily (it ships in campus_python).

    Resolved via importlib with a string name: the module is optional
    (a stale campus_python predating #831 lacks it), and opaque
    resolution keeps type checkers happy against older SDK installs.
    Returns None when unavailable — callers degrade to no observation.
    """
    import importlib

    try:
        return importlib.import_module("campus_python.audit.ratelimit")
    except ImportError:
        return None


def _safe_json(response: JsonResponse) -> Any:
    """Best-effort JSON parse for breaker observation."""
    try:
        return response.json()
    except Exception:
        return None


class Traces(ResourceCollection):
    """Resource for trace spans collection.

    URL path: /audit/v1/traces/

    Supports:
    - POST: Ingest spans (single or batch)
    - GET: List recent traces
    - GET /search: Filter/search traces
    """

    path = "traces/"

    def __init__(self, client: Optional[JsonClient] = None, *, root: ResourceRoot):
        """Initialize the traces resource.

        Args:
            client: The HTTP client to use for requests.
            root: The root resource.
        """
        self._client = client
        self.root = root

    @property
    def client(self) -> JsonClient:
        """Get the JsonClient associated with this resource."""
        if self._client:
            return self._client
        if self.root.client:
            return self.root.client
        raise AttributeError("No client defined for Traces")

    def __getitem__(self, trace_id: str) -> "Trace":
        """Get a specific trace resource by ID.

        Args:
            trace_id: The trace identifier.

        Returns:
            A Trace resource for the specific trace.
        """
        return Traces.Trace(trace_id, parent=self)

    def new(self, *spans: dict) -> JsonResponse:
        """Ingest trace spans (single or batch).

        Args:
            *spans: One or more span data dictionaries matching TraceSpan schema.

        Returns:
            Response with created span data (201 on success, 207 on partial failure).

        Raises:
            NetworkError: If the HTTP request fails.

        Examples:
            # Single span
            client.traces.new(span_data)

            # Batch
            client.traces.new(span1, span2, span3)
        """
        response = self.client.post(self.make_path(), json={"spans": list(spans)})
        # Feed the ingest circuit breaker before raising (#831): audit
        # 429s trip the bucket (Retry-After + tripped key from the
        # body), 2xx clears expired trips. Observation is guarded — a
        # breaker failure or a stale SDK can never break ingestion.
        with contextlib.suppress(Exception):
            ratelimit_module = _import_ratelimit()
            if ratelimit_module is not None:
                ratelimit_module.breaker.observe(
                    response.status_code,
                    response.headers,
                    _safe_json(response),
                )
        response.raise_for_status()
        return response

    def list(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> JsonResponse:
        """List recent traces, newest first.

        Args:
            since: ISO 8601 timestamp (optional).
            until: ISO 8601 timestamp (optional).
            limit: Maximum number of traces to return (default: 50).

        Returns:
            Response with list of TraceSummary objects.

        Raises:
            NetworkError: If the HTTP request fails.
        """
        params: dict[str, Any] = {"limit": limit}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until

        response = self.client.get(self.make_path(), params=params)
        response.raise_for_status()
        return response

    def search(
        self,
        *,
        path: str | None = None,
        status: int | None = None,
        api_key_id: str | None = None,
        client_id: str | None = None,
        user_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> JsonResponse:
        """Filter and search traces.

        Args:
            path: Filter by endpoint path.
            status: Filter by HTTP status code.
            api_key_id: Filter by API key.
            client_id: Filter by OAuth client.
            user_id: Filter by user.
            since: ISO 8601 timestamp (optional).
            until: ISO 8601 timestamp (optional).
            limit: Maximum number of traces to return (default: 50).

        Returns:
            Response with filtered trace list matching criteria.

        Raises:
            NetworkError: If the HTTP request fails.
        """
        params: dict[str, Any] = {"limit": limit}
        if path is not None:
            params["path"] = path
        if status is not None:
            params["status"] = status
        if api_key_id is not None:
            params["api_key_id"] = api_key_id
        if client_id is not None:
            params["client_id"] = client_id
        if user_id is not None:
            params["user_id"] = user_id
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until

        response = self.client.get(self.make_path("search"), params=params)
        response.raise_for_status()
        return response

    class Trace(Resource):
        """Single trace resource.

        URL path: /audit/v1/traces/{trace_id}/

        Supports:
        - GET: Get trace tree (all spans in hierarchy)
        """

        def __init__(self, trace_id: str, parent: ResourceCollection):
            """Initialize the trace resource.

            Args:
                trace_id: The trace identifier.
                parent: The parent traces resource.
            """
            super().__init__(trace_id, parent=parent, root=parent.root)
            self.trace_id = trace_id

        @property
        def client(self) -> JsonClient:
            """Get the JsonClient associated with this resource."""
            return self.parent.client

        def get_tree(self) -> JsonResponse:
            """Get the trace tree with hierarchical span structure.

            Returns:
                Response with TraceTree containing root span and nested children.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.get(self.make_path(end_slash=True))
            response.raise_for_status()
            return response

        @property
        def spans(self) -> "Traces.Spans":
            """Get the spans sub-resource for this trace.

            Returns:
                A Spans collection resource for this trace.
            """
            return Traces.Spans(trace_id=self.trace_id, parent=self)

    class Spans(ResourceCollection):
        """Resource for spans within a trace.

        URL path: /audit/v1/traces/{trace_id}/spans/

        Supports:
        - GET: List all spans in trace (flat list)
        """

        trace_id: str

        def __init__(self, *, trace_id: str, parent: Resource):
            """Initialize the trace spans resource.

            Args:
                trace_id: The trace identifier.
                parent: The parent trace resource.
            """
            self._client = None  # Will use parent's client
            # ResourceCollection requires root and path to be set
            self.root = parent.root
            self.path = "spans/"
            self.parent = parent
            self.trace_id = trace_id

        @property
        def client(self) -> JsonClient:
            """Get the JsonClient associated with this resource."""
            return self.parent.client

        def make_path(self, part: str | None = None) -> str:
            """Create a full path for a sub-resource or action.

            Override because Spans is nested under Trace (not directly under root).
            """
            if part:
                return (
                    f"{self.parent.path.rstrip(SLASH)}"
                    f"/{self.path.lstrip(SLASH).rstrip(SLASH)}"
                    f"/{part.lstrip(SLASH)}"
                )
            return (
                f"{self.parent.path.rstrip(SLASH)}"
                f"/{self.path.lstrip(SLASH)}"
            )

        def list(self) -> JsonResponse:
            """List all spans in the trace (flat list).

            Returns:
                Response with list of TraceSpan objects.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.get(self.make_path())
            response.raise_for_status()
            return response

        def __getitem__(self, span_id: str) -> "Traces.Spans.Span":
            """Get a specific span resource by ID.

            Args:
                span_id: The span identifier.

            Returns:
                A Span resource for the specific span.
            """
            return Traces.Spans.Span(span_id=span_id, parent=self)

        class Span(Resource):
            """Single span resource.

            URL path: /audit/v1/traces/{trace_id}/spans/{span_id}/

            Supports:
            - GET: Get individual span details
            """

            def __init__(self, span_id: str, parent: "Traces.Spans"):
                """Initialize the span resource.

                Args:
                    span_id: The span identifier.
                    parent: The parent spans resource.
                """
                super().__init__(span_id, parent=parent, root=parent.root)
                self.span_id = span_id

            @property
            def client(self) -> JsonClient:
                """Get the JsonClient associated with this resource."""
                return self.parent.client

            def get(self) -> JsonResponse:
                """Get the span details.

                Returns:
                    Response with TraceSpan object.

                Raises:
                    NetworkError: If the HTTP request fails.
                """
                response = self.client.get(self.make_path(end_slash=True))
                response.raise_for_status()
                return response
