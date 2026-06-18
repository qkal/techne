"""Diagnostic normalization helpers for supported quality tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from agent_quality_mcp.models import Diagnostic, DiagnosticRange, DiagnosticSeverity

DiagnosticSource = Literal["system", "security", "workspace", "patch", "uv", "ruff", "pyright"]


def diagnostic_from_message(
    source: DiagnosticSource,
    code: str,
    message: str,
    severity: DiagnosticSeverity | str,
    is_blocking: bool,
    file: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Diagnostic:
    """Build a normalized diagnostic from trusted internal fields."""

    normalized_severity = _coerce_severity(severity, fallback=DiagnosticSeverity.WARNING)
    normalized_metadata = dict(metadata or {})
    return _build_diagnostic(
        source=source,
        code=code,
        message=message,
        severity=normalized_severity,
        is_blocking=is_blocking,
        file=file,
        metadata=normalized_metadata,
    )


def normalize_ruff(raw: Any) -> list[Diagnostic]:
    """Normalize Ruff JSON diagnostics into shared response diagnostics."""

    diagnostics: list[Diagnostic] = []
    if not isinstance(raw, list):
        return diagnostics

    for item in raw:
        if not isinstance(item, dict):
            continue

        code = _string_or_default(item.get("code"), "ruff")
        message = _string_or_default(item.get("message"), "Ruff diagnostic")
        file = _optional_string(item.get("filename") or item.get("file"))
        diagnostic_range = _ruff_range(item.get("location"), item.get("end_location"))
        is_fixable = item.get("fix") is not None or bool(item.get("fixable"))

        diagnostics.append(
            _build_diagnostic(
                source="ruff",
                code=code,
                message=message,
                severity=DiagnosticSeverity.WARNING,
                is_blocking=False,
                file=file,
                diagnostic_range=diagnostic_range,
                is_fixable=is_fixable,
                raw_source="ruff",
                metadata={"rule": code},
            )
        )

    return diagnostics


def normalize_pyright(raw: Any) -> list[Diagnostic]:
    """Normalize Pyright ``--outputjson`` diagnostics into shared diagnostics."""

    diagnostics: list[Diagnostic] = []
    if not isinstance(raw, dict):
        return diagnostics

    raw_diagnostics = raw.get("generalDiagnostics", [])
    if not isinstance(raw_diagnostics, list):
        return diagnostics

    for item in raw_diagnostics:
        if not isinstance(item, dict):
            continue

        severity, is_blocking = _pyright_severity(item.get("severity"))
        code = _string_or_default(item.get("rule") or item.get("code"), "pyright")
        message = _string_or_default(item.get("message"), "Pyright diagnostic")
        file = _optional_string(item.get("file"))

        diagnostics.append(
            _build_diagnostic(
                source="pyright",
                code=code,
                message=message,
                severity=severity,
                is_blocking=is_blocking,
                file=file,
                diagnostic_range=_pyright_range(item.get("range")),
                raw_source="pyright",
                metadata={"severity": _string_or_default(item.get("severity"), "unknown")},
            )
        )

    return diagnostics


def _build_diagnostic(
    *,
    source: DiagnosticSource,
    code: str,
    message: str,
    severity: DiagnosticSeverity,
    is_blocking: bool,
    file: str | None = None,
    diagnostic_range: DiagnosticRange | None = None,
    is_fixable: bool = False,
    raw_source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Diagnostic:
    normalized_metadata = _canonical_metadata_dict(metadata)
    diagnostic_id = _stable_diagnostic_id(
        source=source,
        code=code,
        message=message,
        severity=severity,
        is_blocking=is_blocking,
        file=file,
        diagnostic_range=diagnostic_range,
        is_fixable=is_fixable,
        metadata=normalized_metadata,
    )
    return Diagnostic(
        id=diagnostic_id,
        source=source,
        severity=severity,
        code=code,
        message=message,
        file=file,
        range=diagnostic_range,
        is_blocking=is_blocking,
        is_fixable=is_fixable,
        raw_source=raw_source,
        metadata=normalized_metadata,
    )


def _stable_diagnostic_id(
    *,
    source: DiagnosticSource,
    code: str,
    message: str,
    severity: DiagnosticSeverity,
    is_blocking: bool,
    file: str | None,
    diagnostic_range: DiagnosticRange | None,
    is_fixable: bool,
    metadata: dict[str, Any],
) -> str:
    payload = {
        "source": source,
        "code": code,
        "message": message,
        "severity": severity.value,
        "is_blocking": is_blocking,
        "file": file,
        "range": diagnostic_range.model_dump() if diagnostic_range is not None else None,
        "is_fixable": is_fixable,
        "metadata": metadata,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{source}-{code}-{digest}"


def _canonical_metadata_dict(metadata: dict[str, Any] | None) -> dict[str, Any]:
    canonical = _canonical_metadata(metadata or {})
    if isinstance(canonical, dict):
        return canonical
    return {}


def _canonical_metadata(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple):
        return [
            canonical_item
            for item in value
            if (canonical_item := _canonical_metadata(item)) is not _UnsupportedMetadata.VALUE
        ]
    if isinstance(value, dict):
        canonical_dict: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            canonical_item = _canonical_metadata(item)
            if canonical_item is not _UnsupportedMetadata.VALUE:
                canonical_dict[key] = canonical_item
        return canonical_dict
    return _UnsupportedMetadata.VALUE


class _UnsupportedMetadata:
    VALUE = object()


def _coerce_severity(
    severity: DiagnosticSeverity | str,
    *,
    fallback: DiagnosticSeverity,
) -> DiagnosticSeverity:
    if isinstance(severity, DiagnosticSeverity):
        return severity
    try:
        return DiagnosticSeverity(severity)
    except ValueError:
        return fallback


def _pyright_severity(raw_severity: Any) -> tuple[DiagnosticSeverity, bool]:
    severity = str(raw_severity or "").casefold()
    if severity == "error":
        return DiagnosticSeverity.ERROR, True
    if severity == "warning":
        return DiagnosticSeverity.WARNING, False
    if severity in {"information", "info", "hint"}:
        return DiagnosticSeverity.INFO, False
    return DiagnosticSeverity.WARNING, False


def _ruff_range(location: Any, end_location: Any) -> DiagnosticRange | None:
    if not isinstance(location, dict) or not isinstance(end_location, dict):
        return None

    start_line = _positive_int(location.get("row"))
    start_column = _positive_int(location.get("column"))
    end_line = _positive_int(end_location.get("row"))
    end_column = _positive_int(end_location.get("column"))
    return _diagnostic_range(start_line, start_column, end_line, end_column)


def _pyright_range(raw_range: Any) -> DiagnosticRange | None:
    if not isinstance(raw_range, dict):
        return None

    start = raw_range.get("start")
    end = raw_range.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None

    start_line = _zero_based_positive_int(start.get("line"))
    start_column = _zero_based_positive_int(start.get("character"))
    end_line = _zero_based_positive_int(end.get("line"))
    end_column = _zero_based_positive_int(end.get("character"))
    return _diagnostic_range(start_line, start_column, end_line, end_column)


def _diagnostic_range(
    start_line: int | None,
    start_column: int | None,
    end_line: int | None,
    end_column: int | None,
) -> DiagnosticRange | None:
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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    if integer <= 0:
        return None
    return integer


def _zero_based_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    if integer < 0:
        return None
    return integer + 1


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value)
    if not text:
        return default
    return text


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
