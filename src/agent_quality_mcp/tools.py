"""FastMCP tool wrappers for Agent Quality MCP services."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agent_quality_mcp.models import InspectWorkspaceRequest, ValidatePatchRequest
from agent_quality_mcp.response import build_error_response
from agent_quality_mcp.service import inspect_workspace_service, validate_patch_service


def validate_patch_tool(
    workspace_root: str,
    changed_files: list[str],
    patch_unified_diff: str | None = None,
    mode: str | None = None,
    safety_mode: str | None = None,
    request_id: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a proposed patch and return JSON-safe response data."""

    request_data: dict[str, Any] = {
        "workspace_root": workspace_root,
        "changed_files": changed_files,
        "patch_unified_diff": patch_unified_diff,
        "mode": mode,
        "safety_mode": safety_mode,
        "config_overrides": config_overrides,
    }
    if request_id is not None:
        request_data["request_id"] = request_id

    try:
        request = ValidatePatchRequest(**request_data)
    except ValidationError:
        return build_error_response(
            request_id=_safe_request_id(request_id),
            workspace_root=_safe_workspace_root(workspace_root),
            mode=_safe_optional_string(mode),
            safety_mode=_safe_optional_string(safety_mode),
            code="invalid_request",
            message="Invalid validate_patch request",
        ).model_dump(mode="json")
    return validate_patch_service(request).model_dump(mode="json")


def inspect_workspace_tool(
    workspace_root: str,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect a workspace and return JSON-safe response data."""

    request = InspectWorkspaceRequest(
        workspace_root=workspace_root,
        config_overrides=config_overrides,
    )
    return inspect_workspace_service(
        request.workspace_root,
        request.config_overrides,
    ).model_dump(mode="json")


def register_tools(app: Any) -> None:
    """Register Agent Quality MCP tools on a FastMCP app."""

    app.tool(name="validate_patch")(validate_patch_tool)
    app.tool(name="inspect_workspace")(inspect_workspace_tool)


def _safe_request_id(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(uuid4())


def _safe_workspace_root(value: object) -> str:
    if isinstance(value, str):
        return value
    return "<invalid>"


def _safe_optional_string(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return None
