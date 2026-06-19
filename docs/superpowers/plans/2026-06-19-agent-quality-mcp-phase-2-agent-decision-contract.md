# Agent Quality MCP Phase 2 Decision Engine Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Phase 1 `validate_patch` response with the Phase 2 agent decision contract through a decision-engine-first sequence.

**Architecture:** Keep the existing shadow-workspace service and CLI adapters as fact producers. First add internal grouping, decision, and action-generation modules while the public response remains Phase 1-compatible. Then add Phase 2 response assembly and switch the MCP service/tool boundary in one final slice.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, FastMCP, existing uv/Ruff/Pyright CLI adapters, existing shadow workspace and diagnostic infrastructure.

---

## Source Specs

Use these specs as the implementation contract:

- `docs/superpowers/specs/2026-06-19-agent-quality-mcp-phase-2-agent-decision-contract-design.md`
- `docs/superpowers/specs/2026-06-19-agent-quality-mcp-phase-2-decision-engine-split-design.md`

The split design supersedes the previous schema-first plan. This file replaces that stale plan.

## Scope Check

This plan covers one subsystem: the `validate_patch` decision contract and the response assembly layer that exposes it. `inspect_workspace` remains source-compatible. No Phase 1 compatibility flag, dual response mode, broad project-shape detection, LSP orchestration, or real-workspace mutation is included.

## Required Check Matrix

Define this matrix before coding decision behavior:

| Mode | Required checks | Optional checks | Missing required route | Confidence rule |
| --- | --- | --- | --- | --- |
| `quick` | Ruff and Pyright scoped to safe changed Python files | uv availability/version evidence | `fix_tooling` if a required tool is unavailable; `request_human_review` if a required command timed out | `apply_patch` may be medium confidence when all required checks complete cleanly |
| `standard` | uv availability, Ruff scoped to safe changed Python files, Pyright scoped to safe changed Python files | uv lock check when no `pyproject.toml` exists | `fix_tooling` for unavailable required tools or broken project metadata; `request_human_review` for timeouts | `apply_patch` should normally be high confidence |
| `strict` | uv availability, Ruff project-wide, Pyright project-wide, uv lock check when `pyproject.toml` exists | none for Python quality checks | `fix_tooling` for unavailable required tools or broken project metadata; `request_human_review` for incomplete or timed-out validation | incomplete validation should not produce `apply_patch` |

The first implementation may model required checks at the tool level. If later code needs finer-grained uv lock-check evidence, add it as evidence without changing the public decision values.

## File Structure

- Create: `src/agent_quality_mcp/decision.py`
  - Owns internal decision enums, blocker models, confidence models, required-check evaluation, and final decision precedence.
- Create: `src/agent_quality_mcp/grouping.py`
  - Converts normalized diagnostics and compression metadata into ranked `DecisionBlocker` clusters.
- Create: `src/agent_quality_mcp/actions.py`
  - Builds ordered `NextAction` values and optional `FixPlan` guidance from the internal decision result.
- Create: `src/agent_quality_mcp/response.py`
  - Added only after the decision engine is tested. Defines the public Phase 2 response models and maps internal decision results into the public payload.
- Modify: `src/agent_quality_mcp/service.py`
  - Milestone 1: unchanged public response.
  - Milestone 3: call `response.build_validate_patch_response()` from `_response_from_parts()` and `_final_response()`.
- Modify: `src/agent_quality_mcp/tools.py`
  - Milestone 3 only: import `build_error_response` from `response.py`.
- Modify: `src/agent_quality_mcp/models.py`
  - Keep Phase 1 `ValidatePatchResponse` and `build_error_response()` through Milestone 2.
  - Milestone 3 may leave them unused for compatibility inside the codebase, but tests must stop relying on them for `validate_patch`.
- Create: `tests/unit/test_grouping.py`
- Create: `tests/unit/test_decision.py`
- Create: `tests/unit/test_actions.py`
- Create: `tests/unit/test_response_contract.py`
- Modify: `tests/unit/test_service.py`
- Modify: `tests/unit/test_tools_server.py`
- Modify: `tests/unit/test_models.py`
- Modify: `tests/integration/test_validate_patch_demo.py`
- Modify: `README.md`

## Milestone 1: Internal Decision Engine

Milestone 1 must not change the public `validate_patch` payload. Existing service and MCP tool-wrapper tests should remain Phase 1-compatible until Milestone 3.

### Task 1: Internal Decision Types And Diagnostic Grouping

**Files:**
- Create: `src/agent_quality_mcp/decision.py`
- Create: `src/agent_quality_mcp/grouping.py`
- Create: `tests/unit/test_grouping.py`

- [ ] **Step 1: Write failing grouping tests**

Create `tests/unit/test_grouping.py`:

