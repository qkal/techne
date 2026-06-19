"""Group diagnostics into deterministic decision blockers."""

from __future__ import annotations

from collections import Counter
from typing import Any

from agent_quality_mcp.decision import BlockerFixability, BlockerKind, DecisionBlocker
from agent_quality_mcp.models import Diagnostic, DiagnosticSeverity

CompressedGroupKey = tuple[str, str, str, str | None, str, bool]

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

    compressed_counts = _compressed_group_counts(compressed_groups)
    grouped: dict[tuple[BlockerKind, str, str | None], list[Diagnostic]] = {}
    for diagnostic in diagnostics:
        kind = _kind_for_diagnostic(diagnostic)
        key = (kind, diagnostic.code, diagnostic.file)
        grouped.setdefault(key, []).append(diagnostic)

    blockers = [
        _blocker_from_group(kind, items, compressed_counts)
        for (kind, _, _), items in grouped.items()
    ]
    blockers.sort(
        key=lambda blocker: (
            BLOCKER_KIND_ORDER[blocker.kind],
            blocker.files[0] if blocker.files else "",
            blocker.title,
            blocker.id,
        )
    )
    return blockers


def _blocker_from_group(
    kind: BlockerKind,
    diagnostics: list[Diagnostic],
    compressed_counts: dict[CompressedGroupKey, int],
) -> DecisionBlocker:
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
        count=_count_for_group(diagnostics, compressed_counts),
        fixability=_fixability_for_kind(kind),
    )


def _kind_for_diagnostic(diagnostic: Diagnostic) -> BlockerKind:
    if diagnostic.code == "apply_safe_fixes_not_supported":
        return BlockerKind.REQUEST
    if diagnostic.code in {"request_timeout", "timeout"}:
        return BlockerKind.TIMEOUT
    if diagnostic.source == "security":
        return BlockerKind.SECURITY
    if diagnostic.source == "patch":
        return BlockerKind.PATCH
    if diagnostic.source == "system" and diagnostic.code in {"tool_missing", "tool_unavailable"}:
        return BlockerKind.TOOLING
    if diagnostic.source == "system" and diagnostic.code == "invalid_request":
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


def _count_for_group(
    diagnostics: list[Diagnostic],
    compressed_counts: dict[CompressedGroupKey, int],
) -> int:
    raw_counts = Counter(
        _compressed_group_key_for_diagnostic(diagnostic) for diagnostic in diagnostics
    )
    return sum(
        max(raw_count, compressed_counts.get(key, raw_count))
        for key, raw_count in raw_counts.items()
    )


def _compressed_group_counts(
    compressed_groups: list[dict[str, Any]],
) -> dict[CompressedGroupKey, int]:
    counts: dict[CompressedGroupKey, int] = {}
    for group in compressed_groups:
        key = _compressed_group_key(group)
        count = group.get("count")
        if key is None or not isinstance(count, int) or count < 1:
            continue
        counts[key] = max(counts.get(key, 0), count)
    return counts


def _compressed_group_key(group: dict[str, Any]) -> CompressedGroupKey | None:
    source = group.get("source")
    code = group.get("code")
    message = group.get("message")
    file = group.get("file")
    severity = group.get("severity")
    is_fixable = group.get("is_fixable", False)
    if not all(isinstance(value, str) for value in (source, code, message, severity)):
        return None
    if file is not None and not isinstance(file, str):
        return None
    if not isinstance(is_fixable, bool):
        return None
    return (source, code, message, file, severity, is_fixable)


def _compressed_group_key_for_diagnostic(diagnostic: Diagnostic) -> CompressedGroupKey:
    return (
        diagnostic.source,
        diagnostic.code,
        diagnostic.message,
        diagnostic.file,
        diagnostic.severity.value,
        diagnostic.is_fixable,
    )
