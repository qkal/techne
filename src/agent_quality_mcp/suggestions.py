"""Suggested next actions derived from normalized diagnostics."""

from __future__ import annotations

from collections import defaultdict

from agent_quality_mcp.models import Diagnostic, SuggestedAction

ALLOWED_COMMANDS = frozenset({"ruff", "pyright", "uv"})
SOURCE_ORDER = {"ruff": 0, "pyright": 1, "uv": 2, "system": 3}


def build_suggestions(diagnostics: list[Diagnostic]) -> list[SuggestedAction]:
    """Build deterministic, allowlisted follow-up actions for known diagnostic sources."""

    suggestions: list[tuple[int, str, SuggestedAction]] = []
    suggestions.extend(_ruff_suggestions(diagnostics))
    suggestions.extend(_pyright_suggestions(diagnostics))
    suggestions.extend(_uv_suggestions(diagnostics))
    suggestions.extend(_missing_tool_suggestions(diagnostics))
    suggestions.sort(key=lambda item: (item[0], item[1], item[2].title))
    return [suggestion for _, _, suggestion in suggestions]


def _ruff_suggestions(diagnostics: list[Diagnostic]) -> list[tuple[int, str, SuggestedAction]]:
    grouped = _diagnostics_by_file(diagnostics, source="ruff")
    suggestions: list[tuple[int, str, SuggestedAction]] = []
    for file, related_diagnostics in grouped.items():
        command = ["ruff", "check", file] if file is not None else ["ruff", "check"]
        suggestions.append(
            (
                SOURCE_ORDER["ruff"],
                file or "",
                SuggestedAction(
                    title="Run Ruff check",
                    description="Run Ruff against the affected Python file.",
                    priority=2,
                    related_diagnostic_ids=[diagnostic.id for diagnostic in related_diagnostics],
                    command=command,
                    is_safe_to_run=True,
                ),
            )
        )
    return suggestions


def _pyright_suggestions(diagnostics: list[Diagnostic]) -> list[tuple[int, str, SuggestedAction]]:
    grouped = _diagnostics_by_file(diagnostics, source="pyright")
    suggestions: list[tuple[int, str, SuggestedAction]] = []
    for file, related_diagnostics in grouped.items():
        command = ["pyright", file] if file is not None else ["pyright"]
        suggestions.append(
            (
                SOURCE_ORDER["pyright"],
                file or "",
                SuggestedAction(
                    title="Run Pyright",
                    description="Run Pyright type checking for the affected target.",
                    priority=1,
                    related_diagnostic_ids=[diagnostic.id for diagnostic in related_diagnostics],
                    command=command,
                    is_safe_to_run=True,
                ),
            )
        )
    return suggestions


def _uv_suggestions(diagnostics: list[Diagnostic]) -> list[tuple[int, str, SuggestedAction]]:
    uv_diagnostics = [diagnostic for diagnostic in diagnostics if diagnostic.source == "uv"]
    if not uv_diagnostics:
        return []
    return [
        (
            SOURCE_ORDER["uv"],
            "",
            SuggestedAction(
                title="Check uv environment",
                description="Run a dry-run dependency sync to inspect environment issues.",
                priority=2,
                related_diagnostic_ids=[diagnostic.id for diagnostic in uv_diagnostics],
                command=["uv", "sync", "--dry-run"],
                is_safe_to_run=True,
            ),
        )
    ]


def _missing_tool_suggestions(
    diagnostics: list[Diagnostic],
) -> list[tuple[int, str, SuggestedAction]]:
    suggestions: list[tuple[int, str, SuggestedAction]] = []
    for diagnostic in diagnostics:
        if (
            diagnostic.source != "system"
            or diagnostic.code not in {"tool_missing", "tool_unavailable"}
        ):
            continue
        tool = diagnostic.metadata.get("tool")
        if not isinstance(tool, str) or tool not in ALLOWED_COMMANDS:
            continue
        suggestions.append(
            (
                SOURCE_ORDER["system"],
                tool,
                SuggestedAction(
                    title=f"Check {tool} availability",
                    description="Verify that the expected quality tool is available on PATH.",
                    priority=1,
                    related_diagnostic_ids=[diagnostic.id],
                    command=[tool, "--version"],
                    is_safe_to_run=True,
                ),
            )
        )
    return suggestions


def _diagnostics_by_file(
    diagnostics: list[Diagnostic],
    *,
    source: str,
) -> dict[str | None, list[Diagnostic]]:
    grouped: dict[str | None, list[Diagnostic]] = defaultdict(list)
    for diagnostic in diagnostics:
        if diagnostic.source == source:
            grouped[diagnostic.file].append(diagnostic)
    return dict(grouped)
