"""FastMCP server entrypoint for Agent Quality MCP."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_quality_mcp.tools import register_tools


def create_app() -> FastMCP:
    """Create the FastMCP app."""

    app = FastMCP("agent-quality-mcp")
    register_tools(app)
    return app


def main() -> None:
    """Run the MCP server over stdio."""

    create_app().run()