```python
from __future__ import annotations

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.decision import BlockerFixability, BlockerKind
from agent_quality_mcp.grouping import group_diagnostics_for_decision
from agent_quality_mcp.models import DiagnosticSeverity


def _diagnostic(source: str, code: str, message: str, *, file: str | None = None):
    return diagnostic_from_message(
        source=source,  # type: ignore[arg-type]
        code=code,
        message=message,
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file=file,
    )


def test_group_diagnostics_ranks_security_before_quality() -> None:
    security = diagnostic_from_message(
        source="security",
        code="security_error",
        message="Unsafe path",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )
    ruff = _diagnostic("ruff", "F401", "Unused import", file="pkg/app.py")

    blockers = group_diagnostics_for_decision([ruff, security], compressed_groups=[])

    assert [blocker.kind for blocker in blockers] == [
        BlockerKind.SECURITY,
        BlockerKind.QUALITY,
    ]
    assert blockers[0].fixability == BlockerFixability.NOT_FIXABLE
    assert blockers[1].files == ["pkg/app.py"]


def test_group_diagnostics_combines_duplicate_ruff_findings() -> None:
    first = _diagnostic("ruff", "F401", "Unused import", file="pkg/app.py")
    second = _diagnostic("ruff", "F401", "Unused import", file="pkg/app.py")

    blockers = group_diagnostics_for_decision([first, second], compressed_groups=[])

    assert len(blockers) == 1
    assert blockers[0].kind == BlockerKind.QUALITY
    assert blockers[0].count == 2
    assert blockers[0].related_diagnostic_ids == [first.id, second.id]


def test_group_diagnostics_maps_known_sources_to_blocker_kinds() -> None:
    diagnostics = [
        _diagnostic("patch", "patch_apply_error", "Patch failed"),
        _diagnostic("pyright", "reportAssignmentType", "Bad type", file="pkg/app.py"),
        _diagnostic("uv", "command_failed", "Lock check failed"),
        diagnostic_from_message(
            source="system",
            code="tool_unavailable",
            message="ruff missing",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"tool": "ruff"},
        ),
        _diagnostic("ruff", "timeout", "ruff command timed out"),
    ]

    blockers = group_diagnostics_for_decision(diagnostics, compressed_groups=[])

    assert [blocker.kind for blocker in blockers] == [
        BlockerKind.PATCH,
        BlockerKind.TOOLING,
        BlockerKind.TIMEOUT,
        BlockerKind.TYPE,
        BlockerKind.DEPENDENCY,
    ]
```

- [ ] **Step 2: Run grouping tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_grouping.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `agent_quality_mcp.decision` or `agent_quality_mcp.grouping`.

- [ ] **Step 3: Create internal decision types**

Create `src/agent_quality_mcp/decision.py`:

```python
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
```

- [ ] **Step 4: Create diagnostic grouping**

Create `src/agent_quality_mcp/grouping.py`:

```python
"""Group diagnostics into deterministic decision blockers."""

from __future__ import annotations

from typing import Any

from agent_quality_mcp.decision import BlockerFixability, BlockerKind, DecisionBlocker
from agent_quality_mcp.models import Diagnostic, DiagnosticSeverity

BLOCKER_KIND_ORDER = {
    BlockerKind.REQUEST: 0,
    BlockerKind.SECURITY: 1,
    BlockerKind.PATCH: 2,
    BlockerKind.TOOLING: 3,
    BlockerKind.TIMEOUT: 4,
    BlockerKind.TYPE: 5,
    BlockerKind.QUALITY: 6,
    BlockerKind.DEPENDENCY: 7,
    BlockerKind.HUMAN_REVIEW: 8,
}


def group_diagnostics_for_decision(
    diagnostics: list[Diagnostic],
    *,
    compressed_groups: list[dict[str, Any]],
) -> list[DecisionBlocker]:
    """Return ranked blocker clusters from normalized diagnostics."""

    del compressed_groups
    grouped: dict[tuple[BlockerKind, str, str | None], list[Diagnostic]] = {}
    for diagnostic in diagnostics:
        kind = _kind_for_diagnostic(diagnostic)
        key = (kind, diagnostic.code, diagnostic.file)
        grouped.setdefault(key, []).append(diagnostic)

    blockers = [_blocker_from_group(kind, items) for (kind, _, _), items in grouped.items()]
    blockers.sort(
        key=lambda blocker: (
            BLOCKER_KIND_ORDER[blocker.kind],
            blocker.files[0] if blocker.files else "",
            blocker.title,
            blocker.id,
        )
    )
    return blockers


def _blocker_from_group(kind: BlockerKind, diagnostics: list[Diagnostic]) -> DecisionBlocker:
    first = diagnostics[0]
    files = _unique_sorted_files(diagnostics)
    return DecisionBlocker(
        id=f"{kind.value}-{first.code}-{first.id[:8]}",
        kind=kind,
        severity=_severity_for_group(diagnostics),
        title=_title_for_kind(kind, first),
        details=first.message,
        files=files,
        related_diagnostic_ids=_unique_ids(diagnostics),
        first_evidence=first.message,
        count=len(diagnostics),
        fixability=_fixability_for_kind(kind),
    )


def _kind_for_diagnostic(diagnostic: Diagnostic) -> BlockerKind:
    if diagnostic.source == "security":
        return BlockerKind.SECURITY
    if diagnostic.source == "patch":
        return BlockerKind.PATCH
    if diagnostic.code == "timeout":
        return BlockerKind.TIMEOUT
    if diagnostic.source == "system" and diagnostic.code in {"tool_missing", "tool_unavailable"}:
        return BlockerKind.TOOLING
    if diagnostic.source == "system" and diagnostic.code in {
        "invalid_request",
        "apply_safe_fixes_not_supported",
    }:
        return BlockerKind.REQUEST
    if diagnostic.source == "pyright":
        return BlockerKind.TYPE
    if diagnostic.source == "ruff":
        return BlockerKind.QUALITY
    if diagnostic.source == "uv":
        return BlockerKind.DEPENDENCY
    if diagnostic.source == "workspace":
        return BlockerKind.SECURITY
    return BlockerKind.HUMAN_REVIEW


def _severity_for_group(diagnostics: list[Diagnostic]) -> DiagnosticSeverity:
    if any(item.severity == DiagnosticSeverity.BLOCKER or item.is_blocking for item in diagnostics):
        return DiagnosticSeverity.BLOCKER
    if any(item.severity == DiagnosticSeverity.ERROR for item in diagnostics):
        return DiagnosticSeverity.ERROR
    if any(item.severity == DiagnosticSeverity.WARNING for item in diagnostics):
        return DiagnosticSeverity.WARNING
    return DiagnosticSeverity.INFO


def _title_for_kind(kind: BlockerKind, diagnostic: Diagnostic) -> str:
    titles = {
        BlockerKind.REQUEST: "Request is invalid or unsupported",
        BlockerKind.SECURITY: "Request failed security validation",
        BlockerKind.PATCH: "Patch could not be applied",
        BlockerKind.QUALITY: "Ruff reported patch issues",
        BlockerKind.TYPE: "Pyright reported type issues",
        BlockerKind.TOOLING: "Required tooling is unavailable",
        BlockerKind.TIMEOUT: "Validation timed out",
        BlockerKind.DEPENDENCY: "Dependency validation reported issues",
        BlockerKind.HUMAN_REVIEW: "Validation needs human review",
    }
    if diagnostic.file:
        return f"{titles[kind]} in {diagnostic.file}"
    return titles[kind]


def _fixability_for_kind(kind: BlockerKind) -> BlockerFixability:
    if kind in {BlockerKind.PATCH, BlockerKind.QUALITY, BlockerKind.TYPE, BlockerKind.DEPENDENCY}:
        return BlockerFixability.AGENT_FIXABLE
    if kind == BlockerKind.TOOLING:
        return BlockerFixability.TOOLING_FIXABLE
    if kind in {BlockerKind.TIMEOUT, BlockerKind.HUMAN_REVIEW}:
        return BlockerFixability.HUMAN_REVIEW
    return BlockerFixability.NOT_FIXABLE


def _unique_sorted_files(diagnostics: list[Diagnostic]) -> list[str]:
    return sorted({diagnostic.file for diagnostic in diagnostics if diagnostic.file})


def _unique_ids(diagnostics: list[Diagnostic]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for diagnostic in diagnostics:
        if diagnostic.id in seen:
            continue
        seen.add(diagnostic.id)
        ids.append(diagnostic.id)
    return ids
```

