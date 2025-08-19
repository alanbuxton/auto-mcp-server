# file: http_mcp_server_inspector_friendly.py
import os
import json
import logging
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool, TextContent, Resource
from typing import Dict, Any
from util.shared import extract_tools_from_openapi

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment ---
load_dotenv()
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
OPENAPI_JSON = os.getenv("OPENAPI_JSON", ".well-known/openapi.json")
OPENAPI_SPEC_URL = f"{API_BASE_URL}/{OPENAPI_JSON}"
API_TOKEN = os.getenv("API_TOKEN", "")
API_TOKEN_PREFIX = os.getenv("API_TOKEN_PREFIX", "")
AUTH_HEADER_NAME = os.getenv("AUTH_HEADER_NAME", "Authorization")
AUTH_HEADER = {AUTH_HEADER_NAME: f"{API_TOKEN_PREFIX} {API_TOKEN}".strip()}
SERVER_TITLE = os.getenv("SERVER_TITLE", "My MCP Server")

# --- Global caches ---
openapi_spec: Dict[str, Any] = {}
tools_cache: Dict[str, Dict[str, Any]] = {}
raw_openapi_spec: str = ""

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
mcp = FastMCP(SERVER_TITLE)

# --- Register Resources ---
@mcp.resource(uri=OPENAPI_SPEC_URL)
def get_openapi_spec() -> str:
    """Return the OpenAPI specification"""
    if not raw_openapi_spec:
        load_openapi_spec()
    return raw_openapi_spec

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
    
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8001"))
    
    logger.info(f"Starting MCP HTTP server")
    logger.info(f"Registered {len(tools_cache)} tools from OpenAPI spec")
    
    # FastMCP with streamable-http transport
    try:
        # Use streamable-http transport (the correct transport name)
        mcp.run(transport="streamable-http", host=host, port=port)
        logger.info(f"Connect to: http://{host}:{port}/mcp")
    except TypeError as e:
        logger.error(f"Error with transport parameters: {e}")
        # Fallback: use environment variables
        os.environ["FASTMCP_HOST"] = host
        os.environ["FASTMCP_PORT"] = str(port)
        logger.info(f"Set environment: FASTMCP_HOST={host}, FASTMCP_PORT={port}")
        logger.info(f"Connect to: http://{host}:{port}/mcp")
        mcp.run(transport="streamable-http")