from pydantic import ValidationError

import agent_quality_mcp.models as models_module
from agent_quality_mcp.models import (
    AgentQualityConfig,
    Diagnostic,
    DiagnosticRange,
    DiagnosticSeverity,
    SafetyMode,
    ValidatePatchRequest,
)


def test_validate_patch_request_generates_request_id() -> None:
    request = ValidatePatchRequest(workspace_root="/workspace/demo", changed_files=["pkg/app.py"])

    assert request.request_id
    assert request.mode is None
    assert request.safety_mode is None


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


def test_config_rejects_unsafe_secret_redaction_patterns() -> None:
    try:
        AgentQualityConfig(secret_redaction_patterns=["(a|aa)+$"])
    except ValidationError as exc:
        assert "secret_redaction_patterns" in str(exc)
    else:
        raise AssertionError("unsafe secret_redaction_patterns should fail validation")


def test_config_rejects_optional_dot_wildcard_secret_redaction_pattern() -> None:
    try:
        AgentQualityConfig(secret_redaction_patterns=[r"prefix.?secret"])
    except ValidationError as exc:
        assert "secret_redaction_patterns" in str(exc)
    else:
        raise AssertionError("optional dot wildcard secret_redaction_patterns should fail")


def test_config_rejects_quantified_literal_secret_redaction_patterns() -> None:
    for pattern in ["a*a*a*b", "a?a?a?a", "sk-[A-Za-z0-9_-]+"]:
        try:
            AgentQualityConfig(secret_redaction_patterns=[pattern])
        except ValidationError as exc:
            assert "secret_redaction_patterns" in str(exc)
        else:
            raise AssertionError(f"{pattern!r} should fail validation")


def test_config_accepts_literal_secret_redaction_pattern() -> None:
    config = AgentQualityConfig(secret_redaction_patterns=["internal-secret-marker"])

    assert config.secret_redaction_patterns == ["internal-secret-marker"]


def test_config_rejects_empty_secret_redaction_pattern() -> None:
    try:
        AgentQualityConfig(secret_redaction_patterns=[""])
    except ValidationError as exc:
        assert "secret_redaction_patterns" in str(exc)
    else:
        raise AssertionError("empty secret_redaction_patterns should fail validation")


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


def test_models_do_not_export_stale_validate_patch_response_contract() -> None:
    assert not hasattr(models_module, "ValidatePatchResponse")
    assert not hasattr(models_module, "build_error_response")
