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
    raise NotImplementedError("Implemented in Task 2")


def decide_validation(
    *,
    mode: ValidationMode,
    blockers: list[DecisionBlocker],
    diagnostics: list[Diagnostic],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    required_checks: list[RequiredCheckOutcome],
) -> InternalDecisionResult:
    raise NotImplementedError("Implemented in Task 2")