- [ ] **Step 5: Run grouping tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_grouping.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit grouping and internal types**

Run:

```bash
git add src/agent_quality_mcp/decision.py src/agent_quality_mcp/grouping.py tests/unit/test_grouping.py
git commit -m "feat: add phase 2 diagnostic grouping"
```

### Task 2: Required Checks And Decision Precedence

**Files:**
- Modify: `src/agent_quality_mcp/decision.py`
- Create: `tests/unit/test_decision.py`

- [ ] **Step 1: Write failing decision tests**

Create `tests/unit/test_decision.py`:

```python
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
        cwd="/tmp/demo",
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
```

- [ ] **Step 2: Run decision tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_decision.py -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement required checks and decision precedence**

Replace the placeholder functions at the bottom of `src/agent_quality_mcp/decision.py` with:

```python
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
        str(diagnostic.metadata.get("tool"))
        for diagnostic in diagnostics
        if diagnostic.code in {"tool_missing", "tool_unavailable"}
        and isinstance(diagnostic.metadata.get("tool"), str)
    }

    outcomes: list[RequiredCheckOutcome] = []
    for tool in SUPPORTED_DECISION_TOOLS:
        required = tool in required_tools
        completed = required and tool in attempted and tool not in timed_out and tool not in unavailable
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
    if execution.timed_out or BlockerKind.TIMEOUT in kinds or BlockerKind.HUMAN_REVIEW in kinds:
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
```

- [ ] **Step 4: Run decision and grouping tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_grouping.py tests/unit/test_decision.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit decision logic**

Run:

```bash
git add src/agent_quality_mcp/decision.py tests/unit/test_decision.py
git commit -m "feat: add phase 2 decision precedence"
```

### Task 3: Next Actions And Fix Plans

**Files:**
- Create: `src/agent_quality_mcp/actions.py`
- Create: `tests/unit/test_actions.py`

- [ ] **Step 1: Write failing action tests**

Create `tests/unit/test_actions.py`:

```python
from __future__ import annotations

from agent_quality_mcp.actions import build_fix_plan, build_next_actions
from agent_quality_mcp.decision import (
    BlockerFixability,
    BlockerKind,
    Confidence,
    ConfidenceLevel,
    DecisionBlocker,
    DecisionSummary,
    InternalDecisionResult,
    PatchDecision,
)
from agent_quality_mcp.models import DiagnosticSeverity, SafeFixPreview, ValidationMode


def _result(decision: PatchDecision, blocker: DecisionBlocker) -> InternalDecisionResult:
    return InternalDecisionResult(
        decision=decision,
        confidence=Confidence(
            score=80,
            level=ConfidenceLevel.HIGH,
            rationale=["test"],
            factors=[],
        ),
        summary=DecisionSummary(title="title", detail="detail", blocker_count=1),
        blockers=[blocker],
        required_checks=[],
    )


def _blocker(kind: BlockerKind, fixability: BlockerFixability) -> DecisionBlocker:
    return DecisionBlocker(
        id=f"{kind.value}-1",
        kind=kind,
        severity=DiagnosticSeverity.WARNING,
        title="blocker",
        details="details",
        files=["pkg/app.py"],
        related_diagnostic_ids=["diag-1"],
        count=1,
        fixability=fixability,
    )


def test_build_next_actions_for_revise_patch_includes_edit_then_rerun() -> None:
    blocker = _blocker(BlockerKind.QUALITY, BlockerFixability.AGENT_FIXABLE)
    result = _result(PatchDecision.REVISE_PATCH, blocker)
    fix_plan = build_fix_plan(result, safe_fixes=[], mode=ValidationMode.STANDARD)

    actions = build_next_actions(result, mode=ValidationMode.STANDARD, fix_plan=fix_plan)

    assert [action.kind for action in actions] == ["edit", "rerun"]
    assert actions[0].safe_to_run is False
    assert actions[1].command == ["validate_patch", "--mode", "standard"]


def test_build_next_actions_for_tooling_uses_allowlisted_version_command() -> None:
    blocker = _blocker(BlockerKind.TOOLING, BlockerFixability.TOOLING_FIXABLE)
    blocker = blocker.model_copy(update={"details": "ruff is unavailable"})
    result = _result(PatchDecision.FIX_TOOLING, blocker)

    actions = build_next_actions(result, mode=ValidationMode.STANDARD, fix_plan=None)

    assert actions[0].kind == "fix_tooling"
    assert actions[0].safe_to_run is True
    assert actions[0].command == ["ruff", "--version"]


def test_build_next_actions_for_reject_request_stops() -> None:
    blocker = _blocker(BlockerKind.SECURITY, BlockerFixability.NOT_FIXABLE)
    result = _result(PatchDecision.REJECT_REQUEST, blocker)

    actions = build_next_actions(result, mode=ValidationMode.STANDARD, fix_plan=None)

    assert len(actions) == 1
    assert actions[0].kind == "stop"
    assert actions[0].requires_human is True
    assert actions[0].command is None


def test_build_fix_plan_includes_safe_fix_previews_only_for_revise_patch() -> None:
    blocker = _blocker(BlockerKind.QUALITY, BlockerFixability.AGENT_FIXABLE)
    result = _result(PatchDecision.REVISE_PATCH, blocker)
    preview = SafeFixPreview(
        tool="ruff",
        description="Ruff safe-fix diff preview",
        files=["pkg/app.py"],
        diff_preview="--- a/pkg/app.py\n+++ b/pkg/app.py\n",
        is_safe=True,
        requires_human_review=True,
    )

    fix_plan = build_fix_plan(result, safe_fixes=[preview], mode=ValidationMode.QUICK)

    assert fix_plan is not None
    assert fix_plan.target_files == ["pkg/app.py"]
    assert fix_plan.safe_fix_previews == [preview]
    assert fix_plan.rerun_hint == "Rerun validate_patch in quick mode after editing the patch."
```

- [ ] **Step 2: Run action tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_actions.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_quality_mcp.actions'`.

- [ ] **Step 3: Create action generation**

Create `src/agent_quality_mcp/actions.py`:

