"""campus.audit.client.v1.apikeys

API-keys resource for audit v1 API client.

Mirrors /audit/v1/apikeys: mint, list, inspect, re-scope, revoke, and
rotate audit API keys. Mutations demand the apikeys:write scope and
reads apikeys:read — the caller's key must already hold them (in
practice the operator key or a key minted from it).

URL path mapping:
    /audit/v1/apikeys/                     → ApiKeys (new, list)
    /audit/v1/apikeys/{api_key_id}/        → ApiKey (get, update, revoke)
    /audit/v1/apikeys/{api_key_id}/regenerate → ApiKey (rotate)
"""

from typing import Any, Optional

from campus.audit.http.interface import JsonClient, JsonResponse

from ..interface import Resource, ResourceCollection, ResourceRoot


class ApiKeys(ResourceCollection):
    """Resource for audit API keys.

    URL path: /audit/v1/apikeys/

    Supports:
    - POST: Mint a key (plaintext shown once, in the response)
    - GET: List keys
    """

    path = "apikeys/"

    def __init__(self, client: Optional[JsonClient] = None, *, root: ResourceRoot):
        """Initialize the apikeys resource.

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
        raise AttributeError("No client defined for ApiKeys")

    def __getitem__(self, api_key_id: str) -> "ApiKeys.ApiKey":
        """Get a specific key resource by id.

        Args:
            api_key_id: The API key identifier.

        Returns:
            An ApiKey resource for the specific key.
        """
        return ApiKeys.ApiKey(api_key_id, parent=self)

    def new(
        self,
        *,
        name: str,
        owner_id: str,
        scopes: list[str] | None = None,
        role: str | None = None,
        rate_limit: int | None = None,
        expires_at: str | None = None,
    ) -> JsonResponse:
        """Mint a new API key.

        Give either a role (a named scope bundle) or a raw scope list,
        not both. The plaintext appears once, in the response's
        ``api_key`` field, and is never stored server-side.

        Returns:
            Response with the key record plus the plaintext.

        Raises:
            NetworkError: If the HTTP request fails.
        """
        body: dict[str, Any] = {"name": name, "owner_id": owner_id}
        if scopes is not None:
            body["scopes"] = scopes
        if role is not None:
            body["role"] = role
        if rate_limit is not None:
            body["rate_limit"] = rate_limit
        if expires_at is not None:
            body["expires_at"] = expires_at
        response = self.client.post(self.make_path(), json=body)
        response.raise_for_status()
        return response

    def list(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool | None = None,
        limit: int | None = None,
    ) -> JsonResponse:
        """List API keys.

        Args:
            owner_id: Filter by owner (optional).
            active_only: Only non-expired, non-revoked keys. The API
                defaults to true; pass False to include revoked and
                expired keys (e.g. for a management view).
            limit: Maximum number of keys to return.

        Returns:
            Response with {"api_keys": [...], "count": N}.

        Raises:
            NetworkError: If the HTTP request fails.
        """
        params: dict[str, Any] = {}
        if owner_id is not None:
            params["owner_id"] = owner_id
        if active_only is not None:
            params["active_only"] = "true" if active_only else "false"
        if limit is not None:
            params["limit"] = limit
        response = self.client.get(self.make_path(), params=params)
        response.raise_for_status()
        return response

    class ApiKey(Resource):
        """Single API key resource.

        URL path: /audit/v1/apikeys/{api_key_id}/

        Supports:
        - GET: Key details (never the plaintext or its hash)
        - PATCH: Update mutable fields (name, scopes, rate_limit)
        - DELETE: Revoke the key
        - POST regenerate: Rotate (new plaintext, same key id)
        """

        def __init__(self, api_key_id: str, parent: "ApiKeys"):
            """Initialize the key resource.

            Args:
                api_key_id: The API key identifier.
                parent: The parent apikeys resource.
            """
            super().__init__(api_key_id, parent=parent, root=parent.root)
            self.api_key_id = api_key_id

        @property
        def client(self) -> JsonClient:
            """Get the JsonClient associated with this resource."""
            return self.parent.client

        def get(self) -> JsonResponse:
            """Get the key's details (excluding key_hash).

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.get(self.make_path(end_slash=True))
            response.raise_for_status()
            return response

        def update(
            self,
            *,
            name: str | None = None,
            scopes: list[str] | None = None,
            role: str | None = None,
            rate_limit: int | None = None,
        ) -> JsonResponse:
            """Update mutable fields of the key.

            Give either a role or a scope list, not both. Only name,
            scopes, and rate_limit are mutable; revocation is DELETE.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            body: dict[str, Any] = {}
            if name is not None:
                body["name"] = name
            if role is not None:
                body["role"] = role
            if scopes is not None:
                body["scopes"] = scopes
            if rate_limit is not None:
                body["rate_limit"] = rate_limit
            response = self.client.patch(self.make_path(end_slash=True), json=body)
            response.raise_for_status()
            return response

        def revoke(self) -> JsonResponse:
            """Revoke the key (204 on success). The record is kept for
            audit purposes; the key stops authenticating immediately.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.delete(self.make_path(end_slash=True))
            response.raise_for_status()
            return response

        def regenerate(self) -> JsonResponse:
            """Rotate the key: a new plaintext (response's ``key``
            field, shown once), the same key id. The old plaintext
            stops working immediately.

            Raises:
                NetworkError: If the HTTP request fails.
            """
            response = self.client.post(self.make_path("regenerate"))
            response.raise_for_status()
            return response
