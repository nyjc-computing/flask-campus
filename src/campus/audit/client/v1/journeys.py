"""campus.audit.client.v1.journeys

Journeys resource for audit v1 API client.

Provides methods to query the member traces of one journey (#803).

URL path mapping:
    /audit/v1/journeys/                → Journeys (collection root)
    /audit/v1/journeys/{journey_id}/   → Journey (get member traces)
"""

from typing import Optional

from campus.audit.http.interface import JsonClient, JsonResponse

from ..interface import Resource, ResourceCollection, ResourceRoot


class Journeys(ResourceCollection):
    """Resource for journey queries.

    URL path: /audit/v1/journeys/

    Supports:
    - GET /{journey_id}: Member traces of one journey, oldest first
    """

    path = "journeys/"

    def __init__(self, client: Optional[JsonClient] = None, *, root: ResourceRoot):
        """Initialize the journeys resource.

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
        raise AttributeError("No client defined for Journeys")

    def __getitem__(self, journey_id: str) -> "Journeys.Journey":
        """Get a specific journey resource by id.

        Args:
            journey_id: The journey tag carried on the member spans.

        Returns:
            A Journey resource for the specific journey.
        """
        return Journeys.Journey(journey_id, parent=self)

    class Journey(Resource):
        """Single journey resource.

        URL path: /audit/v1/journeys/{journey_id}/

        Supports:
        - GET: Member traces of the journey, oldest first
        """

        def __init__(self, journey_id: str, parent: "Journeys"):
            """Initialize the journey resource.

            Args:
                journey_id: The journey tag carried on the member spans.
                parent: The parent journeys resource.
            """
            super().__init__(journey_id, parent=parent, root=parent.root)
            self.journey_id = journey_id

        @property
        def client(self) -> JsonClient:
            """Get the JsonClient associated with this resource."""
            return self.parent.client

        def get(self) -> JsonResponse:
            """Get the journey's member traces.

            Returns:
                Response with {"journey_id": ..., "trace_count": N,
                "traces": [...]}.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.get(self.make_path(end_slash=True))
            response.raise_for_status()
            return response
