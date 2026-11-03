"""campus.audit.http

Abstractions for HTTP requests.

In production, HTTP requests are usually sent with a library like `requests` or
`httpx`. In development, the requests may be sent to a local server, while in
testing the requests may even bypass the network and be sent directly to the
app, e.g. Flask's test client.

This module provides abstractions for making HTTP requests and handling
responses.
"""

__all__ = [
    "AccessDeniedError",
    "AuthenticationError",
    "ConflictError",
    "DefaultClient",
    "DefaultResponse",
    "HttpClientError",
    "InvalidRequestError",
    "JsonClient",
    "JsonResponse",
    "MalformedResponseError",
    "NetworkError",
    "NotFoundError",
    "get_client",
]

from .default import DefaultClient, DefaultResponse
from .errors import (
    AccessDeniedError,
    AuthenticationError,
    ConflictError,
    HttpClientError,
    InvalidRequestError,
    MalformedResponseError,
    NetworkError,
    NotFoundError,
)
from .interface import JsonClient, JsonResponse

# Cache instantiated clients to reuse sessions
__client_cache: dict[tuple, JsonClient] = {}
__kwd_mark = object()
client_factory = DefaultClient


def _hash_args_kwargs(*args, **kwargs) -> tuple:
    """Hash function args and kwargs (assume simple values) and return key."""
    # Ref: https://stackoverflow.com/a/10220908
    key = args + (__kwd_mark,) + tuple(sorted(kwargs.items()))
    return key


def get_client(*args, cache: bool = True, **kwargs) -> JsonClient:
    """Returns a JSON client for making requests.

    Caches instantiated clients to reuse sessions.
    """
    key = _hash_args_kwargs(*args, **kwargs)
    if not cache:
        return client_factory(*args, **kwargs)
    if key not in __client_cache:
        __client_cache[key] = client_factory(*args, **kwargs)
    return __client_cache[key]
