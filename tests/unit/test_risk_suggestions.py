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

    assert suggestions[0].command == ["ruff", "check", "pkg/app.py"]
    assert suggestions[0].related_diagnostic_ids == [ruff.id]
    for suggestion in suggestions:
        if suggestion.command is not None:
            assert suggestion.command[0] in {"ruff", "pyright", "uv"}
            assert "rm -rf" not in suggestion.command
