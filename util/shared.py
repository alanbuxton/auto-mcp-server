from typing import Dict, Any
import os



def extract_tools_from_openapi(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Extract tools from OpenAPI spec"""
    tools = {}
    paths = spec.get("paths", {})
    for path, methods in paths.items():
        for method, operation in methods.items():
            method_upper = method.upper()
            method_lower = method.lower()
            if method_upper not in ("GET", "POST"):
                continue

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
