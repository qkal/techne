import json

from agent_quality_mcp.compression import compress_diagnostics
from agent_quality_mcp.diagnostics import (
    diagnostic_from_message,
    normalize_pyright,
    normalize_ruff,
)
from agent_quality_mcp.models import AgentQualityConfig, DiagnosticSeverity


def test_diagnostic_from_message_builds_blocking_diagnostic() -> None:
    diagnostic = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        file="pkg/app.py",
        metadata={"tool": "ruff"},
    )
    repeated = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        file="pkg/app.py",
        metadata={"tool": "ruff"},
    )

    assert diagnostic.source == "system"
    assert diagnostic.code == "tool_missing"
    assert diagnostic.message == "Required tool is missing"
    assert diagnostic.severity == DiagnosticSeverity.BLOCKER
    assert diagnostic.is_blocking is True
    assert diagnostic.file == "pkg/app.py"
    assert diagnostic.metadata == {"tool": "ruff"}
    assert diagnostic.id == repeated.id


def test_diagnostic_id_ignores_non_primitive_metadata_for_stability() -> None:
    first = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={"detail": object()},
    )
    second = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={"detail": object()},
    )

    assert first.id == second.id


def test_diagnostic_metadata_drops_unsupported_values_for_json_serialization() -> None:
    diagnostic = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={
            "tool": "ruff",
            "unsupported": object(),
            "nested": {"safe": True, "unsupported": object()},
            "items": ["safe", object(), 3],
        },
    )

    serialized = diagnostic.model_dump_json()

    assert diagnostic.metadata == {
        "tool": "ruff",
        "nested": {"safe": True},
        "items": ["safe", 3],
    }
    assert "<object object at" not in serialized
    assert "0x" not in serialized


def test_diagnostic_metadata_drops_non_finite_floats_for_strict_json() -> None:
    diagnostic = diagnostic_from_message(
        source="system",
        code="tool_missing",
        message="Required tool is missing",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
        metadata={
            "finite": 1.5,
            "nan": float("nan"),
            "inf": float("inf"),
            "negative_inf": float("-inf"),
            "nested": {"ok": 2.5, "bad": float("nan")},
            "items": [3.5, float("inf"), "safe"],
        },
    )

    json.dumps(diagnostic.model_dump(), allow_nan=False)

    assert diagnostic.metadata == {
        "finite": 1.5,
        "nested": {"ok": 2.5},
        "items": [3.5, "safe"],
    }


def test_normalize_ruff_preserves_rule_fixability_and_range() -> None:
    diagnostics = normalize_ruff(
        [
            {
                "code": "F401",
                "message": "`os` imported but unused",
                "filename": "pkg/app.py",
                "location": {"row": 2, "column": 1},
                "end_location": {"row": 2, "column": 10},
                "fix": {"message": "Remove unused import"},
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "ruff"
    assert diagnostic.code == "F401"
    assert diagnostic.message == "`os` imported but unused"
    assert diagnostic.file == "pkg/app.py"
    assert diagnostic.severity == DiagnosticSeverity.WARNING
    assert diagnostic.is_blocking is False
    assert diagnostic.is_fixable is True
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 2
    assert diagnostic.range.start_column == 1
    assert diagnostic.range.end_line == 2
    assert diagnostic.range.end_column == 10


def test_normalize_ruff_omits_float_coordinate_ranges() -> None:
    diagnostics = normalize_ruff(
        [
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/app.py",
                "location": {"row": 1.9, "column": 1},
                "end_location": {"row": 2, "column": 8},
            }
        ]
    )

    assert diagnostics[0].range is None


def test_normalize_ruff_omits_numeric_string_coordinate_ranges() -> None:
    diagnostics = normalize_ruff(
        [
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/app.py",
                "location": {"row": "1", "column": "1"},
                "end_location": {"row": "2", "column": "8"},
            }
        ]
    )

    assert diagnostics[0].range is None


def test_normalize_ruff_rejects_malformed_fixable_values() -> None:
    diagnostics = normalize_ruff(
        [
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/string_false.py",
                "fixable": "false",
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/string_zero.py",
                "fixable": "0",
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/list.py",
                "fixable": [1],
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/dict.py",
                "fixable": {"value": True},
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/true.py",
                "fixable": True,
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/fix.py",
                "fix": {"message": "Remove import"},
            },
        ]
    )

    assert [diagnostic.is_fixable for diagnostic in diagnostics] == [
        False,
        False,
        False,
        False,
        True,
        True,
    ]


def test_normalize_ruff_rejects_malformed_top_level_without_crashing() -> None:
    assert normalize_ruff(None) == []
    assert normalize_ruff({"not": "ruff-json"}) == []


