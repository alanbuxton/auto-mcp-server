# file: http_mcp_server_inspector_friendly.py
import os
import json
import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from util.shared import extract_tools_from_openapi
from util.log import logger
from util.vars import *
from starlette.requests import Request
from starlette.responses import JSONResponse

# --- Load OpenAPI Spec ---
def load_openapi_spec():
    global openapi_spec, tools_cache, raw_openapi_spec
    try:
        logger.info(f"Loading OpenAPI spec from {OPENAPI_SPEC_URL}...")
        resp = requests.get(OPENAPI_SPEC_URL, headers=AUTH_HEADER, timeout=10)
        resp.raise_for_status()
        raw_openapi_spec = resp.text
        openapi_spec = resp.json()
        tools_cache = extract_tools_from_openapi(openapi_spec)
        logger.info(f"Loaded OpenAPI spec and cached {len(tools_cache)} tools")
    except Exception as e:
        logger.error(f"Failed to load OpenAPI spec: {e}")
        openapi_spec = {}
        tools_cache = {}
        raw_openapi_spec = ""

# --- Create FastMCP server ---
mcp = FastMCP(SERVER_TITLE,port=MCP_SERVER_PORT)

# --- Register Resources ---
@mcp.resource(uri=OPENAPI_SPEC_URL)
def get_openapi_spec() -> str:
    """Return the OpenAPI specification"""
    if not raw_openapi_spec:
        load_openapi_spec()
    return raw_openapi_spec

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({"status": "healthy"})

@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def serve_mcp_manifest(request):
    """Serve the MCP manifest at /.well-known/mcp.json for web clients"""
    if not openapi_spec:
        logger.error("OpenAPI spec not loaded when serving manifest")
        from starlette.responses import JSONResponse
        return JSONResponse({"error": "OpenAPI spec not loaded"}, status_code=500)

    try:
        manifest = {
            "name": openapi_spec.get("info", {}).get("title", SERVER_TITLE),
            "version": openapi_spec.get("info", {}).get("version", "1.0"),
            "description": openapi_spec.get("info", {}).get("description", ""),
            "type": "streamable-http",
            "servers": openapi_spec.get("servers", []),
            "security": openapi_spec.get("security", []),
            "securitySchemes": openapi_spec.get("components", {}).get("securitySchemes", {}),
            "resources": [
                {
                    "uri": OPENAPI_SPEC_URL,
                    "name": "OpenAPI Specification",
                    "description": "The OpenAPI specification used to generate this MCP server's tools",
                    "mimeType": "application/json"
                }
            ],
            "tools": [
                {
                    "name": tool_name,
                    "description": tool_info["description"],
                    "path": tool_info["endpoint"],    
                    "method": tool_info["method"],     
                    "parameters": tool_info["inputSchema"],
                    "security": tool_info.get("security", openapi_spec.get("security", [])),
                    "responses": tool_info.get("responses", {}),
                } for tool_name, tool_info in tools_cache.items()
            ],
        }
        
        logger.info(f"Serving MCP manifest with {len(manifest['tools'])} tools")
        from starlette.responses import JSONResponse
        return JSONResponse(manifest)
        
    except Exception as e:
        logger.error(f"Error generating MCP manifest: {e}")
        from starlette.responses import JSONResponse
        return JSONResponse({"error": f"Error generating manifest: {str(e)}"}, status_code=500)

# --- Register Tools (dynamically from OpenAPI spec) ---
def register_api_tools():
    """Register tools dynamically from the OpenAPI spec"""
    if not tools_cache:
        load_openapi_spec()
    
    for tool_name, tool_info in tools_cache.items():
        def create_tool_handler(tool_data):
            def tool_handler(**arguments) -> list[TextContent]:
                try:
                    endpoint = tool_data["endpoint"]
                    params = arguments.copy()

                    # Replace path parameters
                    for key, value in list(params.items()):
                        placeholder = "{" + key + "}"
                        if placeholder in endpoint:
                            endpoint = endpoint.replace(placeholder, str(value))
                            del params[key]

                    url = API_BASE_URL.rstrip("/") + endpoint
                    headers = {"Content-Type": "application/json"} | AUTH_HEADER

                    logger.info(f"Making {tool_data['method']} request to {url}")

                    if tool_data["method"].upper() == "GET":
                        resp = requests.get(url, params=params, headers=headers, timeout=30)
                    elif tool_data["method"].upper() == "POST":
                        resp = requests.post(url, json=params, headers=headers, timeout=30)
                    else:
                        return [TextContent(type="text", text=f"Unsupported method: {tool_data['method']}")]

                    resp.raise_for_status()
                    
                    try:
                        result_json = resp.json()
                        success_msg = f"Successfully called {tool_name}. Response: {json.dumps(result_json, indent=2)}"
                        logger.info(f"Tool '{tool_name}' executed successfully")
                        return [TextContent(type="text", text=success_msg)]
                    except json.JSONDecodeError:
                        logger.info(f"Tool '{tool_name}' returned non-JSON response")
                        return [TextContent(type="text", text=f"Response from {tool_name}: {resp.text}")]

                except requests.exceptions.Timeout:
                    error_msg = f"Request to {tool_name} timed out after 30 seconds"
                    logger.error(error_msg)
                    return [TextContent(type="text", text=error_msg)]
                except requests.exceptions.RequestException as e:
                    error_msg = f"API request failed: {str(e)}"
                    logger.error(error_msg)
                    return [TextContent(type="text", text=error_msg)]
                except Exception as e:
                    error_msg = f"Error executing tool '{tool_name}': {str(e)}"
                    logger.error(error_msg)
                    return [TextContent(type="text", text=error_msg)]

            return tool_handler

        # Register the tool with FastMCP
        tool_handler = create_tool_handler(tool_info)
        mcp.tool(
            name=tool_name,
            description=tool_info["description"]
        )(tool_handler)

# --- Main ---
if __name__ == "__main__":    
    # Load OpenAPI spec and register tools
    load_openapi_spec()
    register_api_tools()
    
    logger.info(f"Starting MCP HTTP server")
    logger.info(f"Registered {len(tools_cache)} tools from OpenAPI spec")

    mcp.run(transport="streamable-http")

