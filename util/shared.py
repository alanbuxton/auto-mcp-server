from typing import Dict, Any
import requests
from util.vars import OPENAPI_SPEC_URL, AUTH_HEADER, ALLOWED_TOOLS
from util.log import logger
from abc import ABC

class OpenAPISpec(ABC):
    def __init__(self):
        try:
            logger.info(f"Loading OpenAPI spec from {OPENAPI_SPEC_URL}...")
            resp = requests.get(OPENAPI_SPEC_URL, headers=AUTH_HEADER, timeout=10)
            resp.raise_for_status()
            self.raw_openapi_spec = resp.text
            self.openapi_spec = resp.json()
            self.version = self.openapi_spec.get("info", {}).get("version", "0.0.0")
            self.cookie_auth = extract_cookie_auth_scheme(self.openapi_spec)
            self.header_apikey_schemes = extract_header_apikey_schemes(self.openapi_spec)
            self.tools_cache = extract_tools_from_openapi(self.openapi_spec)
            logger.info(f"Loaded OpenAPI spec and cached {len(self.tools_cache)} tools")
        except Exception as e:
            logger.error(f"Failed to load OpenAPI spec: {e}")
            raise


def extract_cookie_auth_scheme(spec: Dict[str, Any]) -> Dict[str, Any] | None:
    """Return the first cookie-based security scheme declared by the OpenAPI
    spec (``type: apiKey``, ``in: cookie``), or ``None`` if the API declares no
    cookie auth. Lets the discovery doc advertise cookie auth only when the
    underlying API actually supports it, using the cookie name from the spec.
    """
    schemes = spec.get("components", {}).get("securitySchemes", {})
    for scheme in schemes.values():
        if scheme.get("type") == "apiKey" and scheme.get("in") == "cookie":
            return scheme
    return None


