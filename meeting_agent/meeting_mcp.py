"""MCP requests shared by local agents. The caller binds tools to exactly one meeting."""

import json

from .agent_tools import LOCAL_TOOLS, execute, schema

# Streamable HTTP and the Claude SDK transport use the same tools capability.
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def meeting_mcp_reply(message, handler, *, tools=LOCAL_TOOLS):
    if not isinstance(message, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        }
    if "id" not in message:
        return None
    response = {"jsonrpc": "2.0", "id": message["id"]}
    method, params = message.get("method"), message.get("params", {})
    if message.get("jsonrpc") != "2.0" or not isinstance(params, dict):
        response["error"] = {"code": -32600, "message": "Invalid request"}
    elif method == "initialize":
        version = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": version if version in PROTOCOLS else PROTOCOLS[0],
            "serverInfo": {"name": "tingsu-meeting", "version": "1"},
            "capabilities": {"tools": {}},
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {
            "tools": (
                [
                    {"name": name, "description": desc, "inputSchema": schema(fields)}
                    for name, (desc, fields) in tools.items()
                ]
                if handler is not None
                else []
            )
        }
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in tools or handler is None:
            response["error"] = {"code": -32602, "message": "Unknown meeting tool"}
        else:
            output = execute(handler, name, params.get("arguments", {}), tools=tools)
            response["result"] = {
                "content": [{"type": "text", "text": output}],
                "isError": "error" in json.loads(output),
            }
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response
