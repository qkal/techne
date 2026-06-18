"""Minimal Pyright adapter for type-check diagnostics."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Protocol

from agent_quality_mcp.cli.runner import CommandRunResult
from agent_quality_mcp.diagnostics import diagnostic_from_message, normalize_pyright
from agent_quality_mcp.exceptions import ToolUnavailableError
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
)


class Runner(Protocol):
    config: AgentQualityConfig

    def run_with_output(self, command: str, args: list[str], cwd: Path) -> CommandRunResult: ...


class PyrightAdapter:
    """Run Pyright and parse JSON diagnostics."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        file_args: list[str] = []
        diagnostics: list[Diagnostic] = []
        if mode == "quick":
            file_args, diagnostics = _safe_path_args(cwd, changed_files)
        records: list[CommandExecutionRecord] = []

        try:
            result = self.runner.run_with_output("pyright", ["--outputjson", *file_args], cwd)
        except ToolUnavailableError as exc:
            diagnostics.append(_tool_unavailable("pyright", exc))
            return diagnostics, records
        record = result.record
        records.append(record)
        diagnostics.extend(_diagnostics_from_result(result))
        return diagnostics, records


def _diagnostics_from_result(result: CommandRunResult) -> list[Diagnostic]:
    record = result.record
    diagnostics = _timeout_diagnostic(record)
    if record.timed_out:
        return diagnostics

    raw_text = result.stdout.strip()
    if not raw_text:
        if record.exit_code not in (0, None):
            diagnostics.append(_command_failed(record))
        return diagnostics

    try:
        raw_json: Any = json.loads(raw_text)
    except JSONDecodeError as exc:
        diagnostics.append(_invalid_json(exc))
        return diagnostics

    if not isinstance(raw_json, dict):
        diagnostics.append(_invalid_json(None))
        return diagnostics

    normalized = normalize_pyright(raw_json)
    if not normalized and record.exit_code not in (0, None):
        diagnostics.append(_command_failed(record))
    return [*diagnostics, *normalized]


def _safe_path_args(cwd: Path, changed_files: list[Path]) -> tuple[list[str], list[Diagnostic]]:
    safe_args: list[str] = []
    diagnostics: list[Diagnostic] = []
    for path in changed_files:
        path_arg = path.as_posix()
        if _is_safe_path_arg(path_arg) and not path_arg.startswith("-"):
            safe_args.append(path_arg)
            continue
        diagnostics.append(
            diagnostic_from_message(
                source="pyright",
                code="unsafe_path",
                message="Skipped unsafe changed file path",
                severity=DiagnosticSeverity.WARNING,
                is_blocking=False,
                file=path_arg,
            )
        )
    return safe_args, diagnostics


def _is_safe_path_arg(path_arg: str) -> bool:
    path = Path(path_arg)
    if path.is_absolute() or path_arg in {"", "."} or path_arg.startswith("-"):
        return False
    if ".." in path.parts:
        return False
    return all(character.isprintable() for character in path_arg)


def _timeout_diagnostic(record: CommandExecutionRecord) -> list[Diagnostic]:
    if not record.timed_out:
        return []
    return [
        diagnostic_from_message(
            source="pyright",
            code="timeout",
            message="pyright command timed out",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"args": record.args},
        )
    ]


def _command_failed(record: CommandExecutionRecord) -> Diagnostic:
    detail = record.stderr_preview or record.stdout_preview or "pyright command failed"
    return diagnostic_from_message(
        source="pyright",
        code="command_failed",
        message=detail,
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"exit_code": record.exit_code, "args": record.args},
    )


def _invalid_json(exc: JSONDecodeError | None) -> Diagnostic:
    message = "pyright returned invalid JSON"
    if exc is not None:
        message = f"{message}: {exc.msg}"
    return diagnostic_from_message(
        source="pyright",
        code="invalid_json",
        message=message,
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
    )


def _tool_unavailable(tool: str, exc: ToolUnavailableError) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )
