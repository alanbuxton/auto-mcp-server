"""Unit tests for the stateless Streamable HTTP MCP server helpers.

These cover the two pure functions in the module: ``prepare_auth_headers``
(request-header forwarding/rewriting) and ``generate_mcp_discovery_document``
(the ``.well-known/mcp.json`` builder). The network/ASGI parts of ``main`` are
out of scope for unit tests.
"""

import types

import pytest

import stateless_streaming_http_mcp_server as server


# --------------------------------------------------------------------------- #
# prepare_auth_headers
# --------------------------------------------------------------------------- #
class TestPrepareAuthHeaders:
    def test_rewrites_token_prefix(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Token")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        result = server.prepare_auth_headers({"authorization": "Bearer abc123"})
        assert result == {"Authorization": "Token abc123"}

    def test_passes_through_when_no_prefix_configured(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        result = server.prepare_auth_headers({"authorization": "Bearer abc123"})
        assert result == {"Authorization": "Bearer abc123"}

    def test_single_token_value_passed_through(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Token")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        # No space -> not a two-part header, so it is forwarded verbatim.
        result = server.prepare_auth_headers({"authorization": "abc123"})
        assert result == {"Authorization": "abc123"}

    def test_uses_custom_auth_header_name(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Token")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "X-Api-Key")
        result = server.prepare_auth_headers({"authorization": "Bearer abc123"})
        assert result == {"X-Api-Key": "Token abc123"}

    def test_forwards_cookie_header(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "")
        result = server.prepare_auth_headers({"cookie": "session=xyz"})
        assert result == {"Cookie": "session=xyz"}

    def test_no_auth_or_cookie_returns_empty(self):
        assert server.prepare_auth_headers({}) == {}


# --------------------------------------------------------------------------- #
# generate_mcp_discovery_document
# --------------------------------------------------------------------------- #
def _spec_with_tools(
    tools_cache,
    version="9.9.9",
    cookie_auth=None,
    header_apikey_schemes=None,
    openapi_spec=None,
):
    """A lightweight stand-in for OpenAPISpec carrying a tools_cache."""
    return types.SimpleNamespace(
        tools_cache=tools_cache,
        version=version,
        cookie_auth=cookie_auth,
        header_apikey_schemes=header_apikey_schemes or {},
        openapi_spec=openapi_spec or {"components": {"securitySchemes": {}}, "security": []},
    )


@pytest.fixture
def base_env(monkeypatch):
    monkeypatch.setattr(server, "MCP_SERVER_NAME", "Test Server")
    monkeypatch.setattr(server, "API_BASE_URL", "http://api.test")
    monkeypatch.setattr(server, "OPENAPI_SPEC_URL", "http://api.test/openapi.json")
    monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")


class TestGenerateDiscoveryDocument:
    def test_bearer_prefix_uses_http_scheme(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        doc = server.generate_mcp_discovery_document(_spec_with_tools({}))
        scheme = doc["components"]["securitySchemes"]["apiToken"]
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"

    def test_non_bearer_prefix_uses_apikey_scheme(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Token")
        doc = server.generate_mcp_discovery_document(_spec_with_tools({}))
        scheme = doc["components"]["securitySchemes"]["apiToken"]
        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "header"
        assert scheme["name"] == "Authorization"

    def test_cookie_auth_advertised_when_spec_declares_it(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        cookie_auth = {"type": "apiKey", "in": "cookie", "name": "sessionid"}
        doc = server.generate_mcp_discovery_document(
            _spec_with_tools({}, cookie_auth=cookie_auth)
        )
        scheme = doc["components"]["securitySchemes"]["cookieAuth"]
        assert scheme["in"] == "cookie"
        assert scheme["name"] == "sessionid"
        assert "cookieAuth" in doc["transport"]["authentication"]["methods"]

    def test_cookie_auth_omitted_when_spec_has_none(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        doc = server.generate_mcp_discovery_document(_spec_with_tools({}))
        assert "cookieAuth" not in doc["components"]["securitySchemes"]
        assert "cookieAuth" not in doc["transport"]["authentication"]["methods"]

    def test_server_metadata_and_transport(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        doc = server.generate_mcp_discovery_document(_spec_with_tools({}, version="2.5.0"))
        assert doc["server"]["name"] == "Test Server"
        assert doc["server"]["version"] == "2.5.0"
        assert doc["mcpVersion"] == server.types.LATEST_PROTOCOL_VERSION
        assert doc["transport"]["baseUrl"] == "http://api.test/mcp"
        assert doc["transport"]["authentication"]["required"] is False

    def test_auth_tool_advertises_security(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        tools_cache = {
            "secure": {
                "name": "secure",
                "description": "secured",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": True,
            }
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        tool = doc["tools"][0]
        assert tool["_meta"]["requiresAuth"] is True
        assert tool["security"] == [{"apiToken": []}]

    def test_transport_auth_required_when_all_tools_need_auth(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        tools_cache = {
            "a": {"name": "a", "description": "", "inputSchema": {}, "requires_auth": True},
            "b": {"name": "b", "description": "", "inputSchema": {}, "requires_auth": True},
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        assert doc["transport"]["authentication"]["required"] is True

    def test_transport_auth_not_required_when_a_tool_is_keyless(self, base_env, monkeypatch):
        # e.g. a register_and_get_key bootstrap tool — clients must be able to
        # connect anonymously to reach it.
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        tools_cache = {
            "register_and_get_key": {"name": "register_and_get_key", "description": "", "inputSchema": {}, "requires_auth": False},
            "secure": {"name": "secure", "description": "", "inputSchema": {}, "requires_auth": True},
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        assert doc["transport"]["authentication"]["required"] is False

    def test_public_tool_omits_security(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        tools_cache = {
            "public": {
                "name": "public",
                "description": "open",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": False,
            }
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        tool = doc["tools"][0]
        assert tool["_meta"]["requiresAuth"] is False
        assert "security" not in tool

    def test_existing_responses_are_preserved(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        responses = {"200": {"description": "custom"}}
        tools_cache = {
            "t": {
                "name": "t",
                "description": "d",
                "inputSchema": {"type": "object", "properties": {}},
                "responses": responses,
                "requires_auth": False,
            }
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        assert doc["tools"][0]["responses"] == responses

    def test_default_responses_added_when_absent(self, base_env, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        tools_cache = {
            "t": {
                "name": "t",
                "description": "d",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": False,
            }
        }
        doc = server.generate_mcp_discovery_document(_spec_with_tools(tools_cache))
        responses = doc["tools"][0]["responses"]
        assert "200" in responses and "error" in responses

    def test_extra_header_apikey_scheme_from_spec_is_advertised(self, base_env, monkeypatch):
        """An X-API-Key-style scheme in the OpenAPI spec must appear in the
        discovery doc alongside the env-var-configured apiToken scheme."""
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        header_apikey_schemes = {
            "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key", "description": "API key"}
        }
        doc = server.generate_mcp_discovery_document(
            _spec_with_tools({}, header_apikey_schemes=header_apikey_schemes)
        )
        schemes = doc["components"]["securitySchemes"]
        assert "xApiKey" in schemes
        assert schemes["xApiKey"]["type"] == "apiKey"
        assert schemes["xApiKey"]["in"] == "header"
        assert schemes["xApiKey"]["name"] == "X-API-Key"
        assert "xApiKey" in doc["transport"]["authentication"]["methods"]

    def test_header_scheme_covered_by_auth_header_name_not_duplicated(self, base_env, monkeypatch):
        """If AUTH_HEADER_NAME already names the same header as an OpenAPI apiKey
        scheme, the scheme must not appear twice in the discovery doc."""
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Token")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "X-API-Key")
        header_apikey_schemes = {
            "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        doc = server.generate_mcp_discovery_document(
            _spec_with_tools({}, header_apikey_schemes=header_apikey_schemes)
        )
        schemes = doc["components"]["securitySchemes"]
        assert "apiToken" in schemes
        assert "xApiKey" not in schemes

    def test_per_tool_security_translated_from_openapi_scheme_names(self, base_env, monkeypatch):
        """Per-tool security must use discovery-doc names, not raw OpenAPI names.
        Also verifies that an apiKey/header/Authorization scheme (tokenAuth) is
        correctly mapped to apiToken even when apiToken is declared as http/bearer."""
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")

        raw_spec = {
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                    "tokenAuth": {"type": "apiKey", "in": "header", "name": "Authorization"},
                    "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                    "cookieAuth": {"type": "apiKey", "in": "cookie", "name": "sessionid"},
                }
            },
            "security": [],
        }
        header_apikey_schemes = {
            "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        cookie_auth = {"type": "apiKey", "in": "cookie", "name": "sessionid"}
        tools_cache = {
            "bearer_tool": {
                "name": "bearer_tool",
                "description": "needs bearer",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": True,
                "security": [{"bearerAuth": []}],
            },
            "token_tool": {
                "name": "token_tool",
                "description": "needs tokenAuth (apiKey/Authorization header)",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": True,
                "security": [{"tokenAuth": []}],
            },
            "apikey_tool": {
                "name": "apikey_tool",
                "description": "needs x-api-key",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": True,
                "security": [{"xApiKey": []}],
            },
            "multi_auth_tool": {
                "name": "multi_auth_tool",
                "description": "accepts cookie, tokenAuth, or apiKey",
                "inputSchema": {"type": "object", "properties": {}},
                "requires_auth": True,
                "security": [{"cookieAuth": []}, {"tokenAuth": []}, {"xApiKey": []}],
            },
        }
        doc = server.generate_mcp_discovery_document(
            _spec_with_tools(
                tools_cache,
                cookie_auth=cookie_auth,
                header_apikey_schemes=header_apikey_schemes,
                openapi_spec=raw_spec,
            )
        )
        tools_by_name = {t["name"]: t for t in doc["tools"]}
        assert tools_by_name["bearer_tool"]["security"] == [{"apiToken": []}]
        assert tools_by_name["token_tool"]["security"] == [{"apiToken": []}]
        assert tools_by_name["apikey_tool"]["security"] == [{"xApiKey": []}]
        assert tools_by_name["multi_auth_tool"]["security"] == [
            {"cookieAuth": []}, {"apiToken": []}, {"xApiKey": []}
        ]

    def test_security_requirements_derived_from_spec_global_security(self, base_env, monkeypatch):
        """When the OpenAPI spec declares global security, the discovery doc's
        top-level security must reflect it (translated to discovery names)."""
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")

        raw_spec = {
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                    "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                }
            },
            "security": [{"bearerAuth": []}, {"xApiKey": []}],
        }
        header_apikey_schemes = {
            "xApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        doc = server.generate_mcp_discovery_document(
            _spec_with_tools({}, header_apikey_schemes=header_apikey_schemes, openapi_spec=raw_spec)
        )
        assert {"apiToken": []} in doc["security"]
        assert {"xApiKey": []} in doc["security"]


# --------------------------------------------------------------------------- #
# prepare_auth_headers — extra apiKey header forwarding
# --------------------------------------------------------------------------- #
class TestPrepareAuthHeadersExtraHeaders:
    def test_forwards_extra_apikey_header(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        result = server.prepare_auth_headers(
            {"x-api-key": "my-key"},
            frozenset(["X-API-Key"]),
        )
        assert result == {"X-API-Key": "my-key"}

    def test_does_not_forward_unknown_headers(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "Bearer")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        result = server.prepare_auth_headers(
            {"x-custom": "value"},
            frozenset(["X-API-Key"]),
        )
        assert "x-custom" not in result
        assert "X-API-Key" not in result

    def test_extra_headers_combined_with_auth_and_cookie(self, monkeypatch):
        monkeypatch.setattr(server, "API_TOKEN_PREFIX", "")
        monkeypatch.setattr(server, "AUTH_HEADER_NAME", "Authorization")
        result = server.prepare_auth_headers(
            {"authorization": "Bearer tok", "cookie": "s=1", "x-api-key": "k"},
            frozenset(["X-API-Key"]),
        )
        assert result["Authorization"] == "Bearer tok"
        assert result["Cookie"] == "s=1"
        assert result["X-API-Key"] == "k"
