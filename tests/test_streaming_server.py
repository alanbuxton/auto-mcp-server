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
def _spec_with_tools(tools_cache, version="9.9.9", cookie_auth=None):
    """A lightweight stand-in for OpenAPISpec carrying a tools_cache."""
    return types.SimpleNamespace(
        tools_cache=tools_cache, version=version, cookie_auth=cookie_auth
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
