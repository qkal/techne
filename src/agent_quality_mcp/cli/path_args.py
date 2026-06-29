"""Shared safe path argument helpers for CLI adapters."""

from __future__ import annotations

from pathlib import Path

from agent_quality_mcp.diagnostics import DiagnosticSource, diagnostic_from_message
from agent_quality_mcp.exceptions import SecurityError
from agent_quality_mcp.models import Diagnostic, DiagnosticSeverity
from agent_quality_mcp.paths import validate_changed_files


def safe_python_path_args(
    cwd: Path,
    changed_files: list[Path],
    *,
    source: DiagnosticSource,
) -> tuple[list[str], list[Diagnostic]]:
    """Return safe relative Python file args and diagnostics for skipped paths."""

    safe_args: list[str] = []
    diagnostics: list[Diagnostic] = []
    for path in changed_files:
        path_arg = path.as_posix()
        if has_unsafe_path_syntax(path_arg) or is_directory_target(cwd, path_arg):
            diagnostics.append(unsafe_path_diagnostic(source, path_arg))
            continue
        if Path(path_arg).suffix != ".py":
            continue
        if is_safe_path_arg(cwd, path_arg):
            safe_args.append(path_arg)
            continue
        diagnostics.append(unsafe_path_diagnostic(source, path_arg))
    return safe_args, diagnostics


def has_unsafe_path_syntax(path_arg: str) -> bool:
    path = Path(path_arg)
    return (
        path.is_absolute()
        or path_arg in {"", "."}
        or path_arg.startswith("-")
        or ".." in path.parts
        or not all(character.isprintable() for character in path_arg)
    )


def is_directory_target(cwd: Path, path_arg: str) -> bool:
    return (cwd / Path(path_arg)).is_dir()


def is_safe_path_arg(cwd: Path, path_arg: str) -> bool:
    path = Path(path_arg)
    if has_unsafe_path_syntax(path_arg):
        return False
    try:
        validate_changed_files(cwd, [path_arg])
    except (OSError, SecurityError):
        return False
    candidate = cwd / path
    if candidate.is_symlink() or not candidate.is_file():
        return False
    return True


def unsafe_path_diagnostic(source: DiagnosticSource, path_arg: str) -> Diagnostic:
    return diagnostic_from_message(
        source=source,
        code="unsafe_path",
        message="Skipped unsafe changed file path",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file=path_arg,
    )
