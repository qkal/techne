"""Minimal uv adapter for dependency quality checks."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import ToolUnavailableError
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
)


class Runner(Protocol):
    config: AgentQualityConfig

    def run(self, command: str, args: list[str], cwd: Path) -> CommandExecutionRecord: ...


class UvAdapter:
    """Run uv availability and lock/sync checks."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def check(self, cwd: Path, mode: str) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        diagnostics: list[Diagnostic] = []
        records: list[CommandExecutionRecord] = []

        for args in _commands(cwd, mode, self.runner.config):
            try:
                record = self.runner.run("uv", args, cwd)
            except ToolUnavailableError as exc:
                diagnostics.append(_tool_unavailable("uv", exc))
                return diagnostics, records
            records.append(record)
            diagnostics.extend(_record_diagnostics(record))

        return diagnostics, records


def _commands(cwd: Path, mode: str, config: AgentQualityConfig) -> list[list[str]]:
    commands = [["--version"]]
    if mode in {"standard", "strict"} and (cwd / "pyproject.toml").is_file():
        commands.append(["lock", "--check"])
        if config.uv_sync_dry_run:
            commands.append(["sync", "--locked", "--dry-run"])
    return commands


def _record_diagnostics(record: CommandExecutionRecord) -> list[Diagnostic]:
    if record.timed_out:
        return [
            diagnostic_from_message(
                source="uv",
                code="timeout",
                message="uv command timed out",
                severity=DiagnosticSeverity.WARNING,
                is_blocking=False,
                metadata={"args": record.args},
            )
        ]
    if record.exit_code not in (0, None):
        detail = record.stderr_preview or record.stdout_preview or "uv command failed"
        return [
            diagnostic_from_message(
                source="uv",
                code="command_failed",
                message=detail,
                severity=DiagnosticSeverity.WARNING,
                is_blocking=False,
                metadata={"exit_code": record.exit_code, "args": record.args},
            )
        ]
    return []


def _tool_unavailable(tool: str, exc: ToolUnavailableError) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )
