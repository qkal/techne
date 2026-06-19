"""Public Phase 2 response contract for validate_patch."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_quality_mcp.actions import (
    FixPlan,
    NextAction,
    build_fix_plan,
    build_next_actions,
)
from agent_quality_mcp.decision import (
    BlockerFixability,
    BlockerKind,
    Confidence,
    DecisionBlocker,
    DecisionSummary,
    InternalDecisionResult,
    build_required_checks,
    decide_validation,
)
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.grouping import group_diagnostics_for_decision
from agent_quality_mcp.models import (
    AgentQualityBaseModel,
    AuditSummary,
    Diagnostic,
    DiagnosticSeverity,
    ExecutionMetadata,
    RiskLevel,
    RiskScore,
    SafeFixPreview,
    SafetyMode,
    ValidationMode,
)


class ResponseEvidence(AgentQualityBaseModel):
    diagnostic_count: int = 0
    grouped_diagnostic_count: int = 0
    compressed_groups: list[dict[str, Any]] = Field(default_factory=list)
    command_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    tool_availability: dict[str, bool] = Field(default_factory=dict)
    required_checks: list[dict[str, Any]] = Field(default_factory=list)
    risk_score: RiskScore
    output_truncated: bool = False
    timed_out: bool = False
    real_workspace_modified: bool = False
    shadow_workspace_used: bool = False


class ValidatePatchResponse(AgentQualityBaseModel):
    request_id: str
    workspace_root: str
    mode: ValidationMode
    safety_mode: SafetyMode
    decision: str
    confidence: Confidence
    summary: DecisionSummary
    blockers: list[DecisionBlocker] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    fix_plan: FixPlan | None = None
    evidence: ResponseEvidence
    execution: ExecutionMetadata
    audit: AuditSummary


def build_error_response(
    *,
    request_id: str,
    workspace_root: str,
    mode: ValidationMode | str | None,
    safety_mode: SafetyMode | str | None,
    code: str,
    message: str,
) -> ValidatePatchResponse:
    normalized_mode = _validation_mode_or_default(mode)
    normalized_safety_mode = _safety_mode_or_default(safety_mode)
    diagnostic = diagnostic_from_message(
        source="system",
        code=code,
        message=message,
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )
    blocker = DecisionBlocker(
        id=f"request-{code}-{diagnostic.id[:8]}",
        kind=BlockerKind.REQUEST,
        severity=DiagnosticSeverity.BLOCKER,
        title="Request is invalid or unsupported",
        details=message,
        related_diagnostic_ids=[diagnostic.id],
        first_evidence=message,
        count=1,
        fixability=BlockerFixability.NOT_FIXABLE,
    )
    execution = ExecutionMetadata()
    risk_score = RiskScore(
        score=100,
        level=RiskLevel.CRITICAL,
        factors=[message],
    )
    required_checks = build_required_checks(normalized_mode, execution, [diagnostic])
    decision_result = decide_validation(
        mode=normalized_mode,
        blockers=[blocker],
        diagnostics=[diagnostic],
        risk_score=risk_score,
        execution=execution,
        required_checks=required_checks,
    )
    fix_plan = build_fix_plan(decision_result, safe_fixes=[], mode=normalized_mode)
    return _assemble_response(
        request_id=request_id,
        workspace_root=workspace_root,
        mode=normalized_mode,
        safety_mode=normalized_safety_mode,
        diagnostics=[diagnostic],
        compressed_groups=[],
        risk_score=risk_score,
        execution=execution,
        audit=AuditSummary(),
        decision_result=decision_result,
        fix_plan=fix_plan,
        real_workspace_modified=False,
        shadow_workspace_used=False,
    )


def build_validate_patch_response(
    *,
    request_id: str,
    workspace_root: str,
    mode: ValidationMode | str | None,
    safety_mode: SafetyMode | str | None,
    diagnostics: list[Diagnostic],
    compressed_groups: list[dict[str, Any]],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    audit: AuditSummary,
    safe_fixes: list[SafeFixPreview],
    real_workspace_modified: bool,
    shadow_workspace_used: bool,
) -> ValidatePatchResponse:
    normalized_mode = _validation_mode_or_default(mode)
    normalized_safety_mode = _safety_mode_or_default(safety_mode)
    blockers = group_diagnostics_for_decision(
        diagnostics,
        compressed_groups=compressed_groups,
    )
    required_checks = build_required_checks(normalized_mode, execution, diagnostics)
    decision_result = decide_validation(
        mode=normalized_mode,
        blockers=blockers,
        diagnostics=diagnostics,
        risk_score=risk_score,
        execution=execution,
        required_checks=required_checks,
    )
    fix_plan = build_fix_plan(
        decision_result,
        safe_fixes=safe_fixes,
        mode=normalized_mode,
    )
    return _assemble_response(
        request_id=request_id,
        workspace_root=workspace_root,
        mode=normalized_mode,
        safety_mode=normalized_safety_mode,
        diagnostics=diagnostics,
        compressed_groups=compressed_groups,
        risk_score=risk_score,
        execution=execution,
        audit=audit,
        decision_result=decision_result,
        fix_plan=fix_plan,
        real_workspace_modified=real_workspace_modified,
        shadow_workspace_used=shadow_workspace_used,
    )


def _assemble_response(
    *,
    request_id: str,
    workspace_root: str,
    mode: ValidationMode,
    safety_mode: SafetyMode,
    diagnostics: list[Diagnostic],
    compressed_groups: list[dict[str, Any]],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    audit: AuditSummary,
    decision_result: InternalDecisionResult,
    fix_plan: FixPlan | None,
    real_workspace_modified: bool,
    shadow_workspace_used: bool,
) -> ValidatePatchResponse:
    next_actions = build_next_actions(
        decision_result,
        mode=mode,
        fix_plan=fix_plan,
    )
    return ValidatePatchResponse(
        request_id=request_id,
        workspace_root=workspace_root,
        mode=mode,
        safety_mode=safety_mode,
        decision=decision_result.decision.value,
        confidence=decision_result.confidence,
        summary=decision_result.summary,
        blockers=decision_result.blockers,
        next_actions=next_actions,
        fix_plan=fix_plan,
        evidence=ResponseEvidence(
            diagnostic_count=len(diagnostics),
            grouped_diagnostic_count=len(decision_result.blockers),
            compressed_groups=compressed_groups,
            command_outcomes=[
                {
                    "command": record.command,
                    "exit_code": record.exit_code,
                    "timed_out": record.timed_out,
                    "stdout_truncated": record.stdout_truncated,
                    "stderr_truncated": record.stderr_truncated,
                }
                for record in execution.commands
            ],
            tool_availability=dict(execution.tool_availability),
            required_checks=[
                check.model_dump(mode="json")
                for check in decision_result.required_checks
            ],
            risk_score=risk_score,
            output_truncated=execution.output_truncated,
            timed_out=execution.timed_out,
            real_workspace_modified=real_workspace_modified,
            shadow_workspace_used=shadow_workspace_used,
        ),
        execution=execution,
        audit=audit,
    )


def _validation_mode_or_default(mode: ValidationMode | str | None) -> ValidationMode:
    if mode is None:
        return ValidationMode.STANDARD
    try:
        return ValidationMode(mode)
    except ValueError:
        return ValidationMode.STANDARD


def _safety_mode_or_default(safety_mode: SafetyMode | str | None) -> SafetyMode:
    if safety_mode is None:
        return SafetyMode.READ_ONLY
    try:
        return SafetyMode(safety_mode)
    except ValueError:
        return SafetyMode.READ_ONLY
