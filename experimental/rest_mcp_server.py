import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional
import uvicorn
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import logging
from util.shared import extract_tools_from_openapi

load_dotenv()

logger = logging.getLogger("uvicorn.error")

API_BASE_URL = os.environ.get("API_BASE_URL","http://localhost:8000")
OPENAPI_JSON = os.environ.get("OPENAPI_JSON",".well-known/openapi.json")
OPENAPI_SPEC_URL = f"{API_BASE_URL}/{OPENAPI_JSON}"

MCP_SERVER_SCHEME = os.environ.get("MCP_SERVER_SCHEME", "http")
MCP_SERVER_PORT = int(os.environ.get("MCP_SERVER_PORT", "9000"))
MCP_SERVER_URL = f"{MCP_SERVER_SCHEME}://localhost:{MCP_SERVER_PORT}" 

SERVER_TITLE = os.environ.get("SERVER_TITLE", "My MCP Server")

openapi_spec: Dict[str, Any] = {}
tools_cache: Dict[str, Dict[str, Any]] = {}

class ToolRequest(BaseModel):
    params: Optional[Dict[str, Any]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global openapi_spec, tools_cache
    try:
        logger.info(f"Loading OpenAPI spec from {OPENAPI_SPEC_URL} ...")
        resp = requests.get(OPENAPI_SPEC_URL)
        resp.raise_for_status()
        openapi_spec = resp.json()
        tools_cache = extract_tools_from_openapi(openapi_spec)
        logger.info(f"Loaded OpenAPI spec and cached {len(tools_cache)} tools")
    except Exception as e:
        logger.info(f"Failed to load OpenAPI spec: {e}")
        openapi_spec = {}
        tools_cache = {}
    yield
    # No shutdown tasks

app = FastAPI(title=SERVER_TITLE, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/.well-known/mcp.json")
async def serve_mcp_manifest():
    if not openapi_spec:
        raise HTTPException(status_code=500, detail="OpenAPI spec not loaded")

    manifest = {
        "name": openapi_spec.get("info", {}).get("title", "Foo bar"),
        "version": openapi_spec.get("info", {}).get("version", "1.0"),
        "description": openapi_spec.get("info", {}).get("description", ""),
        "type": "rest",
        "servers": openapi_spec.get("servers", []),
        "security": openapi_spec.get("security", []),
        "securitySchemes": openapi_spec.get("components", {}).get("securitySchemes", {}),
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "path": t["endpoint"],    
                "method": t["method"],     
                "parameters": t["parameters_schema"],
                "security": get_operation_security(openapi_spec, t["endpoint"], t["method"]),
            } for t in tools_cache.values()
        ],
    }
    return JSONResponse(manifest)

def get_operation_security(spec: Dict[str, Any], path: str, method: str):
    """
    Extract the security requirement for a given path and method from the OpenAPI spec.
    Return list or empty list if none.
    """
    path_item = spec.get("paths", {}).get(path, {})
    operation = path_item.get(method.lower(), {})
    return operation.get("security", [])

@app.api_route("/tools/{tool_name}", methods=["GET", "POST"])
async def call_tool(tool_name: str, req: ToolRequest, request: Request):
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

    # Extract relevant auth headers from incoming request
    incoming_headers = {}
    # Forward Authorization header if present
    auth_header = request.headers.get("authorization")
    if auth_header:
        incoming_headers["Authorization"] = auth_header

    # Forward Cookie header if present
    cookie_header = request.headers.get("cookie")
    if cookie_header:
        incoming_headers["Cookie"] = cookie_header

    try:
        if tool["method"] == "GET":
            resp = requests.get(url, params=params, headers=incoming_headers)
        elif tool["method"] == "POST":
            resp = requests.post(url, json=params, headers=incoming_headers)
        else:
            raise HTTPException(status_code=405, detail="Unsupported method")

        resp.raise_for_status()
        return JSONResponse(content=resp.json())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")
    


if __name__ == "__main__":
    port = int(os.environ.get("MCP_SERVER_PORT", "9011"))
    uvicorn.run("rest_mcp_server:app", host="0.0.0.0", port=port, log_level="info")
