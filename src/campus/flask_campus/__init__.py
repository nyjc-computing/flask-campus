"""campus.flask_campus

Common utility functions for validation of flask requests and responses.
"""

from .journeys import journey
from .login_manager import OAuthLoginManager
from .types import (
    HtmlResponse,
    JsonResponse,
    JsonViewFunction,
    ViewFunctionDecorator,
)
from .utils import (
    get_request_headers,
    get_request_payload,
    get_user_agent,
    unpack_into,
    unpack_request,
    validate_json_response,
    validate_request_and_extract_json,
    validate_request_and_extract_urlparams,
)

__all__ = [
    "HtmlResponse",
    "JsonResponse",
    "JsonViewFunction",
    "OAuthLoginManager",
    "ViewFunctionDecorator",
    "get_user_agent",
    "get_request_headers",
    "get_request_payload",
    "journey",
    "unpack_into",
    "unpack_request",
    "validate_request_and_extract_json",
    "validate_request_and_extract_urlparams",
    "validate_json_response",
]
