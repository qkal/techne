from __future__ import annotations

from agent_quality_mcp.decision import (
    BlockerFixability,
    BlockerKind,
    DecisionBlocker,
    PatchDecision,
    build_required_checks,
    decide_validation,
)
from agent_quality_mcp.models import (
    CommandExecutionRecord,
    DiagnosticSeverity,
    ExecutionMetadata,
    RiskLevel,
    RiskScore,
    ValidationMode,
)


def _record(command: str, *, timed_out: bool = False) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, "--version"],
        cwd="/workspace/demo",
        duration_ms=10,
        exit_code=None if timed_out else 0,
        timed_out=timed_out,
    )


def _blocker(kind: BlockerKind) -> DecisionBlocker:
    return DecisionBlocker(
        id=f"{kind.value}-1",
        kind=kind,
        severity=DiagnosticSeverity.BLOCKER,
        title=f"{kind.value} blocker",
        details="details",
        count=1,
        fixability=BlockerFixability.AGENT_FIXABLE,
    )


def test_required_check_matrix_marks_quick_uv_optional() -> None:
    checks = build_required_checks(
        ValidationMode.QUICK,
        ExecutionMetadata(commands=[_record("ruff"), _record("pyright")]),
        [],
    )

    by_tool = {check.tool: check for check in checks}
    assert by_tool["uv"].required is False
    assert by_tool["ruff"].required is True
    assert by_tool["pyright"].required is True
    assert by_tool["ruff"].completed is True
    assert by_tool["pyright"].completed is True


def test_decide_validation_rejects_security_before_quality() -> None:
    result = decide_validation(
        mode=ValidationMode.STANDARD,
        blockers=[_blocker(BlockerKind.QUALITY), _blocker(BlockerKind.SECURITY)],
        diagnostics=[],
        risk_score=RiskScore(score=100, level=RiskLevel.CRITICAL),
        execution=ExecutionMetadata(commands=[_record("uv"), _record("ruff"), _record("pyright")]),
        required_checks=[],
    )

    assert result.decision == PatchDecision.REJECT_REQUEST
    assert result.confidence.level == "high"
    assert result.summary.blocker_count == 2


def test_decide_validation_routes_missing_required_check_to_fix_tooling() -> None:
    execution = ExecutionMetadata(
        commands=[_record("uv"), _record("ruff")],
        tool_availability={"uv": True, "ruff": True, "pyright": False},
    )
    checks = build_required_checks(ValidationMode.STANDARD, execution, [])

    result = decide_validation(
        mode=ValidationMode.STANDARD,
        blockers=[],
        diagnostics=[],
        risk_score=RiskScore(score=30, level=RiskLevel.MEDIUM),
        execution=execution,
        required_checks=checks,
    )

    assert result.decision == PatchDecision.FIX_TOOLING
    assert result.confidence.level == "medium"
    assert "required checks did not complete" in result.summary.detail


def test_decide_validation_routes_timeout_to_human_review_before_tooling() -> None:
    execution = ExecutionMetadata(
        commands=[_record("uv"), _record("ruff", timed_out=True)],
        timed_out=True,
    )
    checks = build_required_checks(ValidationMode.STANDARD, execution, [])

    result = decide_validation(
        mode=ValidationMode.STANDARD,
        blockers=[],
        diagnostics=[],
        risk_score=RiskScore(score=0, level=RiskLevel.LOW),
        execution=execution,
        required_checks=checks,
    )

    assert result.decision == PatchDecision.REQUEST_HUMAN_REVIEW
    assert result.confidence.level == "low"


def test_decide_validation_allows_clean_quick_apply_patch() -> None:
    execution = ExecutionMetadata(commands=[_record("ruff"), _record("pyright")])
    checks = build_required_checks(ValidationMode.QUICK, execution, [])

    result = decide_validation(
        mode=ValidationMode.QUICK,
        blockers=[],
        diagnostics=[],
        risk_score=RiskScore(score=0, level=RiskLevel.LOW),
        execution=execution,
        required_checks=checks,
    )

    assert result.decision == PatchDecision.APPLY_PATCH
    assert result.confidence.level in {"medium", "high"}
    assert result.summary.title == "Patch validation passed"