```python
"""Next-action and fix-plan generation for Phase 2 decisions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_quality_mcp.decision import (
    BlockerFixability,
    BlockerKind,
    DecisionBlocker,
    InternalDecisionResult,
    PatchDecision,
)
from agent_quality_mcp.models import AgentQualityBaseModel, SafeFixPreview, ValidationMode


class NextActionKind(StrEnum):
    EDIT = "edit"
    RERUN = "rerun"
    INSPECT = "inspect"
    FIX_TOOLING = "fix_tooling"
    ASK_HUMAN = "ask_human"
    STOP = "stop"


class NextAction(AgentQualityBaseModel):
    id: str
    kind: NextActionKind
    priority: int = Field(ge=1)
    title: str
    details: str
    safe_to_run: bool
    requires_human: bool
    command: list[str] | None = None
    related_blocker_ids: list[str] = Field(default_factory=list)
    expected_result: str


class FixPlan(AgentQualityBaseModel):
    strategy: str
    steps: list[str]
    target_files: list[str] = Field(default_factory=list)
    safe_fix_previews: list[SafeFixPreview] = Field(default_factory=list)
    related_blocker_ids: list[str] = Field(default_factory=list)
    rerun_hint: str


def build_fix_plan(
    decision_result: InternalDecisionResult,
    *,
    safe_fixes: list[SafeFixPreview],
    mode: ValidationMode,
) -> FixPlan | None:
    """Build edit guidance for localizable revise-patch decisions."""

    if decision_result.decision != PatchDecision.REVISE_PATCH:
        return None
    fixable_blockers = [
        blocker
        for blocker in decision_result.blockers
        if blocker.fixability == BlockerFixability.AGENT_FIXABLE
    ]
    if not fixable_blockers:
        return None
    target_files = sorted({file for blocker in fixable_blockers for file in blocker.files})
    return FixPlan(
        strategy="Revise the proposed patch to resolve the grouped validation blockers.",
        steps=[_step_for_blocker(blocker) for blocker in fixable_blockers],
        target_files=target_files,
        safe_fix_previews=safe_fixes,
        related_blocker_ids=[blocker.id for blocker in fixable_blockers],
        rerun_hint=f"Rerun validate_patch in {mode.value} mode after editing the patch.",
    )


def build_next_actions(
    decision_result: InternalDecisionResult,
    *,
    mode: ValidationMode,
    fix_plan: FixPlan | None,
) -> list[NextAction]:
    """Build ordered next actions from the internal decision."""

    decision = decision_result.decision
    blockers = decision_result.blockers
    if decision == PatchDecision.APPLY_PATCH:
        return [_apply_action()]
    if decision == PatchDecision.REVISE_PATCH:
        return _revision_actions(blockers, mode, fix_plan)
    if decision == PatchDecision.FIX_TOOLING:
        return [_tooling_action(blockers)]
    if decision == PatchDecision.REQUEST_HUMAN_REVIEW:
        return [_human_review_action(blockers)]
    return [_stop_action(blockers)]


def _apply_action() -> NextAction:
    return NextAction(
        id="apply-patch",
        kind=NextActionKind.RERUN,
        priority=1,
        title="Apply patch",
        details="Validation found no blockers in the completed required checks.",
        safe_to_run=False,
        requires_human=False,
        related_blocker_ids=[],
        expected_result="The caller may apply the already-reviewed patch outside this server.",
    )


def _revision_actions(
    blockers: list[DecisionBlocker],
    mode: ValidationMode,
    fix_plan: FixPlan | None,
) -> list[NextAction]:
    related = [blocker.id for blocker in blockers]
    actions = [
        NextAction(
            id="revise-patch",
            kind=NextActionKind.EDIT,
            priority=1,
            title="Revise patch",
            details=fix_plan.strategy if fix_plan else "Edit the patch to resolve blockers.",
            safe_to_run=False,
            requires_human=False,
            related_blocker_ids=related,
            expected_result="A revised patch is ready for validation.",
        ),
        NextAction(
            id="rerun-validate-patch",
            kind=NextActionKind.RERUN,
            priority=2,
            title="Validate revised patch",
            details=f"Call validate_patch again in {mode.value} mode after editing the patch.",
            safe_to_run=True,
            requires_human=False,
            command=["validate_patch", "--mode", mode.value],
            related_blocker_ids=related,
            expected_result="The revised patch receives a fresh decision.",
        ),
    ]
    return actions


def _tooling_action(blockers: list[DecisionBlocker]) -> NextAction:
    tool = _tool_from_blockers(blockers)
    command = [tool, "--version"] if tool is not None else None
    return NextAction(
        id="fix-tooling",
        kind=NextActionKind.FIX_TOOLING,
        priority=1,
        title="Fix validation tooling",
        details="Install or repair the required validation tool, then rerun validation.",
        safe_to_run=command is not None,
        requires_human=False,
        command=command,
        related_blocker_ids=[blocker.id for blocker in blockers],
        expected_result="Required validation tooling is available.",
    )


def _human_review_action(blockers: list[DecisionBlocker]) -> NextAction:
    return NextAction(
        id="request-human-review",
        kind=NextActionKind.ASK_HUMAN,
        priority=1,
        title="Request human review",
        details="Validation was incomplete or ambiguous and should not be resolved autonomously.",
        safe_to_run=False,
        requires_human=True,
        related_blocker_ids=[blocker.id for blocker in blockers],
        expected_result="A human decides whether to retry, revise, or stop.",
    )


def _stop_action(blockers: list[DecisionBlocker]) -> NextAction:
    return NextAction(
        id="stop",
        kind=NextActionKind.STOP,
        priority=1,
        title="Stop request",
        details="The request is invalid, unsafe, or unsupported.",
        safe_to_run=False,
        requires_human=True,
        related_blocker_ids=[blocker.id for blocker in blockers],
        expected_result="The caller does not apply this patch.",
    )


def _step_for_blocker(blocker: DecisionBlocker) -> str:
    if blocker.files:
        return f"Update {', '.join(blocker.files)} to resolve: {blocker.title}."
    return f"Resolve: {blocker.title}."


def _tool_from_blockers(blockers: list[DecisionBlocker]) -> str | None:
    allowed = {"uv", "ruff", "pyright"}
    for blocker in blockers:
        text = f"{blocker.title} {blocker.details}".lower()
        for tool in sorted(allowed):
            if tool in text:
                return tool
    return None
```

- [ ] **Step 4: Run Milestone 1 tests and Phase 1 boundary tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_grouping.py tests/unit/test_decision.py tests/unit/test_actions.py tests/unit/test_service.py tests/unit/test_tools_server.py -v
```

Expected: PASS. `tests/unit/test_service.py` and `tests/unit/test_tools_server.py` must still assert the Phase 1 public response shape.

- [ ] **Step 5: Commit action generation**

Run:

```bash
git add src/agent_quality_mcp/actions.py tests/unit/test_actions.py
git commit -m "feat: add phase 2 next actions"
```

## Milestone 2: Response Assembly Without Service Switch

### Task 4: Phase 2 Response Models And Builder

**Files:**
- Create: `src/agent_quality_mcp/response.py`
- Create: `tests/unit/test_response_contract.py`

- [ ] **Step 1: Write failing response builder tests**

Create `tests/unit/test_response_contract.py`:

```python
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
from agent_quality_mcp.response import build_error_response, build_validate_patch_response


