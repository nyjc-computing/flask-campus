"""campus.flask_campus.openapi

OpenAPI 3 generator for Campus Flask services (campus#1029).

Each service serves a public, unauthenticated OpenAPI 3 JSON at
``/{service-prefix}/openapi.json`` behind campus-gateway. The spec is
**generated** from the Flask app object and **committed** as a
``campus/{service}/docs/openapi.json`` artifact (regenerate with the
CLI below; drift and version-pinning unit tests enforce the
regenerate-and-bump discipline).

Two halves, deliberately separable:

- **Schema emitter**: ``functools.singledispatch`` over
  ``campus.common.schema`` types (``Email``, ``DateTime``, ``CampusID``,
  ``String``, ``Integer``, ``Boolean``, ``Array``, ``Object``, ...) and
  builtin fallbacks, producing plain JSON-Schema dicts. Dispatch lives
  entirely in the generator — ``campus.common.schema`` stays
  spec-agnostic.
- **App introspector**: walks ``app.url_map`` (Flask converter →
  OpenAPI parameter), introspects view signatures (annotations →
  parameters, defaults → optional), and uses **docstrings as the
  operation documentation**: the first line becomes ``summary`` and the
  remainder becomes ``description``. There are no overlay files; the
  error-envelope components are generated once from the
  ``campus.common.errors.api_errors`` taxonomy (#714).

Parameter extraction follows the ``flask_campus.unpack_request``
signature conventions: GET handlers receive query parameters, all other
methods receive a JSON body; path parameters come from the URL rule;
a parameter without a default is required. Parameters read from
``flask.request`` directly (outside ``unpack_request``) are invisible
to the generator.

This module is the pure generator core: it imports nothing from the
services repo, so it moves with the flask-campus carve (#993) untouched.
The AppModule-to-app convenience (``build_spec_from_module``) lives in
``campus.deploy`` beside ``create_app`` — the legal import direction is
service-repo glue importing the consumer layer, never the reverse. The
regen entrypoint is ``scripts/generate_openapi.py`` in the services
repo (generation builds the service app, so it needs that repo's
environment).

Usage::

    from campus.flask_campus import openapi
    spec = openapi.generate_spec(
        app, title="Campus Auth API", version="0.2.0",
    )
    openapi.write_spec(spec, "campus/auth/docs/openapi.json")

Version pinning: ``version`` is explicit and required — the generator
never guesses whose release it is pinning. The services repo pins the
campus-suite version from its pyproject.toml; a PR that changes the
spec must regenerate the artifact and bump that version (minor for
spec changes, patch for API-preserving changes).

Security schemes (bearer/apikey gates) are a phase-2 item and are not
emitted yet; response bodies are documented generically (``200`` +
``default`` error envelope).
"""

import functools
import inspect
import json
import re
import typing
from collections.abc import Iterable
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union

import flask
from werkzeug import routing

from campus.common import schema
from campus.common.errors import api_errors
from campus.common.errors.base import ErrorConstant

from . import parameter

__all__ = [
    "DEFAULT_EXCLUDED_ENDPOINTS",
    "annotation_to_schema",
    "emit_schema",
    "generate_spec",
    "write_spec",
]


# ---------------------------------------------------------------------------
# Schema emitter: singledispatch on campus.common.schema types + builtins.
# Handlers produce plain JSON-Schema dicts and ignore their argument, so the
# same table serves both instance dispatch (below) and class lookup (via
# the registry walk in _emit_for_class).
# ---------------------------------------------------------------------------


@functools.singledispatch
def emit_schema(value: Any) -> dict[str, Any]:
    """Emit an OpenAPI/JSON-Schema dict for a schema-typed value.

    Dispatches on the value's type; unknown types map to the empty
    schema (any value).
    """
    return {}


@emit_schema.register
def _emit_str(value: str) -> dict[str, Any]:
    return {"type": "string"}


@emit_schema.register
def _emit_bool(value: bool) -> dict[str, Any]:
    return {"type": "boolean"}


@emit_schema.register
def _emit_int(value: int) -> dict[str, Any]:
    return {"type": "integer"}


