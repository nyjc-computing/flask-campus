"""campus.audit.http.default

Default implementation for JsonClient and JsonResponse, using `requests`.
"""

import logging
from typing import Any, Callable, Iterable, Mapping, Optional, Self, TypedDict
from urllib.parse import urljoin

import requests

from campus.common import env
from campus.common.utils import secret

from .errors import (
    AuthenticationError,
    NetworkError,
)
from .interface import JsonClient, JsonDict, JsonResponse

logger = logging.getLogger(__name__)


ClientFactory = Callable[[], JsonClient]
ClientHeader = dict[str, str]


class BasicCredentials(TypedDict):
    """Client credentials for authentication."""
    client_id: str
    client_secret: str


class BearerCredentials(TypedDict):
    """Client credentials for authentication."""
    access_token: str


def _load_credentials_from_env() -> tuple[str, str] | str:
    """
    Load client credentials from environment variables.

    Attempts to load ACCESS_TOKEN (for bearer auth) or CLIENT_ID and
    CLIENT_SECRET (for basic auth) from environment variables.

    Raises:
        AuthenticationError: If required environment variables are absent.
    """
    if (value := env.get("ACCESS_TOKEN")):
        return value
    id_, secret = env.get("CLIENT_ID"), env.get("CLIENT_SECRET")
    if id_ and secret:
        return id_, secret
    raise AuthenticationError(
        f"Missing credentials {'CLIENT_ID' if not id_ else 'CLIENT_SECRET'}"
    )


def check_env_var(var_name: str) -> str:
    """Check if an environment variable is set and return its value.

    Args:
        var_name: The name of the environment variable to check.

    Raises:
        AuthenticationError: If the environment variable is not set.
    """
    value = env.get(var_name)
    if not value:
        raise AuthenticationError(f"Missing {var_name} environment variable")
    return value


class DefaultResponse(JsonResponse):
    """Default response wrapper, using `requests` package."""
    # pylint: disable=missing-function-docstring

    def __init__(self, response: requests.Response):
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> dict[str, str]:
        # Convert to plain dict[str, str]
        return dict(self._response.headers.items())

    @property
    def text(self) -> str:
        return self._response.text

    def json(self):
        return self._response.json()


class DefaultClient(JsonClient):
    """
    Default client for Campus service communication, using `requests` package.

    This implementation uses a persistent requests.Session for all requests,
    setting authentication and content headers once per credential change. It
    provides methods for sending HTTP requests and handling authentication
    automatically.
    """
    credentials: BasicCredentials | BearerCredentials

    def __init__(
            self,
            base_url: Optional[str] = None,
            *,
            auth: Iterable[str] | str | None = None,
            headers: Mapping[str, str] | None = None,
    ):
        """
        Initialize the RequestsClient.

        Args:
            base_url: Override default base URL for the service.
            auth_scheme: 'basic' or 'bearer' (default: 'basic').

        This sets up a persistent requests.Session and loads credentials from the environment.
        Headers are set once and reused for all requests until credentials change.
        """
        self.base_url = base_url
        assert headers is None or 'Authorization' not in headers, (
            "'Authorization' in headers conflicts with provided auth"
        )

        # Prepare a persistent session and set default headers
        self._session = requests.Session()
        # Load credentials from environment if not provided
        auth = auth or _load_credentials_from_env()
        match auth:
            case (client_id, client_secret):
                self.headers['Authorization'] = secret.encode_http_basic_auth(
                    client_id,
                    client_secret
                )
            case str():
                self.headers['Authorization'] = f"Bearer {auth}"
            case _:
                raise ValueError("Unknown auth {auth!r}")
        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    @property
    def headers(self):
        return self._session.headers

    def _make_request(
            self,
            method: str,
            path: str,
            params: Any = None,
            json: Any = None,
    ) -> DefaultResponse:
        """
        Make an HTTP request using the persistent session.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE).
            path: API path (will be joined with base_url).
            json: Optional JSON body for the request.

        Returns:
            RequestsResponse: A response wrapper object.

        Raises:
            NetworkError: If the network request fails.
        """
        # if base_url is present, expect a partial path
        # if base_url is absent, expect a full URL
        url = (
            urljoin(self.base_url, path.lstrip('/')) if self.base_url
            else path
        )
        
        logger.debug(f"HTTP {method} {url}")
        if json:
            logger.debug(f"Request body: {json}")
            
        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                timeout=30
            )
            logger.debug(f"HTTP {method} {url} -> {response.status_code}")
            
            # Log response details for error cases
            if response.status_code >= 400:
                try:
                    response_text = response.text[:500]  # Limit to avoid huge logs
                    logger.warning(f"HTTP {method} {url} failed ({response.status_code}): {response_text}")
                except Exception:
                    logger.warning(f"HTTP {method} {url} failed ({response.status_code}): <unable to read response>")
                    
        except requests.RequestException as e:
            logger.error(f"HTTP {method} {url} network error: {e}")
            raise NetworkError(f"Network request failed: {e}") from e
        else:
            return DefaultResponse(response)

    def get(self: Self, path: str, params: JsonDict | None = None) -> JsonResponse:
        return self._make_request("GET", path, params=params)

    def post(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        return self._make_request("POST", path, json=json)

    def put(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        return self._make_request("PUT", path, json=json)

    def patch(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        return self._make_request("PATCH", path, json=json)

    def delete(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        return self._make_request("DELETE", path, json=json)
