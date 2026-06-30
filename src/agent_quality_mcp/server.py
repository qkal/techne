"""FastMCP server entrypoint for Agent Quality MCP."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mcp.server.fastmcp import FastMCP

from agent_quality_mcp import __version__
from agent_quality_mcp.service import close_pyright_lsp_manager
from agent_quality_mcp.tools import register_tools


def create_app() -> FastMCP:
    """Create the FastMCP app."""

    app = FastMCP("agent-quality-mcp")
    register_tools(app)
    return app


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse CLI arguments.

    Exits via SystemExit (raised by argparse) for --version, --help, and
    unrecognized arguments, instead of silently falling through to the
    blocking stdio server loop.
    """

    parser = argparse.ArgumentParser(
        prog="agent-quality-mcp",
        description=(
            "Agent Quality MCP server. Run with no arguments to speak MCP "
            "over stdio. See https://github.com/qkal/techne for the "
            "validate_patch / inspect_workspace tool contract."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"agent-quality-mcp {__version__}",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Run the MCP server over stdio."""

    parse_args(sys.argv[1:])
    try:
        create_app().run()
    finally:
        close_pyright_lsp_manager()
