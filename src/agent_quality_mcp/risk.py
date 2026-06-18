"""Deterministic risk scoring for validation results."""

from __future__ import annotations

from collections.abc import Iterable

from agent_quality_mcp.models import Diagnostic, DiagnosticSeverity, RiskLevel, RiskScore


def compute_risk_score(
    diagnostics: Iterable[Diagnostic],
    *,
    patch_bytes: int,
    changed_file_count: int,
    missing_tools: Iterable[str],
) -> RiskScore:
    """Compute a deterministic 0-100 risk score from validation signals."""

    diagnostic_list = list(diagnostics)
    normalized_missing_tools = sorted({tool for tool in missing_tools if tool})

    score = 0
    factors: list[str] = []

    if normalized_missing_tools:
        score += 30
        factors.append(f"Missing quality tools: {', '.join(normalized_missing_tools)}")

    blocker_count = sum(
        1
        for diagnostic in diagnostic_list
        if diagnostic.is_blocking or diagnostic.severity == DiagnosticSeverity.BLOCKER
    )
    if blocker_count:
        score += min(65, 40 + (blocker_count - 1) * 10)
        factors.append(f"Blocking diagnostics: {blocker_count}")

    non_blocking_errors = sum(
        1
        for diagnostic in diagnostic_list
        if not diagnostic.is_blocking and diagnostic.severity == DiagnosticSeverity.ERROR
    )
    if non_blocking_errors:
        score += min(30, 15 + (non_blocking_errors - 1) * 5)
        factors.append(f"Non-blocking errors: {non_blocking_errors}")

    warnings = sum(
        1 for diagnostic in diagnostic_list if diagnostic.severity == DiagnosticSeverity.WARNING
    )
    if warnings:
        score += min(15, warnings * 3)
        factors.append(f"Warnings: {warnings}")

    if patch_bytes > 200_000:
        score += 20
        factors.append(f"Large patch: {patch_bytes} bytes")
    elif patch_bytes > 50_000:
        score += 10
        factors.append(f"Moderate patch size: {patch_bytes} bytes")
    elif patch_bytes > 10_000:
        score += 5
        factors.append(f"Elevated patch size: {patch_bytes} bytes")

    if changed_file_count > 50:
        score += 20
        factors.append(f"Many changed files: {changed_file_count}")
    elif changed_file_count > 20:
        score += 10
        factors.append(f"Broad changed file set: {changed_file_count}")
    elif changed_file_count > 10:
        score += 5
        factors.append(f"Elevated changed file count: {changed_file_count}")

    score = min(score, 100)
    return RiskScore(score=score, level=_risk_level(score), factors=factors)


def _risk_level(score: int) -> RiskLevel:
    if score >= 75:
        return RiskLevel.CRITICAL
    if score >= 50:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
