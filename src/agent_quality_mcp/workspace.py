"""Workspace inspection without returning source contents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_quality_mcp.models import AgentQualityConfig


@dataclass(frozen=True)
class WorkspaceFileInspection:
    """Safe workspace file counts and config discovery."""

    python_file_count: int
    config_files: list[str]


def _is_excluded(path: Path, root: Path, config: AgentQualityConfig) -> bool:
    relative = path.relative_to(root)
    return any(part in set(config.workspace_exclusions) for part in relative.parts)


def inspect_workspace_files(root: Path, config: AgentQualityConfig) -> WorkspaceFileInspection:
    """Count Python files and config files without reading source contents."""

    python_count = 0
    config_files: list[str] = []
    config_names = {"pyproject.toml", "ruff.toml", ".ruff.toml", "pyrightconfig.json"}
    for path in root.rglob("*"):
        if _is_excluded(path, root, config):
            continue
        if path.is_file() and path.suffix == ".py":
            python_count += 1
        if path.is_file() and path.name in config_names:
            config_files.append(path.relative_to(root).as_posix())
    return WorkspaceFileInspection(
        python_file_count=python_count,
        config_files=sorted(config_files),
    )