@emit_schema.register
def _emit_float(value: float) -> dict[str, Any]:
    return {"type": "number"}


@emit_schema.register
def _emit_list(value: list) -> dict[str, Any]:
    return {"type": "array"}


@emit_schema.register
def _emit_dict(value: dict) -> dict[str, Any]:
    return {"type": "object"}


# campus.common.schema classes subclass the builtin they emulate, so the
# builtin registrations above already cover them by MRO; the explicit
# registrations keep the type→schema table complete in one place and add
# format information. CampusID is an alias of schema.String and UserID an
# alias of schema.Email, so both need no registration of their own.
@emit_schema.register
def _emit_string(value: schema.String) -> dict[str, Any]:
    return {"type": "string"}


@emit_schema.register
def _emit_boolean(value: schema.Boolean) -> dict[str, Any]:
    # schema.Boolean subclasses int (builtin bool is final): without this
    # registration the MRO walk would emit {"type": "integer"}.
    return {"type": "boolean"}


@emit_schema.register
def _emit_integer(value: schema.Integer) -> dict[str, Any]:
    return {"type": "integer"}


@emit_schema.register
def _emit_number(value: schema.Number) -> dict[str, Any]:
    return {"type": "number"}


@emit_schema.register
def _emit_email(value: schema.Email) -> dict[str, Any]:
    return {"type": "string", "format": "email"}


@emit_schema.register
def _emit_datetime(value: schema.DateTime) -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


@emit_schema.register
def _emit_date(value: schema.Date) -> dict[str, Any]:
    return {"type": "string", "format": "date"}


@emit_schema.register
def _emit_time(value: schema.Time) -> dict[str, Any]:
    # HHMM (24-hr) is not a standard OpenAPI string format; format is an
    # open value, so Campus defines its own.
    return {"type": "string", "format": "hhmm"}


@emit_schema.register
def _emit_url(value: schema.Url) -> dict[str, Any]:
    return {"type": "string", "format": "uri"}


def _emit_for_class(tp: type) -> dict[str, Any]:
    """Emit the schema for a class object by walking its MRO through the
    emit_schema registry.

    singledispatch alone cannot dispatch on class objects (their type is
    ``type`` for every class), so the registry is consulted directly; the
    registrations above remain the single source of the type→schema table.
    """
    for base in tp.__mro__:
        emitter = emit_schema.registry.get(base)
        if emitter is not None:
            return dict(emitter(tp))
    return {}


def annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Emit an OpenAPI schema dict for a type annotation.

    Handles the typing constructs that appear in view signatures
    (``X | None`` unions, ``Literal[...]``, ``list[T]``, ``dict[K, V]``)
    and delegates concrete classes to the emitter.
    """
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    if annotation is None or annotation is type(None):
        return {"nullable": True}
    origin = typing.get_origin(annotation)
    if origin is Union or origin is UnionType:
        members = list(typing.get_args(annotation))
        non_none = [m for m in members if m is not type(None)]
        if len(non_none) == 1:
            merged = annotation_to_schema(non_none[0])
            if len(non_none) != len(members):
                merged = dict(merged)
                merged["nullable"] = True
            return merged
        return {"anyOf": [annotation_to_schema(m) for m in members]}
    if origin is Literal:
        literal: dict[str, Any] = {"enum": list(typing.get_args(annotation))}
        value_types = {type(v) for v in typing.get_args(annotation)}
        if value_types and value_types <= {str}:
            literal["type"] = "string"
        elif value_types and all(isinstance(v, bool) for v in typing.get_args(annotation)):
            literal["type"] = "boolean"
        elif value_types and value_types <= {int}:
            literal["type"] = "integer"
        elif value_types and value_types <= {float}:
            literal["type"] = "number"
        return literal
    if origin is list:
        args = typing.get_args(annotation)
        items = annotation_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}
    if origin is dict:
        args = typing.get_args(annotation)
        obj: dict[str, Any] = {"type": "object"}
        if len(args) == 2 and args[1] is not Any:
            obj["additionalProperties"] = annotation_to_schema(args[1])
        return obj
    if isinstance(annotation, type):
        return _emit_for_class(annotation)
    return {}


# ---------------------------------------------------------------------------
# Error-envelope components, generated from the campus.common.errors
# taxonomy (#714): codes from ErrorConstant, status codes from the
# api_errors.APIError subclasses.
# ---------------------------------------------------------------------------


def _error_codes() -> list[str]:
    """All UPPER_SNAKE_CASE error codes declared in ErrorConstant."""
    return sorted(
        value
        for name, value in vars(ErrorConstant).items()
        if name.isupper() and isinstance(value, str)
    )


def _error_status_codes() -> list[int]:
    """Status codes declared by the api_errors taxonomy classes."""
    statuses: set[int] = set()
    for value in vars(api_errors).values():
        if isinstance(value, type) and issubclass(value, api_errors.APIError):
            status = getattr(value, "status_code", None)
            if isinstance(status, int):
                statuses.add(status)
    return sorted(statuses)


def _error_components() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the error-envelope schemas and the shared Error response."""
    status_list = ", ".join(str(s) for s in _error_status_codes())
    schemas = {
        "ErrorEnvelope": {
            "type": "object",
            "required": ["error"],
            "properties": {
                "error": {"$ref": "#/components/schemas/ErrorBody"},
            },
        },
        "ErrorBody": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Campus error code (UPPER_SNAKE_CASE; AUTH_* for OAuth "
                        "protocol errors, RFC 6749 §5.2)"
                    ),
                    "enum": _error_codes(),
                },
                "message": {"type": "string"},
                "details": {
                    "type": "object",
                    "description": (
                        "Additional error details, including OAuth protocol "
                        "fields (error, error_description)"
                    ),
                },
                "errors": {
                    "type": "array",
                    "description": (
                        "Field-level validation errors; present on the 422 "
                        "ValidationError shape only"
                    ),
                    "items": {"$ref": "#/components/schemas/FieldError"},
                },
                "request_id": {"type": "string", "nullable": True},
            },
        },
        "FieldError": {
            "type": "object",
            "required": ["field", "code", "message"],
            "properties": {
                "field": {"type": "string"},
                "code": {
                    "type": "string",
                    "description": (
                        "Machine-readable field error code, e.g. MISSING, "
                        "INVALID_TYPE, UNRECOGNIZED_FIELD"
                    ),
                },
                "message": {"type": "string"},
            },
        },
    }
    responses = {
        "Error": {
            "description": (
                "Campus error envelope (see ErrorEnvelope); malformed or "
                "invalid fields use the ValidationError shape (422, with "
                f"error.errors). Possible statuses: {status_list}."
            ),
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                }
            },
        }
    }
    return schemas, responses


# ---------------------------------------------------------------------------
# App introspector: url_map walk + view signature introspection.
# ---------------------------------------------------------------------------

# Endpoint names that are deployment conventions (#842) rather than API
# surface: Flask's static handler, the health checks, the generic dev
# index / landing pages registered by campus.deploy, and the OpenAPI
# spec endpoint itself (meta-documentation, not API; see
# campus.deploy.register_openapi). Matched against the last segment of
# the endpoint name so nested blueprints (e.g. ``audit_ui.static``)
# are excluded too.
DEFAULT_EXCLUDED_ENDPOINTS = frozenset({
    "static",
    "health_check",
    "landing",
    "index",
    "openapi_spec",
})

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_PATH_PARAM_RE = re.compile(r"<(?:(?P<converter>[^:<>]+):)?(?P<name>[^<>]+)>")

_TRAILING_SLASH_NOTE = (
    "## Trailing slashes\n\n"
    "Paths are listed exactly as routed (Flask): a path shown with a "
    "trailing slash must be requested with it — the no-slash variant "
    "answers with a 308 redirect."
)


def _converter_schema(converter: routing.BaseConverter | None) -> dict[str, Any]:
    """Map a werkzeug URL converter to an OpenAPI schema."""
    if isinstance(converter, routing.IntegerConverter):
        return {"type": "integer"}
    if isinstance(converter, routing.FloatConverter):
        return {"type": "number"}
    if isinstance(converter, routing.UUIDConverter):
        return {"type": "string", "format": "uuid"}
    # Default/string/any/path converters all bind a string.
    return {"type": "string"}


