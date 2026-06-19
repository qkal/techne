from __future__ import annotations

from agent_quality_mcp.actions import (
    FixPlan,
    NextActionKind,
    build_fix_plan,
    build_next_actions,
)
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


def _blocker(
    blocker_id: str,
    *,
    kind: BlockerKind = BlockerKind.QUALITY,
    title: str = "Quality blocker",
    details: str = "A validation blocker was found.",
    files: list[str] | None = None,
    fixability: BlockerFixability = BlockerFixability.AGENT_FIXABLE,
) -> DecisionBlocker:
    return DecisionBlocker(
        id=blocker_id,
        kind=kind,
        severity=DiagnosticSeverity.BLOCKER,
        title=title,
        details=details,
        files=files or [],
        fixability=fixability,
    )


def _decision_result(
    decision: PatchDecision,
    *,
    blockers: list[DecisionBlocker] | None = None,
) -> InternalDecisionResult:
    blockers = blockers or []
    return InternalDecisionResult(
        decision=decision,
        confidence=Confidence(
            score=90,
            level=ConfidenceLevel.HIGH,
            rationale=["test decision"],
        ),
        summary=DecisionSummary(
            title="Test decision",
            detail="Decision detail",
            blocker_count=len(blockers),
        ),
        blockers=blockers,
        required_checks=[],
    )


def test_build_next_actions_for_revise_patch_edits_then_reruns_standard() -> None:
    result = _decision_result(
        PatchDecision.REVISE_PATCH,
        blockers=[_blocker("quality-1", files=["pkg/app.py"])],
    )
    fix_plan = build_fix_plan(result, safe_fixes=[], mode=ValidationMode.STANDARD)

    actions = build_next_actions(result, mode=ValidationMode.STANDARD, fix_plan=fix_plan)

    assert [action.kind for action in actions] == [
        NextActionKind.EDIT,
        NextActionKind.RERUN,
    ]
    assert actions[0].safe_to_run is False
    assert actions[1].command == ["validate_patch", "--mode", "standard"]
    assert actions[1].safe_to_run is True
    assert actions[1].requires_human is False


def test_build_next_actions_for_fix_tooling_allowlists_tool_version_command() -> None:
    result = _decision_result(
        PatchDecision.FIX_TOOLING,
        blockers=[
            _blocker(
                "tooling-1",
                kind=BlockerKind.TOOLING,
                title="ruff is unavailable",
                details="The ruff command did not run.",
                fixability=BlockerFixability.TOOLING_FIXABLE,
            )
        ],
    )

    actions = build_next_actions(result, mode=ValidationMode.STANDARD, fix_plan=None)

    assert len(actions) == 1
    assert actions[0].kind == NextActionKind.FIX_TOOLING
    assert actions[0].command == ["ruff", "--version"]
    assert actions[0].safe_to_run is True


def test_build_next_actions_for_reject_request_stops_for_human_review() -> None:
    result = _decision_result(
        PatchDecision.REJECT_REQUEST,
        blockers=[
            _blocker(
                "security-1",
                kind=BlockerKind.SECURITY,
                fixability=BlockerFixability.NOT_FIXABLE,
            )
        ],
    )

    actions = build_next_actions(result, mode=ValidationMode.QUICK, fix_plan=None)

    assert len(actions) == 1
    assert actions[0].kind == NextActionKind.STOP
    assert actions[0].requires_human is True
    assert actions[0].command is None


def test_build_fix_plan_uses_fixable_blockers_and_safe_fix_previews() -> None:
    safe_fix = SafeFixPreview(
        tool="ruff",
        description="Remove unused import",
        files=["pkg/app.py"],
        diff_preview="--- a/pkg/app.py\n+++ b/pkg/app.py\n",
        is_safe=True,
        requires_human_review=False,
    )
    result = _decision_result(
        PatchDecision.REVISE_PATCH,
        blockers=[
            _blocker(
                "security-1",
                kind=BlockerKind.SECURITY,
                files=["pkg/secret.py"],
                fixability=BlockerFixability.NOT_FIXABLE,
            ),
            _blocker(
                "quality-1",
                files=["pkg/app.py", "pkg/utils.py", "pkg/app.py"],
            ),
        ],
    )

    fix_plan = build_fix_plan(result, safe_fixes=[safe_fix], mode=ValidationMode.QUICK)

    assert fix_plan is not None
    assert fix_plan.target_files == ["pkg/app.py", "pkg/utils.py"]
    assert fix_plan.safe_fix_previews == [safe_fix]
    assert fix_plan.related_blocker_ids == ["quality-1"]
    assert fix_plan.rerun_hint == "Rerun validate_patch in quick mode after editing the patch."


def test_build_fix_plan_returns_none_for_non_revise_patch_decisions() -> None:
    safe_fix = SafeFixPreview(
        tool="ruff",
        description="Remove unused import",
        files=["pkg/app.py"],
        diff_preview="--- a/pkg/app.py\n+++ b/pkg/app.py\n",
        is_safe=True,
        requires_human_review=False,
    )
    result = _decision_result(
        PatchDecision.APPLY_PATCH,
        blockers=[_blocker("quality-1", files=["pkg/app.py"])],
    )

    fix_plan = build_fix_plan(result, safe_fixes=[safe_fix], mode=ValidationMode.QUICK)

    assert fix_plan is None


def test_fix_plan_list_fields_default_to_empty_lists() -> None:
    fix_plan = FixPlan(strategy="Edit patch.", rerun_hint="Rerun validation.")
    second_fix_plan = FixPlan(strategy="Edit another patch.", rerun_hint="Rerun validation.")

    assert fix_plan.steps == []
    assert fix_plan.target_files == []
    assert fix_plan.safe_fix_previews == []
    assert fix_plan.related_blocker_ids == []

    fix_plan.steps.append("Edit the patch.")

    assert second_fix_plan.steps == []
