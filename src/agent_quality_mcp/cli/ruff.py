"""Minimal Ruff adapter for lint diagnostics and safe-fix previews."""

from __future__ import annotations

import json
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Protocol

from agent_quality_mcp.cli.runner import CommandRunResult
from agent_quality_mcp.diagnostics import DiagnosticSource, diagnostic_from_message, normalize_ruff
from agent_quality_mcp.exceptions import CommandExecutionError, SecurityError
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
    SafeFixPreview,
)
from agent_quality_mcp.paths import validate_changed_files

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$")


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
        if mode == "strict":
            file_args: list[str] = []
            diagnostics: list[Diagnostic] = []
        else:
            file_args, diagnostics = _safe_python_path_args(cwd, changed_files, source="ruff")
        records: list[CommandExecutionRecord] = []
        safe_fixes: list[SafeFixPreview] = []
        if mode != "strict" and changed_files and not file_args:
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
        except CommandExecutionError as exc:
            diagnostics.append(_tool_unavailable("ruff", exc))
            return diagnostics, records, safe_fixes
        record = result.record
        records.append(record)
        diagnostics.extend(_diagnostics_from_result(result))
        if record.timed_out:
            return diagnostics, records, safe_fixes

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
            except CommandExecutionError as exc:
                diagnostics.append(_tool_unavailable("ruff", exc))
                return diagnostics, records, safe_fixes
            fix_record = fix_result.record
            records.append(fix_record)
            diagnostics.extend(_timeout_diagnostic(fix_record, source="ruff"))
            if fix_record.timed_out:
                return diagnostics, records, safe_fixes
            if fix_record.stdout_preview and _is_valid_safe_fix_preview(
                fix_result.stdout,
                fix_record,
                cwd,
                file_args,
            ):
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
            elif fix_record.stdout_preview:
                diagnostics.append(_invalid_safe_fix_preview(fix_record))
            elif fix_record.exit_code not in (0, None):
                diagnostics.append(_command_failed(fix_record, source="ruff"))

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


def _safe_python_path_args(
    cwd: Path,
    changed_files: list[Path],
    *,
    source: DiagnosticSource,
) -> tuple[list[str], list[Diagnostic]]:
    safe_args: list[str] = []
    diagnostics: list[Diagnostic] = []
    for path in changed_files:
        path_arg = path.as_posix()
        if _has_unsafe_path_syntax(path_arg) or _is_directory_target(cwd, path_arg):
            diagnostics.append(_unsafe_path_diagnostic(source, path_arg))
            continue
        if Path(path_arg).suffix != ".py":
            continue
        if _is_safe_path_arg(cwd, path_arg):
            safe_args.append(path_arg)
            continue
        diagnostics.append(_unsafe_path_diagnostic(source, path_arg))
    return safe_args, diagnostics


def _has_unsafe_path_syntax(path_arg: str) -> bool:
    path = Path(path_arg)
    return (
        path.is_absolute()
        or path_arg in {"", "."}
        or path_arg.startswith("-")
        or ".." in path.parts
        or not all(character.isprintable() for character in path_arg)
    )


def _is_directory_target(cwd: Path, path_arg: str) -> bool:
    return (cwd / Path(path_arg)).is_dir()


def _is_safe_path_arg(cwd: Path, path_arg: str) -> bool:
    path = Path(path_arg)
    if _has_unsafe_path_syntax(path_arg):
        return False
    try:
        validate_changed_files(cwd, [path_arg])
    except (OSError, SecurityError):
        return False
    candidate = cwd / path
    if candidate.is_symlink() or not candidate.is_file():
        return False
    return True


def _unsafe_path_diagnostic(source: DiagnosticSource, path_arg: str) -> Diagnostic:
    return diagnostic_from_message(
        source=source,
        code="unsafe_path",
        message="Skipped unsafe changed file path",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file=path_arg,
    )


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


def _is_valid_safe_fix_preview(
    stdout: str,
    record: CommandExecutionRecord,
    cwd: Path,
    scoped_file_args: list[str],
) -> bool:
    return record.exit_code == 1 and _looks_like_safe_unified_diff(
        stdout,
        cwd,
        scoped_file_args,
    )


def _looks_like_safe_unified_diff(text: str, cwd: Path, scoped_file_args: list[str]) -> bool:
    lines = text.splitlines()
    if not lines:
        return False

    allowed_paths = set(scoped_file_args)
    index = 0
    saw_file_diff = False
    while index < len(lines):
        old_path = _diff_header_path(lines[index], "--- ")
        if old_path is None:
            return False
        index += 1

        if index >= len(lines):
            return False
        new_path = _diff_header_path(lines[index], "+++ ")
        if new_path is None:
            return False
        index += 1

        if old_path != new_path or not _is_safe_diff_path(cwd, new_path, allowed_paths):
            return False

        saw_hunk = False
        while index < len(lines):
            if (
                lines[index].startswith("--- ")
                and index + 1 < len(lines)
                and lines[index + 1].startswith("+++ ")
            ):
                break
            if HUNK_HEADER_RE.match(lines[index]) is not None:
                saw_hunk = True
            index += 1
        if not saw_hunk:
            return False
        saw_file_diff = True

    return saw_file_diff


def _diff_header_path(line: str, prefix: str) -> str | None:
    if not line.startswith(prefix):
        return None
    path_text = line[len(prefix) :].split("\t", 1)[0]
    if not path_text or path_text != path_text.strip():
        return None
    return path_text


def _is_safe_diff_path(cwd: Path, path_arg: str, allowed_paths: set[str]) -> bool:
    if _has_unsafe_diff_path_syntax(path_arg):
        return False
    try:
        normalized = validate_changed_files(cwd, [path_arg])[0].as_posix()
    except (OSError, SecurityError):
        return False
    if allowed_paths and normalized not in allowed_paths:
        return False
    candidate = cwd / normalized
    return candidate.is_file() and not candidate.is_symlink()


def _has_unsafe_diff_path_syntax(path_arg: str) -> bool:
    if path_arg == "" or path_arg.startswith("-") or "\\" in path_arg:
        return True
    if not all(character.isprintable() for character in path_arg):
        return True
    parts = path_arg.split("/")
    return (
        Path(path_arg).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part) >= 2 and part[1] == ":" and part[0].isalpha() for part in parts)
    )


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


def _invalid_safe_fix_preview(record: CommandExecutionRecord) -> Diagnostic:
    return diagnostic_from_message(
        source="ruff",
        code="invalid_preview",
        message="Ruff safe-fix preview did not return a valid unified diff",
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


def _tool_unavailable(tool: str, exc: CommandExecutionError) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )
