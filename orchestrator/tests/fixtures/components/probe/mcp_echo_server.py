"""Minimal stdio MCP server with one ``raidar_echo`` tool (discovery-probe fixture)."""

import json
import sys

TOOLS = [
    {
        "name": "raidar_echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]


def respond(message_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}) + "\n")
    sys.stdout.flush()


def handle(message):
    method, message_id = message.get("method"), message.get("id")
    if message_id is None:
        return
    if method == "initialize":
        version = message.get("params", {}).get("protocolVersion", "2025-06-18")
        respond(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "raidar-echo", "version": "1"},
            },
        )
    elif method == "tools/list":
        respond(message_id, {"tools": TOOLS})
    elif method == "tools/call":
        text = message.get("params", {}).get("arguments", {}).get("text", "")
        respond(message_id, {"content": [{"type": "text", "text": text}]})
    else:
        respond(message_id, {})


for line in sys.stdin:
    if line.strip():
        handle(json.loads(line))
