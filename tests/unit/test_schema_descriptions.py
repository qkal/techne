"""Schema self-description tests for MCP tool request models."""

from __future__ import annotations

from agent_quality_mcp.models import InspectWorkspaceRequest, ValidatePatchRequest


def _assert_all_properties_described(schema: dict) -> None:
    properties = schema.get("properties", {})
    assert properties, "schema must declare properties"
    for name, definition in properties.items():
        assert definition.get("description"), f"{name} is missing a description"


def test_validate_patch_request_schema_is_self_describing() -> None:
    _assert_all_properties_described(ValidatePatchRequest.model_json_schema())


def test_inspect_workspace_request_schema_is_self_describing() -> None:
    _assert_all_properties_described(InspectWorkspaceRequest.model_json_schema())
