"""Internal validator capability and result models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field

from agent_quality_mcp.models import (
    AgentQualityBaseModel,
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    SafeFixPreview,
    SafetyMode,
    ValidationMode,
)


class ValidatorCapability(StrEnum):
    DEPENDENCY_LOCK_CHECK = "dependency_lock_check"
    DEPENDENCY_SYNC_DRY_RUN = "dependency_sync_dry_run"
    LINT_DIAGNOSTICS = "lint_diagnostics"
    SAFE_FIX_PREVIEW = "safe_fix_preview"
    TYPE_DIAGNOSTICS = "type_diagnostics"
    CHANGED_FILE_SCOPE = "changed_file_scope"
    WORKSPACE_SCOPE = "workspace_scope"
    CLI_FALLBACK = "cli_fallback"
    LSP_REUSE = "lsp_reuse"


class ValidatorScope(StrEnum):
    CHANGED_FILES = "changed_files"
    WORKSPACE = "workspace"


class SkippedCheck(AgentQualityBaseModel):
    provider: str
    capability: ValidatorCapability
    reason: str


class ValidatorRequest(AgentQualityBaseModel):
    real_workspace_root: Path
    shadow_workspace_root: Path
    changed_files: list[Path]
    mode: ValidationMode
    safety_mode: SafetyMode
    requested_scope: ValidatorScope
    timeout_budget_seconds: float = Field(gt=0)
    request_id: str
    config: AgentQualityConfig


class ValidatorResult(AgentQualityBaseModel):
    provider: str
    capabilities: list[ValidatorCapability] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    commands: list[CommandExecutionRecord] = Field(default_factory=list)
    safe_fixes: list[SafeFixPreview] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    skipped_checks: list[SkippedCheck] = Field(default_factory=list)
    fallback_reason: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    output_truncated: bool = False


class ValidatorProvider(Protocol):
    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        """Run validation and return normalized internal facts."""
        ...


def wrap_uv_result(
    *,
    request: ValidatorRequest,
    diagnostics: list[Diagnostic],
    records: list[CommandExecutionRecord],
    project_detected: bool,
    lock_check_requested: bool,
    lock_check_completed: bool,
    sync_dry_run_available: bool,
    sync_dry_run_enabled: bool,
    sync_dry_run_completed: bool,
    skipped_reason: str | None,
    duration_ms: int,
) -> ValidatorResult:
    capabilities = [ValidatorCapability.DEPENDENCY_LOCK_CHECK]
    if sync_dry_run_available:
        capabilities.append(ValidatorCapability.DEPENDENCY_SYNC_DRY_RUN)
    skipped_checks: list[SkippedCheck] = []
    if skipped_reason is not None:
        skipped_checks.append(
            SkippedCheck(
                provider="uv",
                capability=ValidatorCapability.DEPENDENCY_LOCK_CHECK,
                reason=skipped_reason,
            )
        )
    return ValidatorResult(
        provider="uv",
        capabilities=capabilities,
        diagnostics=diagnostics,
        commands=records,
        metadata={
            "project_detected": project_detected,
            "pyproject_present": project_detected,
            "lock_check_requested": lock_check_requested,
            "lock_check_completed": lock_check_completed,
            "sync_dry_run_available": sync_dry_run_available,
            "sync_dry_run_enabled": sync_dry_run_enabled,
            "sync_dry_run_completed": sync_dry_run_completed,
            "skipped_reason": skipped_reason,
            "mode": request.mode.value,
        },
        skipped_checks=skipped_checks,
        duration_ms=duration_ms,
        timed_out=any(record.timed_out for record in records),
        output_truncated=any(
            record.stdout_truncated or record.stderr_truncated for record in records
        ),
    )


def wrap_ruff_result(
    *,
    request: ValidatorRequest,
    diagnostics: list[Diagnostic],
    records: list[CommandExecutionRecord],
    safe_fixes: list[SafeFixPreview],
    scope: ValidatorScope,
    scoped_files: list[str],
    rule_codes: list[str],
    fixable_rule_codes: list[str],
    safe_fix_preview_requested: bool,
    safe_fix_preview_completed: bool,
    skipped_reason: str | None,
    duration_ms: int,
) -> ValidatorResult:
    capabilities = [ValidatorCapability.LINT_DIAGNOSTICS]
    capabilities.append(
        ValidatorCapability.WORKSPACE_SCOPE
        if scope is ValidatorScope.WORKSPACE
        else ValidatorCapability.CHANGED_FILE_SCOPE
    )
    if safe_fix_preview_requested:
        capabilities.append(ValidatorCapability.SAFE_FIX_PREVIEW)
    skipped_checks: list[SkippedCheck] = []
    if skipped_reason is not None:
        skipped_checks.append(
            SkippedCheck(
                provider="ruff",
                capability=ValidatorCapability.LINT_DIAGNOSTICS,
                reason=skipped_reason,
            )
        )
    return ValidatorResult(
        provider="ruff",
        capabilities=capabilities,
        diagnostics=diagnostics,
        commands=records,
        safe_fixes=safe_fixes,
        metadata={
            "scope": scope.value,
            "scoped_files": scoped_files,
            "json_diagnostics_completed": bool(records)
            and not any(record.timed_out for record in records),
            "safe_fix_preview_requested": safe_fix_preview_requested,
            "safe_fix_preview_completed": safe_fix_preview_completed,
            "rule_codes": rule_codes,
            "fixable_rule_codes": fixable_rule_codes,
            "skipped_reason": skipped_reason,
            "mode": request.mode.value,
        },
        skipped_checks=skipped_checks,
        duration_ms=duration_ms,
        timed_out=any(record.timed_out for record in records),
        output_truncated=any(
            record.stdout_truncated or record.stderr_truncated for record in records
        ),
    )
