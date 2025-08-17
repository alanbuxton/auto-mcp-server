import requests
import sseclient
from urllib.parse import urljoin
import os
import re
from dotenv import load_dotenv

load_dotenv()

# Configuration
MCP_SERVER_SCHEME = os.environ.get("MCP_SERVER_SCHEME", "http")
MCP_SERVER_HOST = os.environ.get("MCP_SERVER_HOST", "localhost")
MCP_SERVER_PORT = os.environ.get("MCP_SERVER_PORT", "9000")
if MCP_SERVER_PORT is not None:
    port_string = f":{MCP_SERVER_PORT}"
else:
    port_string = ""
MCP_JSON_URL = f"{MCP_SERVER_SCHEME}://{MCP_SERVER_HOST}{port_string}/.well-known/mcp.json"

# Dummy payload for POST testing
DUMMY_PAYLOAD = {"test": True}

API_TOKEN = os.environ.get("API_TOKEN", "abc123")
API_TOKEN_PREFIX = os.environ.get("API_TOKEN_PREFIX", "Bearer")

# Example auth headers — replace with your real token or cookie values for testing
AUTH_HEADER = {"Authorization": f"{API_TOKEN_PREFIX} {API_TOKEN}".strip()} 

print(AUTH_HEADER)

# Dummy parameter values for replacement
DUMMY_PARAMS = {
    "id": "123",
    "uuid": "11111111-2222-3333-4444-555555555555",
    "slug": "test-slug",
}

def replace_path_params(path):
    """
    Replace {param} placeholders with dummy values from DUMMY_PARAMS.
    Falls back to '1' if not found.
    """
    def repl(match):
        param_name = match.group(1)
        return DUMMY_PARAMS.get(param_name, "1")
    return re.sub(r"{([^}]+)}", repl, path)

def load_mcp_json(url=MCP_JSON_URL):
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Failed to load MCP JSON from {url}: {e}")
        return {}

def test_get(url, headers=None):
    print(f"GET {url}")
    try:
        r = requests.get(url, headers=headers)
        print(f"  Status: {r.status_code}")
        try:
            print(f"  Response JSON: {r.json()}")
        except Exception:
            print(f"  Response Text: {r.text[:200]}...")
    except Exception as e:
        print(f"  GET request failed: {e}")

def test_post(url, headers=None):
    print(f"POST {url} with payload {DUMMY_PAYLOAD}")
    try:
        r = requests.post(url, json=DUMMY_PAYLOAD, headers=headers)
        print(f"  Status: {r.status_code}")
        try:
            print(f"  Response JSON: {r.json()}")
        except Exception:
            print(f"  Response Text: {r.text[:200]}...")
    except Exception as e:
        print(f"  POST request failed: {e}")

def test_sse(url):
    print(f"SSE {url}")
    try:
        messages = sseclient.SSEClient(url)
        for i, msg in enumerate(messages):
            print(f"  SSE Message: {msg.data}")
            if i >= 3:
                break
    except Exception as e:
        print(f"  SSE connection failed: {e}")

def test_openapi(openapi_url):
    print(f"Fetching OpenAPI spec from {openapi_url}")
    try:
        spec = requests.get(openapi_url).json()
    except Exception as e:
        print(f"  Failed to fetch OpenAPI spec: {e}")
        return

    base_url = spec.get("servers", [{}])[0].get("url", "").rstrip("/")
    paths = spec.get("paths", {})

    for path, methods in paths.items():
        replaced_path = replace_path_params(path)
        for method in methods.keys():
            url = urljoin(base_url + "/", replaced_path.lstrip("/"))
            if method.lower() == "get":
                test_get(url, headers=AUTH_HEADER)
            elif method.lower() == "post":
                test_post(url, headers=AUTH_HEADER)
            else:
                print(f"Skipping {method.upper()} {url} (not implemented)")

def main():
    mcp = load_mcp_json(MCP_JSON_URL)
    servers = mcp.get("servers", [])
    tools = mcp.get("tools", [])

    if tools:
        print("Testing endpoints from 'tools' in MCP JSON...")
        base_url = servers[0]["url"] if servers else ""
        for tool in tools:
            path = tool.get("path", tool.get("endpoint", ""))
            replaced_path = replace_path_params(path)
            url = urljoin(base_url + "/", replaced_path.lstrip("/"))
            method = tool.get("method", "GET").upper()
            if method == "GET":
                test_get(url, headers=AUTH_HEADER)
            elif method == "POST":
                test_post(url, headers=AUTH_HEADER)
            else:
                print(f"Skipping {method} {url} (unsupported method)")
    elif servers:
        for s in servers:
            if s.get("type") == "sse":
                test_sse(s["url"])
            elif s.get("type") == "openapi":
                openapi_url = urljoin(s["url"].rstrip("/") + "/", "schema/")
                test_openapi(openapi_url)
            else:
                print(f"Unknown server type {s.get('type')} at {s['url']}")
    else:
        print("No tools or servers found in MCP JSON.")

if __name__ == "__main__":
    main()
