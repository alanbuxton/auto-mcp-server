# Based on https://github.com/modelcontextprotocol/python-sdk/blob/09e3a05e13211d1081efcdc9a962affb02e40c05/examples/servers/simple-streamablehttp-stateless/mcp_simple_streamablehttp_stateless/server.py

import contextlib
from collections.abc import AsyncIterator
from typing import Any, Dict

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send
from starlette.requests import Request
import requests 
import json
import uvicorn

from util.vars import (API_BASE_URL, API_TOKEN_PREFIX, AUTH_HEADER_NAME,
                       OPENAPI_SPEC_URL, MCP_SERVER_NAME, HTTP_MCP_SERVER_PORT,
                       MCP_SERVER_DESCRIPTION)
from util.shared import OpenAPISpec
from util.log import logger

def prepare_auth_headers(headers: Dict, extra_apikey_header_names: frozenset = frozenset()) -> Dict:
    new_headers = {}
    # Forward Authorization header if present. Change the
    auth_header = headers.get("authorization")
    if auth_header:
        vals = auth_header.strip().split(" ")
        if len(vals) == 2 and API_TOKEN_PREFIX:
            # perhaps need to change 'Bearer' to another term
            api_val = f"{API_TOKEN_PREFIX} {vals[1]}"
        else:
            api_val = auth_header
        new_headers[AUTH_HEADER_NAME] = api_val

    # Forward Cookie header if present
    cookie_header = headers.get("cookie")
    if cookie_header:
        new_headers["Cookie"] = cookie_header

    # Forward any extra apiKey-in-header headers the spec advertises that are
    # not already covered by the Authorization→AUTH_HEADER_NAME translation.
    for name in extra_apikey_header_names:
        value = headers.get(name.lower())
        if value:
            new_headers[name] = value

    return new_headers

def _find_discovery_name(raw_scheme: dict, security_schemes: dict) -> str | None:
    """Return the discovery-doc scheme name that corresponds to a raw OpenAPI
    security scheme, or None if no match is found.

    The tricky case: the OpenAPI spec may declare the Authorization-header
    scheme as either ``type: http, scheme: bearer`` *or*
    ``type: apiKey, in: header, name: Authorization``.  The discovery doc
    always emits ``apiToken`` for whatever header AUTH_HEADER_NAME names, so
    both OpenAPI representations must map to ``apiToken``.
    """
    scheme_type = raw_scheme.get("type", "")
    scheme_in = raw_scheme.get("in", "")
    scheme_name = raw_scheme.get("name", "").lower()

    # http/bearer → the canonical Authorization-header token entry
    if scheme_type == "http":
        if "apiToken" in security_schemes:
            return "apiToken"

    if scheme_type == "apiKey":
        if scheme_in == "header":
            # apiKey-in-header using the same header as AUTH_HEADER_NAME
            # (e.g. tokenAuth: apiKey/header/Authorization) → apiToken
            if scheme_name == AUTH_HEADER_NAME.lower() and "apiToken" in security_schemes:
                return "apiToken"
            # Other named header schemes: match by header name
            for disc_name, disc_scheme in security_schemes.items():
                if (disc_scheme.get("type") == "apiKey"
                        and disc_scheme.get("in") == "header"
                        and disc_scheme.get("name", "").lower() == scheme_name):
                    return disc_name
        elif scheme_in == "cookie":
            if "cookieAuth" in security_schemes:
                return "cookieAuth"

    return None


