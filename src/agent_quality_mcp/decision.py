"""Internal decision contract for Phase 2 patch validation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_quality_mcp.models import (
    AgentQualityBaseModel,
    Diagnostic,
    DiagnosticSeverity,
    ExecutionMetadata,
    RiskScore,
    ValidationMode,
)

SUPPORTED_DECISION_TOOLS = ("uv", "ruff", "pyright")
REQUIRED_TOOLS_BY_MODE = {
    ValidationMode.QUICK: ("ruff", "pyright"),
    ValidationMode.STANDARD: ("uv", "ruff", "pyright"),
    ValidationMode.STRICT: ("uv", "ruff", "pyright"),
}


class PatchDecision(StrEnum):
    APPLY_PATCH = "apply_patch"
    REVISE_PATCH = "revise_patch"
    FIX_TOOLING = "fix_tooling"
    REQUEST_HUMAN_REVIEW = "request_human_review"
    REJECT_REQUEST = "reject_request"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BlockerKind(StrEnum):
    REQUEST = "request"
    SECURITY = "security"
    PATCH = "patch"
    QUALITY = "quality"
    TYPE = "type"
    TOOLING = "tooling"
    TIMEOUT = "timeout"
    DEPENDENCY = "dependency"
    HUMAN_REVIEW = "human_review"


class BlockerFixability(StrEnum):
    AGENT_FIXABLE = "agent_fixable"
    TOOLING_FIXABLE = "tooling_fixable"
    HUMAN_REVIEW = "human_review"
    NOT_FIXABLE = "not_fixable"


class DecisionBlocker(AgentQualityBaseModel):
    id: str
    kind: BlockerKind
    severity: DiagnosticSeverity
    title: str
    details: str
    files: list[str] = Field(default_factory=list)
    related_diagnostic_ids: list[str] = Field(default_factory=list)
    first_evidence: str | None = None
    count: int = Field(default=1, ge=1)
    fixability: BlockerFixability


class RequiredCheckOutcome(AgentQualityBaseModel):
    tool: str
    required: bool
    completed: bool
    reason: str


class Confidence(AgentQualityBaseModel):
    score: int = Field(ge=0, le=100)
    level: ConfidenceLevel
    rationale: list[str] = Field(default_factory=list)
    factors: list[str] = Field(default_factory=list)


class DecisionSummary(AgentQualityBaseModel):
    title: str
    detail: str
    blocker_count: int = 0
    warning_count: int = 0


class InternalDecisionResult(AgentQualityBaseModel):
    decision: PatchDecision
    confidence: Confidence
    summary: DecisionSummary
    blockers: list[DecisionBlocker] = Field(default_factory=list)
    required_checks: list[RequiredCheckOutcome] = Field(default_factory=list)


def build_required_checks(
    mode: ValidationMode,
    execution: ExecutionMetadata,
    diagnostics: list[Diagnostic],
) -> list[RequiredCheckOutcome]:
    """Return required-check completion state for the validation mode."""

    required_tools = set(REQUIRED_TOOLS_BY_MODE[mode])
    attempted = {record.command for record in execution.commands}
    timed_out = {record.command for record in execution.commands if record.timed_out}
    unavailable = {
        tool
        for diagnostic in diagnostics
        if diagnostic.code in {"tool_missing", "tool_unavailable"}
        and isinstance((tool := diagnostic.metadata.get("tool")), str)
    }

    outcomes: list[RequiredCheckOutcome] = []
    for tool in SUPPORTED_DECISION_TOOLS:
        required = tool in required_tools
        completed = (
            required
            and tool in attempted
            and tool not in timed_out
            and tool not in unavailable
            and execution.tool_availability.get(tool) is not False
        )
        if not required:
            reason = f"{tool} is optional in {mode.value} mode"
        elif completed:
            reason = f"{tool} completed"
        elif tool in unavailable or execution.tool_availability.get(tool) is False:
            reason = f"{tool} is unavailable"
        elif tool in timed_out:
            reason = f"{tool} timed out"
        else:
            reason = f"{tool} did not run"
        outcomes.append(
            RequiredCheckOutcome(
                tool=tool,
                required=required,
                completed=completed,
                reason=reason,
            )
        )
    return outcomes


def decide_validation(
    *,
    mode: ValidationMode,
    blockers: list[DecisionBlocker],
    diagnostics: list[Diagnostic],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    required_checks: list[RequiredCheckOutcome],
) -> InternalDecisionResult:
    """Compute deterministic decision, confidence, and summary."""

    decision = _decision_from_facts(blockers, execution, required_checks)
    confidence = _confidence_for_decision(
        mode=mode,
        decision=decision,
        blockers=blockers,
        risk_score=risk_score,
        execution=execution,
        required_checks=required_checks,
    )
    summary = _summary_for_decision(decision, blockers, diagnostics, required_checks)
    return InternalDecisionResult(
        decision=decision,
        confidence=confidence,
        summary=summary,
        blockers=blockers,
        required_checks=required_checks,
    )


def _decision_from_facts(
    blockers: list[DecisionBlocker],
    execution: ExecutionMetadata,
    required_checks: list[RequiredCheckOutcome],
) -> PatchDecision:
    kinds = {blocker.kind for blocker in blockers}
    if BlockerKind.REQUEST in kinds or BlockerKind.SECURITY in kinds:
        return PatchDecision.REJECT_REQUEST
    if BlockerKind.PATCH in kinds:
        return PatchDecision.REVISE_PATCH
    if (
        execution.timed_out
        or BlockerKind.TIMEOUT in kinds
        or BlockerKind.HUMAN_REVIEW in kinds
    ):
        return PatchDecision.REQUEST_HUMAN_REVIEW
    if _missing_required_checks(required_checks):
        return PatchDecision.FIX_TOOLING
    if BlockerKind.TOOLING in kinds:
        return PatchDecision.FIX_TOOLING
    if kinds & {BlockerKind.QUALITY, BlockerKind.TYPE, BlockerKind.DEPENDENCY}:
        return PatchDecision.REVISE_PATCH
    return PatchDecision.APPLY_PATCH


def _confidence_for_decision(
    *,
    mode: ValidationMode,
    decision: PatchDecision,
    blockers: list[DecisionBlocker],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    required_checks: list[RequiredCheckOutcome],
) -> Confidence:
    score = 95
    factors: list[str] = []
    if mode == ValidationMode.QUICK:
        score -= 15
        factors.append("quick mode has reduced validation depth")
    if blockers:
        score -= min(35, len(blockers) * 10)
        factors.append(f"blockers: {len(blockers)}")
    if risk_score.score:
        score -= min(30, risk_score.score // 3)
        factors.extend(risk_score.factors)
    if _missing_required_checks(required_checks):
        score -= 30
        factors.append("required checks did not complete")
    if execution.timed_out or any(record.timed_out for record in execution.commands):
        score -= 35
        factors.append("validation timed out")
    if execution.output_truncated:
        score -= 10
        factors.append("command output was truncated")
    if decision == PatchDecision.REJECT_REQUEST:
        score = max(score, 75)
        factors.append("unsafe or invalid requests are routed deterministically")
    score = max(0, min(100, score))
    return Confidence(
        score=score,
        level=_confidence_level(score),
        rationale=_rationale_for_decision(decision),
        factors=factors,
    )


def _summary_for_decision(
    decision: PatchDecision,
    blockers: list[DecisionBlocker],
    diagnostics: list[Diagnostic],
    required_checks: list[RequiredCheckOutcome],
) -> DecisionSummary:
    title_by_decision = {
        PatchDecision.APPLY_PATCH: "Patch validation passed",
        PatchDecision.REVISE_PATCH: "Patch needs revision",
        PatchDecision.FIX_TOOLING: "Validation tooling needs attention",
        PatchDecision.REQUEST_HUMAN_REVIEW: "Validation needs human review",
        PatchDecision.REJECT_REQUEST: "Validation request rejected",
    }
    if _missing_required_checks(required_checks):
        detail = "One or more required checks did not complete."
    elif blockers:
        detail = blockers[0].details
    else:
        detail = "All required checks completed without blockers."
    warning_count = sum(
        1 for diagnostic in diagnostics if diagnostic.severity == DiagnosticSeverity.WARNING
    )
    return DecisionSummary(
        title=title_by_decision[decision],
        detail=detail,
        blocker_count=len(blockers),
        warning_count=warning_count,
    )


def _missing_required_checks(required_checks: list[RequiredCheckOutcome]) -> bool:
    return any(check.required and not check.completed for check in required_checks)


def _confidence_level(score: int) -> ConfidenceLevel:
    if score >= 75:
        return ConfidenceLevel.HIGH
    if score >= 40:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _rationale_for_decision(decision: PatchDecision) -> list[str]:
    rationale = {
        PatchDecision.APPLY_PATCH: "No blockers remain after required checks.",
        PatchDecision.REVISE_PATCH: "Patch-attributable validation blockers were found.",
        PatchDecision.FIX_TOOLING: "Required validation tooling did not complete reliably.",
        PatchDecision.REQUEST_HUMAN_REVIEW: "Validation was incomplete or ambiguous.",
        PatchDecision.REJECT_REQUEST: "Request is invalid, unsafe, or unsupported.",
    }
    return [rationale[decision]]
