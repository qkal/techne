"""Shared CLI adapter error diagnostics."""

from __future__ import annotations

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import CommandExecutionError
from agent_quality_mcp.models import Diagnostic, DiagnosticSeverity


def tool_unavailable_diagnostic(tool: str, exc: CommandExecutionError) -> Diagnostic:
    """Build a non-blocking diagnostic for unavailable quality tools."""

    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )
