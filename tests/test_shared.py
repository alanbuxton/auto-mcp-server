"""Unit tests for the OpenAPI-spec parsing helpers in util.shared."""

import pytest

from util import shared
from util.shared import (
    resolve_schema_ref,
    process_schema_properties,
    extract_response_info,
    operation_requires_auth,
    extract_tools_from_openapi,
    extract_header_apikey_schemes,
)


# --------------------------------------------------------------------------- #
# resolve_schema_ref
# --------------------------------------------------------------------------- #
class TestResolveSchemaRef:
    def test_resolves_a_local_ref(self):
        spec = {"components": {"schemas": {"Pet": {"type": "object"}}}}
        assert resolve_schema_ref(spec, "#/components/schemas/Pet") == {"type": "object"}

    def test_non_local_ref_returns_empty(self):
        # External / URL refs are not supported and resolve to {}.
        spec = {"components": {}}
        assert resolve_schema_ref(spec, "https://example.com/Pet.json") == {}

    def test_missing_ref_target_returns_empty(self):
        spec = {"components": {"schemas": {}}}
        assert resolve_schema_ref(spec, "#/components/schemas/Missing") == {}

    def test_ref_into_non_dict_returns_empty(self):
        # Walking into a non-dict value raises TypeError internally -> {}.
        spec = {"components": "not-a-dict"}
        assert resolve_schema_ref(spec, "#/components/schemas/Pet") == {}


# --------------------------------------------------------------------------- #
# process_schema_properties
# --------------------------------------------------------------------------- #
class TestProcessSchemaProperties:
    def test_copies_basic_keywords(self):
        schema = {
            "type": "string",
            "description": "a name",
            "format": "email",
            "enum": ["a", "b"],
        }
        result = process_schema_properties({}, schema)
        assert result == {
            "type": "string",
            "description": "a name",
            "format": "email",
            "enum": ["a", "b"],
        }

    def test_resolves_top_level_ref(self):
        spec = {"components": {"schemas": {"Name": {"type": "string", "description": "n"}}}}
        schema = {"$ref": "#/components/schemas/Name"}
        result = process_schema_properties(spec, schema)
        assert result == {"type": "string", "description": "n"}

    def test_array_items_processed_recursively(self):
        schema = {"type": "array", "items": {"type": "integer", "description": "an int"}}
        result = process_schema_properties({}, schema)
        assert result["type"] == "array"
        assert result["items"] == {"type": "integer", "description": "an int"}

    def test_array_items_ref_is_resolved(self):
        spec = {"components": {"schemas": {"Item": {"type": "string"}}}}
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}
        result = process_schema_properties(spec, schema)
        assert result["items"] == {"type": "string"}

    def test_object_properties_processed_recursively(self):
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "tag": {"type": "string", "description": "a tag"},
            },
        }
        result = process_schema_properties({}, schema)
        assert result["properties"]["id"] == {"type": "integer"}
        assert result["properties"]["tag"] == {"type": "string", "description": "a tag"}

    def test_constraints_are_copied(self):
        schema = {
            "type": "string",
            "minLength": 1,
            "maxLength": 10,
            "pattern": "^a",
            "minimum": 0,
            "maximum": 5,
        }
        result = process_schema_properties({}, schema)
        for key in ("minLength", "maxLength", "pattern", "minimum", "maximum"):
            assert result[key] == schema[key]

    def test_unknown_keywords_are_dropped(self):
        schema = {"type": "string", "deprecated": True, "example": "x"}
        result = process_schema_properties({}, schema)
        assert "deprecated" not in result
        assert "example" not in result


# --------------------------------------------------------------------------- #
# extract_response_info
# --------------------------------------------------------------------------- #
class TestExtractResponseInfo:
    def test_extracts_2xx_with_json_schema(self):
        operation = {
            "responses": {
                "200": {
                    "description": "ok",
                    "content": {
                        "application/json": {"schema": {"type": "object"}}
                    },
                }
            }
        }
        result = extract_response_info({}, operation)
        assert result["200"]["description"] == "ok"
        assert result["200"]["schema"] == {"type": "object"}

    def test_ignores_non_2xx_responses(self):
        operation = {
            "responses": {
                "404": {"description": "missing"},
                "500": {"description": "boom"},
            }
        }
        assert extract_response_info({}, operation) == {}

    def test_2xx_without_json_content_has_no_schema(self):
        operation = {
            "responses": {"204": {"description": "no content"}}
        }
        result = extract_response_info({}, operation)
        assert result["204"] == {"description": "no content"}
        assert "schema" not in result["204"]

    def test_missing_responses_key(self):
        assert extract_response_info({}, {}) == {}


# --------------------------------------------------------------------------- #
# operation_requires_auth
# --------------------------------------------------------------------------- #
class TestOperationRequiresAuth:
    def test_operation_security_overrides_global(self):
        spec = {"security": [{"apiToken": []}]}
        operation = {"security": []}  # explicitly disables auth
        assert operation_requires_auth(spec, operation) is False

    def test_non_empty_operation_security_requires_auth(self):
        spec = {}
        operation = {"security": [{"apiToken": []}]}
        assert operation_requires_auth(spec, operation) is True

    def test_falls_back_to_global_security(self):
        spec = {"security": [{"apiToken": []}]}
        operation = {}
        assert operation_requires_auth(spec, operation) is True

    def test_no_security_anywhere(self):
        assert operation_requires_auth({}, {}) is False


