from dotenv import load_dotenv
import os
from typing import Dict, Any

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
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "9000"))

# --- Global caches ---
openapi_spec: Dict[str, Any] = {}
tools_cache: Dict[str, Dict[str, Any]] = {}
raw_openapi_spec: str = ""

