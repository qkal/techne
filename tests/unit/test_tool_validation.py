from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agent_quality_mcp.models import ValidatePatchRequest
from agent_quality_mcp.tool_validation import (
    format_validation_error_summary,
    sanitize_config_issue_message,
)


def test_format_validation_error_summary_lists_field_locations(tmp_path: Path) -> None:
    try:
        ValidatePatchRequest.model_validate(
            {
                "workspace_root": str(tmp_path),
                "changed_files": [],
                "mode": "not-a-mode",
            }
        )
    except ValidationError as exc:
        summary = format_validation_error_summary(exc)
    else:
        raise AssertionError("expected validation error")

    assert "changed_files" in summary
    assert "mode" in summary


def test_sanitize_config_issue_message_keeps_known_safe_prefixes() -> None:
    message = "Denied untrusted request config fields: max_patch_bytes, request_timeout_seconds"

    assert sanitize_config_issue_message(message) == message


def test_sanitize_config_issue_message_redacts_unknown_errors() -> None:
    message = "invalid config contains raw-secret-token"

    assert sanitize_config_issue_message(message) == "Configuration rejected"
