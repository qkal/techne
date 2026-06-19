"""Next-action and fix-plan generation for Phase 2 decisions."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field

from agent_quality_mcp.decision import (
    BlockerFixability,
    DecisionBlocker,
    InternalDecisionResult,
    PatchDecision,
)
from agent_quality_mcp.models import AgentQualityBaseModel, SafeFixPreview, ValidationMode

ALLOWLISTED_TOOL_COMMANDS = ("uv", "ruff", "pyright")


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
    target_files: list[str]
    safe_fix_previews: list[SafeFixPreview]
    related_blocker_ids: list[str]
    rerun_hint: str


def build_fix_plan(
    decision_result: InternalDecisionResult,
    *,
    safe_fixes: list[SafeFixPreview],
    mode: ValidationMode,
) -> FixPlan | None:
    if decision_result.decision != PatchDecision.REVISE_PATCH:
        return None

    fixable_blockers = _agent_fixable_blockers(decision_result.blockers)
    if not fixable_blockers:
        return None

    related_blocker_ids = [blocker.id for blocker in fixable_blockers]
    target_files = sorted(
        {
            file
            for blocker in fixable_blockers
            for file in blocker.files
        }
    )

    return FixPlan(
        strategy="Edit the patch to resolve agent-fixable validation blockers.",
        steps=[
            f"Review blocker {blocker.id}: {blocker.title}"
            for blocker in fixable_blockers
        ],
        target_files=target_files,
        safe_fix_previews=list(safe_fixes),
        related_blocker_ids=related_blocker_ids,
        rerun_hint=f"Rerun validate_patch in {mode.value} mode after editing the patch.",
    )


def build_next_actions(
    decision_result: InternalDecisionResult,
    *,
    mode: ValidationMode,
    fix_plan: FixPlan | None,
) -> list[NextAction]:
    match decision_result.decision:
        case PatchDecision.APPLY_PATCH:
            return [_apply_action()]
        case PatchDecision.REVISE_PATCH:
            return _revise_patch_actions(decision_result, mode=mode, fix_plan=fix_plan)
        case PatchDecision.FIX_TOOLING:
            return [_fix_tooling_action(decision_result)]
        case PatchDecision.REQUEST_HUMAN_REVIEW:
            return [_ask_human_action(decision_result)]
        case PatchDecision.REJECT_REQUEST:
            return [_stop_action(decision_result)]


def _apply_action() -> NextAction:
    return NextAction(
        id="apply-patch",
        kind=NextActionKind.RERUN,
        priority=1,
        title="Apply reviewed patch",
        details="Validation passed; apply the already-reviewed patch outside this server.",
        safe_to_run=False,
        requires_human=False,
        related_blocker_ids=[],
        expected_result="The caller applies the reviewed patch.",
    )


def _revise_patch_actions(
    decision_result: InternalDecisionResult,
    *,
    mode: ValidationMode,
    fix_plan: FixPlan | None,
) -> list[NextAction]:
    related_blocker_ids = (
        fix_plan.related_blocker_ids
        if fix_plan is not None
        else [blocker.id for blocker in decision_result.blockers]
    )
    edit_details = (
        fix_plan.strategy
        if fix_plan is not None
        else "Edit the patch to resolve validation blockers."
    )
    return [
        NextAction(
            id="edit-patch",
            kind=NextActionKind.EDIT,
            priority=1,
            title="Edit patch",
            details=edit_details,
            safe_to_run=False,
            requires_human=False,
            related_blocker_ids=related_blocker_ids,
            expected_result="The patch no longer triggers the listed blockers.",
        ),
        NextAction(
            id="rerun-validate-patch",
            kind=NextActionKind.RERUN,
            priority=2,
            title="Rerun validate_patch",
            details=f"Rerun validate_patch in {mode.value} mode after editing the patch.",
            safe_to_run=True,
            requires_human=False,
            command=["validate_patch", "--mode", mode.value],
            related_blocker_ids=related_blocker_ids,
            expected_result="Validation completes with updated diagnostics.",
        ),
    ]


def _fix_tooling_action(decision_result: InternalDecisionResult) -> NextAction:
    tool = _allowlisted_tool_from_blockers(decision_result.blockers)
    command = [tool, "--version"] if tool is not None else None
    return NextAction(
        id="fix-tooling",
        kind=NextActionKind.FIX_TOOLING,
        priority=1,
        title="Fix validation tooling",
        details="Inspect the validation toolchain before rerunning validation.",
        safe_to_run=command is not None,
        requires_human=False,
        command=command,
        related_blocker_ids=[blocker.id for blocker in decision_result.blockers],
        expected_result="Required validation tooling is available.",
    )


def _ask_human_action(decision_result: InternalDecisionResult) -> NextAction:
    return NextAction(
        id="request-human-review",
        kind=NextActionKind.ASK_HUMAN,
        priority=1,
        title="Request human review",
        details="Validation is incomplete or ambiguous and needs human review.",
        safe_to_run=False,
        requires_human=True,
        command=None,
        related_blocker_ids=[blocker.id for blocker in decision_result.blockers],
        expected_result="A human reviewer decides the next validation step.",
    )


def _stop_action(decision_result: InternalDecisionResult) -> NextAction:
    return NextAction(
        id="stop-request",
        kind=NextActionKind.STOP,
        priority=1,
        title="Stop request",
        details="The request is invalid, unsafe, or unsupported.",
        safe_to_run=False,
        requires_human=True,
        command=None,
        related_blocker_ids=[blocker.id for blocker in decision_result.blockers],
        expected_result="No automated patch action is taken.",
    )


def _agent_fixable_blockers(blockers: list[DecisionBlocker]) -> list[DecisionBlocker]:
    return [
        blocker
        for blocker in blockers
        if blocker.fixability == BlockerFixability.AGENT_FIXABLE
    ]


def _allowlisted_tool_from_blockers(blockers: list[DecisionBlocker]) -> str | None:
    blocker_text = "\n".join(
        f"{blocker.title}\n{blocker.details}"
        for blocker in blockers
    )
    for tool in ALLOWLISTED_TOOL_COMMANDS:
        if re.search(rf"\b{re.escape(tool)}\b", blocker_text, flags=re.IGNORECASE):
            return tool
    return None