def _to_openapi_path(rule: str) -> str:
    """Convert a Flask rule (``/users/<user_id>/``) to an OpenAPI path
    template (``/users/{user_id}/``).
    """
    return _PATH_PARAM_RE.sub(lambda m: "{" + m.group("name") + "}", rule)


def _endpoint_tag(endpoint: str) -> str | None:
    """Tag an operation with its blueprint name.

    Flask endpoint names are ``[parent.]blueprint.view``; the blueprint
    segment closest to the view is the resource grouping (e.g.
    ``auth.users.get_all`` → ``users``). Views registered directly on
    the app get no tag.
    """
    parts = endpoint.split(".")
    return parts[-2] if len(parts) >= 2 else None


def _split_docstring(doc: str | None) -> tuple[str | None, str | None]:
    """First line → summary, remainder → description (#1029 decision)."""
    cleaned = inspect.cleandoc(doc) if doc else ""
    if not cleaned:
        return None, None
    lines = cleaned.splitlines()
    summary = lines[0].strip()
    description = "\n".join(lines[1:]).strip()
    return summary or None, description or None


def _json_safe(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _introspect_parameters(
        rule: routing.Rule,
        method: str,
        view: Any,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str], bool]:
    """Extract OpenAPI parameters and body fields from a view signature.

    Returns (parameters, body_fields, required_field_names, has_var_keyword).
    Path parameters come from the URL rule (converter schema refined by the
    handler's annotation); for GET the remaining parameters are query
    parameters, for the body methods they are JSON body properties.
    A parameter without a default is required; defaults ride along as
    schema defaults when JSON-serializable.
    """
    parameters: list[dict[str, Any]] = []
    body_fields: dict[str, dict[str, Any]] = {}
    required_fields: list[str] = []
    has_var_keyword = False

    try:
        sig = inspect.signature(view)
    except (TypeError, ValueError):
        sig = None
    sig_params: dict[str, inspect.Parameter] = dict(sig.parameters) if sig else {}
    has_var_keyword = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig_params.values()
    )

    converters = getattr(rule, "_converters", {})
    path_names = set(rule.arguments)
    for name in sorted(rule.arguments):
        schema_dict = _converter_schema(converters.get(name))
        param = sig_params.get(name)
        if param is not None and param.annotation is not inspect.Parameter.empty:
            schema_dict = {**schema_dict, **annotation_to_schema(param.annotation)}
        parameters.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema_dict,
        })

    for name, p in sig_params.items():
        if name in path_names or p.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            # **kwargs absorbs arbitrary payload fields (#1029 conventions);
            # the body stays an open object.
            continue
        optional = parameter.has_default(p)
        schema_dict = annotation_to_schema(p.annotation)
        # A None default is conveyed by nullable: true; emitting
        # "default": null trips openapi-spec-validator on nullable enums.
        if optional and p.default is not None and _json_safe(p.default):
            schema_dict = {**schema_dict, "default": p.default}
        if method in _BODY_METHODS:
            body_fields[name] = schema_dict
            if not optional:
                required_fields.append(name)
        else:
            parameters.append({
                "name": name,
                "in": "query",
                "required": not optional,
                "schema": schema_dict,
            })
    return parameters, body_fields, required_fields, has_var_keyword


def _request_body(
        body_fields: dict[str, dict[str, Any]],
        required_fields: list[str],
) -> dict[str, Any]:
    body_schema: dict[str, Any] = {"type": "object"}
    if body_fields:
        body_schema["properties"] = body_fields
        if required_fields:
            body_schema["required"] = required_fields
    return {
        "required": bool(required_fields),
        "content": {"application/json": {"schema": body_schema}},
    }


