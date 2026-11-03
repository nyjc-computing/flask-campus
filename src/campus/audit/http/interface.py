"""campus.audit.http.interface

Interface descriptions for the Campus client interface.

This interface is designed to:
- wrap `flask.testing.FlaskClient`
- wrap most common client interfaces e.g. `requests`
- provide a common Response interface that wraps `werkzeug.test.TestResponse,
  `requests.Response`, etc
- so aa to enable WSGI hooks or unit testing with a local WSGI app.
"""

__all__ = [
    "JsonClient",
    "JsonResponse",
]

from collections.abc import Mapping
from typing import Any, Iterable, Protocol, Self, runtime_checkable

from campus.audit.http.errors import (
    AccessDeniedError,
    AuthenticationError,
    ConflictError,
    HttpClientError,
    InvalidRequestError,
    NotFoundError,
)

Header = Mapping[str, str]
JsonDict = dict[str, Any]


@runtime_checkable
class JsonResponse(Protocol):
    """This class describes the public interface required from Response
    wrappers for JSON responses.
    """
    # pylint: disable=unnecessary-ellipsis

    def __init__(self, response: Any):
        self._response = response  # type: ignore

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        ...

    @property
    def headers(self) -> dict[str, str]:
        """Returns headers of the response as a dict."""
        ...

    @property
    def text(self) -> str:
        """Returns the response body as a string."""
        ...

    def json(self) -> Any:
        """Returns the response body as JSON."""
        ...

    def ok(self) -> bool:
        """Returns True if the response status code is 2xx, False otherwise."""
        return 200 <= self.status_code < 300

    def client_error(self) -> bool:
        """Returns True if the response status code is 4xx, False otherwise."""
        return 400 <= self.status_code < 500

    def server_error(self) -> bool:
        """Returns True if the response status code is 5xx, False otherwise."""
        return 500 <= self.status_code < 600

    def raise_for_status(self) -> None:
        """Raises an exception if the response status code indicates an error.

        Raises:
            AuthenticationError: If the status code is 401
            AccessDeniedError: If the status code is 403
            NotFoundError: If the status code is 404
            ConflictError: If the status code is 409
            InvalidRequestError: If the status code is 400 or 422
            HttpClientError: For other 4xx or 5xx status codes
        """
        if not (self.client_error() or self.server_error()):
            return

        status = self.status_code

        # The response body rides along verbatim as an exception note
        # below, so no structured-detail parsing is needed here. (A
        # previous draft computed an `error_msg` with fallback
        # expressions but never used it — pyright 1.414 flagged the
        # dead expressions and the block was removed.)

        match status:
            case 400:
                error = InvalidRequestError(f"{status} Bad Request")
            case 401:
                error = AuthenticationError(f"{status} Unauthorized")
            case 403:
                error = AccessDeniedError(f"{status} Forbidden")
            case 404:
                error = NotFoundError(f"{status} Not Found")
            case 409:
                error = ConflictError(f"{status} Conflict")
            case 422:
                error = InvalidRequestError(f"{status} Unprocessable Entity")
            case _:
                # Generic error for other 4xx/5xx codes
                error = HttpClientError(f"{status} Unknown HTTP Error")
        error.add_note(f"Headers: {self.headers}")
        error.add_note(f"Body: {self.json() or self}")
        raise error from None


@runtime_checkable
class JsonClient(Protocol):
    """This class describes the public interface required from Client classes,
    which are used to send JSON requests.
    """
    base_url: str | None
    # pylint: disable=unnecessary-ellipsis

    def __init__(
            self,
            base_url: str | None = None,
            *,
            auth: Iterable[str] | str | None = None,
            headers: Mapping[str, str] | None = None,
            **kwargs: Any
    ):
        raise NotImplementedError(
            "Subclasses must override __init__()"
        )

    def get(self: Self, path: str, params: JsonDict | None = None) -> JsonResponse:
        """Sends a GET request."""
        ...

    def post(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        """Sends a POST request."""
        ...

    def put(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        """Sends a PUT request."""
        ...

    def delete(self: Self, path: str, json: JsonDict | None = None) -> JsonResponse:
        """Sends a DELETE request."""
        ...

    def patch(self: Self, path: str, json: Any = None) -> JsonResponse:
        """Sends a PATCH request."""
        ...
