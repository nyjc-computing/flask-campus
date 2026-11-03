"""campus.audit.client.v1.root

Root resource for audit v1 API.
"""

from ..interface import ResourceRoot


class AuditRoot(ResourceRoot):
    """Root resource for audit v1 API.

    Groups all v1 resources under a single entry point.
    """

    url_prefix: str = "audit/v1"

    def __init__(self, json_client=None):
        """Initialize the audit root.

        Args:
            json_client: The HTTP client to use for requests.
        """
        super().__init__(json_client=json_client)

    @property
    def traces(self):
        """Get the traces resource."""
        if not hasattr(self, "_traces"):
            from .traces import Traces
            self._traces = Traces(
                client=self._client,
                root=self
            )
        return self._traces