def extract_header_apikey_schemes(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return all header-based apiKey security schemes keyed by their OpenAPI
    scheme name (``type: apiKey``, ``in: header``).  Used to populate the
    discovery doc with any X-API-Key-style schemes the underlying API declares
    beyond what the AUTH_HEADER_NAME env-var already covers.
    """
    schemes = spec.get("components", {}).get("securitySchemes", {})
    return {
        name: scheme
        for name, scheme in schemes.items()
        if scheme.get("type") == "apiKey" and scheme.get("in") == "header"
    }


def resolve_schema_ref(spec: Dict[str, Any], ref: str) -> Dict[str, Any]:
    """Resolve a $ref to its actual schema definition"""
    if not ref.startswith("#/"):
        return {}
    
    path_parts = ref[2:].split("/")  # Remove "#/" prefix
    current = spec
    
    try:
        for part in path_parts:
            current = current[part]
        return current
    except (KeyError, TypeError):
        return {}


def process_schema_properties(spec: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Process schema properties, resolving $ref and extracting descriptions"""
    if "$ref" in schema:
        schema = resolve_schema_ref(spec, schema["$ref"])
    
    processed = {}
    
    # Handle basic type information
    if "type" in schema:
        processed["type"] = schema["type"]
    
    # Handle description
    if "description" in schema:
        processed["description"] = schema["description"]
    
    # Handle format (for things like date-time, email, uri)
    if "format" in schema:
        processed["format"] = schema["format"]
    
    # Handle enum values
    if "enum" in schema:
        processed["enum"] = schema["enum"]
    
    # Handle array items
    if schema.get("type") == "array" and "items" in schema:
        items_schema = schema["items"]
        if "$ref" in items_schema:
            items_schema = resolve_schema_ref(spec, items_schema["$ref"])
        processed["items"] = process_schema_properties(spec, items_schema)
    
    # Handle object properties
    if schema.get("type") == "object" and "properties" in schema:
        processed["properties"] = {}
        for prop_name, prop_schema in schema["properties"].items():
            processed["properties"][prop_name] = process_schema_properties(spec, prop_schema)
    
    # Handle additional constraints
    for constraint in ["minimum", "maximum", "minLength", "maxLength", "pattern"]:
        if constraint in schema:
            processed[constraint] = schema[constraint]
    
    return processed


def extract_response_info(spec: Dict[str, Any], operation: Dict[str, Any]) -> Dict[str, Any]:
    """Extract response information from operation"""
    responses = operation.get("responses", {})
    response_info = {}
    
    for status_code, response_data in responses.items():
        if status_code.startswith("2"):  # Success responses (2xx)
            content = response_data.get("content", {})
            description = response_data.get("description", "")
            
            response_info[status_code] = {
                "description": description
            }
            
            # Process JSON response schema
            if "application/json" in content:
                json_content = content["application/json"]
                if "schema" in json_content:
                    schema = json_content["schema"]
                    response_info[status_code]["schema"] = process_schema_properties(spec, schema)
    
    return response_info


def operation_requires_auth(spec: Dict[str, Any], operation: Dict[str, Any]) -> bool:
    """Determine whether an operation requires authentication.

    Per the OpenAPI spec, an operation-level ``security`` field overrides the
    top-level ``security`` field. An empty list (``security: []``) explicitly
    disables authentication for that scope. A non-empty list means at least one
    security requirement must be satisfied.
    """
    op_security = operation.get("security")
    if op_security is not None:
        # Operation-level security overrides the global default.
        return len(op_security) > 0

    # Fall back to the global security requirements.
    return len(spec.get("security", [])) > 0


def extract_tools_from_openapi(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Extract tools from OpenAPI spec with enhanced parameter and response documentation"""
    tools = {}
    paths = spec.get("paths", {})

    for path, methods in paths.items():
        for method, operation in methods.items():
            method_upper = method.upper()
            method_lower = method.lower()
            
            if method_upper not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue

            # Generate tool name
            name = operation.get("operationId") or f"{method_lower}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
            name = name.replace(" ", "_")

            if ALLOWED_TOOLS and name not in ALLOWED_TOOLS:
                logger.info(f"Ignoring {name} - not in {ALLOWED_TOOLS}")
                continue

            # Process parameters
            parameters = operation.get("parameters", [])
            props = {}
            required_params = []
            
            for param in parameters:
                pname = param.get("name")
                pschema = param.get("schema", {})
                
                # Process the parameter schema with full details
                param_info = process_schema_properties(spec, pschema)
                
                # Add parameter-specific information
                param_info["in"] = param.get("in")  # query, path, header, etc.
                
                # Use parameter description if available, otherwise schema description
                if "description" in param:
                    param_info["description"] = param["description"]
                
                props[pname] = param_info
                
                if param.get("required", False):
                    required_params.append(pname)

            # Add POST/PUT/PATCH requestBody schema props if JSON object
            if method_upper in ("POST", "PUT", "PATCH") and "requestBody" in operation:
                request_body = operation["requestBody"]
                content = request_body.get("content", {})
                json_schema = content.get("application/json", {}).get("schema", {})
                
                if json_schema.get("type") == "object":
                    body_props = json_schema.get("properties", {})
                    for pname, pschema in body_props.items():
                        if pname not in props:
                            processed_prop = process_schema_properties(spec, pschema)
                            props[pname] = processed_prop
                    
                    # Add required fields from request body
                    body_required = json_schema.get("required", [])
                    required_params.extend([req for req in body_required if req not in required_params])

            # Extract response information
            response_info = extract_response_info(spec, operation)

            # Build comprehensive tool description
            base_description = operation.get("description", "")
            summary = operation.get("summary", "")
            
            # Combine summary and description
            full_description = []
            if summary and summary != base_description:
                full_description.append(summary)
            if base_description:
                full_description.append(base_description)
            
            # Add parameter details to description if there are many parameters
            if len(props) > 3:  # Only add param list for complex endpoints
                param_descriptions = []
                for param_name, param_info in props.items():
                    param_desc = f"- {param_name}"
                    if param_info.get("description"):
                        param_desc += f": {param_info['description']}"
                    if param_name in required_params:
                        param_desc += " (required)"
                    param_descriptions.append(param_desc)
                
                if param_descriptions:
                    full_description.append("\nParameters:")
                    full_description.extend(param_descriptions)

            tool_info = {
                "name": name,
                "description": "\n".join(full_description) if full_description else f"{method_upper} {path}",
                "endpoint": path,
                "method": method_upper,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": required_params,
                },
                "responses": response_info,
                "requires_auth": operation_requires_auth(spec, operation),
            }

            # Add tags if available
            if "tags" in operation:
                tool_info["tags"] = operation["tags"]

            # Add security requirements if available
            if "security" in operation:
                tool_info["security"] = operation["security"]
            
            tools[name] = tool_info
    
    return tools