# --------------------------------------------------------------------------- #
# extract_tools_from_openapi
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_allowlist(monkeypatch):
    """Disable ALLOWED_TOOLS filtering so every operation is exported."""
    monkeypatch.setattr(shared, "ALLOWED_TOOLS", [])


def _spec(paths, **extra):
    return {"openapi": "3.0.0", "paths": paths, **extra}


class TestExtractToolsFromOpenapi:
    def test_uses_operation_id_as_name(self, no_allowlist):
        spec = _spec({"/pets": {"get": {"operationId": "listPets"}}})
        tools = extract_tools_from_openapi(spec)
        assert "listPets" in tools
        assert tools["listPets"]["endpoint"] == "/pets"
        assert tools["listPets"]["method"] == "GET"

    def test_generates_name_when_operation_id_absent(self, no_allowlist):
        spec = _spec({"/pets/{id}": {"get": {}}})
        tools = extract_tools_from_openapi(spec)
        # path params have braces stripped, slashes -> underscores
        assert "get_pets_id" in tools

    def test_skips_unsupported_methods(self, no_allowlist):
        spec = _spec(
            {
                "/pets": {
                    "get": {"operationId": "listPets"},
                    "options": {"operationId": "optionsPets"},
                    "head": {"operationId": "headPets"},
                }
            }
        )
        tools = extract_tools_from_openapi(spec)
        assert set(tools) == {"listPets"}

    def test_query_parameters_become_input_properties(self, no_allowlist):
        spec = _spec(
            {
                "/pets": {
                    "get": {
                        "operationId": "listPets",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "integer"},
                                "description": "max results",
                            }
                        ],
                    }
                }
            }
        )
        tool = extract_tools_from_openapi(spec)["listPets"]
        props = tool["inputSchema"]["properties"]
        assert props["limit"]["type"] == "integer"
        assert props["limit"]["in"] == "query"
        assert props["limit"]["description"] == "max results"
        assert tool["inputSchema"]["required"] == ["limit"]

    def test_request_body_properties_are_merged(self, no_allowlist):
        spec = _spec(
            {
                "/pets": {
                    "post": {
                        "operationId": "createPet",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "tag": {"type": "string"},
                                        },
                                        "required": ["name"],
                                    }
                                }
                            }
                        },
                    }
                }
            }
        )
        tool = extract_tools_from_openapi(spec)["createPet"]
        props = tool["inputSchema"]["properties"]
        assert "name" in props and "tag" in props
        assert tool["inputSchema"]["required"] == ["name"]

    def test_requires_auth_reflects_security(self, no_allowlist):
        spec = _spec(
            {
                "/secure": {"get": {"operationId": "secure", "security": [{"apiToken": []}]}},
                "/open": {"get": {"operationId": "open", "security": []}},
            },
            security=[{"apiToken": []}],
        )
        tools = extract_tools_from_openapi(spec)
        assert tools["secure"]["requires_auth"] is True
        assert tools["open"]["requires_auth"] is False


# --------------------------------------------------------------------------- #
# extract_header_apikey_schemes
# --------------------------------------------------------------------------- #
class TestExtractHeaderApiKeySchemes:
    def test_returns_header_apikey_scheme(self):
        spec = {
            "components": {
                "securitySchemes": {
                    "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
                }
            }
        }
        result = extract_header_apikey_schemes(spec)
        assert "xApiKey" in result
        assert result["xApiKey"]["name"] == "X-API-Key"

    def test_excludes_cookie_schemes(self):
        spec = {
            "components": {
                "securitySchemes": {
                    "cookieAuth": {"type": "apiKey", "in": "cookie", "name": "session"},
                    "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                }
            }
        }
        result = extract_header_apikey_schemes(spec)
        assert "xApiKey" in result
        assert "cookieAuth" not in result

    def test_excludes_http_bearer_scheme(self):
        spec = {
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                    "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                }
            }
        }
        result = extract_header_apikey_schemes(spec)
        assert "xApiKey" in result
        assert "bearerAuth" not in result

    def test_returns_empty_when_no_schemes(self):
        assert extract_header_apikey_schemes({}) == {}
        assert extract_header_apikey_schemes({"components": {}}) == {}

    def test_returns_multiple_header_schemes(self):
        spec = {
            "components": {
                "securitySchemes": {
                    "key1": {"type": "apiKey", "in": "header", "name": "X-Key-1"},
                    "key2": {"type": "apiKey", "in": "header", "name": "X-Key-2"},
                }
            }
        }
        result = extract_header_apikey_schemes(spec)
        assert set(result) == {"key1", "key2"}


    def test_allowlist_filters_tools(self, monkeypatch):
        monkeypatch.setattr(shared, "ALLOWED_TOOLS", ["listPets"])
        spec = _spec(
            {
                "/pets": {"get": {"operationId": "listPets"}},
                "/owners": {"get": {"operationId": "listOwners"}},
            }
        )
        tools = extract_tools_from_openapi(spec)
        assert set(tools) == {"listPets"}

    def test_description_falls_back_to_method_and_path(self, no_allowlist):
        spec = _spec({"/pets": {"get": {"operationId": "listPets"}}})
        tool = extract_tools_from_openapi(spec)["listPets"]
        assert tool["description"] == "GET /pets"

    def test_summary_and_description_combined(self, no_allowlist):
        spec = _spec(
            {
                "/pets": {
                    "get": {
                        "operationId": "listPets",
                        "summary": "List pets",
                        "description": "Returns all pets",
                    }
                }
            }
        )
        tool = extract_tools_from_openapi(spec)["listPets"]
        assert tool["description"] == "List pets\nReturns all pets"