def test_build_error_response_uses_phase_2_contract() -> None:
    response = build_error_response(
        request_id="req-1",
        workspace_root="/tmp/demo",
        mode="quick",
        safety_mode="read_only",
        code="invalid_request",
        message="Invalid validate_patch request",
    )
    payload = response.model_dump(mode="json")

    assert payload["decision"] == "reject_request"
    assert payload["workspace_root"] == "/tmp/demo"
    assert payload["mode"] == "quick"
    assert payload["safety_mode"] == "read_only"
    assert payload["confidence"]["level"] == "high"
    assert payload["blockers"][0]["kind"] == "request"
    assert payload["next_actions"][0]["kind"] == "stop"
    assert "status" not in payload
    assert "blocking_errors" not in payload


def test_build_validate_patch_response_returns_apply_patch_for_clean_quick_run() -> None:
    response = build_validate_patch_response(
        request_id="req-2",
        workspace_root="/tmp/demo",
        mode=ValidationMode.QUICK,
        safety_mode=SafetyMode.READ_ONLY,
        diagnostics=[],
        compressed_groups=[],
        risk_score=RiskScore(score=0, level=RiskLevel.LOW),
        execution=ExecutionMetadata(
            commands=[
                CommandExecutionRecord(
                    command="ruff",
                    args=["ruff", "check", "--", "pkg/app.py"],
                    cwd="/tmp/demo",
                    duration_ms=10,
                    exit_code=0,
                ),
                CommandExecutionRecord(
                    command="pyright",
                    args=["pyright", "pkg/app.py"],
                    cwd="/tmp/demo",
                    duration_ms=10,
                    exit_code=0,
                ),
            ],
            tool_availability={"ruff": True, "pyright": True},
        ),
        audit=AuditSummary(),
        safe_fixes=[],
        real_workspace_modified=False,
        shadow_workspace_used=True,
    )

    assert response.decision == "apply_patch"
    assert response.blockers == []
    assert response.fix_plan is None
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is True


def test_build_validate_patch_response_maps_security_diagnostic_to_reject_request() -> None:
    diagnostic = diagnostic_from_message(
        source="security",
        code="security_error",
        message="Unsafe path",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )

    response = build_validate_patch_response(
        request_id="req-3",
        workspace_root="/tmp/demo",
        mode=ValidationMode.STANDARD,
        safety_mode=SafetyMode.READ_ONLY,
        diagnostics=[diagnostic],
        compressed_groups=[],
        risk_score=RiskScore(score=100, level=RiskLevel.CRITICAL, factors=["Unsafe path"]),
        execution=ExecutionMetadata(),
        audit=AuditSummary(),
        safe_fixes=[],
        real_workspace_modified=False,
        shadow_workspace_used=False,
    )

    assert response.decision == "reject_request"
    assert response.blockers[0].kind == "security"
    assert response.next_actions[0].kind == "stop"
```

- [ ] **Step 2: Run response tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_response_contract.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_quality_mcp.response'`.

- [ ] **Step 3: Create Phase 2 response assembly**

Create `src/agent_quality_mcp/response.py`:

```python
"""Public Phase 2 response contract for validate_patch."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_quality_mcp.actions import FixPlan, NextAction, build_fix_plan, build_next_actions
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
    """Build a Phase 2 fail-closed response for tool-wrapper validation failures."""

    diagnostic = diagnostic_from_message(
        source="system",
        code=code,
        message=message,
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )
    blocker = DecisionBlocker(
        id=f"request-{code}",
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
    risk_score = RiskScore(score=100, level=RiskLevel.CRITICAL, factors=[message])
    mode_value = _validation_mode_or_default(mode)
    safety_value = _safety_mode_or_default(safety_mode)
    required_checks = build_required_checks(mode_value, execution, [diagnostic])
    decision_result = decide_validation(
        mode=mode_value,
        blockers=[blocker],
        diagnostics=[diagnostic],
        risk_score=risk_score,
        execution=execution,
        required_checks=required_checks,
    )
    fix_plan = build_fix_plan(decision_result, safe_fixes=[], mode=mode_value)
    return _assemble_response(
        request_id=request_id,
        workspace_root=workspace_root,
        mode=mode_value,
        safety_mode=safety_value,
        decision_result=decision_result,
        diagnostics=[diagnostic],
        compressed_groups=[],
        risk_score=risk_score,
        execution=execution,
        audit=AuditSummary(),
        safe_fixes=[],
        real_workspace_modified=False,
        shadow_workspace_used=False,
        fix_plan=fix_plan,
    )


def build_validate_patch_response(
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
    safe_fixes: list[SafeFixPreview],
    real_workspace_modified: bool,
    shadow_workspace_used: bool,
) -> ValidatePatchResponse:
    blockers = group_diagnostics_for_decision(
        diagnostics,
        compressed_groups=compressed_groups,
    )
    required_checks = build_required_checks(mode, execution, diagnostics)
    decision_result = decide_validation(
        mode=mode,
        blockers=blockers,
        diagnostics=diagnostics,
        risk_score=risk_score,
        execution=execution,
        required_checks=required_checks,
    )
    fix_plan = build_fix_plan(decision_result, safe_fixes=safe_fixes, mode=mode)
    return _assemble_response(
        request_id=request_id,
        workspace_root=workspace_root,
        mode=mode,
        safety_mode=safety_mode,
        decision_result=decision_result,
        diagnostics=diagnostics,
        compressed_groups=compressed_groups,
        risk_score=risk_score,
        execution=execution,
        audit=audit,
        safe_fixes=safe_fixes,
        real_workspace_modified=real_workspace_modified,
        shadow_workspace_used=shadow_workspace_used,
        fix_plan=fix_plan,
    )


def _assemble_response(
    *,
    request_id: str,
    workspace_root: str,
    mode: ValidationMode,
    safety_mode: SafetyMode,
    decision_result: InternalDecisionResult,
    diagnostics: list[Diagnostic],
    compressed_groups: list[dict[str, Any]],
    risk_score: RiskScore,
    execution: ExecutionMetadata,
    audit: AuditSummary,
    safe_fixes: list[SafeFixPreview],
    real_workspace_modified: bool,
    shadow_workspace_used: bool,
    fix_plan: FixPlan | None,
) -> ValidatePatchResponse:
    del safe_fixes
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
            tool_availability=execution.tool_availability,
            required_checks=[
                check.model_dump(mode="json") for check in decision_result.required_checks
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
```

