"""Normalize Pyright Language Server Protocol diagnostics."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.models import (
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticRange,
    DiagnosticSeverity,
)
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorResult,
    ValidatorScope,
)

RawLspDiagnostics = dict[str, list[dict[str, object]]]


def lsp_uri_from_path(path: Path) -> str:
    """Return a local file URI for a filesystem path."""

    return path.resolve().as_uri()


def path_from_lsp_uri(uri: str) -> Path:
    """Return a resolved local path from a ``file://`` LSP document URI."""

    if not isinstance(uri, str):
        raise ValueError("LSP URI must be a string")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or not parsed.path:
        raise ValueError("LSP URI must be a local file URI")
    if parsed.query or parsed.fragment:
        raise ValueError("LSP URI must not include query or fragment components")
    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        raise ValueError("LSP file URI path must be absolute")
    return path.resolve()


def normalize_lsp_diagnostics(
    uri: str,
    raw_diagnostics: Any,
    shadow_root: Path,
) -> list[Diagnostic]:
    """Normalize Pyright LSP diagnostics for one shadow-workspace document."""

    try:
        file_path = path_from_lsp_uri(uri)
        shadow_root_resolved = shadow_root.resolve()
        relative_file = file_path.relative_to(shadow_root_resolved).as_posix()
    except (OSError, ValueError):
        return []

    diagnostics: list[Diagnostic] = []
    if not isinstance(raw_diagnostics, list):
        return diagnostics

    for item in raw_diagnostics:
        if not isinstance(item, dict):
            continue

        severity, is_blocking = _lsp_severity(item.get("severity"))
        code = _string_or_default(item.get("code"), "pyright_lsp")
        message = _string_or_default(item.get("message"), "Pyright diagnostic")
        diagnostic_range = _lsp_range(item.get("range"))
        metadata = {"transport": "lsp"}

        diagnostics.append(
            Diagnostic(
                id=_lsp_diagnostic_id(
                    code=code,
                    message=message,
                    severity=severity,
                    is_blocking=is_blocking,
                    file=relative_file,
                    diagnostic_range=diagnostic_range,
                    metadata=metadata,
                ),
                source="pyright",
                severity=severity,
                code=code,
                message=message,
                file=relative_file,
                range=diagnostic_range,
                is_blocking=is_blocking,
                raw_source="pyright_lsp",
                metadata=metadata,
            )
        )

    return diagnostics


