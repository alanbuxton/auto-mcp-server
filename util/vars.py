from dotenv import load_dotenv
import os

# --- Environment ---
load_dotenv()
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
OPENAPI_JSON = os.getenv("OPENAPI_JSON", ".well-known/openapi.json")
OPENAPI_SPEC_URL = f"{API_BASE_URL}/{OPENAPI_JSON}"

MCP_SERVER_API_TOKEN = os.getenv("MCP_SERVER_API_TOKEN", "")
API_TOKEN_PREFIX = os.getenv("API_TOKEN_PREFIX", "")
AUTH_HEADER_NAME = os.getenv("AUTH_HEADER_NAME", "Authorization")
AUTH_HEADER = {AUTH_HEADER_NAME: f"{API_TOKEN_PREFIX} {MCP_SERVER_API_TOKEN}".strip()}

MCP_SERVER_NAME = os.getenv("SERVER_TITLE", "My MCP Server")
HTTP_MCP_SERVER_PORT = int(os.getenv("HTTP_MCP_SERVER_PORT", "9000"))
