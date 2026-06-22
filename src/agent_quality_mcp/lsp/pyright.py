"""Normalize Pyright Language Server Protocol diagnostics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_quality_mcp.models import Diagnostic, DiagnosticRange, DiagnosticSeverity


def lsp_uri_from_path(path: Path) -> str:
    """Return a local file URI for a filesystem path."""

    return path.resolve().as_uri()


def path_from_lsp_uri(uri: str) -> Path:
    """Return a resolved local path from a ``file://`` LSP document URI."""

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
    return f"pyright-{code}-{digest}"


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
    return text
