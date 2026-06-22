from __future__ import annotations

from pathlib import Path

from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    SafeFixPreview,
    SafetyMode,
    ValidationMode,
)
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorScope,
    wrap_ruff_result,
    wrap_uv_result,
)


def _request(
    tmp_path: Path, *, mode: ValidationMode = ValidationMode.STANDARD
) -> ValidatorRequest:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    return ValidatorRequest(
        real_workspace_root=tmp_path,
        shadow_workspace_root=shadow,
        changed_files=[Path("pkg/app.py")],
        mode=mode,
        safety_mode=SafetyMode.READ_ONLY,
        requested_scope=ValidatorScope.CHANGED_FILES,
        timeout_budget_seconds=30.0,
        request_id="req-1",
        config=AgentQualityConfig(),
    )


def _record(
    command: str, args: list[str], cwd: Path, *, exit_code: int = 0
) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, *args],
        cwd=str(cwd),
        duration_ms=7,
        exit_code=exit_code,
    )


def test_validator_request_keeps_real_and_shadow_roots_separate(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    assert request.real_workspace_root == tmp_path
    assert request.shadow_workspace_root == tmp_path / "shadow"
    assert request.real_workspace_root != request.shadow_workspace_root
    assert request.requested_scope is ValidatorScope.CHANGED_FILES


def test_wrap_uv_result_reports_project_and_lock_metadata(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=ValidationMode.STRICT)
    records = [
        _record("uv", ["--version"], request.shadow_workspace_root),
        _record("uv", ["lock", "--check"], request.shadow_workspace_root),
    ]

    result = wrap_uv_result(
        request=request,
        diagnostics=[],
        records=records,
        project_detected=True,
        lock_check_requested=True,
        lock_check_completed=True,
        sync_dry_run_available=True,
        sync_dry_run_enabled=False,
        sync_dry_run_completed=False,
        skipped_reason=None,
        duration_ms=12,
    )

    assert result.provider == "uv"
    assert ValidatorCapability.DEPENDENCY_LOCK_CHECK in result.capabilities
    assert result.commands == records
    assert result.metadata["project_detected"] is True
    assert result.metadata["lock_check_completed"] is True
    assert result.skipped_checks == []


def test_wrap_uv_result_records_skipped_lock_check(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=ValidationMode.STANDARD)

    result = wrap_uv_result(
        request=request,
        diagnostics=[],
        records=[_record("uv", ["--version"], request.shadow_workspace_root)],
        project_detected=False,
        lock_check_requested=False,
        lock_check_completed=False,
        sync_dry_run_available=False,
        sync_dry_run_enabled=False,
        sync_dry_run_completed=False,
        skipped_reason="pyproject.toml not present",
        duration_ms=4,
    )

    assert result.metadata["pyproject_present"] is False
    assert result.skipped_checks[0].provider == "uv"
    assert result.skipped_checks[0].reason == "pyproject.toml not present"


def test_wrap_ruff_result_reports_scope_rule_codes_and_safe_fix_preview(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    preview = SafeFixPreview(
        tool="ruff",
        description="Ruff safe-fix diff preview",
        files=["pkg/app.py"],
        diff_preview="--- pkg/app.py\n+++ pkg/app.py\n",
        is_safe=True,
        requires_human_review=True,
    )

    result = wrap_ruff_result(
        request=request,
        diagnostics=[],
        records=[
            _record(
                "ruff",
                ["check", "--output-format", "json"],
                request.shadow_workspace_root,
            )
        ],
        safe_fixes=[preview],
        scope=ValidatorScope.CHANGED_FILES,
        scoped_files=["pkg/app.py"],
        rule_codes=["F401"],
        fixable_rule_codes=["F401"],
        safe_fix_preview_requested=True,
        safe_fix_preview_completed=True,
        skipped_reason=None,
        duration_ms=9,
    )

    assert result.provider == "ruff"
    assert ValidatorCapability.LINT_DIAGNOSTICS in result.capabilities
    assert ValidatorCapability.SAFE_FIX_PREVIEW in result.capabilities
    assert result.safe_fixes == [preview]
    assert result.metadata["scope"] == "changed_files"
    assert result.metadata["rule_codes"] == ["F401"]
    assert result.metadata["fixable_rule_codes"] == ["F401"]
