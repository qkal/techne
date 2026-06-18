"""Service orchestration for validation and workspace inspection."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from agent_quality_mcp.audit import AuditRecorder
from agent_quality_mcp.cli.pyright import PyrightAdapter
from agent_quality_mcp.cli.ruff import RuffAdapter
from agent_quality_mcp.cli.runner import CommandRunner, resolve_allowed_command
from agent_quality_mcp.cli.uv import UvAdapter
from agent_quality_mcp.compression import compress_diagnostics
from agent_quality_mcp.config import load_config
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import (
    AgentQualityMcpError,
    CommandExecutionError,
    ConfigurationError,
    PatchApplyError,
    SecurityError,
    ToolUnavailableError,
    WorkspaceError,
)
from agent_quality_mcp.models import (
    AgentQualityConfig,
    AuditSummary,
    CommandExecutionRecord,
    ContextSummary,
    Diagnostic,
    DiagnosticSeverity,
    ExecutionMetadata,
    InspectWorkspaceResponse,
    ResponseStatus,
    RiskLevel,
    RiskScore,
    SafeFixPreview,
    SafetyMode,
    ValidatePatchRequest,
    ValidatePatchResponse,
)
from agent_quality_mcp.patching import apply_unified_diff
from agent_quality_mcp.paths import resolve_workspace_root, validate_changed_files
from agent_quality_mcp.risk import compute_risk_score
from agent_quality_mcp.shadow import create_shadow_workspace
from agent_quality_mcp.suggestions import build_suggestions
from agent_quality_mcp.workspace import inspect_workspace_files

SUPPORTED_TOOLS = ("uv", "ruff", "pyright")


def validate_patch_service(request: ValidatePatchRequest) -> ValidatePatchResponse:
    """Validate a patch request without mutating the real workspace."""

    started_at = time.monotonic()
    safe_config = AgentQualityConfig()
    audit = AuditRecorder(request_id=request.request_id, redaction_config=safe_config)
    shadow_workspace_used = False
    shadow_workspace_path: str | None = None
    shadow_workspace_preserved = False
    commands: list[CommandExecutionRecord] = []
    diagnostics: list[Diagnostic] = []
    safe_fixes: list[SafeFixPreview] = []
    patch_bytes = _patch_size(request.patch_unified_diff)
    resolved_root_text = request.workspace_root

    if request.safety_mode == SafetyMode.APPLY_SAFE_FIXES:
        audit.permission("Denied apply_safe_fixes because real workspace mutation is unsupported")
        diagnostic = diagnostic_from_message(
            source="security",
            code="apply_safe_fixes_not_supported",
            message="apply_safe_fixes is not supported; validation is read-only",
            severity=DiagnosticSeverity.BLOCKER,
            is_blocking=True,
        )
        return _final_response(
            request=request,
            workspace_root=resolved_root_text,
            status=ResponseStatus.ERROR,
            diagnostics=[diagnostic],
            safe_fixes=[],
            config=safe_config,
            audit_summary=audit.summary(),
            started_at=started_at,
            shadow_workspace_used=False,
            shadow_workspace_path=None,
            shadow_workspace_preserved=False,
            commands=[],
            timed_out=False,
            patch_bytes=patch_bytes,
            changed_file_count=len(request.changed_files),
        )

    active_config = safe_config

    try:
        workspace_root = resolve_workspace_root(request.workspace_root)
        resolved_root_text = str(workspace_root)
        config = load_config(workspace_root, request.config_overrides)
        active_config = config
        audit.redaction_config = config
        audit.permission("Validation will run in a shadow workspace only")
        _raise_if_timed_out(started_at, config)
        changed_files = validate_changed_files(workspace_root, request.changed_files)
        _validate_request_limits(workspace_root, changed_files, patch_bytes, config, audit)
        _raise_if_timed_out(started_at, config)

        with create_shadow_workspace(workspace_root, config) as shadow:
            shadow_workspace_used = True
            shadow_workspace_preserved = shadow.preserved
            if shadow.preserved:
                shadow_workspace_path = str(shadow.path)
            audit.permission("Created isolated shadow workspace for validation")
            _raise_if_timed_out(started_at, config)

            if request.patch_unified_diff is not None:
                apply_unified_diff(shadow.path, changed_files, request.patch_unified_diff)
                audit.permission("Applied request patch to shadow workspace only")
                _raise_if_timed_out(started_at, config)

            runner = CommandRunner(config)
            adapter_result = _run_adapters(
                runner=runner,
                shadow_root=shadow.path,
                changed_files=changed_files,
                mode=request.mode.value,
                preview_safe_fixes=request.safety_mode == SafetyMode.PREVIEW_SAFE_FIXES,
                timeout_check=lambda: _raise_if_timed_out(started_at, config),
            )
            diagnostics.extend(adapter_result.diagnostics)
            commands.extend(adapter_result.commands)
            safe_fixes.extend(adapter_result.safe_fixes)

        _raise_if_timed_out(started_at, config)
        compressed, context_summary = compress_diagnostics(diagnostics, config)
        status = _validation_status(compressed)
        risk_score = compute_risk_score(
            compressed,
            patch_bytes=patch_bytes,
            changed_file_count=len(changed_files),
            missing_tools=_missing_tools(compressed),
        )
        return _response_from_parts(
            request=request,
            workspace_root=resolved_root_text,
            status=status,
            diagnostics=compressed,
            safe_fixes=safe_fixes,
            risk_score=risk_score,
            context_summary=context_summary,
            audit_summary=audit.summary(),
            started_at=started_at,
            shadow_workspace_used=shadow_workspace_used,
            shadow_workspace_path=shadow_workspace_path,
            shadow_workspace_preserved=shadow_workspace_preserved,
            commands=commands,
            timed_out=False,
        )
    except _RequestTimeoutError:
        audit.resource_limit("Request timed out before validation completed")
        diagnostic = diagnostic_from_message(
            source="system",
            code="request_timeout",
            message="Validation exceeded the configured request timeout",
            severity=DiagnosticSeverity.BLOCKER,
            is_blocking=True,
        )
        return _final_response(
            request=request,
            workspace_root=resolved_root_text,
            status=ResponseStatus.ERROR,
            diagnostics=[diagnostic],
            safe_fixes=[],
            config=active_config,
            audit_summary=audit.summary(),
            started_at=started_at,
            shadow_workspace_used=shadow_workspace_used,
            shadow_workspace_path=shadow_workspace_path,
            shadow_workspace_preserved=shadow_workspace_preserved,
            commands=commands,
            timed_out=True,
            patch_bytes=patch_bytes,
            changed_file_count=len(request.changed_files),
        )
    except AgentQualityMcpError as exc:
        diagnostic = _exception_diagnostic(exc)
        _record_error_decision(audit, exc)
        return _final_response(
            request=request,
            workspace_root=resolved_root_text,
            status=ResponseStatus.ERROR,
            diagnostics=[diagnostic],
            safe_fixes=[],
            config=active_config,
            audit_summary=audit.summary(),
            started_at=started_at,
            shadow_workspace_used=shadow_workspace_used,
            shadow_workspace_path=shadow_workspace_path,
            shadow_workspace_preserved=shadow_workspace_preserved,
            commands=commands,
            timed_out=False,
            patch_bytes=patch_bytes,
            changed_file_count=len(request.changed_files),
        )


def inspect_workspace_service(
    workspace_root: str,
    config_overrides: dict[str, object] | None = None,
) -> InspectWorkspaceResponse:
    """Return response-safe workspace metadata without source contents."""

    root = resolve_workspace_root(workspace_root)
    security_decisions = [
        "Inspection returns metadata only and does not include source contents",
        "Command resolution excludes workspace-owned executables",
    ]
    try:
        config = load_config(root, config_overrides)
    except ConfigurationError:
        config = AgentQualityConfig()
        security_decisions.append("Configuration rejected; safe defaults used")
    file_inspection = inspect_workspace_files(root, config)
    availability, resolved_paths, command_decisions = _inspect_command_availability(root, config)
    security_decisions.extend(command_decisions)

    return InspectWorkspaceResponse(
        workspace_root=str(root),
        config=_inspect_response_config(config),
        command_availability=availability,
        resolved_command_paths=resolved_paths,
        default_limits=_default_limits(config),
        python_file_count=file_inspection.python_file_count,
        config_files=file_inspection.config_files,
        excluded_directories=_safe_list_summary(
            config.workspace_exclusions,
            "workspace_exclusions",
        ),
        security_decisions=security_decisions,
    )


class _AdapterRunResult:
    def __init__(
        self,
        *,
        diagnostics: list[Diagnostic],
        commands: list[CommandExecutionRecord],
        safe_fixes: list[SafeFixPreview],
    ) -> None:
        self.diagnostics = diagnostics
        self.commands = commands
        self.safe_fixes = safe_fixes


class _RequestTimeoutError(AgentQualityMcpError):
    """Internal sentinel for request timeout checks."""


def _raise_if_timed_out(started_at: float, config: AgentQualityConfig) -> None:
    if time.monotonic() - started_at > config.request_timeout_seconds:
        raise _RequestTimeoutError


def _run_adapters(
    *,
    runner: CommandRunner,
    shadow_root: Path,
    changed_files: list[Path],
    mode: str,
    preview_safe_fixes: bool,
    timeout_check: Callable[[], None],
) -> _AdapterRunResult:
    diagnostics: list[Diagnostic] = []
    commands: list[CommandExecutionRecord] = []
    safe_fixes: list[SafeFixPreview] = []

    timeout_check()
    uv_diagnostics, uv_records = _adapter_call(
        "uv",
        lambda: UvAdapter(runner).check(shadow_root, mode),
        fallback_empty=([],),
    )
    diagnostics.extend(uv_diagnostics)
    commands.extend(uv_records)

    timeout_check()
    ruff_diagnostics, ruff_records, ruff_fixes = _adapter_call(
        "ruff",
        lambda: RuffAdapter(runner).check(
            shadow_root,
            changed_files,
            mode,
            preview_safe_fixes=preview_safe_fixes,
        ),
        fallback_empty=([], []),
    )
    diagnostics.extend(ruff_diagnostics)
    commands.extend(ruff_records)
    safe_fixes.extend(ruff_fixes)

    timeout_check()
    pyright_diagnostics, pyright_records = _adapter_call(
        "pyright",
        lambda: PyrightAdapter(runner).check(shadow_root, changed_files, mode),
        fallback_empty=([],),
    )
    diagnostics.extend(pyright_diagnostics)
    commands.extend(pyright_records)

    timeout_check()
    return _AdapterRunResult(diagnostics=diagnostics, commands=commands, safe_fixes=safe_fixes)


def _adapter_call(tool: str, call: Callable[[], tuple], fallback_empty: tuple) -> tuple:
    try:
        return call()
    except ToolUnavailableError as exc:
        return ([_tool_diagnostic(tool, exc)], *fallback_empty)
    except CommandExecutionError as exc:
        return ([_command_warning(tool, exc)], *fallback_empty)


def _validate_request_limits(
    workspace_root: Path,
    changed_files: list[Path],
    patch_bytes: int,
    config: AgentQualityConfig,
    audit: AuditRecorder,
) -> None:
    if len(changed_files) > config.max_changed_files:
        audit.resource_limit("changed_files exceeds configured max_changed_files")
        raise WorkspaceError("changed_files exceeds configured max_changed_files")
    if patch_bytes > config.max_patch_bytes:
        audit.resource_limit("patch_unified_diff exceeds configured max_patch_bytes")
        raise PatchApplyError("patch_unified_diff exceeds configured max_patch_bytes")
    audit.resource_limit(f"Changed file count accepted: {len(changed_files)}")
    audit.resource_limit(f"Patch size accepted: {patch_bytes} bytes")
    for relative_path in changed_files:
        target = workspace_root / relative_path
        if not target.exists() or not target.is_file():
            continue
        size = target.stat().st_size
        if size > config.max_changed_file_bytes:
            audit.resource_limit(
                f"changed file exceeds configured max_changed_file_bytes: "
                f"{relative_path.as_posix()}"
            )
            raise WorkspaceError(
                f"changed file exceeds configured max_changed_file_bytes: "
                f"{relative_path.as_posix()}"
            )


def _final_response(
    *,
    request: ValidatePatchRequest,
    workspace_root: str,
    status: ResponseStatus,
    diagnostics: list[Diagnostic],
    safe_fixes: list[SafeFixPreview],
    config: AgentQualityConfig,
    audit_summary: AuditSummary,
    started_at: float,
    shadow_workspace_used: bool,
    shadow_workspace_path: str | None,
    shadow_workspace_preserved: bool,
    commands: list[CommandExecutionRecord],
    timed_out: bool,
    patch_bytes: int,
    changed_file_count: int,
) -> ValidatePatchResponse:
    compressed, context_summary = compress_diagnostics(diagnostics, config)
    risk_score = compute_risk_score(
        compressed,
        patch_bytes=patch_bytes,
        changed_file_count=changed_file_count,
        missing_tools=_missing_tools(compressed),
    )
    if status == ResponseStatus.ERROR and risk_score.score < 100:
        risk_score = RiskScore(
            score=100,
            level=RiskLevel.CRITICAL,
            factors=[*risk_score.factors, "Validation stopped before quality checks completed"],
        )
    return _response_from_parts(
        request=request,
        workspace_root=workspace_root,
        status=status,
        diagnostics=compressed,
        safe_fixes=safe_fixes,
        risk_score=risk_score,
        context_summary=context_summary,
        audit_summary=audit_summary,
        started_at=started_at,
        shadow_workspace_used=shadow_workspace_used,
        shadow_workspace_path=shadow_workspace_path,
        shadow_workspace_preserved=shadow_workspace_preserved,
        commands=commands,
        timed_out=timed_out,
    )


def _response_from_parts(
    *,
    request: ValidatePatchRequest,
    workspace_root: str,
    status: ResponseStatus,
    diagnostics: list[Diagnostic],
    safe_fixes: list[SafeFixPreview],
    risk_score: RiskScore,
    context_summary: ContextSummary,
    audit_summary: AuditSummary,
    started_at: float,
    shadow_workspace_used: bool,
    shadow_workspace_path: str | None,
    shadow_workspace_preserved: bool,
    commands: list[CommandExecutionRecord],
    timed_out: bool,
) -> ValidatePatchResponse:
    blocking_errors, warnings, info = _categorize_diagnostics(diagnostics)
    return ValidatePatchResponse(
        request_id=request.request_id,
        status=status,
        workspace_root=workspace_root,
        mode=request.mode,
        safety_mode=request.safety_mode,
        real_workspace_modified=False,
        shadow_workspace_used=shadow_workspace_used,
        blocking_errors=blocking_errors,
        warnings=warnings,
        info=info,
        safe_fixes=safe_fixes,
        suggested_actions=build_suggestions(diagnostics),
        risk_score=risk_score,
        execution=ExecutionMetadata(
            duration_ms=_duration_ms(started_at),
            shadow_workspace_path=shadow_workspace_path,
            shadow_workspace_preserved=shadow_workspace_preserved,
            commands=commands,
            tool_availability=_tool_availability(diagnostics),
            timed_out=timed_out or any(record.timed_out for record in commands),
            output_truncated=any(
                record.stdout_truncated or record.stderr_truncated for record in commands
            ),
        ),
        audit=audit_summary,
        context_summary=context_summary,
    )


def _categorize_diagnostics(
    diagnostics: list[Diagnostic],
) -> tuple[list[Diagnostic], list[Diagnostic], list[Diagnostic]]:
    blocking_errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    info: list[Diagnostic] = []
    for diagnostic in diagnostics:
        if diagnostic.is_blocking or diagnostic.severity == DiagnosticSeverity.BLOCKER:
            blocking_errors.append(diagnostic)
        elif diagnostic.severity == DiagnosticSeverity.INFO:
            info.append(diagnostic)
        else:
            warnings.append(diagnostic)
    return blocking_errors, warnings, info


def _validation_status(diagnostics: list[Diagnostic]) -> ResponseStatus:
    if any(
        diagnostic.is_blocking or diagnostic.severity == DiagnosticSeverity.BLOCKER
        for diagnostic in diagnostics
    ):
        return ResponseStatus.FAILED
    return ResponseStatus.PASSED


def _exception_diagnostic(exc: AgentQualityMcpError) -> Diagnostic:
    source = "system"
    code = "agent_quality_mcp_error"
    if isinstance(exc, ConfigurationError):
        code = "configuration_error"
    elif isinstance(exc, WorkspaceError):
        source = "workspace"
        code = "workspace_error"
    elif isinstance(exc, SecurityError):
        source = "security"
        code = "security_error"
    elif isinstance(exc, PatchApplyError):
        source = "patch"
        code = "patch_apply_error"
    elif isinstance(exc, ToolUnavailableError):
        code = "tool_unavailable"
    elif isinstance(exc, CommandExecutionError):
        code = "command_execution_error"
    return diagnostic_from_message(
        source=source,
        code=code,
        message=str(exc),
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )


def _record_error_decision(audit: AuditRecorder, exc: AgentQualityMcpError) -> None:
    if isinstance(exc, SecurityError):
        audit.permission(f"Security validation denied request: {exc}")
    elif isinstance(exc, WorkspaceError):
        audit.resource_limit(f"Workspace validation stopped request: {exc}")
    elif isinstance(exc, PatchApplyError):
        audit.permission(f"Patch validation stopped before tools ran: {exc}")
    elif isinstance(exc, ConfigurationError):
        audit.permission(f"Configuration validation stopped request: {exc}")
    else:
        audit.permission(f"Validation stopped before completion: {exc}")


def _tool_diagnostic(tool: str, exc: CommandExecutionError) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )


def _command_warning(tool: str, exc: CommandExecutionError) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="command_execution_error",
        message=str(exc),
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )


def _tool_availability(diagnostics: list[Diagnostic]) -> dict[str, bool]:
    unavailable = set(_missing_tools(diagnostics))
    return {tool: tool not in unavailable for tool in SUPPORTED_TOOLS}


def _missing_tools(diagnostics: list[Diagnostic]) -> list[str]:
    missing: list[str] = []
    for diagnostic in diagnostics:
        if diagnostic.source != "system":
            continue
        if diagnostic.code not in {"tool_missing", "tool_unavailable"}:
            continue
        tool = diagnostic.metadata.get("tool")
        if isinstance(tool, str):
            missing.append(tool)
    return missing


def _inspect_command_availability(
    root: Path,
    config: AgentQualityConfig,
) -> tuple[dict[str, bool], dict[str, str | None], list[str]]:
    availability: dict[str, bool] = {}
    resolved_paths: dict[str, str | None] = {}
    decisions: list[str] = []
    for tool in SUPPORTED_TOOLS:
        try:
            resolved = resolve_allowed_command(tool, config, cwd=root)
        except (CommandExecutionError, SecurityError) as exc:
            availability[tool] = False
            resolved_paths[tool] = None
            decisions.append(f"{tool} unavailable or unsafe: {exc}")
            continue
        availability[tool] = True
        resolved_paths[tool] = resolved
        decisions.append(f"{tool} resolved to safe executable path")
    return availability, resolved_paths, decisions


def _inspect_response_config(config: AgentQualityConfig) -> AgentQualityConfig:
    return config.model_copy(
        update={
            "workspace_exclusions": _safe_list_summary(
                config.workspace_exclusions,
                "workspace_exclusions",
            ),
            "secret_file_patterns": _safe_list_summary(
                config.secret_file_patterns,
                "secret_file_patterns",
            ),
            "secret_redaction_patterns": [],
        }
    )


def _safe_list_summary(values: list[str], label: str) -> list[str]:
    if not values:
        return []
    return [f"<{label}:count={len(values)}>"]


def _default_limits(config: AgentQualityConfig) -> dict[str, int]:
    return {
        "request_timeout_seconds": config.request_timeout_seconds,
        "subprocess_timeout_seconds": config.subprocess_timeout_seconds,
        "max_patch_bytes": config.max_patch_bytes,
        "max_changed_files": config.max_changed_files,
        "max_changed_file_bytes": config.max_changed_file_bytes,
        "max_workspace_copy_bytes": config.max_workspace_copy_bytes,
        "max_output_bytes": config.max_output_bytes,
        "max_diagnostics": config.max_diagnostics,
    }


def _patch_size(patch_text: str | None) -> int:
    if patch_text is None:
        return 0
    return len(patch_text.encode("utf-8"))


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))
