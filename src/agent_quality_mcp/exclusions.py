"""Shared workspace path exclusion rules."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from agent_quality_mcp.models import (
    DEFAULT_SECRET_FILE_PATTERNS,
    DEFAULT_WORKSPACE_EXCLUSIONS,
    AgentQualityConfig,
)


def is_workspace_path_excluded(
    path: Path,
    root: Path,
    config: AgentQualityConfig,
    *,
    include_secret_files: bool = True,
) -> bool:
    """Return whether a workspace path should be skipped during copy or inspection."""

    relative = path.relative_to(root)
    exclusions = {*DEFAULT_WORKSPACE_EXCLUSIONS, *config.workspace_exclusions}
    if any(part in exclusions for part in relative.parts):
        return True
    if include_secret_files and path.is_file() and matches_secret_filename(path, config):
        return True
    return False


def matches_secret_filename(path: Path, config: AgentQualityConfig) -> bool:
    """Return whether a file name matches configured or built-in secret patterns."""

    patterns = (*DEFAULT_SECRET_FILE_PATTERNS, *tuple(config.secret_file_patterns))
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)
