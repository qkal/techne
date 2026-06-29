from agent_quality_mcp.models import RiskLevel
from agent_quality_mcp.risk import compute_risk_score


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
