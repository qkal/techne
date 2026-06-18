"""Minimal Ruff adapter for lint diagnostics and safe-fix previews."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Protocol

from agent_quality_mcp.cli.runner import CommandRunResult
from agent_quality_mcp.diagnostics import DiagnosticSource, diagnostic_from_message, normalize_ruff
from agent_quality_mcp.exceptions import SecurityError, ToolUnavailableError
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
    SafeFixPreview,
)
from agent_quality_mcp.paths import validate_changed_files


class Runner(Protocol):
    config: AgentQualityConfig

    def run_with_output(self, command: str, args: list[str], cwd: Path) -> CommandRunResult: ...


class RuffAdapter:
    """Run Ruff checks and parse JSON diagnostics."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
        preview_safe_fixes: bool = False,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
        del mode
        file_args, diagnostics = _safe_path_args(cwd, changed_files, source="ruff")
        records: list[CommandExecutionRecord] = []
        safe_fixes: list[SafeFixPreview] = []
        if changed_files and not file_args:
            return diagnostics, records, safe_fixes

        args = [
            "check",
            "--no-cache",
            "--output-format",
            "json",
            *_file_args_with_delimiter(file_args),
        ]
        try:
            result = self.runner.run_with_output("ruff", args, cwd)
        except ToolUnavailableError as exc:
            diagnostics.append(_tool_unavailable("ruff", exc))
            return diagnostics, records, safe_fixes
        record = result.record
        records.append(record)
        diagnostics.extend(_diagnostics_from_result(result))

        if preview_safe_fixes:
            fix_args = [
                "check",
                "--no-cache",
                "--fix",
                "--diff",
                *_file_args_with_delimiter(file_args),
            ]
            try:
                fix_result = self.runner.run_with_output("ruff", fix_args, cwd)
            except ToolUnavailableError as exc:
                diagnostics.append(_tool_unavailable("ruff", exc))
                return diagnostics, records, safe_fixes
            fix_record = fix_result.record
            records.append(fix_record)
            if fix_record.stdout_preview:
                safe_fixes.append(
                    SafeFixPreview(
                        tool="ruff",
                        description="Ruff safe-fix diff preview",
                        files=file_args,
                        diff_preview=fix_record.stdout_preview,
                        is_safe=True,
                        requires_human_review=True,
                    )
                )
            diagnostics.extend(_timeout_diagnostic(fix_record, source="ruff"))

        return diagnostics, records, safe_fixes


def _diagnostics_from_result(result: CommandRunResult) -> list[Diagnostic]:
    record = result.record
    diagnostics = _timeout_diagnostic(record, source="ruff")
    if record.timed_out:
        return diagnostics

    raw_text = result.stdout.strip()
    if not raw_text:
        if record.exit_code not in (0, None):
            diagnostics.append(_command_failed(record, source="ruff"))
        return diagnostics

    try:
        raw_json: Any = json.loads(raw_text)
    except JSONDecodeError as exc:
        diagnostics.append(_invalid_json("ruff", exc))
        return diagnostics

    if not isinstance(raw_json, list):
        diagnostics.append(_invalid_json("ruff", None))
        return diagnostics

    normalized = normalize_ruff(raw_json)
    if not normalized and record.exit_code not in (0, None):
        diagnostics.append(_command_failed(record, source="ruff"))
    return [*diagnostics, *normalized]


def _safe_path_args(
    cwd: Path,
    changed_files: list[Path],
    *,
    source: DiagnosticSource,
) -> tuple[list[str], list[Diagnostic]]:
    safe_args: list[str] = []
    diagnostics: list[Diagnostic] = []
    for path in changed_files:
        path_arg = path.as_posix()
        if _is_safe_path_arg(cwd, path_arg):
            safe_args.append(path_arg)
            continue
        diagnostics.append(
            diagnostic_from_message(
                source=source,
                code="unsafe_path",
                message="Skipped unsafe changed file path",
                severity=DiagnosticSeverity.WARNING,
                is_blocking=False,
                file=path_arg,
            )
        )
    return safe_args, diagnostics


def _is_safe_path_arg(cwd: Path, path_arg: str) -> bool:
    path = Path(path_arg)
    if path.is_absolute() or path_arg in {"", "."} or path_arg.startswith("-"):
        return False
    if ".." in path.parts:
        return False
    if not all(character.isprintable() for character in path_arg):
        return False
    try:
        validate_changed_files(cwd, [path_arg])
    except (OSError, SecurityError):
        return False
    candidate = cwd / path
    if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
        return False
    return True


def _file_args_with_delimiter(file_args: list[str]) -> list[str]:
    if not file_args:
        return []
    return ["--", *file_args]


def _timeout_diagnostic(
    record: CommandExecutionRecord,
    *,
    source: DiagnosticSource,
) -> list[Diagnostic]:
    if not record.timed_out:
        return []
    return [
        diagnostic_from_message(
            source=source,
            code="timeout",
            message=f"{source} command timed out",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"args": record.args},
        )
    ]


def _command_failed(record: CommandExecutionRecord, *, source: DiagnosticSource) -> Diagnostic:
    detail = record.stderr_preview or record.stdout_preview or f"{source} command failed"
    return diagnostic_from_message(
        source=source,
        code="command_failed",
        message=detail,
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"exit_code": record.exit_code, "args": record.args},
    )


def _invalid_json(source: DiagnosticSource, exc: JSONDecodeError | None) -> Diagnostic:
    message = f"{source} returned invalid JSON"
    if exc is not None:
        message = f"{message}: {exc.msg}"
    return diagnostic_from_message(
        source=source,
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