def generate_mcp_discovery_document(openapi_spec: OpenAPISpec) -> dict:
    """Generate the .well-known/mcp.json discovery document"""

    security_schemes = {}
    # Maps OpenAPI spec scheme names → discovery-doc scheme names so that
    # per-operation security requirements can be translated faithfully.
    openapi_to_discovery: dict[str, str] = {}

    if AUTH_HEADER_NAME:
        description = (
            f"API token authentication using {AUTH_HEADER_NAME} header"
            + (f" with {API_TOKEN_PREFIX} prefix" if API_TOKEN_PREFIX else "")
        )
        # OpenAPI's "http" type requires a registered HTTP auth scheme
        # (RFC 7235). "Bearer" qualifies; any other prefix does not, so fall
        # back to a header-based apiKey scheme.
        if API_TOKEN_PREFIX.lower() == "bearer":
            security_schemes["apiToken"] = {
                "type": "http",
                "scheme": "bearer",
                "description": description,
            }
        else:
            security_schemes["apiToken"] = {
                "type": "apiKey",
                "in": "header",
                "name": AUTH_HEADER_NAME,
                "description": description,
            }

    # Add header-based apiKey schemes declared in the OpenAPI spec that are
    # not already covered by the AUTH_HEADER_NAME env-var configuration.
    covered_headers = {AUTH_HEADER_NAME.lower()} if AUTH_HEADER_NAME else set()
    for scheme_name, scheme in openapi_spec.header_apikey_schemes.items():
        header_name = scheme.get("name", "")
        if header_name.lower() in covered_headers:
            continue
        security_schemes[scheme_name] = {
            "type": "apiKey",
            "in": "header",
            "name": header_name,
            "description": scheme.get("description", f"API key via {header_name} header"),
        }
        covered_headers.add(header_name.lower())

    # Advertise cookie auth only if the OpenAPI spec declares a cookie-based
    # scheme; the cookie name is taken from the spec rather than hard-coded.
    if openapi_spec.cookie_auth:
        security_schemes["cookieAuth"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": openapi_spec.cookie_auth.get("name", "session"),
            "description": "Cookie-based authentication"
        }

    # Build a mapping from raw OpenAPI scheme names to discovery-doc names so
    # that per-operation security requirements can be translated faithfully.
    raw_schemes = openapi_spec.openapi_spec.get("components", {}).get("securitySchemes", {})
    for raw_name, raw_scheme in raw_schemes.items():
        disc_name = _find_discovery_name(raw_scheme, security_schemes)
        if disc_name:
            openapi_to_discovery[raw_name] = disc_name

    # Derive top-level security requirements from the spec's global security
    # field (translated to discovery names). Fall back to requiring every
    # advertised scheme when the spec has no global security declaration.
    raw_top_security = openapi_spec.openapi_spec.get("security", [])
    if raw_top_security:
        security_requirements = [
            {openapi_to_discovery[k]: v for k, v in req.items() if k in openapi_to_discovery}
            for req in raw_top_security
        ]
        security_requirements = [r for r in security_requirements if r]
    else:
        security_requirements = [{name: []} for name in security_schemes]

    # Build enhanced tools with response schemas
    enhanced_tools = []
    for tool_info in openapi_spec.tools_cache.values():
        tool_def = {
            "name": tool_info["name"],
            "description": tool_info["description"],
            "inputSchema": tool_info["inputSchema"]
        }

        # Add response schema if available from OpenAPI spec
        if "responses" in tool_info:
            tool_def["responses"] = tool_info["responses"]
        else:
            # Default response schema based on your implementation
            tool_def["responses"] = {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "API response data"
                            }
                        },
                        "text/plain": {
                            "schema": {
                                "type": "string",
                                "description": "Plain text response"
                            }
                        }
                    }
                },
                "error": {
                    "description": "Error response",
                    "content": {
                        "text/plain": {
                            "schema": {
                                "type": "string",
                                "description": "Error message"
                            }
                        }
                    }
                }
            }

        # Expose whether this tool requires authentication so clients can
        # distinguish auth from no-auth tools.
        requires_auth = tool_info.get("requires_auth", False)
        tool_def["_meta"] = {"requiresAuth": requires_auth}

        # Only advertise security requirements for tools that actually need them.
        # Translate the tool's raw OpenAPI security to discovery-doc scheme names;
        # fall back to the top-level requirements when the tool has none.
        if requires_auth and security_requirements:
            raw_tool_security = tool_info.get("security")
            if raw_tool_security is not None:
                tool_security = [
                    {openapi_to_discovery[k]: v for k, v in req.items() if k in openapi_to_discovery}
                    for req in raw_tool_security
                ]
                tool_security = [r for r in tool_security if r]
                tool_def["security"] = tool_security or security_requirements
            else:
                tool_def["security"] = security_requirements

        enhanced_tools.append(tool_def)
    
    discovery_doc = {
        "mcpVersion": types.LATEST_PROTOCOL_VERSION,
        "server": {
            "name": MCP_SERVER_NAME,
            "version": openapi_spec.version,
            "description": MCP_SERVER_DESCRIPTION,
        },
        "capabilities": {
            "tools": {
                "listChanged": True
            },
            "resources": {
                "subscribe": False,
                "listChanged": False
            }
        },
        "tools": enhanced_tools,
        "resources": [
            {
                "uri": OPENAPI_SPEC_URL,
                "name": "API Schema", 
                "description": "OpenAPI specification for available endpoints",
                "mimeType": "application/json"
            }
        ]
    }
    
    # Add security information
    if security_schemes:
        discovery_doc["components"] = {
            "securitySchemes": security_schemes
        }
        discovery_doc["security"] = security_requirements
    
    # Auth is required to connect only when there is no anonymous entry point —
    # i.e. every tool requires auth. If any tool is keyless , clients must be able to connect
    # anonymously to reach it, so strict clients honoring required:true would
    # otherwise refuse to connect without a key. Per-tool security is still
    # advertised above for the tools that need it.
    tools = openapi_spec.tools_cache.values()
    auth_required = bool(tools) and all(
        t.get("requires_auth", False) for t in tools
    )

    # Add transport information
    discovery_doc["transport"] = {
        "type": "http",
        "baseUrl": f"{API_BASE_URL}/mcp",
        "authentication": {
            "required": auth_required,
            "methods": list(security_schemes.keys()) if security_schemes else []
        }
    }
    
    return discovery_doc

