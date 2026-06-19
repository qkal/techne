from __future__ import annotations

from agent_quality_mcp.decision import BlockerFixability, BlockerKind
from agent_quality_mcp.diagnostics import diagnostic_from_message
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


def test_group_diagnostics_uses_matching_compressed_group_count() -> None:
    diagnostic = _diagnostic("ruff", "F401", "Unused import", file="pkg/app.py")

    blockers = group_diagnostics_for_decision(
        [diagnostic],
        compressed_groups=[
            {
                "source": "ruff",
                "code": "F401",
                "message": "Unused import",
                "file": "pkg/app.py",
                "severity": "warning",
                "count": 5,
            }
        ],
    )

    assert len(blockers) == 1
    assert blockers[0].kind == BlockerKind.QUALITY
    assert blockers[0].count == 5
    assert blockers[0].related_diagnostic_ids == [diagnostic.id]


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


def test_group_diagnostics_maps_real_service_codes_to_request_and_timeout() -> None:
    diagnostics = [
        diagnostic_from_message(
            source="system",
            code="request_timeout",
            message="validation timed out",
            severity=DiagnosticSeverity.BLOCKER,
            is_blocking=True,
        ),
        diagnostic_from_message(
            source="security",
            code="apply_safe_fixes_not_supported",
            message="apply_safe_fixes is unsupported",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
        ),
    ]

    blockers = group_diagnostics_for_decision(diagnostics, compressed_groups=[])

    assert [blocker.kind for blocker in blockers] == [
        BlockerKind.REQUEST,
        BlockerKind.TIMEOUT,
    ]


def test_group_diagnostics_maps_planned_contract_sources() -> None:
    diagnostics = [
        diagnostic_from_message(
            source="system",
            code="invalid_request",
            message="Invalid request",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
        ),
        diagnostic_from_message(
            source="workspace",
            code="unsafe_path",
            message="Unsafe workspace path",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
        ),
        diagnostic_from_message(
            source="system",
            code="unexpected_condition",
            message="Unexpected diagnostic",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
        ),
    ]

    blockers = group_diagnostics_for_decision(diagnostics, compressed_groups=[])

    assert [blocker.kind for blocker in blockers] == [
        BlockerKind.REQUEST,
        BlockerKind.SECURITY,
        BlockerKind.HUMAN_REVIEW,
    ]
