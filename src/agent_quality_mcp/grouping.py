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
        related_diagnostic_ids=_diagnostic_ids(diagnostics),
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


def _diagnostic_ids(diagnostics: list[Diagnostic]) -> list[str]:
    return [diagnostic.id for diagnostic in diagnostics]
