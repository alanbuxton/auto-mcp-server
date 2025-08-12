# auto-mcp-server
Generic MCP Server that auto-generates MCP from an OpenAPI spec.

It was made for https://github.com/alanbuxton/syracuse-neo but can be used for any server that has an OpenAPI spec.

See `.env.sample` for some config options.

1. Ensure backend API server is running with openapi.json available. By default we use `http://localhost/.well-known/openapi.json`
2. Run MCP server with `python mcp_auto_server.py`
3. In a different window, test that you can access the expected MCP server with `python test_client.py`. If API token is needed then provide it as env variable API_TOKEN. (See `.env.sample`)

