from pydantic import ValidationError

from agent_quality_mcp.models import (
    AgentQualityConfig,
    Diagnostic,
    DiagnosticRange,
    DiagnosticSeverity,
    SafetyMode,
    ValidatePatchRequest,
    ValidatePatchResponse,
    ValidationMode,
    build_error_response,
)


def test_validate_patch_request_generates_request_id() -> None:
    request = ValidatePatchRequest(workspace_root="/workspace/demo", changed_files=["pkg/app.py"])

    assert request.request_id
    assert request.mode == ValidationMode.STANDARD
    assert request.safety_mode == SafetyMode.READ_ONLY


def test_apply_safe_fixes_is_representable_for_structured_rejection() -> None:
    request = ValidatePatchRequest.model_validate(
        {
            "workspace_root": "/workspace/demo",
            "changed_files": ["pkg/app.py"],
            "safety_mode": "apply_safe_fixes",
        }
    )

    assert request.safety_mode == SafetyMode.APPLY_SAFE_FIXES


def test_config_rejects_negative_limits() -> None:
    try:
        AgentQualityConfig(max_patch_bytes=-1)
    except ValidationError as exc:
        assert "max_patch_bytes" in str(exc)
    else:
        raise AssertionError("negative max_patch_bytes should fail validation")


def test_diagnostic_range_is_optional_and_typed() -> None:
    diagnostic = Diagnostic(
        id="ruff-F401-demo",
        source="ruff",
        severity=DiagnosticSeverity.WARNING,
        code="F401",
        message="Unused import",
        file="pkg/app.py",
        range=DiagnosticRange(start_line=1, start_column=1, end_line=1, end_column=10),
        is_blocking=False,
        is_fixable=True,
    )

    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 1


def test_error_response_has_error_status_and_blocker() -> None:
    response = build_error_response(
        request_id="req-1",
        workspace_root="/workspace/demo",
        mode=ValidationMode.STANDARD,
        safety_mode=SafetyMode.READ_ONLY,
        code="security_error",
        message="Unsafe path",
    )

    assert isinstance(response, ValidatePatchResponse)
    assert response.status == "error"
    assert response.real_workspace_modified is False
    assert response.shadow_workspace_used is False
    assert response.blocking_errors[0].code == "security_error"


def test_error_response_falls_back_for_invalid_mode_strings() -> None:
    response = build_error_response(
        request_id="req-1",
        workspace_root="/workspace/demo",
        mode="bad",
        safety_mode="bad",
        code="security_error",
        message="Unsafe path",
    )

    assert response.status == "error"
    assert response.mode == ValidationMode.STANDARD
    assert response.safety_mode == SafetyMode.READ_ONLY
    assert response.blocking_errors[0].code == "security_error"
