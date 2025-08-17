# auto-mcp-server

So, you've got a web app with a REST API. 

Maybe it's a Django app and you've generated an OpenAPI schema using `drf-spectacular`. 

Now you want to let an LLM talk to it.

If so then, welcome, you've come to the right place. 

`auto-mcp-server` is an MCP server that auto-generates MCP tools based on an OpenAPI and can be called directly from the likes of Claude Desktop.

It was originally made for https://syracuse.1145.am but can be used for any server that has an OpenAPI spec.

See `.env.sample` for config options.

## stdio_mcp_server

Claude Desktop expects MCP servers to run locally. Here's how you configure this

Pre-requisites:
1. `uv` is installed (see https://docs.astral.sh/uv/getting-started/installation/)
2. The backend API server is running with an OpenAPI json schema available. Configure where to find this with a combination of API_BASE_URL and OPENAPI_JSON

Claude configuration, assuming you only have one MCP server defined:

```
{
  "mcpServers": {
    "Your app name": {
      "command": "/path/to/uv",
      "args": [
        "--directory",
        "/path/to/auto-mcp-server",
        "run",
        "stdio_mcp_server.py"
      ]
    }
  }
}
```


## experimental files

This is intended as a remote MCP server but is currently in progress until it can be validated properly.