- [ ] **Step 4: Run response and internal decision tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_grouping.py tests/unit/test_decision.py tests/unit/test_actions.py tests/unit/test_response_contract.py -v
```

Expected: PASS. Existing service tests still use the Phase 1 response.

- [ ] **Step 5: Commit response assembly**

Run:

```bash
git add src/agent_quality_mcp/response.py tests/unit/test_response_contract.py
git commit -m "feat: add phase 2 response assembly"
```

## Milestone 3: Public Service Switch

### Task 5: Switch Service And Tool Wrapper To Phase 2

**Files:**
- Modify: `src/agent_quality_mcp/service.py`
- Modify: `src/agent_quality_mcp/tools.py`
- Modify: `tests/unit/test_service.py`
- Modify: `tests/unit/test_tools_server.py`
- Modify: `tests/unit/test_models.py`

- [ ] **Step 1: Update service tests for the breaking response**

In `tests/unit/test_service.py`, update clean adapter helpers so clean validations produce command records:

```python
def _record(command: str) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, "--version"],
        cwd="/tmp/shadow",
        duration_ms=1,
        exit_code=0,
    )
```

Then change `CleanUvAdapter.check()` to return `[], [_record("uv")]`, `CleanRuffAdapter.check()` to return `[], [_record("ruff")], []`, and `CleanPyrightAdapter.check()` to return `[], [_record("pyright")]`.

Update assertions:

```python
assert response.decision == "reject_request"
assert response.evidence.real_workspace_modified is False
assert response.evidence.shadow_workspace_used is False
assert response.blockers[0].kind == "security"
```

for `apply_safe_fixes` and path security failures.

Use these decision expectations:

```python
# clean validation
assert response.decision in {"apply_patch", "fix_tooling"}

# patch apply failure
assert response.decision == "revise_patch"
assert response.blockers[0].kind == "patch"

# timeout
assert response.decision == "request_human_review"

# missing tools
assert response.decision == "fix_tooling"
assert response.evidence.tool_availability == {
    "uv": False,
    "ruff": False,
    "pyright": False,
}
```

Remove Phase 1-only assertions against `status`, `blocking_errors`, `warnings`, `info`, `suggested_actions`, `safe_fixes`, `risk_score`, and `context_summary`.

- [ ] **Step 2: Update tool-wrapper tests for the breaking response**

In `tests/unit/test_tools_server.py`, import `ValidatePatchResponse` and `build_error_response` from `agent_quality_mcp.response` for fake service responses. Update invalid request assertions:

```python
assert result["request_id"] == "req-invalid"
assert result["decision"] == "reject_request"
assert result["blockers"][0]["kind"] == "request"
assert result["evidence"]["real_workspace_modified"] is False
assert "status" not in result
assert "blocking_errors" not in result
```

- [ ] **Step 3: Remove stale Phase 1 response helper tests from model tests**

In `tests/unit/test_models.py`, remove imports of `ValidatePatchResponse` and `build_error_response`. Delete:

```python
def test_error_response_has_error_status_and_blocker() -> None:
    ...


def test_error_response_falls_back_for_invalid_mode_strings() -> None:
    ...
```

Keep request, config, and diagnostic model tests unchanged.

- [ ] **Step 4: Run switched service tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_models.py tests/unit/test_service.py tests/unit/test_tools_server.py -v
```

Expected: FAIL because `service.py` and `tools.py` still return the Phase 1 response.

- [ ] **Step 5: Switch service response assembly**

In `src/agent_quality_mcp/service.py`, import:

```python
from agent_quality_mcp.response import ValidatePatchResponse, build_validate_patch_response
```

Remove the import of `build_suggestions`. Keep importing request/config/diagnostic models from `agent_quality_mcp.models`.

In `_response_from_parts()`, replace the `ValidatePatchResponse(...)` construction with:

```python
    execution = ExecutionMetadata(
        duration_ms=_duration_ms(started_at),
        shadow_workspace_path=shadow_workspace_path,
        shadow_workspace_preserved=shadow_workspace_preserved,
        commands=commands,
        tool_availability=_tool_availability(diagnostics),
        timed_out=timed_out or any(record.timed_out for record in commands),
        output_truncated=any(
            record.stdout_truncated or record.stderr_truncated for record in commands
        ),
    )
    return build_validate_patch_response(
        request_id=request.request_id,
        workspace_root=workspace_root,
        mode=request.mode or config.default_mode,
        safety_mode=request.safety_mode or config.default_safety_mode,
        diagnostics=diagnostics,
        compressed_groups=context_summary.compressed_groups,
        risk_score=risk_score,
        execution=execution,
        audit=audit_summary,
        safe_fixes=safe_fixes,
        real_workspace_modified=False,
        shadow_workspace_used=shadow_workspace_used,
    )
```