def test_normalize_pyright_error_severity_is_blocking_error() -> None:
    diagnostics = normalize_pyright(
        {
            "generalDiagnostics": [
                {
                    "file": "pkg/app.py",
                    "severity": "error",
                    "message": '"str" is not assignable to "int"',
                    "rule": "reportAssignmentType",
                    "range": {
                        "start": {"line": 4, "character": 8},
                        "end": {"line": 4, "character": 12},
                    },
                }
            ]
        }
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "pyright"
    assert diagnostic.code == "reportAssignmentType"
    assert diagnostic.severity == DiagnosticSeverity.ERROR
    assert diagnostic.is_blocking is True
    assert diagnostic.file == "pkg/app.py"
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 5
    assert diagnostic.range.start_column == 9


def test_normalize_pyright_omits_float_coordinate_ranges() -> None:
    diagnostics = normalize_pyright(
        {
            "generalDiagnostics": [
                {
                    "file": "pkg/app.py",
                    "severity": "error",
                    "message": '"str" is not assignable to "int"',
                    "rule": "reportAssignmentType",
                    "range": {
                        "start": {"line": 4.2, "character": 8},
                        "end": {"line": 4, "character": 12},
                    },
                }
            ]
        }
    )

    assert diagnostics[0].range is None


def test_normalize_pyright_omits_numeric_string_coordinate_ranges() -> None:
    diagnostics = normalize_pyright(
        {
            "generalDiagnostics": [
                {
                    "file": "pkg/app.py",
                    "severity": "error",
                    "message": '"str" is not assignable to "int"',
                    "rule": "reportAssignmentType",
                    "range": {
                        "start": {"line": "4", "character": "8"},
                        "end": {"line": "4", "character": "12"},
                    },
                }
            ]
        }
    )

    assert diagnostics[0].range is None


def test_normalize_pyright_rejects_malformed_top_level_without_crashing() -> None:
    assert normalize_pyright(None) == []
    assert normalize_pyright([]) == []


def test_compress_diagnostics_deduplicates_non_blockers_and_truncates() -> None:
    blocker = diagnostic_from_message(
        source="pyright",
        code="reportAssignmentType",
        message="Type mismatch",
        severity=DiagnosticSeverity.ERROR,
        is_blocking=True,
        file="pkg/app.py",
    )
    duplicate_a = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/app.py",
    )
    duplicate_b = diagnostic_from_message(
        source="ruff",
        code="F401",
        message="Unused import",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/app.py",
    )
    extra = diagnostic_from_message(
        source="ruff",
        code="E501",
        message="Line too long",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        file="pkg/app.py",
    )

    compressed, summary = compress_diagnostics(
        [blocker, duplicate_a, duplicate_b, extra],
        AgentQualityConfig(max_diagnostics=2),
    )

    assert compressed == [blocker, duplicate_a]
    assert summary.total_diagnostics == 4
    assert summary.returned_diagnostics == 2
    assert summary.truncated is True
    assert summary.compressed_groups == [
        {
            "source": "ruff",
            "code": "F401",
            "message": "Unused import",
            "file": "pkg/app.py",
            "severity": "warning",
            "count": 2,
        }
    ]


def test_compress_diagnostics_preserves_all_blockers_over_limit() -> None:
    first = diagnostic_from_message(
        source="system",
        code="unsafe_patch",
        message="Patch is unsafe",
        severity=DiagnosticSeverity.BLOCKER,
        is_blocking=True,
    )
    second = diagnostic_from_message(
        source="pyright",
        code="reportGeneralTypeIssues",
        message="Type error",
        severity=DiagnosticSeverity.ERROR,
        is_blocking=True,
        file="pkg/app.py",
    )

    compressed, summary = compress_diagnostics(
        [first, second],
        AgentQualityConfig(max_diagnostics=1),
    )

    assert compressed == [first, second]
    assert summary.returned_diagnostics == 2
    assert summary.total_diagnostics == 2
    assert summary.truncated is False


def test_compress_diagnostics_preserves_distinct_ranges() -> None:
    diagnostics = normalize_ruff(
        [
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/app.py",
                "location": {"row": 1, "column": 1},
                "end_location": {"row": 1, "column": 8},
            },
            {
                "code": "F401",
                "message": "Unused import",
                "filename": "pkg/app.py",
                "location": {"row": 3, "column": 1},
                "end_location": {"row": 3, "column": 8},
            },
        ]
    )

    compressed, summary = compress_diagnostics(diagnostics, AgentQualityConfig())

    assert compressed == diagnostics
    assert summary.returned_diagnostics == 2
    assert summary.compressed_groups == []
