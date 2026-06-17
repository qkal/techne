"""FastMCP server entrypoint for Agent Quality MCP."""

from __future__ import annotations

from typing import Any


def create_app() -> Any:
    """Create the FastMCP app.

    Tool registration is added by the MCP tools task.
    """

    from mcp.server.fastmcp import FastMCP

    return FastMCP("agent-quality-mcp")


def main() -> None:
    """Run the MCP server over stdio."""

    create_app().run()
