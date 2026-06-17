"""Configuration loading for Agent Quality MCP."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_quality_mcp.exceptions import ConfigurationError
from agent_quality_mcp.models import AgentQualityConfig

SAFE_UNTRUSTED_CONFIG_FIELDS = frozenset(
    {
        "default_mode",
        "default_safety_mode",
        "uv_offline",
        "secret_redaction_patterns",
        "workspace_exclusions",
        "secret_file_patterns",
    }
)
DENIED_UNTRUSTED_CONFIG_FIELDS = frozenset(
    {
        "command_paths",
        "preserve_shadow_workspace",
        "request_timeout_seconds",
        "subprocess_timeout_seconds",
        "max_patch_bytes",
        "max_changed_files",
        "max_changed_file_bytes",
        "max_workspace_copy_bytes",
        "max_output_bytes",
        "max_diagnostics",
        "uv_sync_dry_run",
    }
)
SAFE_UNTRUSTED_SAFETY_MODES = frozenset({"read_only", "preview_safe_fixes"})


def _validate_untrusted_config(data: dict[str, Any], source: str) -> None:
    """Reject untrusted fields that can expand authority or resource use."""

    data_fields = set(data)
    denied_fields = sorted(data_fields & DENIED_UNTRUSTED_CONFIG_FIELDS)
    if denied_fields:
        raise ConfigurationError(
            f"Denied untrusted {source} config fields: {', '.join(denied_fields)}"
        )
    unsupported_fields = sorted(data_fields - SAFE_UNTRUSTED_CONFIG_FIELDS)
    if unsupported_fields:
        raise ConfigurationError(
            f"Unsupported untrusted {source} config fields: {', '.join(unsupported_fields)}"
        )
    if data.get("uv_offline") is False:
        raise ConfigurationError(f"Denied untrusted {source} config field: uv_offline=false")
    safety_mode = data.get("default_safety_mode")
    if safety_mode is not None and safety_mode not in SAFE_UNTRUSTED_SAFETY_MODES:
        raise ConfigurationError(
            "Denied untrusted "
            f"{source} config value for default_safety_mode: {safety_mode}"
        )


def _read_pyproject_config(workspace_root: Path) -> dict[str, Any]:
    """Read [tool.agent_quality_mcp] from pyproject.toml when present."""

    pyproject_path = workspace_root / "pyproject.toml"
    if not pyproject_path.exists():
        return {}
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Unable to read pyproject.toml: {exc}") from exc
    tool_data = data.get("tool", {})
    if not isinstance(tool_data, dict):
        return {}
    config_data = tool_data.get("agent_quality_mcp", {})
    if config_data is None:
        return {}
    if not isinstance(config_data, dict):
        raise ConfigurationError("[tool.agent_quality_mcp] must be a table")
    return config_data


def load_config(
    workspace_root: str | Path,
    overrides: dict[str, Any] | None = None,
) -> AgentQualityConfig:
    """Load defaults, workspace config, and validated untrusted overrides."""

    root = Path(workspace_root)
    data: dict[str, Any] = {}
    pyproject_config = _read_pyproject_config(root)
    _validate_untrusted_config(pyproject_config, "workspace")
    data.update(pyproject_config)
    if overrides:
        _validate_untrusted_config(overrides, "override")
        data.update(overrides)
    try:
        return AgentQualityConfig(**data)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc
