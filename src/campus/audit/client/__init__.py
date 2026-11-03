"""campus.audit.client

Internal HTTP client for Campus audit service.

This client is used by campus.auth and campus.api to send trace spans
to the audit service via HTTP.

Example:
    from campus.audit.client import AuditClient

    client = AuditClient()
    client.traces.ingest(span_data)
"""

__all__ = ("AuditClient",)

from campus.audit.http.interface import JsonClient
from campus.common import env

from .v1 import AuditRoot


def _create_http_client(base_url: str) -> JsonClient:
    """Create HTTP client using class attribute or default.

    Priority:
    1. AuditClient.json_client_class (if set)
    2. DefaultClient with explicit AUDIT_API_KEY (Bearer auth) if set
    3. DefaultClient (ambient environment credentials)

    The AUDIT_API_KEY path passes the key explicitly to DefaultClient's
    auth parameter so the Authorization header is scoped to this client
    only; ambient credentials (ACCESS_TOKEN, CLIENT_ID/CLIENT_SECRET)
    shared by other DefaultClient consumers are never picked up (#699).

    Args:
        base_url: The base URL for the client

    Returns:
        A JsonClient instance
    """
    # Priority 1: Class attribute (recommended approach)
    # Use getattr to avoid static type checker errors (attribute is set at runtime)
    client_class = getattr(AuditClient, "json_client_class", None)
    if client_class is not None:
        return client_class(base_url=base_url)

    # Priority 2: Default client with explicit audit API key
    from campus.audit.http import DefaultClient
    api_key = env.get("AUDIT_API_KEY")
    if api_key:
        return DefaultClient(base_url=base_url, auth=api_key)  # type: ignore[return-value]

    # Priority 3: Default client with ambient environment credentials
    return DefaultClient(base_url=base_url)  # type: ignore[return-value]


def _get_base_url() -> str:
    """Get the audit service base URL from environment.

    Delegates to campus.common.services.get_base_url("campus.audit"):
    the registry routes a deployment resolving its own name
    (DEPLOY=campus.audit) to the canonical origin, so trace ingestion
    inside the audit deployment never transits the public URL —
    formerly this function's special case.
    """
    from campus.common.services import get_base_url

    return get_base_url("campus.audit")


class AuditClient:
    """Internal HTTP client for Campus audit service.

    This client provides access to audit service endpoints for internal
    service-to-server communication (e.g., campus.auth → campus.audit).

    The client uses campus.audit.http.DefaultClient which handles:
    - Bearer auth via the AUDIT_API_KEY environment variable (preferred;
      the audit service only accepts `audit_v1_` API keys) (#699)
    - Basic auth via CLIENT_ID/CLIENT_SECRET environment variables
      (legacy fallback; rejected by the audit service front door)
    - Persistent connections via requests.Session
    - Automatic error handling and logging

    For dependency injection in tests, you can:
    1. Set json_client_class class attribute (recommended):
       AuditClient.json_client_class = TestJsonClient
    2. Pass http_client parameter directly (for custom instances):
       AuditClient(http_client=my_custom_client)
    """

    # Configurable JsonClient class for dependency injection
    # Set this to inject a custom JsonClient implementation (e.g., for testing)
    # When None, DefaultClient is used via _create_http_client()
    json_client_class: type[JsonClient] | None = None

    def __init__(
        self,
        *,
        http_client: JsonClient | None = None,
        base_url: str | None = None,
        timeout: int = 30
    ):
        """Initialize the audit client.

        Args:
            http_client: Optional custom JSON client for dependency injection.
                         If None, a client will be created using the factory
                         or DefaultClient.
            base_url: Optional base URL for the audit service. If None, will be
                      determined from environment. Only used when http_client
                      is None.
            timeout: Request timeout in seconds (default: 30). Only used when
                     http_client is None.
        """
        if http_client is None:
            base_url = base_url or _get_base_url()
            http_client = _create_http_client(base_url)
        self._root = AuditRoot(json_client=http_client)

    @property
    def traces(self):
        """Get the traces resource for ingesting and querying spans."""
        return self._root.traces

    @property
    def journeys(self):
        """Get the journeys resource for querying a journey's traces."""
        return self._root.journeys

    @property
    def apikeys(self):
        """Get the apikeys resource for managing audit API keys."""
        return self._root.apikeys
