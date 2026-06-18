"""Diagnostic compression helpers."""

from __future__ import annotations

from typing import Any

from agent_quality_mcp.models import AgentQualityConfig, ContextSummary, Diagnostic

DiagnosticKey = tuple[str, str, str, str | None, str]


def compress_diagnostics(
    diagnostics: list[Diagnostic],
    config: AgentQualityConfig,
) -> tuple[list[Diagnostic], ContextSummary]:
    """Deduplicate non-blocking diagnostics and enforce configured output limits."""

    deduplicated: list[tuple[int, Diagnostic]] = []
    non_blocking_counts: dict[DiagnosticKey, int] = {}
    first_non_blocking_index: dict[DiagnosticKey, int] = {}

    for index, diagnostic in enumerate(diagnostics):
        if diagnostic.is_blocking:
            deduplicated.append((index, diagnostic))
            continue

        key = _diagnostic_key(diagnostic)
        non_blocking_counts[key] = non_blocking_counts.get(key, 0) + 1
        if key not in first_non_blocking_index:
            first_non_blocking_index[key] = len(deduplicated)
            deduplicated.append((index, diagnostic))

    compressed_groups = [
        _compressed_group(key, count)
        for key, count in non_blocking_counts.items()
        if count > 1
    ]

    returned_items, truncated = _apply_limit(deduplicated, config.max_diagnostics)
    returned = [diagnostic for _, diagnostic in returned_items]
    summary = ContextSummary(
        total_diagnostics=len(diagnostics),
        returned_diagnostics=len(returned),
        compressed_groups=compressed_groups,
        truncated=truncated,
    )
    return returned, summary


def _apply_limit(
    deduplicated: list[tuple[int, Diagnostic]],
    max_diagnostics: int,
) -> tuple[list[tuple[int, Diagnostic]], bool]:
    if len(deduplicated) <= max_diagnostics:
        return deduplicated, False

    blockers = [(index, diagnostic) for index, diagnostic in deduplicated if diagnostic.is_blocking]
    non_blockers = [
        (index, diagnostic) for index, diagnostic in deduplicated if not diagnostic.is_blocking
    ]
    capacity = max(max_diagnostics - len(blockers), 0)
    selected = blockers + non_blockers[:capacity]
    selected.sort(key=lambda item: item[0])
    return selected, len(selected) < len(deduplicated)


def _diagnostic_key(diagnostic: Diagnostic) -> DiagnosticKey:
    return (
        diagnostic.source,
        diagnostic.code,
        diagnostic.message,
        diagnostic.file,
        diagnostic.severity.value,
    )


def _compressed_group(key: DiagnosticKey, count: int) -> dict[str, Any]:
    source, code, message, file, severity = key
    return {
        "source": source,
        "code": code,
        "message": message,
        "file": file,
        "severity": severity,
        "count": count,
    }