def _build_operation(rule: routing.Rule, method: str, view: Any) -> dict[str, Any]:
    # operationId is assigned by the caller (_operation_ids), which
    # disambiguates views mounted on several routes.
    operation: dict[str, Any] = {
        "responses": {
            "200": {"description": "Successful response"},
            "default": {"$ref": "#/components/responses/Error"},
        },
    }
    summary, description = _split_docstring(view.__doc__)
    if summary:
        operation["summary"] = summary
    if description:
        operation["description"] = description
    parameters, body_fields, required_fields, has_var_keyword = (
        _introspect_parameters(rule, method, view)
    )
    if parameters:
        operation["parameters"] = parameters
    if method in _BODY_METHODS and (body_fields or has_var_keyword):
        operation["requestBody"] = _request_body(body_fields, required_fields)
    return operation


def _operation_ids(
        operations: list[tuple[routing.Rule, str]],
) -> dict[tuple[str, str, str], str]:
    """Build unique operationIds for (rule, method) pairs.

    The convention is the Flask endpoint name with dots → underscores.
    Views mounted on several routes (stacked route decorators, e.g. the
    device-verification page) would collide, so those get the method
    appended, and if two paths of the same view share the method too,
    a ``_by_<param>`` suffix (``_index`` when the variant takes no path
    parameters) disambiguates them.
    """
    endpoint_counts: dict[str, int] = {}
    method_counts: dict[tuple[str, str], int] = {}
    for rule, method in operations:
        endpoint_counts[rule.endpoint] = endpoint_counts.get(rule.endpoint, 0) + 1
        key = (rule.endpoint, method.lower())
        method_counts[key] = method_counts.get(key, 0) + 1
    ids: dict[tuple[str, str, str], str] = {}
    for rule, method in operations:
        base = rule.endpoint.replace(".", "_")
        op_id = base
        if endpoint_counts[rule.endpoint] > 1:
            op_id = f"{base}_{method.lower()}"
            if method_counts[(rule.endpoint, method.lower())] > 1:
                if rule.arguments:
                    op_id += "_by_" + "_".join(sorted(rule.arguments))
                else:
                    op_id += "_index"
        ids[(rule.endpoint, method, rule.rule)] = op_id
    return ids


def generate_spec(
        app: flask.Flask,
        *,
        title: str,
        description: str = "",
        version: str,
        exclude_endpoints: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Generate an OpenAPI 3 spec dict from a Flask app object.

    Walks ``app.url_map``; every non-excluded rule becomes a path item,
    every method an operation. ``version`` is required and lands in
    ``info.version`` verbatim — callers pass the release they are
    pinning. The 308 trailing-slash convention is appended to the
    description unless the description already mentions it.
    """
    excluded = (
        DEFAULT_EXCLUDED_ENDPOINTS if exclude_endpoints is None
        else frozenset(exclude_endpoints)
    )
    selected: list[tuple[routing.Rule, str]] = []
    for rule in app.url_map.iter_rules():
        leaf = rule.endpoint.rsplit(".", 1)[-1]
        if leaf in excluded:
            continue
        for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
            selected.append((rule, method))

    operation_ids = _operation_ids(selected)

    paths: dict[str, dict[str, Any]] = {}
    tags: set[str] = set()
    for rule, method in selected:
        view = app.view_functions[rule.endpoint]
        operation = _build_operation(rule, method, view)
        operation["operationId"] = operation_ids[(rule.endpoint, method, rule.rule)]
        tag = _endpoint_tag(rule.endpoint)
        if tag:
            operation["tags"] = [tag]
            tags.add(tag)
        path_item = paths.setdefault(_to_openapi_path(rule.rule), {})
        path_item[method.lower()] = operation

    if description and "308" not in description:
        description = f"{description}\n\n{_TRAILING_SLASH_NOTE}"
    elif not description:
        description = _TRAILING_SLASH_NOTE

    error_schemas, error_responses = _error_components()
    return {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "description": description,
            "version": version,
        },
        "tags": [{"name": tag} for tag in sorted(tags)],
        "paths": paths,
        "components": {
            "schemas": error_schemas,
            "responses": error_responses,
        },
    }


def write_spec(spec: dict[str, Any], path: Path | str) -> None:
    """Write the spec as deterministic JSON (sorted keys, 2-space indent).

    Deterministic output keeps committed artifacts diff-stable across
    regeneration runs; LF line endings keep them stable across OSes.
    """
    text = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    Path(path).write_text(text, encoding="utf-8", newline="\n")
