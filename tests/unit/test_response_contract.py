from __future__ import annotations

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.models import (
    AuditSummary,
    CommandExecutionRecord,
    DiagnosticSeverity,
    ExecutionMetadata,
    RiskLevel,
    RiskScore,
    SafetyMode,
    ValidationMode,
)
from agent_quality_mcp.response import (
    build_error_response,
    build_validate_patch_response,
)

WORKSPACE_ROOT = "/tmp/demo"  # noqa: S108 - Phase 2 contract uses this sample path.
INVALID_REQUEST_MESSAGE = "Invalid validate_patch request"


def _record(command: str) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, "--version"],
        cwd=WORKSPACE_ROOT,
        duration_ms=10,
        exit_code=0,
    )


def test_build_error_response_uses_phase_2_contract() -> None:
    response = build_error_response(
        request_id="req-1",
        workspace_root=WORKSPACE_ROOT,
        mode="quick",
        safety_mode="read_only",
        code="invalid_request",
        message=INVALID_REQUEST_MESSAGE,
    )

    payload = response.model_dump(mode="json")

    assert payload["decision"] == "reject_request"
    assert payload["workspace_root"] == WORKSPACE_ROOT
    assert payload["mode"] == "quick"
    assert payload["safety_mode"] == "read_only"
    assert payload["confidence"]["level"] == "high"
    assert payload["summary"]["detail"] == INVALID_REQUEST_MESSAGE
    assert payload["blockers"][0]["kind"] == "request"
    assert payload["next_actions"][0]["kind"] == "stop"
    assert "status" not in payload
    assert "blocking_errors" not in payload


def test_build_error_response_defaults_invalid_mode_and_safety() -> None:
    response = build_error_response(
        request_id="req-1",
        workspace_root=WORKSPACE_ROOT,
        mode="unknown",
        safety_mode="unsafe",
        code="invalid_request",
        message=INVALID_REQUEST_MESSAGE,
    )

    payload = response.model_dump(mode="json")

    assert payload["mode"] == "standard"
    assert payload["safety_mode"] == "read_only"


def test_build_validate_patch_response_returns_apply_patch_for_clean_quick_run() -> None:
    execution = ExecutionMetadata(
        commands=[_record("ruff"), _record("pyright")],
        tool_availability={"ruff": True, "pyright": True},
    )

    response = build_validate_patch_response(
        request_id="req-1",
        workspace_root=WORKSPACE_ROOT,
        mode=ValidationMode.QUICK,
        safety_mode=SafetyMode.READ_ONLY,
        diagnostics=[],
        compressed_groups=[],
        risk_score=RiskScore(score=0, level=RiskLevel.LOW),
        execution=execution,
        audit=AuditSummary(),
        safe_fixes=[],
        real_workspace_modified=False,
        shadow_workspace_used=True,
    )

    assert response.decision == "apply_patch"
    assert response.blockers == []
    assert response.fix_plan is None
    assert response.evidence.command_outcomes == [
        {
            "command": "ruff",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
        {
            "command": "pyright",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
    ]
    assert response.evidence.tool_availability == {"ruff": True, "pyright": True}
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["uv"]["required"] is False
    assert required_checks["uv"]["completed"] is False
    assert required_checks["ruff"]["required"] is True
    assert required_checks["ruff"]["completed"] is True
    assert required_checks["pyright"]["required"] is True
    assert required_checks["pyright"]["completed"] is True
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is True


def test_build_validate_patch_response_rejects_blocking_security_diagnostic() -> None:
    diagnostic = diagnostic_from_message(
        source="security",
        code="security_error",
        message="Unsafe path",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )

    response = build_validate_patch_response(
        request_id="req-1",
        workspace_root=WORKSPACE_ROOT,
        mode=ValidationMode.STANDARD,
        safety_mode=SafetyMode.READ_ONLY,
        diagnostics=[diagnostic],
        compressed_groups=[],
        risk_score=RiskScore(score=100, level=RiskLevel.CRITICAL),
        execution=ExecutionMetadata(),
        audit=AuditSummary(),
        safe_fixes=[],
        real_workspace_modified=False,
        shadow_workspace_used=False,
    )

    assert response.decision == "reject_request"
    assert response.blockers[0].kind == "security"
    assert response.next_actions[0].kind == "stop"
