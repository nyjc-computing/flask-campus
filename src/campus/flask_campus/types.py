"""campus.flask_campus.types

Common utility functions for validation of flask requests and responses.
"""

from typing import (
    Any,
    Callable,
    Generic,
    NoReturn,
    Protocol,
    TypeVar,
)

from werkzeug import Response as FlaskResponse

R = TypeVar("R", covariant=True)

# Only expecting strings or dicts
JsonObject = dict[str, Any]
StatusCode = int


class ViewFunction(Protocol, Generic[R]):
    """A view function that takes arbitrary arguments and returns a response.
    """

    def __call__(self, **kwargs) -> R:
        ...


ViewFunctionDecorator = Callable[[ViewFunction], ViewFunction]
# Actually, view functions may return a variety of return values which Flask is
# able to handle
# But Campus API sticks to JSON-serializable return values, with a status code
JsonResponse = tuple[dict[str, Any], StatusCode]
HtmlResponse = tuple[str, StatusCode]

JsonViewFunction = ViewFunction[JsonResponse]
FlaskViewFunction = ViewFunction[FlaskResponse]


class ErrorHandler(Protocol):
    """Define an ErrorHandler as a function that takes a status code and
    optional keyword arguments.

    Error Handlers must raise an exception.
    """

    def __call__(self, status: StatusCode, **body) -> NoReturn:
        """An error handler returns None"""
        ...
