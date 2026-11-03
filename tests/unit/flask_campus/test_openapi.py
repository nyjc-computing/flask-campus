"""Unit tests for campus.flask_campus.openapi (campus#1029).

Covers the schema emitter (singledispatch over campus.common.schema
types + typing constructs), the app introspector (url_map walk, view
signature params, docstring documentation), the error-envelope
components, the CLI, and the deterministic artifact output.
"""

import json
import unittest
from typing import Literal

import flask
from openapi_spec_validator import validate

from campus import flask_campus
from campus.common import schema
from campus.flask_campus import openapi


def _build_widgets_blueprint() -> flask.Blueprint:
    """A blueprint following the unpack_request handler conventions."""
    bp = flask.Blueprint("widgets", __name__, url_prefix="/api/v1/widgets")

    @bp.get("/")
    @flask_campus.unpack_request
    def list_widgets(
            limit: schema.Integer = 10,
            q: str | None = None,
    ) -> flask_campus.JsonResponse:
        """List widgets.

        Longer description of the listing operation,
        spanning multiple lines.
        """
        return {"widgets": []}, 200

    @bp.post("/")
    @flask_campus.unpack_request
    def new_widget(
            label: str,
            tags: list[str] | None = None,
            priority: schema.Integer = 1,
    ) -> flask_campus.JsonResponse:
        """Create a widget."""
        return {"label": label}, 201

    @bp.get("/<widget_id>/")
    @flask_campus.unpack_request
    def get_widget(widget_id: schema.CampusID) -> flask_campus.JsonResponse:
        """Get a widget by id."""
        return {}, 200

    @bp.delete("/<widget_id>/")
    @flask_campus.unpack_request
    def delete_widget(widget_id: schema.CampusID) -> flask_campus.JsonResponse:
        """Delete a widget."""
        return {}, 204

    @bp.get("/by-year/<int:year>/<path:rest>")
    def get_by_year(year: int, rest: str) -> flask_campus.JsonResponse:
        """Get widgets by year.

        Plain view (no unpack_request); converters still documented.
        """
        return {}, 200

    # One view mounted on several routes (stacked decorators): the
    # device-verification pattern. OperationIds must stay unique.
    @bp.get("/alias")
    @bp.get("/alias/<alias_id>/")
    @bp.post("/alias")
    def alias_view(alias_id: str | None = None) -> flask_campus.JsonResponse:
        """Handle widget aliases."""
        return {}, 200

    return bp


def _make_app() -> flask.Flask:
    app = flask.Flask("dummy")
    app.register_blueprint(_build_widgets_blueprint())

    @app.get("/health")
    def health_check():
        return {"status": "ok"}, 200

    return app


def _generate(**kwargs) -> dict:
    defaults: dict = {
        "title": "Dummy API",
        "description": "Dummy description.",
        "version": "9.9.9",
    }
    defaults.update(kwargs)
    return openapi.generate_spec(_make_app(), **defaults)