async def well_known_mcp_handler(request: Request) -> JSONResponse:
    """Handle requests to /.well-known/mcp.json"""
    # You'll need to pass the openapi_spec to this handler
    # One way is to store it in the app state
    openapi_spec = request.app.state.openapi_spec
    discovery_doc = generate_mcp_discovery_document(openapi_spec)
    return JSONResponse(content=discovery_doc)

def main(
    openapi_spec: OpenAPISpec,
    port: int
) -> int:
    app = Server(MCP_SERVER_NAME, version=openapi_spec.version)

    @app.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=OPENAPI_SPEC_URL,
                name="API Schema",
                description="OpenAPI specification for available endpoints",
                mimeType="application/json"
        )
    ]

    @app.read_resource()
    async def read_resource(uri: str) -> str:
        logger.info(f"Reading resource: {uri}")
        if str(uri) == OPENAPI_SPEC_URL:
            return openapi_spec.raw_openapi_spec
        raise ValueError(f"Unknown resource URI: {uri}")
    
    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=tool_info["name"],
                description=tool_info["description"],
                inputSchema=tool_info["inputSchema"],
                _meta={"requiresAuth": tool_info.get("requires_auth", False)},
            )
            for tool_info in openapi_spec.tools_cache.values()
        ]

    @app.call_tool()
    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> list[types.ContentBlock]:
        tool_data = openapi_spec.tools_cache[tool_name]

        endpoint = tool_data["endpoint"]
        params = arguments.copy()

        # Replace path params in URL (e.g. {id})
        for key, value in list(params.items()):
            placeholder = "{" + key + "}"
            if placeholder in endpoint:
                endpoint = endpoint.replace(placeholder, str(value))
                del params[key]

        url = API_BASE_URL.rstrip("/") + endpoint

        request = app.request_context.request
        extra_apikey_header_names = frozenset(
            s.get("name", "")
            for s in openapi_spec.header_apikey_schemes.values()
            if s.get("name", "").lower() != AUTH_HEADER_NAME.lower()
        )
        headers = prepare_auth_headers(request.headers, extra_apikey_header_names)

        logger.info(f"Making {tool_data['method']} request to {url}")

        try:
            if tool_data["method"].upper() == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=30)
            elif tool_data["method"].upper() == "POST":
                resp = requests.post(url, json=params, headers=headers, timeout=30)
            else:
                return [types.TextContent(type="text", text=f"Unsupported method: {tool_data['method']}")]

            resp.raise_for_status()
            try:
                result_json = resp.json()
                success_msg = f"Successfully called {tool_name}. Response: {json.dumps(result_json, indent=2)}"
                logger.info(f"Tool '{tool_name}' executed successfully")
                return [types.TextContent(type="text", text=success_msg)]
            except json.JSONDecodeError:
                logger.info(f"Tool '{tool_name}' returned non-JSON response")
                return [types.TextContent(type="text", text=f"Response from {tool_name}: {resp.text}")]

        except requests.exceptions.Timeout:
            error_msg = f"Request to {tool_name} timed out after 30 seconds"
            logger.error(error_msg)
            return [types.TextContent(type="text", text=error_msg)]
        except requests.exceptions.RequestException as e:
            error_msg = f"API request failed: {str(e)}"
            logger.error(error_msg)
            return [types.TextContent(type="text", text=error_msg)]
        except Exception as e:
            error_msg = f"Error executing tool '{tool_name}': {str(e)}"
            logger.error(error_msg)
            return [types.TextContent(type="text", text=error_msg)]    

    
    @app.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return []

    @app.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        return []

    # Create the session manager with true stateless mode
    session_manager = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
#        json_response=json_response, # Leaving default for this example
        stateless=True,
    )

    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for session manager."""
        async with session_manager.run():
            logger.info("Application started with StreamableHTTP session manager!")
            try:
                yield
            finally:
                logger.info("Application shutting down...")

    # Create an ASGI application using the transport
    starlette_app = Starlette(
        debug=True,
        routes=[
            Route("/.well-known/mcp.json", well_known_mcp_handler, methods=["GET"]),
            Mount("/mcp", app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )

    # Store the openapi_spec in app state so the discovery handler can access it
    starlette_app.state.openapi_spec = openapi_spec

    # Wrap ASGI application with CORS middleware to expose Mcp-Session-Id header
    # for browser-based clients (ensures 500 errors get proper CORS headers)
    starlette_app = CORSMiddleware(
        starlette_app,
        allow_origins=["*"],  # Allow all origins - adjust as needed for production
        allow_methods=["GET", "POST", "DELETE"],  # MCP streamable HTTP methods
        expose_headers=["Mcp-Session-Id"],
    )

    uvicorn.run(starlette_app, host="127.0.0.1", port=port)

    return 0

if __name__ == '__main__':
    main(openapi_spec=OpenAPISpec(),port=HTTP_MCP_SERVER_PORT)