Leave `_categorize_diagnostics()` in place until no code references it, then remove it in the same commit if Ruff reports it unused.

- [ ] **Step 6: Switch tool-wrapper error response**

In `src/agent_quality_mcp/tools.py`, replace the models import block with:

```python
from agent_quality_mcp.models import InspectWorkspaceRequest, ValidatePatchRequest
from agent_quality_mcp.response import build_error_response
```

No other behavior is needed in `validate_patch_tool()` because it already dumps the response model to JSON.

- [ ] **Step 7: Run switched unit tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_models.py tests/unit/test_service.py tests/unit/test_tools_server.py tests/unit/test_response_contract.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit the service switch**

Run:

```bash
git add src/agent_quality_mcp/service.py src/agent_quality_mcp/tools.py tests/unit/test_models.py tests/unit/test_service.py tests/unit/test_tools_server.py
git commit -m "feat: switch validate_patch to decision response"
```

### Task 6: Integration Test And README Update

**Files:**
- Modify: `tests/integration/test_validate_patch_demo.py`
- Modify: `README.md`

- [ ] **Step 1: Update integration expectations**

In `tests/integration/test_validate_patch_demo.py`, change the response import to:

```python
from agent_quality_mcp.response import ValidatePatchResponse
```

Replace Phase 1 response assertions with:

```python
assert response.evidence.real_workspace_modified is False
assert response.evidence.shadow_workspace_used is True
assert response.execution.shadow_workspace_preserved is True
assert response.decision in {
    "apply_patch",
    "revise_patch",
    "fix_tooling",
    "request_human_review",
}
assert response.evidence.risk_score.score >= 0
assert response.next_actions
```

Keep the real-workspace mutation checks and command `cwd` checks unchanged. Update `_assert_tool_recorded_or_structured_unavailable()` to inspect `response.blockers` and `response.evidence.tool_availability` instead of Phase 1 diagnostic buckets.

- [ ] **Step 2: Run integration test and verify it fails or passes for the expected reason**

Run:

```bash
.venv/bin/python -m pytest tests/integration/test_validate_patch_demo.py -v
```

Expected: PASS if local uv/Ruff/Pyright behavior is available as before. If it fails because an external CLI is unavailable, confirm the failure is represented as `decision: fix_tooling` and update the test expectation without hiding real shadow-workspace failures.

- [ ] **Step 3: Update README response docs**

In `README.md`, replace the `validate_patch` response excerpt with a Phase 2-shaped example:

```json
{
  "request_id": "demo-1",
  "workspace_root": "/path/to/python-project",
  "mode": "quick",
  "safety_mode": "preview_safe_fixes",
  "decision": "apply_patch",
  "confidence": {
    "score": 80,
    "level": "high",
    "rationale": ["No blockers remain after required checks."],
    "factors": ["quick mode has reduced validation depth"]
  },
  "summary": {
    "title": "Patch validation passed",
    "detail": "All required checks completed without blockers.",
    "blocker_count": 0,
    "warning_count": 0
  },
  "blockers": [],
  "next_actions": [
    {
      "id": "apply-patch",
      "kind": "rerun",
      "priority": 1,
      "title": "Apply patch",
      "safe_to_run": false,
      "requires_human": false
    }
  ],
  "fix_plan": null,
  "evidence": {
    "real_workspace_modified": false,
    "shadow_workspace_used": true
  }
}
```

Add a short migration table:

```markdown
| Phase 1 field | Phase 2 location |
| --- | --- |
| `status` | `decision` |
| `blocking_errors` | `blockers` |
| `warnings` / `info` | `evidence` and grouped `blockers` |
| `suggested_actions` | `next_actions` |
| `safe_fixes` | `fix_plan.safe_fix_previews` |
| `risk_score` | `evidence.risk_score` |
| `context_summary` | `evidence.compressed_groups` and diagnostic counts |
```

- [ ] **Step 4: Run integration, README-safe checks, and full unit tests**

Run:

```bash
.venv/bin/python -m pytest tests/integration/test_validate_patch_demo.py -v
.venv/bin/python -m pytest tests/unit -v
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Commit docs and integration updates**

Run:

```bash
git add README.md tests/integration/test_validate_patch_demo.py
git commit -m "docs: document phase 2 decision response"
```

## Final Verification

- [ ] **Step 1: Run the full verification suite**

Run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Inspect final diff and commit history**

Run:

```bash
git status --short
git log --oneline -6
```

Expected: no unstaged implementation files. An unrelated `.DS_Store` may remain untracked and should not be committed.

- [ ] **Step 3: Stop if verification fails**

If any command fails, do not summarize the task as complete. Fix the failing test, lint, type, or whitespace issue with a new red/green loop and rerun the full verification suite.

## Plan Self-Review Checklist

- Spec coverage: this plan covers decision grouping, precedence, confidence, next actions, fix plans, response assembly, public service switch, tool-wrapper invalid requests, README migration notes, `inspect_workspace` source compatibility, redaction preservation by reusing existing sanitized diagnostics, and full verification.
- Placeholder scan target: the plan should not contain unresolved marker text.
- Type consistency: internal decision types live in `decision.py`; public response models live in `response.py`; action models live in `actions.py`; the service imports the public `response.ValidatePatchResponse` only during Milestone 3.