class TestSchemaEmitter(unittest.TestCase):
    """annotation_to_schema: schema classes, builtins, typing constructs."""

    def test_schema_classes(self) -> None:
        cases = {
            schema.Email: {"type": "string", "format": "email"},
            schema.UserID: {"type": "string", "format": "email"},
            schema.DateTime: {"type": "string", "format": "date-time"},
            schema.Date: {"type": "string", "format": "date"},
            schema.Time: {"type": "string", "format": "hhmm"},
            schema.Url: {"type": "string", "format": "uri"},
            schema.String: {"type": "string"},
            schema.CampusID: {"type": "string"},
            schema.Integer: {"type": "integer"},
            schema.Boolean: {"type": "boolean"},
            schema.Number: {"type": "number"},
            schema.Array: {"type": "array"},
            schema.Object: {"type": "object"},
        }
        for annotation, expected in cases.items():
            self.assertEqual(openapi.annotation_to_schema(annotation), expected)

    def test_builtin_fallbacks(self) -> None:
        cases = {
            str: {"type": "string"},
            bool: {"type": "boolean"},
            int: {"type": "integer"},
            float: {"type": "number"},
            list: {"type": "array"},
            dict: {"type": "object"},
        }
        for annotation, expected in cases.items():
            self.assertEqual(openapi.annotation_to_schema(annotation), expected)

    def test_instance_dispatch_matches_class_dispatch(self) -> None:
        self.assertEqual(
            openapi.emit_schema(schema.DateTime.utcnow()),
            openapi.annotation_to_schema(schema.DateTime),
        )

    def test_optional_union_is_nullable(self) -> None:
        self.assertEqual(
            openapi.annotation_to_schema(str | None),
            {"type": "string", "nullable": True},
        )

    def test_unannotated_and_any_are_empty(self) -> None:
        import inspect
        self.assertEqual(openapi.annotation_to_schema(inspect.Parameter.empty), {})
        self.assertEqual(openapi.annotation_to_schema(None), {"nullable": True})

    def test_literal_is_enum(self) -> None:
        self.assertEqual(
            openapi.annotation_to_schema(Literal["a", "b"]),
            {"enum": ["a", "b"], "type": "string"},
        )

    def test_generic_list_and_dict(self) -> None:
        self.assertEqual(
            openapi.annotation_to_schema(list[str]),
            {"type": "array", "items": {"type": "string"}},
        )
        self.assertEqual(
            openapi.annotation_to_schema(dict[str, schema.Email]),
            {
                "type": "object",
                "additionalProperties": {"type": "string", "format": "email"},
            },
        )
        self.assertEqual(openapi.annotation_to_schema(dict[str, str]), {
            "type": "object",
            "additionalProperties": {"type": "string"},
        })


