import asyncio
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource, Prompt
import json
from util.shared import OpenAPISpec
from util.log import logger
from util.vars import MCP_SERVER_NAME, OPENAPI_SPEC_URL, AUTH_HEADER, API_BASE_URL


# Create MCP server
server = Server(MCP_SERVER_NAME)

openapi_spec = OpenAPISpec()
logger.info("Loaded openapi spec")

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return list of available tools"""
    tools = []
    for tool_info in openapi_spec.tools_cache.values():
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
        return openapi_spec.raw_openapi_spec
    else:
        error_msg = f"Unknown resource URI: {uri}"
        logger.error(error_msg)
        raise ValueError(error_msg)

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a tool call"""
    logger.info(f"Calling tool '{name}' with arguments: {arguments}")
    
    tool = openapi_spec.tools_cache.get(name)
    if not tool:
        error_msg = f"Tool '{name}' not found. Available tools: {list(openapi_spec.tools_cache.keys())}"
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
    
    logger.info("MCP server ready and waiting for connections...")
    
    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        logger.info("Connected to client via stdio")
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())