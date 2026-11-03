"""campus.audit.client.interface

Interface definitions for audit client resources.

These are simplified versions of the campus-api-python interfaces,
adapted for internal service-to-server communication.
"""

from typing import Any, Optional

SLASH = "/"


class ResourceRoot:
    """Root of all resources.

    This class is used to group all top-level resources together.
    """

    _client: Optional[Any] = None
    url_prefix: str

    def __init__(self, json_client: Optional[Any] = None):
        self._client = json_client

    @property
    def base_url(self) -> str:
        """Get the base URL for this resource root."""
        if not self._client:
            raise AttributeError("No client defined")
        return self._client.base_url

    @property
    def client(self) -> Any:
        """Get the JsonClient associated with this resource root."""
        if not self._client:
            raise AttributeError("No client defined")
        return self._client

    def make_path(self, part: str | None = None) -> str:
        """Create a full path for the resource root or a sub-resource.

        Args:
            part: Optional sub-resource or action path.

        Returns:
            Full path for the resource root or sub-resource.
        """
        if part:
            return f"/{self.url_prefix.lstrip(SLASH)}/{part.lstrip(SLASH)}"
        return f"/{self.url_prefix.lstrip(SLASH)}"

    def make_url(self) -> str:
        """Create a full URL for the resource root."""
        return f"{self.base_url}/{self.url_prefix.lstrip(SLASH)}"


class ResourceCollection:
    """Collection of resources.

    This class is used to group related resources together.
    """
    _client: Optional[Any] = None
    path: str
    root: ResourceRoot

    def __init__(
        self,
        client: Optional[Any] = None,
        *,
        root: ResourceRoot
    ):
        self._client = client
        self.root = root
        # Validate path to ensure trailing slashes
        if not self.path.endswith(SLASH):
            raise ValueError(
                f"{self.path}: ResourceCollection path must end with {SLASH!r}"
            )

    @property
    def client(self) -> Any:
        """Get the JsonClient associated with this resource."""
        if self._client:
            return self._client
        if self.root.client:
            return self.root.client
        raise AttributeError(f"No client defined for {self}")

    def make_path(self, part: str | None = None) -> str:
        """Create a full path for a sub-resource or action."""
        if part:
            return (
                f"/{self.root.url_prefix.lstrip(SLASH)}"
                f"/{self.path.lstrip(SLASH).rstrip(SLASH)}"
                f"/{part.lstrip(SLASH)}"
            )
        return f"/{self.root.url_prefix.lstrip(SLASH)}/{self.path.lstrip(SLASH)}"

    def make_url(self, part: str | None = None) -> str:
        """Create a full URL for a sub-resource or action."""
        return f"{self.root.base_url}{self.make_path(part)}"


class Resource:
    """Resource class that represents API resources.

    The resource class uses a JsonClient instance to handle all API requests.
    It only tracks the path of the current resource.
    """
    _client: Optional[Any] = None
    parent: Any
    path: str

    def __init__(
        self,
        *parts: str,
        parent: Any,
        root: ResourceRoot,
        client: Optional[Any] = None,
    ):
        """Initialize a resource.

        Args:
            *parts: Path parts to join.
            parent: The parent resource (Resource or ResourceCollection).
            root: The root resource.
            client: Optional client to use instead of parent's client.
        """
        self._client = client
        self.parent = parent
        self.root = root
        self.path = parent.make_path(SLASH.join(parts))

    def __repr__(self) -> str:
        return f"Resource(client={self.client}, path={self.path})"

    def __str__(self) -> str:
        return self.path

    @property
    def client(self) -> Any:
        """Get the JsonClient associated with this resource."""
        if self._client:
            return self._client
        if self.parent and hasattr(self.parent, "client"):
            return self.parent.client
        raise AttributeError(f"No client defined for {self}")

    def make_path(self, part: str | None = None, end_slash: bool = False) -> str:
        """Create a full path for a sub-resource or action.

        Args:
            part: Optional sub-resource path.
            end_slash: Whether to ensure trailing slash.

        Returns:
            Full path for the resource or sub-resource.
        """
        if part:
            full_path = (
                f"/{self.path.strip(SLASH)}"
                f"/{part.strip(SLASH)}"
                f"{SLASH if end_slash else ''}"
            )
        else:
            full_path = f"/{self.path.lstrip(SLASH)}"
        if end_slash and not full_path.endswith(SLASH):
            full_path += SLASH
        return full_path

    def make_url(self, part: str | None = None) -> str:
        """Create a full URL for a sub-resource or action.

        Args:
            part: Optional sub-resource path.

        Returns:
            Full URL for the resource or sub-resource.
        """
        return f"{self.root.base_url}{self.make_path(part)}"
