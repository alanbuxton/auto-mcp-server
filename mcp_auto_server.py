import asyncio
import json
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional
import uvicorn
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL")
MCP_SPEC_URL = f"{API_BASE_URL}/.well-known/mcp.json"
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:9000")  # Change if running elsewhere
assert API_BASE_URL is not None, "Expected API_BASE_URL to be set - please check your env variables"

SERVER_TITLE = os.environ.get("SERVER_TITLE", "My MCP Server")

openapi_spec: Dict[str, Any] = {}
tools_cache: Dict[str, Dict[str, Any]] = {}

class ToolRequest(BaseModel):
    params: Optional[Dict[str, Any]] = {}

def extract_tools_from_openapi(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Scan the OpenAPI spec paths and generate MCP tool info for GET and POST endpoints.
    """
    tools = {}
    paths = spec.get("paths", {})
    for path, methods in paths.items():
        for method, operation in methods.items():
            method_upper = method.upper()
            method_lower = method.lower()
            if method_upper not in ("GET", "POST"):
                continue  # Only GET and POST for now

            name = operation.get("operationId") or f"{method_lower}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
            name = name.replace(" ", "_")

            parameters = operation.get("parameters", [])
            props = {}
            required_params = []
            for param in parameters:
                pname = param.get("name")
                pschema = param.get("schema", {})
                ptype = pschema.get("type", "string")
                props[pname] = {"type": ptype}
                if param.get("required", False):
                    required_params.append(pname)

            # Add POST requestBody schema props if JSON object
            if method_upper == "POST" and "requestBody" in operation:
                content = operation["requestBody"].get("content", {})
                json_schema = content.get("application/json", {}).get("schema", {})
                if json_schema.get("type") == "object":
                    body_props = json_schema.get("properties", {})
                    for pname, pschema in body_props.items():
                        if pname not in props:
                            props[pname] = {"type": pschema.get("type", "string")}
                    required_params += json_schema.get("required", [])

            tool_info = {
                "name": name,
                "description": operation.get("description", ""),
                "endpoint": path,
                "method": method_upper,
                "parameters_schema": {
                    "type": "object",
                    "properties": props,
                    "required": required_params,
                },
            }
            tools[name] = tool_info
    return tools

@asynccontextmanager
async def lifespan(app: FastAPI):
    global openapi_spec, tools_cache
    try:
        print(f"Loading OpenAPI spec from {MCP_SPEC_URL} ...")
        resp = requests.get(MCP_SPEC_URL)
        resp.raise_for_status()
        openapi_spec = resp.json()
        tools_cache = extract_tools_from_openapi(openapi_spec)
        print(f"Loaded OpenAPI spec and cached {len(tools_cache)} tools")
    except Exception as e:
        print(f"Failed to load OpenAPI spec: {e}")
        openapi_spec = {}
        tools_cache = {}
    yield
    # No shutdown tasks

app = FastAPI(title=f"{SERVER_TITLE} MCP Server", lifespan=lifespan)

@app.get("/.well-known/mcp.json")
async def serve_mcp_manifest():
    if not openapi_spec:
        raise HTTPException(status_code=500, detail="OpenAPI spec not loaded")

    manifest = {
        "name": f"{SERVER_TITLE} API MCP",
        "version": openapi_spec.get("info", {}).get("version", "1.0"),
        "type": "sse",
        "endpoints": [
            {"url": f"{MCP_SERVER_URL}/sse"}
        ],
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters_schema"],
            } for t in tools_cache.values()
        ],
    }
    return JSONResponse(manifest)

@app.post("/tools/{tool_name}")
async def call_tool(tool_name: str, req: ToolRequest):
    if not openapi_spec:
        raise HTTPException(status_code=500, detail="OpenAPI spec not loaded")

    tool = tools_cache.get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    endpoint = tool["endpoint"]
    params = req.params or {}

    # Replace path params in URL (e.g. {id})
    for key, value in list(params.items()):
        placeholder = "{" + key + "}"
        if placeholder in endpoint:
            endpoint = endpoint.replace(placeholder, str(value))
            del params[key]

    url = API_BASE_URL.rstrip("/") + endpoint

    try:
        if tool["method"] == "GET":
            resp = requests.get(url, params=params)
        elif tool["method"] == "POST":
            resp = requests.post(url, json=params)
        else:
            raise HTTPException(status_code=405, detail="Unsupported method")

        resp.raise_for_status()
        return JSONResponse(content=resp.json())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

@app.get("/sse")
async def sse_endpoint():
    async def event_generator():
        while True:
            data = {"type": "ping", "payload": "keep-alive"}
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run("mcp_auto_server:app", host="0.0.0.0", port=9000, log_level="info")
