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
                       OPENAPI_SPEC_URL, MCP_SERVER_NAME, HTTP_MCP_SERVER_PORT)
from util.shared import OpenAPISpec
from util.log import logger

def prepare_auth_headers(headers: Dict):
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
    
    return new_headers

def generate_mcp_discovery_document(openapi_spec: OpenAPISpec) -> dict:
    """Generate the .well-known/mcp.json discovery document"""
    
    # Build security schemes from your authentication setup
    security_schemes = {}
    security_requirements = []
    
    if AUTH_HEADER_NAME:
        security_schemes["apiToken"] = {
            "type": "http",
            "scheme": "bearer",
            "description": f"API token authentication using {AUTH_HEADER_NAME} header with {API_TOKEN_PREFIX} prefix"
        }
        security_requirements.append({"apiToken": []})
    
    # Add cookie auth if you're handling cookies
    security_schemes["cookieAuth"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": "session",
        "description": "Cookie-based authentication"
    }
    
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
        
        # Add security requirements for this tool
        if security_requirements:
            tool_def["security"] = security_requirements
            
        enhanced_tools.append(tool_def)
    
    discovery_doc = {
        "mcpVersion": "2024-11-05",
        "server": {
            "name": MCP_SERVER_NAME,
            "version": "1.0.0",
            "description": f"MCP server exposing tools from OpenAPI specification"
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
    
    # Add transport information
    discovery_doc["transport"] = {
        "type": "http",
        "baseUrl": f"{API_BASE_URL}/mcp",
        "authentication": {
            "required": bool(security_requirements),
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
    app = Server(MCP_SERVER_NAME)

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
                inputSchema=tool_info["inputSchema"]
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
        headers = prepare_auth_headers(request.headers)

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