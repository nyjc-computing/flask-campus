"""campus.audit.http.errors

Common error types used across all campus HTTP client modules.
"""


class HttpClientError(Exception):
    """Base exception for all campus client errors."""
    pass


class AuthenticationError(HttpClientError):
    """Raised when authentication fails."""
    pass


class AccessDeniedError(HttpClientError):
    """Raised when the client lacks required permissions."""
    pass


class ConflictError(HttpClientError):
    """Raised when a conflict occurs."""
    pass


class NotFoundError(HttpClientError):
    """Raised when a requested resource is not found."""
    pass


class InvalidRequestError(HttpClientError):
    """Raised when request validation fails."""
    pass


class NetworkError(HttpClientError):
    """Raised when network communication fails."""
    pass


class MalformedResponseError(HttpClientError):
    """Raised when the API response is malformed."""
    pass
