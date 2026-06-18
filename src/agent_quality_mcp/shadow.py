"""Shadow workspace creation for read-only validation."""

from __future__ import annotations

import fnmatch
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from agent_quality_mcp.exceptions import SecurityError, WorkspaceError
from agent_quality_mcp.models import (
    DEFAULT_SECRET_FILE_PATTERNS,
    DEFAULT_WORKSPACE_EXCLUSIONS,
    AgentQualityConfig,
)


@dataclass
class ShadowWorkspace:
    """Temporary workspace copy used for validation."""

    path: Path
    real_workspace_modified: bool = False
    preserved: bool = False


class ShadowWorkspaceContext:
    """Context manager around a temporary shadow workspace."""

    def __init__(self, source_root: Path, config: AgentQualityConfig) -> None:
        self.source_root = source_root
        self.config = config
        self._temporary_root: Path | None = None
        self.shadow: ShadowWorkspace | None = None

    def __enter__(self) -> ShadowWorkspace:
        self._temporary_root = Path(tempfile.mkdtemp(prefix="agent-quality-mcp-"))
        shadow_root = self._temporary_root / "workspace"
        try:
            _copy_workspace(self.source_root, shadow_root, self.config)
        except Exception:
            shutil.rmtree(self._temporary_root)
            raise
        self.shadow = ShadowWorkspace(
            path=shadow_root,
            real_workspace_modified=False,
            preserved=self.config.preserve_shadow_workspace,
        )
        return self.shadow

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.config.preserve_shadow_workspace:
            return
        if self._temporary_root is not None:
            shutil.rmtree(self._temporary_root)


def _matches_secret_pattern(path: Path, config: AgentQualityConfig) -> bool:
    patterns = (*DEFAULT_SECRET_FILE_PATTERNS, *tuple(config.secret_file_patterns))
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def _is_excluded(path: Path, root: Path, config: AgentQualityConfig) -> bool:
    relative = path.relative_to(root)
    exclusions = {*DEFAULT_WORKSPACE_EXCLUSIONS, *config.workspace_exclusions}
    if any(part in exclusions for part in relative.parts):
        return True
    return path.is_file() and _matches_secret_pattern(path, config)


def _copy_workspace(source_root: Path, shadow_root: Path, config: AgentQualityConfig) -> None:
    copied_bytes = 0
    shadow_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.rglob("*"):
        if _is_excluded(source, source_root, config):
            continue
        relative = source.relative_to(source_root)
        target = shadow_root / relative
        if source.is_symlink():
            resolved = source.resolve()
            try:
                resolved.relative_to(source_root)
            except ValueError as exc:
                raise SecurityError(f"symlink escapes workspace: {relative.as_posix()}") from exc
            continue
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if source.is_file():
            size = source.stat().st_size
            if size > config.max_changed_file_bytes:
                raise WorkspaceError(
                    f"file exceeds configured max_changed_file_bytes: {relative.as_posix()}"
                )
            copied_bytes += size
            if copied_bytes > config.max_workspace_copy_bytes:
                raise WorkspaceError("workspace copy exceeds configured max_workspace_copy_bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def create_shadow_workspace(
    source_root: Path,
    config: AgentQualityConfig,
) -> ShadowWorkspaceContext:
    """Create a temporary shadow workspace context."""

    return ShadowWorkspaceContext(source_root, config)
