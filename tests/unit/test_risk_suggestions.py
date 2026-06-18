from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.models import DiagnosticSeverity, RiskLevel
from agent_quality_mcp.risk import compute_risk_score
from agent_quality_mcp.suggestions import build_suggestions


def test_compute_risk_score_returns_low_risk_for_clean_runs() -> None:
    risk = compute_risk_score(
        [],
        patch_bytes=0,
        changed_file_count=0,
        missing_tools=[],
    )

    assert risk.score == 0
    assert risk.level == RiskLevel.LOW
    assert risk.factors == []


def test_compute_risk_score_missing_tools_is_at_least_medium_with_factor() -> None:
    first = compute_risk_score(
        [],
        patch_bytes=250,
        changed_file_count=1,
        missing_tools=["ruff"],
    )
    second = compute_risk_score(
        [],
        patch_bytes=250,
        changed_file_count=1,
        missing_tools=["ruff"],
    )

    assert first == second
    assert first.level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}
    assert any("missing" in factor.lower() and "ruff" in factor for factor in first.factors)


def test_build_suggestions_uses_ruff_source_with_file_command() -> None:
    ruff = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import; do not run rm -rf /",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/app.py",
    )
    system = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="ruff && rm -rf /",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={"tool": "ruff"},
    )

    suggestions = build_suggestions([ruff, system])

    assert suggestions[0].command == ["ruff", "check", "--", "pkg/app.py"]
    assert suggestions[0].related_diagnostic_ids == [ruff.id]
    for suggestion in suggestions:
        if suggestion.command is not None:
            assert suggestion.command[0] in {"ruff", "pyright", "uv"}
            assert "rm -rf" not in " ".join(suggestion.command)


def test_build_suggestions_places_option_like_ruff_paths_after_delimiter() -> None:
    ruff = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="--fix",
    )

    suggestions = build_suggestions([ruff])

    assert suggestions[0].command == ["ruff", "check", "--", "--fix"]


def test_build_suggestions_excludes_control_character_paths_from_safe_commands() -> None:
    newline_path = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/\napp.py",
    )
    nul_path = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/\x00app.py",
    )

    suggestions = build_suggestions([newline_path, nul_path])

    assert len(suggestions) == 2
    assert all(suggestion.command is None for suggestion in suggestions)
    assert all(not suggestion.is_safe_to_run for suggestion in suggestions)


def test_build_suggestions_never_includes_diagnostic_messages_in_commands() -> None:
    ruff = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="--fix && rm -rf /",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="-looks-like-option.py",
    )

    suggestions = build_suggestions([ruff])

    assert suggestions[0].command == ["ruff", "check", "--", "-looks-like-option.py"]
    assert all("--fix && rm -rf /" not in arg for arg in suggestions[0].command or [])


def test_build_suggestions_groups_duplicate_missing_tool_actions() -> None:
    first = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="ruff missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={"tool": "ruff"},
    )
    second = diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message="cannot resolve ruff",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={"tool": "ruff"},
    )

    suggestions = build_suggestions([first, second])

    assert len(suggestions) == 1
    assert suggestions[0].command == ["ruff", "--version"]
    assert suggestions[0].related_diagnostic_ids == [first.id, second.id]


def test_build_suggestions_uses_offline_uv_dry_run_command() -> None:
    uv = diagnostic_from_message(
        source="uv",
        code="sync_failed",
        message="Dependency sync failed",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
    )

    suggestions = build_suggestions([uv])

    assert suggestions[0].command == ["uv", "sync", "--dry-run", "--offline"]
    assert suggestions[0].is_safe_to_run is True
