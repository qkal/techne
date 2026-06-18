from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_quality_mcp import tools as tools_module
from agent_quality_mcp.models import (
    ValidatePatchRequest,
    ValidatePatchResponse,
    build_error_response,
)
from agent_quality_mcp.server import create_app
from agent_quality_mcp.tools import inspect_workspace_tool, validate_patch_tool


async def _tool_names(app: Any) -> set[str]:
    return {tool.name for tool in await app.list_tools()}


async def _call_tool_structured(
    app: Any,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await app.call_tool(name, arguments)
    if isinstance(result, tuple):
        structured = result[1]
        assert isinstance(structured, dict)
        return structured
    assert isinstance(result, dict)
    return result


def _write_python_file(workspace_root: Path) -> None:
    package_dir = workspace_root / "pkg"
    package_dir.mkdir()
    (package_dir / "app.py").write_text("value = 1\n", encoding="utf-8")


def test_create_app_registers_named_fastmcp_tools(tmp_path: Path) -> None:
    _write_python_file(tmp_path)

    app = create_app()

    assert app.name == "agent-quality-mcp"
    assert asyncio.run(_tool_names(app)) == {"validate_patch", "inspect_workspace"}
    result = asyncio.run(
        _call_tool_structured(
            app,
            "inspect_workspace",
            {"workspace_root": str(tmp_path)},
        )
    )
    assert result["workspace_root"] == str(tmp_path.resolve())


def test_validate_patch_tool_builds_request_and_returns_json_dict(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    captured: dict[str, ValidatePatchRequest] = {}

    def fake_validate_patch_service(request: ValidatePatchRequest) -> ValidatePatchResponse:
        captured["request"] = request
        return build_error_response(
            request_id=request.request_id,
            workspace_root=request.workspace_root,
            mode=request.mode,
            safety_mode=request.safety_mode,
            code="test_blocker",
            message="test blocker",
        )

    monkeypatch.setattr(tools_module, "validate_patch_service", fake_validate_patch_service)

    result = validate_patch_tool(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        patch_unified_diff=None,
        mode="quick",
        safety_mode="read_only",
        config_overrides={"request_timeout_seconds": 1},
    )

    assert isinstance(result, dict)
    assert result["real_workspace_modified"] is False
    assert result["mode"] == "quick"
    assert result["safety_mode"] == "read_only"
    request = captured["request"]
    assert request.workspace_root == str(tmp_path)
    assert request.changed_files == ["pkg/app.py"]
    assert request.request_id
    assert request.config_overrides == {"request_timeout_seconds": 1}


def test_validate_patch_tool_preserves_provided_request_id(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)

    def fake_validate_patch_service(request: ValidatePatchRequest) -> ValidatePatchResponse:
        return build_error_response(
            request_id=request.request_id,
            workspace_root=request.workspace_root,
            mode=request.mode,
            safety_mode=request.safety_mode,
            code="test_blocker",
            message="test blocker",
        )

    monkeypatch.setattr(tools_module, "validate_patch_service", fake_validate_patch_service)

    result = validate_patch_tool(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        request_id="req-test-1",
    )

    assert result["request_id"] == "req-test-1"


def test_inspect_workspace_tool_returns_resolved_workspace_json(tmp_path: Path) -> None:
    _write_python_file(tmp_path)

    result = inspect_workspace_tool(workspace_root=str(tmp_path / "."))

    assert isinstance(result, dict)
    assert result["workspace_root"] == str(tmp_path.resolve())
    assert result["python_file_count"] == 1
    assert isinstance(result["config"], dict)