def _lsp_diagnostic_id(
    *,
    code: str,
    message: str,
    severity: DiagnosticSeverity,
    is_blocking: bool,
    file: str,
    diagnostic_range: DiagnosticRange | None,
    metadata: dict[str, str],
) -> str:
    payload = {
        "source": "pyright",
        "code": code,
        "message": message,
        "severity": severity.value,
        "is_blocking": is_blocking,
        "file": file,
        "range": diagnostic_range.model_dump() if diagnostic_range is not None else None,
        "metadata": metadata,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"pyright-lsp-{digest}"


def _lsp_severity(raw_severity: Any) -> tuple[DiagnosticSeverity, bool]:
    if isinstance(raw_severity, bool) or not isinstance(raw_severity, int):
        return DiagnosticSeverity.WARNING, False
    if raw_severity == 1:
        return DiagnosticSeverity.ERROR, True
    if raw_severity == 2:
        return DiagnosticSeverity.WARNING, False
    if raw_severity in {3, 4}:
        return DiagnosticSeverity.INFO, False
    return DiagnosticSeverity.WARNING, False


def _lsp_range(raw_range: Any) -> DiagnosticRange | None:
    if not isinstance(raw_range, dict):
        return None

    start = raw_range.get("start")
    end = raw_range.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None

    start_line = _zero_based_lsp_int(start.get("line"))
    start_column = _zero_based_lsp_int(start.get("character"))
    end_line = _zero_based_lsp_int(end.get("line"))
    end_column = _zero_based_lsp_int(end.get("character"))
    if (
        start_line is None
        or start_column is None
        or end_line is None
        or end_column is None
    ):
        return None
    if (end_line, end_column) < (start_line, start_column):
        return None

    return DiagnosticRange(
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def _zero_based_lsp_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value + 1


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value)
    if not text:
        return default
    try:
        text.encode("utf-8")
        json.dumps(text, ensure_ascii=False).encode("utf-8")
    except (TypeError, UnicodeEncodeError):
        return default
    return text


class PyrightLspSession(Protocol):
    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[RawLspDiagnostics | None, str | None]:
        """Collect Pyright LSP diagnostics for a shadow workspace."""
        ...


class PyrightLspManager(Protocol):
    def session_for(self, real_workspace_root: Path) -> PyrightLspSession:
        """Return the reusable Pyright LSP session for a real workspace root."""
        ...


class PyrightCliAdapter(Protocol):
    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        """Run the Pyright CLI fallback."""
        ...


class PyrightLspProvider:
    """Validate Pyright diagnostics through a reusable LSP session with CLI fallback."""

    def __init__(self, manager: PyrightLspManager, cli_adapter: PyrightCliAdapter) -> None:
        self.manager = manager
        self.cli_adapter = cli_adapter

    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        started_at = time.perf_counter()
        scope = request.requested_scope
        documents_opened = _changed_python_documents(request.changed_files)

        try:
            session = self.manager.session_for(request.real_workspace_root)
            raw_by_uri, fallback_reason = session.collect_diagnostics(
                shadow_root=request.shadow_workspace_root,
                changed_files=request.changed_files,
                scope=scope,
                timeout_seconds=request.timeout_budget_seconds,
            )
        except Exception as exc:
            return self._fallback(
                request=request,
                reason=str(exc) or exc.__class__.__name__,
                started_at=started_at,
                documents_opened=documents_opened,
            )
        finally:
            _close_shadow_root(self.manager, request.shadow_workspace_root)

        if raw_by_uri is not None and fallback_reason is None:
            diagnostics = _normalize_lsp_diagnostics_by_uri(
                raw_by_uri,
                request.shadow_workspace_root,
            )
            return ValidatorResult(
                provider="pyright",
                capabilities=[
                    ValidatorCapability.TYPE_DIAGNOSTICS,
                    ValidatorCapability.LSP_REUSE,
                    _scope_capability(scope),
                ],
                diagnostics=diagnostics,
                metadata={
                    "lsp_reused": True,
                    "fallback_to_cli": False,
                    "diagnostic_scope": scope.value,
                    "documents_opened": documents_opened,
                    "diagnostics_completed": True,
                },
                duration_ms=_duration_ms(started_at),
            )

        return self._fallback(
            request=request,
            reason=fallback_reason or "pyright LSP diagnostics unavailable",
            started_at=started_at,
            documents_opened=documents_opened,
        )

    def _fallback(
        self,
        *,
        request: ValidatorRequest,
        reason: str,
        started_at: float,
        documents_opened: list[str],
    ) -> ValidatorResult:
        cli_diagnostics, records = self.cli_adapter.check(
            request.shadow_workspace_root,
            request.changed_files,
            request.mode.value,
        )
        fallback_diagnostic = diagnostic_from_message(
            source="pyright",
            code="lsp_fallback",
            message=f"Pyright LSP unavailable; falling back to CLI: {reason}",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"fallback_reason": reason},
        )
        return ValidatorResult(
            provider="pyright",
            capabilities=[
                ValidatorCapability.TYPE_DIAGNOSTICS,
                ValidatorCapability.CLI_FALLBACK,
            ],
            diagnostics=[fallback_diagnostic, *cli_diagnostics],
            commands=records,
            metadata={
                "lsp_reused": False,
                "fallback_to_cli": True,
                "fallback_reason": reason,
                "diagnostic_scope": request.requested_scope.value,
                "documents_opened": documents_opened,
                "diagnostics_completed": True,
            },
            fallback_reason=reason,
            duration_ms=_duration_ms(started_at),
            timed_out=any(record.timed_out for record in records),
            output_truncated=any(
                record.stdout_truncated or record.stderr_truncated for record in records
            ),
        )


def _normalize_lsp_diagnostics_by_uri(
    raw_by_uri: RawLspDiagnostics,
    shadow_root: Path,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for uri, raw_diagnostics in raw_by_uri.items():
        diagnostics.extend(normalize_lsp_diagnostics(uri, raw_diagnostics, shadow_root))
    return diagnostics


def _changed_python_documents(changed_files: list[Path]) -> list[str]:
    return [
        changed_file.as_posix()
        for changed_file in changed_files
        if changed_file.suffix == ".py"
    ]


def _scope_capability(scope: ValidatorScope) -> ValidatorCapability:
    if scope is ValidatorScope.WORKSPACE:
        return ValidatorCapability.WORKSPACE_SCOPE
    return ValidatorCapability.CHANGED_FILE_SCOPE


def _close_shadow_root(manager: PyrightLspManager, shadow_root: Path) -> None:
    close_shadow_root = getattr(manager, "close_shadow_root", None)
    if callable(close_shadow_root):
        close_shadow_root(shadow_root)


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))
