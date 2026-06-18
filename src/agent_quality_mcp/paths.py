"""Path validation helpers for secure workspace access."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from agent_quality_mcp.exceptions import SecurityError, WorkspaceError


def resolve_workspace_root(workspace_root: str | Path) -> Path:
    """Resolve and validate an existing workspace directory."""

    path = Path(workspace_root).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise WorkspaceError(f"workspace_root must be an existing directory: {workspace_root}")
    return path


def _validate_relative_path(path_text: str) -> Path:
    """Validate a changed file path as a safe relative path."""

    if path_text in {"", "."}:
        raise SecurityError("changed file paths must identify a relative file")
    if "\0" in path_text:
        raise SecurityError("changed file paths must not contain null bytes")
    if "\\" in path_text:
        raise SecurityError("changed file paths must use forward slash separators")
    parts = path_text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("changed file paths must not contain empty, dot, or dot-dot segments")
    if any(len(part) >= 2 and part[1] == ":" and part[0].isalpha() for part in parts):
        raise SecurityError("changed file paths must not contain drive prefixes")
    pure = PurePosixPath(path_text)
    if pure.is_absolute():
        raise SecurityError("changed file paths must be relative")
    candidate = Path(*pure.parts)
    return candidate


def ensure_within_directory(root: Path, candidate: Path) -> Path:
    """Resolve candidate and ensure it remains inside root."""

    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SecurityError(f"path escapes workspace: {candidate}") from exc
    return resolved


def validate_changed_files(workspace_root: Path, changed_files: list[str]) -> list[Path]:
    """Validate changed files and return normalized relative paths."""

    normalized: list[Path] = []
    root = workspace_root.resolve()
    for path_text in changed_files:
        relative = _validate_relative_path(path_text)
        absolute = root / relative
        if absolute.exists():
            resolved = ensure_within_directory(root, absolute)
            if absolute.is_symlink() or resolved.is_symlink():
                raise SecurityError(f"changed file must not be a symlink: {relative.as_posix()}")
        else:
            ensure_within_directory(root, absolute.parent)
        normalized.append(relative)
    return normalized