class TestIntrospector(unittest.TestCase):
    """generate_spec: url_map walk, parameters, docstrings, responses."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = _generate()

    def test_spec_is_valid_openapi(self) -> None:
        validate(self.spec)

    def test_paths_cover_api_routes_only(self) -> None:
        self.assertEqual(set(self.spec["paths"]), {
            "/api/v1/widgets/",
            "/api/v1/widgets/{widget_id}/",
            "/api/v1/widgets/by-year/{year}/{rest}",
            "/api/v1/widgets/alias",
            "/api/v1/widgets/alias/{alias_id}/",
        })
        # Deployment conventions are excluded (#842 endpoints).
        self.assertNotIn("/health", self.spec["paths"])

    def test_methods_exclude_head_options(self) -> None:
        for path_item in self.spec["paths"].values():
            for key in path_item:
                self.assertIn(key, {"get", "post", "put", "patch", "delete"})

    def test_docstring_is_operation_documentation(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/"]["get"]
        self.assertEqual(op["summary"], "List widgets.")
        self.assertTrue(op["description"].startswith("Longer description"))

    def test_operation_id_and_tag(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/{widget_id}/"]["get"]
        self.assertEqual(op["operationId"], "widgets_get_widget")
        self.assertEqual(op["tags"], ["widgets"])
        self.assertIn({"name": "widgets"}, self.spec["tags"])

    def test_path_params_from_converters_and_annotations(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/{widget_id}/"]["get"]
        self.assertEqual(op["parameters"], [{
            "name": "widget_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }])
        op = self.spec["paths"]["/api/v1/widgets/by-year/{year}/{rest}"]["get"]
        schemas = {p["name"]: p["schema"] for p in op["parameters"]}
        self.assertEqual(schemas["year"], {"type": "integer"})
        self.assertEqual(schemas["rest"], {"type": "string"})
        self.assertTrue(all(p["required"] for p in op["parameters"]))

    def test_get_params_are_query_parameters(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/"]["get"]
        params = {p["name"]: p for p in op["parameters"]}
        self.assertEqual(set(params), {"limit", "q"})
        self.assertTrue(all(p["in"] == "query" for p in params.values()))
        self.assertEqual(params["limit"]["schema"], {
            "type": "integer", "default": 10,
        })
        self.assertFalse(params["limit"]["required"])
        self.assertEqual(params["q"]["schema"], {
            "type": "string", "nullable": True,
        })
        self.assertFalse(params["q"]["required"])

    def test_body_methods_get_request_body(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/"]["post"]
        body = op["requestBody"]
        self.assertTrue(body["required"])
        schema_dict = body["content"]["application/json"]["schema"]
        self.assertEqual(schema_dict["type"], "object")
        self.assertEqual(schema_dict["required"], ["label"])
        self.assertEqual(schema_dict["properties"]["label"], {"type": "string"})
        self.assertEqual(schema_dict["properties"]["tags"], {
            "type": "array", "items": {"type": "string"},
            "nullable": True,
        })
        self.assertEqual(schema_dict["properties"]["priority"], {
            "type": "integer", "default": 1,
        })
        self.assertNotIn("parameters", op)

    def test_delete_without_body_fields_has_no_request_body(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/{widget_id}/"]["delete"]
        self.assertNotIn("requestBody", op)
        self.assertIn("parameters", op)

    def test_error_response_reference(self) -> None:
        op = self.spec["paths"]["/api/v1/widgets/"]["get"]
        self.assertEqual(
            op["responses"]["default"],
            {"$ref": "#/components/responses/Error"},
        )
        self.assertIn("200", op["responses"])

    def test_multi_route_view_gets_unique_operation_ids(self) -> None:
        ids = {
            op["operationId"]
            for path_spec in self.spec["paths"].values()
            for op in path_spec.values()
            if op["operationId"].startswith("widgets_alias_view")
        }
        self.assertEqual(ids, {
            "widgets_alias_view_get_index",
            "widgets_alias_view_get_by_alias_id",
            "widgets_alias_view_post",
        })
        validate(self.spec)


class TestErrorComponents(unittest.TestCase):
    """Error-envelope components generated from the api_errors taxonomy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = _generate()
        cls.components = cls.spec["components"]

    def test_envelope_schemas_present(self) -> None:
        schemas = self.components["schemas"]
        for name in ("ErrorEnvelope", "ErrorBody", "FieldError"):
            self.assertIn(name, schemas)

    def test_error_codes_come_from_taxonomy(self) -> None:
        enum = (
            self.components["schemas"]["ErrorBody"]["properties"]["code"]["enum"]
        )
        for code in (
                "NOT_FOUND",
                "VALIDATION_FAILED",
                "AUTH_INVALID_GRANT",
                "SERVICE_UNAVAILABLE",
        ):
            self.assertIn(code, enum)

    def test_error_response(self) -> None:
        error = self.components["responses"]["Error"]
        self.assertIn("422", error["description"])
        # Status list is derived from the api_errors classes (#714).
        self.assertIn("503", error["description"])
        self.assertEqual(
            error["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/ErrorEnvelope"},
        )


class TestSpecConventions(unittest.TestCase):
    """Version pinning, trailing-slash note, determinism."""

    def test_version_is_required(self) -> None:
        # The generator never guesses whose release it is pinning
        # (#993 carve boundary): callers pass the version explicitly.
        with self.assertRaises(TypeError):
            openapi.generate_spec(_make_app(), title="Dummy API")

    def test_explicit_version_wins(self) -> None:
        self.assertEqual(_generate(version="1.2.3")["info"]["version"], "1.2.3")

    def test_trailing_slash_note_appended(self) -> None:
        spec = _generate()
        self.assertIn("308 redirect", spec["info"]["description"])

    def test_trailing_slash_note_not_duplicated(self) -> None:
        description = "Service docs mentioning a 308 redirect already."
        spec = _generate(description=description)
        self.assertNotIn("## Trailing slashes", spec["info"]["description"])

    def test_generation_is_deterministic(self) -> None:
        first = json.dumps(_generate(), sort_keys=True)
        second = json.dumps(_generate(), sort_keys=True)
        self.assertEqual(first, second)
