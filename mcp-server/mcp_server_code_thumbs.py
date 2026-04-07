#!/usr/bin/env python3
"""
============================================================================
Copyright 2026 real-wimpSquad

SPDX-License-Identifier: MIT
See LICENSE file for full license text.
============================================================================

MCP Server for Code Quality Tools (code_thumbs)
STDIO Transport - exposes formatting, linting, and fixing as MCP tools
"""

import os
import httpx
from typing import Any
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

# Configuration from environment
CODE_THUMBS_URL = os.environ.get("CODE_THUMBS_API_URL", "http://localhost:8072")

# MCP Server
server = Server("code-quality-tools")


async def call_api(endpoint: str, method: str = "POST", json_data: dict = None) -> dict:
    """Call Code Thumbs API"""
    headers = {"Content-Type": "application/json"}
    url = f"{CODE_THUMBS_URL}{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.post(url, headers=headers, json=json_data)

        response.raise_for_status()
        return response.json()


# ============================================================================
# MCP Tool Definitions
# ============================================================================


@server.list_tools()
async def handle_list_tools() -> list[Any]:
    """List available code quality tools"""
    return [
        {
            "name": "format_code",
            "description": "Format code. Returns: tool:X|changed:yes|no + formatted_content OR fmt_check:clean|needs_fmt|tool:X",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "csharp", "rust", "go", "java", "c", "cpp", "shell", "sql", "php", "kotlin", "swift", "ruby", "markdown", "yaml"],
                        "description": "Programming language",
                    },
                    "content": {"type": "string", "description": "Source code to format"},
                    "tool": {
                        "type": "string",
                        "description": "Specific formatter (optional - see /languages endpoint for full list)",
                    },
                    "check_only": {
                        "type": "boolean",
                        "description": "Only check if formatting needed without modifying (default: false)",
                        "default": False,
                    },
                },
                "required": ["language", "content"],
            },
        },
        {
            "name": "lint_code",
            "description": "Lint code. Returns: tool:X|err:N|warn:N|info:N\\nL123:e|w|i[f]:CODE:msg OR clean|tool:X",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "csharp", "rust", "go", "java", "c", "cpp", "shell", "sql", "php", "kotlin", "swift", "ruby", "markdown", "yaml"],
                        "description": "Programming language",
                    },
                    "content": {"type": "string", "description": "Source code to lint"},
                    "tool": {
                        "type": "string",
                        "description": "Specific linter (optional - see /languages endpoint for full list)",
                    },
                },
                "required": ["language", "content"],
            },
        },
        {
            "name": "fix_code",
            "description": "Auto-fix issues. Returns: tool:X|fixed:yes|no|remaining:N[|issues:L123:msg]+fixed_content",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "go", "sql", "php", "kotlin", "ruby", "markdown"],
                        "description": "Programming language (fix only supported for these languages)",
                    },
                    "content": {"type": "string", "description": "Source code to fix"},
                    "tool": {
                        "type": "string",
                        "description": "Specific fixing tool (optional - see /languages endpoint for full list)",
                    },
                },
                "required": ["language", "content"],
            },
        },
        {
            "name": "check_code",
            "description": "Format+lint check. Returns: fmt:clean|needs_fmt|lint:clean|err:N+warn:N[\\nL123:e|w:msg] OR clean:fmt+lint",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript", "csharp", "rust", "go", "java", "c", "cpp", "shell", "sql", "php", "kotlin", "swift", "ruby", "markdown", "yaml"],
                        "description": "Programming language",
                    },
                    "content": {"type": "string", "description": "Source code to check"},
                },
                "required": ["language", "content"],
            },
        },
        {
            "name": "list_languages",
            "description": "List languages. Returns: lang:.ext|fmt:tool1+tool2|lint:tool1+tool2|fix:tool|none (one per line)",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[Any]:
    """Handle tool execution - pass through ml-exclusive output from API"""
    try:
        # API endpoints that return compressed format
        if name == "format_code":
            result = await call_api("/format", "POST", arguments)
            return [{"type": "text", "text": result["result"]}]

        elif name == "lint_code":
            result = await call_api("/lint", "POST", arguments)
            return [{"type": "text", "text": result["result"]}]

        elif name == "fix_code":
            result = await call_api("/fix", "POST", arguments)
            return [{"type": "text", "text": result["result"]}]

        elif name == "check_code":
            result = await call_api("/check", "POST", arguments)
            return [{"type": "text", "text": result["result"]}]

        elif name == "list_languages":
            # /languages still returns structured format, compress it here
            result = await call_api("/languages", "GET")
            langs_compressed = []
            for lang in result["languages"]:
                name = lang["name"]
                exts = "+".join(lang["extensions"])
                fmt = "+".join(lang["tools"]["format"])
                lint = "+".join(lang["tools"]["lint"])
                fix = "+".join(lang["tools"]["fix"]) if lang["tools"]["fix"] else "none"
                langs_compressed.append(f"{name}:{exts}|fmt:{fmt}|lint:{lint}|fix:{fix}")

            return [{"type": "text", "text": "\n".join(langs_compressed)}]

        else:
            return [{"type": "text", "text": f"err:unknown_tool:{name}"}]

    except httpx.HTTPStatusError as e:
        # API now returns ml-exclusive errors, extract from response
        try:
            error_data = e.response.json()
            if "result" in error_data:
                return [{"type": "text", "text": error_data["result"]}]
        except:
            pass
        return [{"type": "text", "text": f"err:http_{e.response.status_code}|detail:{e.response.text[:100]}"}]
    except Exception as e:
        return [{"type": "text", "text": f"err:exception|msg:{str(e)[:100]}"}]


# ============================================================================
# Server Entry Point
# ============================================================================


async def main():
    """Run MCP server with STDIO transport"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="code-quality-tools",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
