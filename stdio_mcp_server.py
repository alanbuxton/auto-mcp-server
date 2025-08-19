import asyncio
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource, Prompt
from typing import Dict, Any
import json
import os
from dotenv import load_dotenv
import logging
from util.shared import extract_tools_from_openapi

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
OPENAPI_JSON = os.environ.get("OPENAPI_JSON", ".well-known/openapi.json")
OPENAPI_SPEC_URL = f"{API_BASE_URL}/{OPENAPI_JSON}"
SERVER_TITLE = os.environ.get("SERVER_TITLE", "My MCP Server")


# Authentication configuration
API_TOKEN = os.environ.get("API_TOKEN")  
API_TOKEN_PREFIX = os.environ.get("API_TOKEN_PREFIX","")
AUTH_HEADER_NAME = os.environ.get("AUTH_HEADER_NAME", "Authorization")  # Header name
AUTH_HEADER = {AUTH_HEADER_NAME: f"{API_TOKEN_PREFIX} {API_TOKEN}".strip()}

# Global cache
openapi_spec: Dict[str, Any] = {}
tools_cache: Dict[str, Dict[str, Any]] = {}
raw_openapi_spec: str = ""


async def load_openapi_spec():
    """Load OpenAPI spec at startup"""
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

# Create MCP server
server = Server(SERVER_TITLE)

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return list of available tools"""
    if not tools_cache:
        await load_openapi_spec()

    tools = []
    for tool_info in tools_cache.values():
        tool_name = tool_info["name"]
        logger.info(tool_name)
        tools.append(Tool(
            name=tool_name,
            description=tool_info["description"],
            inputSchema=tool_info["inputSchema"]
        ))
        logger.info(f"Added {tool_name}")
    
    logger.info(f"Listed {len(tools)} tools")
    return tools

@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Return list of available prompts"""
    # If your server doesn't provide prompts, return empty list
    # You can add prompts here if you want to provide reusable prompt templates
    return []

@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri=OPENAPI_SPEC_URL,
            name="API Schema",
            description="OpenAPI specification for available endpoints",
            mimeType="application/json"
        ),
        # Static documentation, public configs, etc.
    ]

@server.read_resource()
async def read_resource(uri: str) -> str:
    """Read and return resource content"""
    logger.info(f"Reading resource: {uri}")
    
    if str(uri) == OPENAPI_SPEC_URL:
        if not raw_openapi_spec:
            await load_openapi_spec()
        return raw_openapi_spec
        # try:
        #     # Fetch the OpenAPI spec
        #     resp = requests.get(OPENAPI_SPEC_URL, headers=AUTH_HEADER, timeout=10)
        #     resp.raise_for_status()
            
        #     # Return the raw JSON text
        #     return resp.text
            
        # except requests.exceptions.RequestException as e:
        #     error_msg = f"Failed to fetch OpenAPI spec: {str(e)}"
        #     logger.error(error_msg)
        #     raise RuntimeError(error_msg)
    else:
        error_msg = f"Unknown resource URI: {uri}"
        logger.error(error_msg)
        raise ValueError(error_msg)

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a tool call"""
    logger.info(f"Calling tool '{name}' with arguments: {arguments}")
    
    if not tools_cache:
        await load_openapi_spec()
    
    tool = tools_cache.get(name)
    if not tool:
        error_msg = f"Tool '{name}' not found. Available tools: {list(tools_cache.keys())}"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]

    try:
        endpoint = tool["endpoint"]
        params = arguments.copy()

        # Replace path params in URL
        for key, value in list(params.items()):
            placeholder = "{" + key + "}"
            if placeholder in endpoint:
                endpoint = endpoint.replace(placeholder, str(value))
                del params[key]

        url = API_BASE_URL.rstrip("/") + endpoint
        logger.info(f"Making {tool['method']} request to {url}")

        # Prepare headers with authentication
        headers = {'Content-Type': 'application/json'} | AUTH_HEADER
        # Make the API call
        if tool["method"] == "GET":
            resp = requests.get(url, params=params, headers=headers, timeout=30)
        elif tool["method"] == "POST":
            resp = requests.post(url, json=params, headers=headers, timeout=30)
        else:
            error_msg = f"Unsupported method: {tool['method']}"
            logger.error(error_msg)
            return [TextContent(type="text", text=error_msg)]

        resp.raise_for_status()
        
        # Return the response
        try:
            result = resp.json()
            success_msg = f"Successfully called {name}. Response: {json.dumps(result, indent=2)}"
            logger.info(f"Tool '{name}' executed successfully")
            return [TextContent(type="text", text=success_msg)]
        except json.JSONDecodeError:
            logger.info(f"Tool '{name}' returned non-JSON response")
            return [TextContent(type="text", text=f"Response from {name}: {resp.text}")]
            
    except requests.exceptions.Timeout:
        error_msg = f"Request to {name} timed out after 30 seconds"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]
    except Exception as e:
        error_msg = f"Error executing tool '{name}': {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]

async def main():
    """Main entry point"""
    logger.info("Starting MCP server...")
    
    # Load spec at startup
    await load_openapi_spec()
    
    logger.info("MCP server ready and waiting for connections...")
    
    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        logger.info("Connected to Claude Desktop via stdio")
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())